import asyncio
import base64
import html
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import backfill, clip, db, extract, feeds, opml, search, thumbs
from .config import Config, load

STATIC = Path(__file__).parent / "static"
ENTRY_COLS = "e.id, e.feed_id, e.url, e.title, e.author, e.published, e.summary, e.image, e.is_read, e.starred, e.saved_path, f.title AS feed_title"


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or load()
    conn = db.connect(cfg.db_path)
    lock = asyncio.Lock()  # one refresh at a time

    # ---- settings: saved in the database, with the environment supplying defaults ----
    def setting(key: str, default: str) -> str:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def inbox_dir() -> Path:
        return Path(setting("inbox_dir", str(cfg.inbox_dir))).expanduser()

    def wikilink_author() -> bool:
        return setting("wikilink_author", "1" if cfg.wikilink_author else "0") == "1"

    def refresh_minutes() -> int:
        try:
            return max(5, int(setting("refresh_minutes", str(cfg.refresh_minutes))))
        except ValueError:
            return cfg.refresh_minutes

    def git_enabled() -> bool:
        return setting("git_push", "1" if cfg.git_root else "0") == "1"

    def git_root() -> Path | None:
        """The checkout to commit in. The env-configured root wins while the inbox is unchanged from it."""
        if cfg.git_root and inbox_dir() == cfg.inbox_dir:
            return cfg.git_root
        return clip.git_toplevel(inbox_dir())

    async def refresh() -> int:
        if lock.locked():
            return 0
        async with lock:
            return await feeds.refresh_all(conn, cfg)

    async def poller():
        while True:
            await asyncio.sleep(refresh_minutes() * 60)
            try:
                await refresh()
            except Exception:
                pass

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(poller())
        yield
        task.cancel()

    app = FastAPI(title="forage", lifespan=lifespan)

    # ---- feeds ----
    @app.get("/api/feeds")
    def list_feeds():
        rows = conn.execute(
            """SELECT f.*, COALESCE(SUM(CASE WHEN e.is_read=0 THEN 1 END),0) AS unread
               FROM feeds f LEFT JOIN entries e ON e.feed_id=f.id GROUP BY f.id
               ORDER BY lower(f.folder), lower(f.title)"""
        ).fetchall()
        return [dict(r) for r in rows]

    @app.post("/api/feeds")
    async def add_feed(body: dict = Body(...)):
        url = (body.get("url") or "").strip()
        if not url:
            raise HTTPException(400, "url required")
        async with feeds.client(cfg) as http:
            try:
                feed_url = await feeds.discover(http, url)
            except Exception as exc:
                raise HTTPException(422, f"couldn't find a feed: {exc}")
        cur = conn.execute(
            "INSERT OR IGNORE INTO feeds (url, folder, title) VALUES (?,?,?)",
            (feed_url, (body.get("folder") or "").strip(), ""),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM feeds WHERE url=?", (feed_url,)).fetchone()
        async with feeds.client(cfg) as http:
            await feeds.refresh_feed(conn, cfg, http, row)
        return dict(conn.execute("SELECT * FROM feeds WHERE id=?", (row["id"],)).fetchone()) | {"added": bool(cur.rowcount)}

    def settings_view() -> dict:
        inbox = inbox_dir()
        root = git_root()
        return {
            "inbox_dir": str(inbox),
            "inbox_exists": inbox.is_dir(),
            "inbox_writable": os.access(inbox if inbox.exists() else inbox.parent, os.W_OK),
            "git_push": git_enabled(),
            "git_root": str(root) if root else None,
            "sync_script": bool(cfg.sync_script),
            "wikilink_author": wikilink_author(),
            "refresh_minutes": refresh_minutes(),
        }

    @app.get("/api/settings")
    def get_settings():
        return settings_view()

    @app.put("/api/settings")
    def put_settings(body: dict = Body(...)):
        updates: dict[str, str] = {}
        if "inbox_dir" in body:
            raw = str(body["inbox_dir"]).strip()
            p = Path(raw).expanduser()
            if not raw or not p.is_absolute():
                raise HTTPException(422, "Inbox folder must be an absolute path")
            try:
                p.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise HTTPException(422, f"Can't use that folder: {exc.strerror or exc}")
            if not os.access(p, os.W_OK):
                raise HTTPException(422, "That folder isn't writable by Forage")
            updates["inbox_dir"] = str(p)
        if "git_push" in body:
            updates["git_push"] = "1" if body["git_push"] else "0"
        if "wikilink_author" in body:
            updates["wikilink_author"] = "1" if body["wikilink_author"] else "0"
        if "refresh_minutes" in body:
            try:
                n = int(body["refresh_minutes"])
            except (TypeError, ValueError):
                raise HTTPException(422, "Refresh interval must be a number of minutes")
            if not 5 <= n <= 1440:
                raise HTTPException(422, "Refresh interval must be between 5 and 1440 minutes")
            updates["refresh_minutes"] = str(n)
        for k, v in updates.items():
            conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
        conn.commit()
        return settings_view()

    @app.get("/api/find")
    async def find_feeds(q: str = Query(..., min_length=1)):
        async with feeds.client(cfg) as http:
            results = await search.search(http, q)
        have = {search.norm(r["url"]) for r in conn.execute("SELECT url FROM feeds")}
        return [r | {"subscribed": search.norm(r["url"]) in have} for r in results]

    @app.patch("/api/feeds/{fid}")
    def patch_feed(fid: int, body: dict = Body(...)):
        for key in ("title", "folder"):
            if key in body:
                conn.execute(f"UPDATE feeds SET {key}=? WHERE id=?", (str(body[key]).strip(), fid))
        conn.commit()
        return {"ok": True}

    @app.post("/api/folders/rename")
    def rename_folder(body: dict = Body(...)):
        old, new = (body.get("from") or "").strip(), (body.get("to") or "").strip()
        if not old or not new:
            raise HTTPException(400, "from and to required")
        # Renaming onto an existing folder name merges the two.
        n = conn.execute("UPDATE feeds SET folder=? WHERE folder=?", (new, old)).rowcount
        conn.commit()
        return {"moved": n}

    @app.post("/api/folders/delete")
    def delete_folder(body: dict = Body(...)):
        name = (body.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "name required")
        n = conn.execute("UPDATE feeds SET folder='' WHERE folder=?", (name,)).rowcount  # feeds are kept, just unfiled
        conn.commit()
        return {"unfiled": n}

    # ---- backfill: older articles than the feed itself carries, and missing feature images ----
    jobs: dict[tuple[int, str], dict] = {}
    bg_tasks: set = set()

    def start_job(fid: int, kind: str, make):
        row = conn.execute("SELECT * FROM feeds WHERE id=?", (fid,)).fetchone()
        if not row:
            raise HTTPException(404, "no such feed")
        if jobs.get((fid, kind), {}).get("state") == "running":
            return jobs[(fid, kind)]
        st = jobs[(fid, kind)] = {"state": "running", "method": None, "new": 0, "checked": 0, "message": ""}

        async def work():
            try:
                st.update(await make(row, st), state="done")
            except Exception as exc:
                st.update(state="error", message=str(exc)[:200])

        task = asyncio.create_task(work())
        bg_tasks.add(task)
        task.add_done_callback(bg_tasks.discard)
        return st

    @app.post("/api/feeds/{fid}/backfill")
    async def start_backfill(fid: int, body: dict | None = Body(None)):
        try:
            limit = int((body or {}).get("limit", 50))
        except (TypeError, ValueError):
            limit = 50
        return start_job(fid, "backfill", lambda row, st: backfill.run(conn, cfg, row, limit, st))

    @app.get("/api/feeds/{fid}/backfill")
    def backfill_status(fid: int):
        return jobs.get((fid, "backfill"), {"state": "idle"})

    @app.post("/api/feeds/{fid}/images")
    async def start_fill_images(fid: int):
        return start_job(fid, "images", lambda row, st: backfill.fill_images(conn, cfg, row, st))

    @app.get("/api/feeds/{fid}/images")
    def fill_images_status(fid: int):
        return jobs.get((fid, "images"), {"state": "idle"})

    @app.delete("/api/feeds/{fid}")
    def delete_feed(fid: int):
        conn.execute("DELETE FROM feeds WHERE id=?", (fid,))
        conn.commit()
        return {"ok": True}

    @app.post("/api/refresh")
    async def refresh_now():
        return {"new": await refresh()}

    @app.post("/api/opml")
    async def import_opml(request: Request):
        try:
            items = opml.parse_opml(await request.body())
        except Exception as exc:
            raise HTTPException(400, f"bad OPML: {exc}")
        added = 0
        for it in items:
            added += conn.execute(
                "INSERT OR IGNORE INTO feeds (url, title, folder) VALUES (?,?,?)",
                (it["url"], it["title"], it["folder"]),
            ).rowcount
        conn.commit()
        asyncio.create_task(refresh())
        return {"added": added, "total": len(items)}

    @app.get("/api/opml")
    def export_opml():
        rows = conn.execute("SELECT * FROM feeds ORDER BY lower(folder), lower(title)").fetchall()
        return Response(opml.build_opml(rows), media_type="text/x-opml",
                        headers={"Content-Disposition": 'attachment; filename="forage.opml"'})

    # ---- entries ----
    @app.get("/api/entries")
    def list_entries(
        feed: int | None = None, folder: str | None = None, unread: bool = False,
        starred: bool = False, q: str | None = None, before: str | None = None,
        limit: int = Query(60, le=200),
    ):
        where, args = [], []
        if feed is not None:
            where.append("e.feed_id=?"); args.append(feed)
        if folder is not None:
            where.append("f.folder=?"); args.append(folder)
        if unread:
            where.append("e.is_read=0")
        if starred:
            where.append("e.starred=1")
        if q:
            where.append("(e.title LIKE ? OR e.summary LIKE ?)"); args += [f"%{q}%"] * 2
        if before:  # cursor: "published|id"
            pub, _, eid = before.partition("|")
            where.append("(e.published < ? OR (e.published = ? AND e.id < ?))"); args += [pub, pub, int(eid or 0)]
        sql = f"SELECT {ENTRY_COLS} FROM entries e JOIN feeds f ON f.id=e.feed_id"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY e.published DESC, e.id DESC LIMIT ?"
        rows = [dict(r) for r in conn.execute(sql, (*args, limit)).fetchall()]
        for r in rows:  # rows stored before entities were decoded at ingest
            r["title"], r["summary"] = html.unescape(r["title"]), html.unescape(r["summary"])
            if r["image"]:  # the hash busts the browser's long-lived cache when the entry's image changes
                r["thumb"] = f"/thumb/{r['id']}?v={thumbs.key(r['image'])[:10]}"
        return rows

    @app.get("/thumb/{eid}")
    async def thumb(eid: int):
        row = conn.execute("SELECT image FROM entries WHERE id=?", (eid,)).fetchone()
        if not row or not row["image"]:
            raise HTTPException(404, "no image")
        async with feeds.client(cfg) as http:
            path = await thumbs.get(http, cfg.data_dir / "thumbs", row["image"])
        if not path:
            raise HTTPException(404, "no usable image")
        return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "public, max-age=31536000, immutable"})

    def _entry(eid: int) -> sqlite3.Row:
        row = conn.execute(
            "SELECT e.*, f.title AS feed_title FROM entries e JOIN feeds f ON f.id=e.feed_id WHERE e.id=?", (eid,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "no such entry")
        return row

    async def _article_html(row: sqlite3.Row) -> str:
        """Best readable body: cached extraction, else feed content if it's full, else fetch the page."""
        if row["full_html"]:
            return row["full_html"]
        if row["feed_html"] and extract.looks_full(row["feed_html"], row["summary"]):
            return row["feed_html"]
        html = None
        if row["url"]:
            try:
                async with feeds.client(cfg) as http:
                    html = await feeds.fetch_article(http, row["url"])
            except Exception:
                html = None
        html = extract.strip_leading_title(html or row["feed_html"] or f"<p>{row['summary']}</p>", row["title"])
        conn.execute("UPDATE entries SET full_html=? WHERE id=?", (html, row["id"]))
        conn.commit()
        return html

    @app.get("/api/entries/{eid}")
    async def get_entry(eid: int):
        row = _entry(eid)
        html = await _article_html(row)
        conn.execute("UPDATE entries SET is_read=1 WHERE id=?", (eid,))
        conn.commit()
        d = {k: row[k] for k in ("id", "feed_id", "url", "title", "author", "published", "starred", "saved_path", "feed_title")}
        return d | {"html": html, "is_read": 1}

    @app.post("/api/entries/{eid}/state")
    def set_state(eid: int, body: dict = Body(...)):
        _entry(eid)
        for key, col in (("read", "is_read"), ("starred", "starred")):
            if key in body:
                conn.execute(f"UPDATE entries SET {col}=? WHERE id=?", (1 if body[key] else 0, eid))
        conn.commit()
        return {"ok": True}

    @app.post("/api/mark-read")
    def mark_read(body: dict = Body(...)):
        where, args = ["is_read=0"], []
        if body.get("feed") is not None:
            where.append("feed_id=?"); args.append(body["feed"])
        if body.get("folder") is not None:
            where.append("feed_id IN (SELECT id FROM feeds WHERE folder=?)"); args.append(body["folder"])
        if body.get("older_than"):
            where.append("published<=?"); args.append(body["older_than"])
        n = conn.execute(f"UPDATE entries SET is_read=1 WHERE {' AND '.join(where)}", args).rowcount
        conn.commit()
        return {"marked": n}

    @app.post("/api/entries/{eid}/save")
    async def save_entry(eid: int):
        row = _entry(eid)
        if row["saved_path"]:
            return {"path": row["saved_path"], "already": True}
        html = await _article_html(row)
        path = clip.write(inbox_dir(), dict(row), html, wikilink_author())
        conn.execute("UPDATE entries SET saved_path=?, is_read=1 WHERE id=?", (path.name, eid))
        conn.commit()
        root = git_root() if git_enabled() else None
        if root:
            asyncio.create_task(_publish(root, path, row["title"]))  # background: don't make the tap wait on a push
        return {"path": path.name, "already": False}

    async def _publish(root: Path, path: Path, title: str):
        # The sync hook is server-side only (env): letting the UI set a script would be remote code execution.
        script = cfg.sync_script if root == cfg.git_root else None
        err = await asyncio.to_thread(clip.commit_and_push, root, path, title, script)
        if err:
            print(f"forage: git publish of {path.name} failed: {err}", flush=True)

    @app.get("/")
    def index():
        # The touch icon is inlined as a data URI: iOS can fetch a linked icon in a separate
        # step that doesn't reach a tailnet-only host, and then shows a letter tile instead.
        icon = base64.b64encode((STATIC / "icons" / "apple-touch-icon.png").read_bytes()).decode()
        page = (STATIC / "index.html").read_text().replace("{{TOUCH_ICON}}", "data:image/png;base64," + icon)
        return Response(page, media_type="text/html", headers={"Cache-Control": "no-cache"})

    @app.get("/apple-touch-icon.png")
    @app.get("/apple-touch-icon-precomposed.png")
    def touch_icon():  # iOS probes the root path when it doesn't use the <link> tag
        return FileResponse(STATIC / "icons" / "apple-touch-icon.png")

    @app.get("/sw.js")
    def sw():  # served from root so its scope covers the whole app
        return FileResponse(STATIC / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})

    @app.middleware("http")
    async def revalidate_code(request: Request, call_next):
        # No Cache-Control + a Last-Modified header lets Safari reuse a stale app.js for days (heuristic caching).
        resp = await call_next(request)
        if request.url.path.endswith((".js", ".css")):
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
