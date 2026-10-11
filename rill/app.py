import asyncio
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import clip, db, extract, feeds, opml
from .config import Config, load

STATIC = Path(__file__).parent / "static"
ENTRY_COLS = "e.id, e.feed_id, e.url, e.title, e.author, e.published, e.summary, e.image, e.is_read, e.starred, e.saved_path, f.title AS feed_title"


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or load()
    conn = db.connect(cfg.db_path)
    lock = asyncio.Lock()  # one refresh at a time

    async def refresh() -> int:
        if lock.locked():
            return 0
        async with lock:
            return await feeds.refresh_all(conn, cfg)

    async def poller():
        while True:
            await asyncio.sleep(cfg.refresh_minutes * 60)
            try:
                await refresh()
            except Exception:
                pass

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(poller())
        yield
        task.cancel()

    app = FastAPI(title="rill", lifespan=lifespan)

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

    @app.patch("/api/feeds/{fid}")
    def patch_feed(fid: int, body: dict = Body(...)):
        for key in ("title", "folder"):
            if key in body:
                conn.execute(f"UPDATE feeds SET {key}=? WHERE id=?", (str(body[key]).strip(), fid))
        conn.commit()
        return {"ok": True}

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
                        headers={"Content-Disposition": 'attachment; filename="rill.opml"'})

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
        return rows

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
        path = clip.write(cfg.inbox_dir, dict(row), html, cfg.wikilink_author)
        conn.execute("UPDATE entries SET saved_path=?, is_read=1 WHERE id=?", (path.name, eid))
        conn.commit()
        return {"path": path.name, "already": False}

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/sw.js")
    def sw():  # served from root so its scope covers the whole app
        return FileResponse(STATIC / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
