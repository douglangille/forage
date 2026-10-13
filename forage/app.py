import asyncio
import base64
import html
import json
import os
import re
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import backfill, clip, db, extract, feeds, opml, organize, search, summarize, thumbs, youtube
from .config import Config, load

STATIC = Path(__file__).parent / "static"
ENTRY_COLS = "e.id, e.feed_id, e.url, e.title, e.author, e.published, e.summary, e.image, e.is_read, e.starred, f.title AS feed_title"


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or load()
    conn = db.connect(cfg.db_path)
    lock = asyncio.Lock()  # one refresh at a time
    bg_tasks: set = set()  # asyncio keeps only weak references to tasks, so hold them until they finish
    publish_lock = asyncio.Lock()  # one git commit/push at a time in the checkout
    write_lock = asyncio.Lock()  # one clip written at a time, so two saves can't pick the same file name
    sum_locks: dict[int, asyncio.Lock] = {}  # per entry, so a double tap makes one request
    fetch_failed: dict[int, float] = {}  # entry id -> when its page fetch last failed (not cached as the article)
    publish_state = {"error": ""}
    thumbs.prune(cfg.data_dir / "thumbs")

    def spawn(coro):
        task = asyncio.create_task(coro)
        bg_tasks.add(task)
        task.add_done_callback(bg_tasks.discard)
        return task

    def http_only(url: str) -> str:
        """Feed links are untrusted: a javascript: or file: link must never reach an href or window.open."""
        return url if re.match(r"^https?://", url or "", re.I) else ""

    # ---- settings: saved in the database, with the environment supplying defaults ----
    def setting(key: str, default: str) -> str:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def inbox_dir() -> Path:
        return Path(setting("inbox_dir", str(cfg.inbox_dir))).expanduser()

    def wikilink_author() -> bool:
        return setting("wikilink_author", "1" if cfg.wikilink_author else "0") == "1"

    def organize_model() -> str:
        return setting("organize_model", "") or cfg.summary_model

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

    async def refresh() -> int | None:
        if lock.locked():
            return None  # already running
        async with lock:
            return await feeds.refresh_all(conn, cfg)

    async def poller():
        while True:
            await asyncio.sleep(refresh_minutes() * 60)
            try:
                await refresh()
            except Exception as exc:
                print(f"forage: scheduled refresh failed: {exc!r}", flush=True)

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
            """SELECT f.*, COALESCE(SUM(CASE WHEN e.is_read=0 THEN 1 END),0) AS unread, MAX(e.published) AS last_post
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
        if cur.rowcount and body.get("suggest") and cfg.summary_key:
            await suggest_folder(row["id"])
        return dict(conn.execute("SELECT * FROM feeds WHERE id=?", (row["id"],)).fetchone()) | {"added": bool(cur.rowcount)}

    def feed_inputs(where: str = "", args: tuple = ()) -> list[dict]:
        """Feeds as the model sees them: id, name, site and the TITLES newest post titles. No feed URL ever goes in."""
        out = []
        for f in conn.execute(f"SELECT id, title, site_url FROM feeds {where} ORDER BY id", args).fetchall():
            titles = [html.unescape(r["title"]) for r in conn.execute(
                "SELECT title FROM entries WHERE feed_id=? AND title!='' ORDER BY published DESC LIMIT ?", (f["id"], organize.TITLES))]
            out.append({"id": f["id"], "title": f["title"], "site_url": f["site_url"], "titles": titles})
        return out

    async def suggest_folder(fid: int) -> None:
        """File a just-added feed in one of the folders that already exist. Best effort: any failure leaves it unfiled."""
        folders = sorted({r["folder"] for r in conn.execute("SELECT folder FROM feeds WHERE folder!=''")})
        feed = feed_inputs("WHERE id=?", (fid,))
        if not folders or not feed or not _summary_budget():
            return
        try:
            folder = await organize.pick(cfg.summary_key, organize_model(), feed[0], folders, cfg.user_agent, cfg.summary_allow_training, cfg.summary_fallback_model)
        except RuntimeError:
            return
        conn.execute("UPDATE feeds SET folder=? WHERE id=?", (folder, fid))
        conn.commit()

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
            "publish_error": publish_state["error"],
            "ai_on": bool(cfg.summary_key),
            "organize_model": setting("organize_model", ""),
            "default_model": cfg.summary_model,
            "folders_undo": bool(setting("folders_undo", "")),
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
        if "organize_model" in body:
            updates["organize_model"] = str(body["organize_model"]).strip()[:100]
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

    # ---- AI folder sorting: a proposal to look at, then an apply that can be undone ----
    def put_setting(key: str, value: str) -> None:
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    @app.post("/api/folders/organize")
    async def organize_folders():
        """Propose folders for every feed from a clean slate. Changes nothing: the reader looks, then calls /apply."""
        if not cfg.summary_key:
            raise HTTPException(404, "AI isn't set up on this server.")
        inputs = feed_inputs()
        if len(inputs) < 2:
            raise HTTPException(422, "Add a few more feeds first.")
        if not _summary_budget():
            raise HTTPException(429, f"Today's limit of {cfg.summary_daily_cap} AI calls is used up.")
        try:
            assign, model = await organize.propose(cfg.summary_key, organize_model(), inputs, cfg.user_agent, cfg.summary_allow_training, cfg.summary_fallback_model)
        except RuntimeError as exc:
            raise HTTPException(502, str(exc))
        names = {f["id"]: f["title"] or f["url"] for f in conn.execute("SELECT id, title, url FROM feeds")}
        groups: dict[str, list] = {}
        for fid, folder in assign.items():
            groups.setdefault(folder, []).append({"id": fid, "title": names.get(fid, "")})
        order = sorted(groups, key=lambda n: (not n, n.lower()))
        return {"model": model, "assign": {str(k): v for k, v in assign.items()},
                "folders": [{"name": n, "feeds": sorted(groups[n], key=lambda f: f["title"].lower())} for n in order]}

    @app.post("/api/folders/apply")
    def apply_folders(body: dict = Body(...)):
        """Set folders from {"assign": {feed id: folder}}. The folders as they were are kept for one /undo."""
        assign = body.get("assign")
        if not isinstance(assign, dict):
            raise HTTPException(400, "assign required")
        have = {r["id"]: r["folder"] for r in conn.execute("SELECT id, folder FROM feeds")}
        new = {}
        for k, v in assign.items():
            try:
                fid = int(k)
            except ValueError:
                continue
            if fid in have:
                new[fid] = organize.folder_name(v)
        before = {str(fid): have[fid] for fid in new}
        put_setting("folders_undo", json.dumps(before))
        for fid, folder in new.items():
            conn.execute("UPDATE feeds SET folder=? WHERE id=?", (folder, fid))
        conn.commit()
        return {"moved": sum(1 for fid in new if new[fid] != have[fid])}

    @app.post("/api/folders/undo")
    def undo_folders():
        try:
            before = json.loads(setting("folders_undo", ""))
        except ValueError:
            before = None
        if not isinstance(before, dict):
            raise HTTPException(404, "Nothing to undo.")
        n = 0
        for k, folder in before.items():
            n += conn.execute("UPDATE feeds SET folder=? WHERE id=?", (folder, int(k))).rowcount
        conn.execute("DELETE FROM settings WHERE key='folders_undo'")
        conn.commit()
        return {"restored": n}

    # ---- backfill: older articles than the feed itself carries ----
    jobs: dict[tuple[int, str], dict] = {}

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

        spawn(work())
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

    @app.delete("/api/feeds/{fid}")
    def delete_feed(fid: int):
        conn.execute("DELETE FROM feeds WHERE id=?", (fid,))
        conn.commit()
        return {"ok": True}

    @app.post("/api/refresh")
    async def refresh_now():
        n = await refresh()
        return {"new": n or 0, "busy": n is None}

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
        spawn(refresh())
        return {"added": added, "total": len(items)}

    @app.get("/api/opml")
    def export_opml():
        rows = conn.execute("SELECT * FROM feeds ORDER BY lower(folder), lower(title)").fetchall()
        return Response(opml.build_opml(rows), media_type="text/x-opml",
                        headers={"Content-Disposition": 'attachment; filename="forage.opml"'})

    # ---- entries ----
    def _since(hours: int) -> str:
        return (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")

    @app.get("/api/entries")
    async def list_entries(
        feed: int | None = None, folder: str | None = None, unread: bool = False,
        starred: bool = False, q: str | None = None, before: str | None = None,
        since_hours: int | None = Query(None, ge=1, le=24 * 365), cards: bool = False, limit: int = Query(60, le=200),
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
        if since_hours:
            where.append("e.published>=?"); args.append(_since(since_hours))
        if q:
            where.append("(e.title LIKE ? OR e.summary LIKE ? OR e.ai_summary LIKE ?)"); args += [f"%{q}%"] * 3
        if before:  # cursor: "published|id"
            pub, _, eid = before.partition("|")
            try:
                eid_n = int(eid or 0)
            except ValueError:
                raise HTTPException(400, "bad cursor")
            where.append("(e.published < ? OR (e.published = ? AND e.id < ?))"); args += [pub, pub, eid_n]
        sql = f"SELECT {ENTRY_COLS} FROM entries e JOIN feeds f ON f.id=e.feed_id"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY e.published DESC, e.id DESC LIMIT ?"
        rows = [dict(r) for r in conn.execute(sql, (*args, limit)).fetchall()]
        for r in rows:  # rows stored before entities were decoded at ingest
            r["title"], r["summary"] = html.unescape(r["title"]), html.unescape(r["summary"])
            r["url"] = http_only(r["url"])
            if r["image"]:  # the hash busts the browser's long-lived cache when the entry's image changes
                v = thumbs.key(r["image"])[:10]
                r["thumb"] = f"/thumb/{r['id']}?v={v}"
        if cards:  # the card picture is only offered once it exists and is usable, with its shape, so a card with no picture holds no room for one
            pics = [r for r in rows if r["image"]]
            async with feeds.client(cfg) as http:
                tasks = {r["id"]: asyncio.ensure_future(thumbs.card_ratio(http, cfg.data_dir / "thumbs", r["image"])) for r in pics}
                await asyncio.wait(tasks.values(), timeout=10)  # slow ones go on building for the next load
                for r in pics:
                    t = tasks[r["id"]]
                    ratio = t.result() if t.done() and not t.cancelled() and not t.exception() else None
                    if ratio:
                        r["card"] = f"/thumb/{r['id']}?card=2&v={thumbs.key(r['image'])[:10]}"
                        r["card_ratio"] = ratio
        return rows

    @app.get("/thumb/{eid}")
    async def thumb(eid: int, card: int = 0):
        row = conn.execute("SELECT image FROM entries WHERE id=?", (eid,)).fetchone()
        if not row or not row["image"]:
            raise HTTPException(404, "no image")
        async with feeds.client(cfg) as http:
            path = await thumbs.get(http, cfg.data_dir / "thumbs", row["image"], bool(card))
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
        """Best readable body: cached extraction, else feed content if it's full, else fetch the page.
        A failed fetch is never cached as the article, or one timeout would leave the summary there for good."""
        vid = youtube.video_id(row["guid"], row["url"])
        if vid:  # the page would only give YouTube's chrome; read the player and the feed's description
            return youtube.player_html(vid) + extract.sanitize(youtube.reader_description(row["feed_html"]), row["url"])
        if row["full_html"]:
            return row["full_html"]
        if row["feed_html"] and extract.looks_full(row["feed_html"], row["summary"]):
            return extract.strip_leading_title(row["feed_html"], row["title"])
        fetched = None
        if row["url"] and time.time() - fetch_failed.get(row["id"], 0) > 300:
            try:
                async with feeds.client(cfg) as http:
                    fetched = await feeds.fetch_article(http, row["url"])
            except Exception:
                fetched = None
            if not fetched:
                fetch_failed[row["id"]] = time.time()
        if fetched:
            body = extract.strip_leading_title(fetched, row["title"])
            conn.execute("UPDATE entries SET full_html=? WHERE id=?", (body, row["id"]))
            conn.commit()
            fetch_failed.pop(row["id"], None)
            return body
        return extract.strip_leading_title(row["feed_html"] or f"<p>{html.escape(row['summary'])}</p>", row["title"])

    @app.get("/api/entries/{eid}")
    async def get_entry(eid: int):
        row = _entry(eid)
        body = await _article_html(row)
        conn.execute("UPDATE entries SET is_read=1 WHERE id=?", (eid,))
        conn.commit()
        d = {k: row[k] for k in ("id", "feed_id", "url", "title", "author", "published", "starred", "feed_title")}
        return d | {"html": body, "url": http_only(row["url"]), "is_read": 1,
                    "summary_on": bool(cfg.summary_key), "summary": row["ai_summary"], "summary_model": row["ai_model"]}

    def _summary_budget() -> bool:
        """Spend one of today's summaries, or say there are none left. Forage has no login, so the cap is the guard on the key."""
        day = datetime.now().strftime("%Y-%m-%d")
        used = int(setting("sum_n", "0")) if setting("sum_day", "") == day else 0
        if used >= cfg.summary_daily_cap:
            return False
        for k, v in (("sum_day", day), ("sum_n", str(used + 1))):
            conn.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
        return True

    @app.post("/api/entries/{eid}/summarize")
    async def summarize_entry(eid: int, body: dict | None = Body(None)):
        """Bullet summary of one article, on request, kept on the entry. {"force": true} makes a fresh one."""
        if not cfg.summary_key:
            raise HTTPException(404, "Summaries aren't set up on this server.")
        _entry(eid)
        async with sum_locks.setdefault(eid, asyncio.Lock()):
            row = _entry(eid)
            if row["ai_summary"] and not (body or {}).get("force"):
                return {"summary": row["ai_summary"], "model": row["ai_model"], "cached": True, "truncated": False}
            vid = youtube.video_id(row["guid"], row["url"])
            note = ""
            if vid:  # a video is summarized from its description and captions
                snips = await asyncio.to_thread(youtube.fetch_transcript, vid)
                text = youtube.description_text(row["feed_html"]) + ("\n\nTranscript:\n" + " ".join(t for _, t in snips) if snips else "")
                note = "" if snips else "No captions were available, so this is from the description only."
            else:
                text = summarize.plain_text(await _article_html(row))
            text = f"Title: {row['title']}\n\n{text}"
            if len(text) < 300:
                raise HTTPException(422, "There isn't enough text here to summarize.")
            if not _summary_budget():
                raise HTTPException(429, f"Today's limit of {cfg.summary_daily_cap} summaries is used up.")
            try:
                out, model = await summarize.summarize(cfg.summary_key, cfg.summary_model, text, cfg.user_agent, cfg.summary_allow_training, cfg.summary_fallback_model)
            except RuntimeError as exc:
                raise HTTPException(502, str(exc))
            conn.execute("UPDATE entries SET ai_summary=?, ai_model=?, ai_at=? WHERE id=?", (out, model, feeds.now_iso(), eid))
        sum_locks.pop(eid, None)
        return {"summary": out, "model": model, "cached": False, "truncated": len(text) > summarize.MAX_CHARS, "note": note}

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
        if body.get("since_hours"):
            where.append("published>=?"); args.append(_since(int(body["since_hours"])))
        n = conn.execute(f"UPDATE entries SET is_read=1 WHERE {' AND '.join(where)}", args).rowcount
        conn.commit()
        return {"marked": n}

    async def _clip_parts(row: sqlite3.Row, transcript: bool = True):
        """The entry as the clip's frontmatter sees it, plus a markdown body when the page isn't the article (a video:
        the player and its transcript, fetched now since only a save needs it)."""
        entry = dict(row) | {"url": http_only(row["url"])}
        vid = youtube.video_id(row["guid"], row["url"])
        if not vid:
            return entry, None
        snips = await asyncio.to_thread(youtube.fetch_transcript, vid) if transcript else []
        return entry | {"url": youtube.watch_url(vid)}, youtube.clip_body(vid, snips, youtube.description_text(row["feed_html"]))

    @app.get("/api/entries/{eid}/clip")
    async def entry_clip(eid: int, transcript: bool = True):
        """The clip as text, for a client that writes it into the vault itself (the Obsidian plugin). transcript=0 skips the
        video transcript fetch, for a client that only wants to read the entry."""
        row = _entry(eid)
        body = await _article_html(row)
        entry, body_md = await _clip_parts(row, transcript)
        text = await asyncio.to_thread(clip.render, entry, body, wikilink_author(), datetime.now().strftime("%Y-%m-%d"), body_md)
        meta = {k: row[k] for k in ("id", "feed_id", "title", "author", "published", "starred", "is_read", "feed_title")}
        return meta | {"url": entry["url"], "filename": clip.filename(row["title"]) + ".md", "markdown": text,
                       "summary_on": bool(cfg.summary_key), "summary": row["ai_summary"], "summary_model": row["ai_model"]}

    @app.post("/api/entries/{eid}/save")
    async def save_entry(eid: int):
        """Write a clip. Saving is not tracked: every call writes a new file (a repeat gets a numbered name)."""
        row = _entry(eid)
        body = await _article_html(row)
        entry, body_md = await _clip_parts(row)
        async with write_lock:
            path = await asyncio.to_thread(clip.write, inbox_dir(), entry, body, wikilink_author(), body_md)
        conn.execute("UPDATE entries SET is_read=1 WHERE id=?", (eid,))
        conn.commit()
        root = git_root() if git_enabled() else None
        if root:
            spawn(_publish(root, path, row["title"]))  # background: don't make the tap wait on a push
        return {"path": path.name}

    async def _publish(root: Path, path: Path, title: str):
        # The sync hook is server-side only (env): letting the UI set a script would be remote code execution.
        script = cfg.sync_script if root == cfg.git_root else None
        async with publish_lock:
            err = await asyncio.to_thread(clip.commit_and_push, root, path, title, script)
        publish_state["error"] = f"{path.name}: {err}" if err else ""
        if err:
            print(f"forage: git publish of {path.name} failed: {err}", flush=True)

    @app.get("/")
    def index():
        # The touch icon is inlined as a data URI: iOS can fetch a linked icon in a separate
        # step that doesn't reach a tailnet-only host, and then shows a letter tile instead.
        icon = base64.b64encode((STATIC / "icons" / "apple-touch-icon.png").read_bytes()).decode()
        page = (STATIC / "index.html").read_text().replace("{{TOUCH_ICON}}", "data:image/png;base64," + icon)
        return Response(page, media_type="text/html", headers={"Cache-Control": "no-cache"})

    @app.get("/favicon.ico")
    def favicon():  # browsers that skip the <link> tags (Safari on the desktop, bookmarks, feed readers) probe this path
        return FileResponse(STATIC / "icons" / "icon-192.png", media_type="image/png")

    @app.get("/apple-touch-icon.png")
    @app.get("/apple-touch-icon-precomposed.png")
    def touch_icon():  # iOS probes the root path when it doesn't use the <link> tag
        return FileResponse(STATIC / "icons" / "apple-touch-icon.png")

    @app.get("/sw.js")
    def sw():  # served from root so its scope covers the whole app
        return FileResponse(STATIC / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})

    @app.middleware("http")
    async def no_cross_site_writes(request: Request, call_next):
        # No login, so a page open in another tab must not be able to fire a write at us. Browsers label every
        # request with Sec-Fetch-Site; curl and the plugin send none, and the plugin may send X-Forage instead.
        if (request.method not in ("GET", "HEAD", "OPTIONS")
                and request.headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none")
                and not request.headers.get("x-forage")):
            return Response("cross-site write refused", status_code=403)
        return await call_next(request)

    @app.middleware("http")
    async def revalidate_code(request: Request, call_next):
        # No Cache-Control + a Last-Modified header lets Safari reuse a stale app.js for days (heuristic caching).
        resp = await call_next(request)
        if request.url.path.endswith((".js", ".css")):
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
