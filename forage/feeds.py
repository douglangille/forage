"""Fetch, parse and discover feeds; store entries."""
import calendar
import html
import re
import sqlite3
from datetime import datetime, timezone
from urllib.parse import urljoin

import feedparser
import httpx
from lxml import html as lxml_html

from . import extract
from .config import Config

FEED_TYPES = ("application/rss+xml", "application/atom+xml", "application/feed+json", "application/xml", "text/xml")


def client(cfg: Config) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": cfg.user_agent, "Accept": "*/*"},
        follow_redirects=True,
        timeout=httpx.Timeout(20.0),
    )


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _entry_time(e) -> str:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        t = e.get(key)
        if t:
            return datetime.fromtimestamp(calendar.timegm(t), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return now_iso()


def _first_image(e, html: str) -> str:
    for m in e.get("media_thumbnail", []) or []:
        if m.get("url"):
            return m["url"]
    for m in e.get("media_content", []) or []:
        if m.get("url") and "image" in (m.get("type") or m.get("medium") or "image"):
            return m["url"]
    for enc in e.get("enclosures", []) or []:
        if "image" in (enc.get("type") or ""):
            return enc.get("href", "")
    m = re.search(r'<img[^>]+src=["\']([^"\']+)', html or "", re.I)
    return m.group(1) if m else ""


def parse(body: bytes | str, feed_url: str):
    return feedparser.parse(body, sanitize_html=False)


def _store_entries(conn: sqlite3.Connection, feed_id: int, parsed, base_url: str, read: bool = False) -> int:
    new = 0
    for e in parsed.entries:
        link = e.get("link", "") or ""
        guid = e.get("id") or link or e.get("title", "")
        if not guid:
            continue
        content = ""
        if e.get("content"):
            content = max((c.get("value", "") for c in e["content"]), key=len, default="")
        summary_html = e.get("summary", "") or ""
        body = content or summary_html
        plain = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", summary_html or content))).strip()[:400]
        author = e.get("author", "") or ""
        cur = conn.execute(
            """INSERT OR IGNORE INTO entries
               (feed_id, guid, url, title, author, published, summary, feed_html, image, is_read)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                feed_id, guid, link, html.unescape(e.get("title") or "").strip() or link, author,
                _entry_time(e), plain,
                extract.sanitize(body, link or base_url) if body else "",
                _first_image(e, body), 1 if read else 0,
            ),
        )
        new += cur.rowcount
    return new


async def refresh_feed(conn: sqlite3.Connection, cfg: Config, http: httpx.AsyncClient, feed: sqlite3.Row) -> int:
    headers = {}
    if feed["etag"]:
        headers["If-None-Match"] = feed["etag"]
    if feed["last_modified"]:
        headers["If-Modified-Since"] = feed["last_modified"]
    try:
        r = await http.get(feed["url"], headers=headers)
        if r.status_code == 304:
            conn.execute("UPDATE feeds SET last_fetch=?, error=NULL WHERE id=?", (now_iso(), feed["id"]))
            conn.commit()
            return 0
        r.raise_for_status()
        parsed = parse(r.content, feed["url"])
        if parsed.bozo and not parsed.entries:
            raise ValueError(f"not a valid feed: {parsed.bozo_exception}")
        new = _store_entries(conn, feed["id"], parsed, feed["url"])
        title = (parsed.feed.get("title") or feed["title"] or "").strip()
        site = parsed.feed.get("link") or feed["site_url"]
        conn.execute(
            "UPDATE feeds SET title=?, site_url=?, etag=?, last_modified=?, last_fetch=?, error=NULL WHERE id=?",
            (title, site, r.headers.get("etag"), r.headers.get("last-modified"), now_iso(), feed["id"]),
        )
        conn.commit()
        return new
    except Exception as exc:  # one bad feed must not stop the rest
        conn.execute("UPDATE feeds SET error=?, last_fetch=? WHERE id=?", (str(exc)[:300], now_iso(), feed["id"]))
        conn.commit()
        return 0


async def refresh_all(conn: sqlite3.Connection, cfg: Config) -> int:
    feeds = conn.execute("SELECT * FROM feeds").fetchall()
    total = 0
    async with client(cfg) as http:
        import asyncio

        sem = asyncio.Semaphore(8)

        async def one(f):
            async with sem:
                return await refresh_feed(conn, cfg, http, f)

        for n in await asyncio.gather(*(one(f) for f in feeds)):
            total += n
    return total


async def discover(http: httpx.AsyncClient, url: str) -> str:
    """Accept a feed URL or a site URL; return the feed URL."""
    if not re.match(r"^https?://", url):
        url = "https://" + url
    r = await http.get(url)
    r.raise_for_status()
    ctype = r.headers.get("content-type", "").lower()
    head = r.content[:2000].lstrip().lower()
    if any(t in ctype for t in ("rss", "atom", "xml")) and (b"<rss" in head or b"<feed" in head or b"<rdf" in head or b"<?xml" in head):
        return str(r.url)
    if head.startswith((b"<rss", b"<feed", b"<?xml")):
        return str(r.url)
    doc = lxml_html.fromstring(r.content)
    for link in doc.xpath('//link[@rel="alternate"]'):
        if (link.get("type") or "").lower() in FEED_TYPES and link.get("href"):
            return urljoin(str(r.url), link.get("href"))
    for guess in ("/feed", "/feed.xml", "/rss", "/rss.xml", "/atom.xml", "/index.xml"):
        try:
            g = await http.get(urljoin(str(r.url), guess))
            if g.status_code == 200 and re.search(rb"<(rss|feed|rdf)", g.content[:2000], re.I):
                return str(g.url)
        except httpx.HTTPError:
            continue
    raise ValueError("no feed found at that address")


async def fetch_article(http: httpx.AsyncClient, url: str) -> str | None:
    r = await http.get(url)
    r.raise_for_status()
    if "html" not in r.headers.get("content-type", "html"):
        return None
    return extract.extract_article(r.text, str(r.url))
