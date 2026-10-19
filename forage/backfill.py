"""Pull articles older than the feed's own window.

Tried in order, stopping at the first that finds something new: archived-feed links (RFC 5005),
Substack's archive API, WordPress-style ?paged= pages, then the site's sitemap.
"""
import asyncio
import re
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx
import trafilatura
from lxml import etree

from . import extract, feeds
from .config import Config

PAGE_DELAY = 0.5      # between feed pages
ARTICLE_DELAY = 1.0   # between article fetches (sitemap method)
MAX_PAGES = 60
SKIP_PATH = re.compile(r"/(tag|tags|category|categories|author|authors|page|feed|search|archive)(/|$)", re.I)
POSTISH = re.compile(r"post|blog|article|news|entr", re.I)
ARTICLE_TYPE = re.compile(r"article|blog|post|news", re.I)  # og:type values that mean "a dated piece"; "website" is a landing page


def _key(url: str) -> str:
    s = urlsplit(url or "")
    return f"{s.netloc.lower()}{s.path.rstrip('/')}"


def _tm(iso: str | None):
    try:
        dt = datetime.fromisoformat((iso or "").strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return (dt.astimezone(timezone.utc) if dt.tzinfo else dt).utctimetuple()


def _fresh(entries, seen: set[str]) -> list:
    """Entries not already stored (by link or guid); marks them seen so a method can't double-count."""
    out = []
    for e in entries:
        keys = {_key(e.get("link", "")), str(e.get("id") or "")} - {""}
        if not keys or keys & seen:
            continue
        seen |= keys
        out.append(e)
    return out


async def _archive_links(http, feed, first, seen, limit, progress):
    """RFC 5005: follow prev-archive (or next) links, same host only."""
    host = urlsplit(feed["url"]).netloc
    parsed, page_url, visited, out = first, feed["url"], {feed["url"]}, []
    while len(out) < limit and len(visited) <= MAX_PAGES:
        links = [l for l in parsed.feed.get("links", []) if l.get("href")]
        pick = next((l for l in links if l.get("rel") == "prev-archive"), None) or next((l for l in links if l.get("rel") == "next"), None)
        if not pick:
            break
        page_url = urljoin(page_url, pick["href"])
        if page_url in visited or urlsplit(page_url).netloc != host:
            break
        visited.add(page_url)
        await asyncio.sleep(PAGE_DELAY)
        r = await http.get(page_url)
        r.raise_for_status()
        parsed = feeds.parse(r.content, page_url)
        if not parsed.entries:
            break
        out += _fresh(parsed.entries, seen)
        progress["new"] = len(out)
    return out[:limit]


async def _substack(http, feed, first, seen, limit, progress):
    """Substack's /feed stops at ~20; its archive API lists everything. Free posts only: the body is fetched on open."""
    parts = urlsplit(feed["url"])
    if "substack" not in (parts.netloc + (first.feed.get("generator") or "")).lower():
        return []
    base, offset, out = f"{parts.scheme}://{parts.netloc}", 0, []
    while len(out) < limit:
        r = await http.get(f"{base}/api/v1/archive", params={"sort": "new", "limit": 50, "offset": offset})
        r.raise_for_status()
        posts = r.json()
        if not isinstance(posts, list) or not posts:
            break
        offset += len(posts)
        batch = []
        for p in posts:
            url = p.get("canonical_url")
            if not url or p.get("audience") not in (None, "everyone") or not _tm(p.get("post_date")):
                continue
            batch.append({
                "link": url, "id": url, "title": p.get("title") or "",
                "summary": p.get("subtitle") or p.get("description") or "",
                "author": ", ".join(b["name"] for b in p.get("publishedBylines") or [] if b.get("name")),
                "published_parsed": _tm(p["post_date"]),
                "media_thumbnail": [{"url": p["cover_image"]}] if p.get("cover_image") else [],
            })
        out += _fresh(batch, seen)
        progress["new"] = len(out)
        await asyncio.sleep(PAGE_DELAY)
    return out[:limit]


def _page(url: str, n: int) -> str:
    s = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(s.query) if k != "paged"] + [("paged", str(n))]
    return urlunsplit(s._replace(query=urlencode(q)))


async def _paged(http, feed, first, seen, limit, progress):
    """WordPress-style ?paged=N. Any site that ignores the parameter just serves page 1 again, which we detect and drop."""
    first_ids = {e.get("id") or e.get("link") for e in first.entries}
    out, n = [], 2
    while len(out) < limit and n <= MAX_PAGES:
        await asyncio.sleep(PAGE_DELAY)
        r = await http.get(_page(feed["url"], n))
        if r.status_code != 200:  # WordPress 404s past the last page
            break
        parsed = feeds.parse(r.content, feed["url"])
        if not parsed.entries:
            break
        if n == 2 and {e.get("id") or e.get("link") for e in parsed.entries} & first_ids:
            return []
        out += _fresh(parsed.entries, seen)
        progress["new"] = len(out)
        n += 1
    return out[:limit]


def _xml(content: bytes):
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
    return etree.fromstring(content, parser)


def _locs(root, tag: str) -> list[tuple[str, str]]:
    out = []
    for el in root.xpath(f"//*[local-name()='{tag}']"):
        loc = (el.xpath("string(*[local-name()='loc'])") or "").strip()
        if loc:
            out.append((loc, (el.xpath("string(*[local-name()='lastmod'])") or "").strip()))
    return out


async def _sitemap_urls(http, root_url: str) -> list[tuple[str, str]]:
    host = urlsplit(root_url).netloc
    candidates = []
    try:
        r = await http.get(root_url + "/robots.txt")
        if r.status_code == 200:
            candidates += re.findall(r"(?im)^sitemap:\s*(\S+)", r.text)
    except httpx.HTTPError:
        pass
    candidates += [root_url + p for p in ("/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml")]
    for cand in [c for c in candidates if urlsplit(c).netloc == host]:
        try:
            r = await http.get(cand)
            if r.status_code != 200:
                continue
            doc = _xml(r.content)
        except (httpx.HTTPError, etree.XMLSyntaxError):
            continue
        if etree.QName(doc).localname == "sitemapindex":
            subs = [u for u, _ in _locs(doc, "sitemap") if urlsplit(u).netloc == host]
            subs = [u for u in subs if POSTISH.search(u)] or subs
            urls = []
            for sub in subs[:12]:
                await asyncio.sleep(PAGE_DELAY)
                try:
                    sr = await http.get(sub)
                    urls += _locs(_xml(sr.content), "url") if sr.status_code == 200 else []
                except (httpx.HTTPError, etree.XMLSyntaxError):
                    continue
            if urls:
                return urls
        else:
            urls = _locs(doc, "url")
            if urls:
                return urls
    return []


async def _sitemap(http, feed, first, seen, limit, progress):
    """Last resort: list the site's pages, fetch the newest ones and run them through the reader extractor."""
    site = urlsplit(first.feed.get("link") or feed["site_url"] or feed["url"])
    root = f"{site.scheme}://{site.netloc}"
    prefix = site.path.rstrip("/")
    cands = []
    for loc, lastmod in await _sitemap_urls(http, root):
        p = urlsplit(loc)
        if p.netloc != site.netloc or p.path.strip("/") == "" or SKIP_PATH.search(p.path) or not p.path.startswith(prefix or "/"):
            continue
        if _key(loc) in seen:
            continue
        cands.append((loc, lastmod))
    cands.sort(key=lambda c: c[1], reverse=True)
    out = []
    # Pages that aren't articles (about, now, indexes) cost a fetch each, so the budget is on pages looked at, not posts found.
    for loc, lastmod in cands[: limit * 3 + 20]:
        if len(out) >= limit:
            break
        progress["checked"] += 1
        await asyncio.sleep(ARTICLE_DELAY)
        try:
            r = await http.get(loc)
            if r.status_code != 200 or "html" not in r.headers.get("content-type", "html"):
                continue
            body = await asyncio.to_thread(extract.extract_article, r.text, str(r.url))
            meta = await asyncio.to_thread(trafilatura.extract_metadata, r.text, default_url=str(r.url))
        except Exception:
            continue
        if meta and meta.pagetype and not ARTICLE_TYPE.search(meta.pagetype):
            continue  # the page says it isn't an article (og:type website, profile, ...)
        when = _tm((meta.date if meta and meta.date else "") or lastmod)
        if not body or not when:
            continue  # not an article, or no date to file it under
        body = extract.strip_leading_title(body, (meta.title if meta and meta.title else "") or "")
        out += _fresh([{
            "link": loc, "id": loc, "title": (meta.title if meta and meta.title else "") or loc,
            "author": (meta.author if meta and meta.author else "") or "",
            "published_parsed": when, "content": [{"value": body}],
            "media_thumbnail": [{"url": urljoin(loc, meta.image)}] if meta and meta.image else [],  # og:image, the feature image
        }], seen)
        progress["new"] = len(out)
    return out


def _yt_source(feed_url: str) -> str:
    """The channel's Videos tab (no Shorts) or the playlist a YouTube feed URL names, or "" for any other feed."""
    p = urlsplit(feed_url)
    if p.netloc.lower().removeprefix("www.") != "youtube.com" or not p.path.startswith("/feeds/videos.xml"):
        return ""
    q = dict(parse_qsl(p.query))
    if q.get("channel_id"):
        return f"https://www.youtube.com/channel/{q['channel_id']}/videos"
    return f"https://www.youtube.com/playlist?list={q['playlist_id']}" if q.get("playlist_id") else ""


def _yt_list(source: str, count: int) -> list[dict]:
    import yt_dlp
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": True, "playlistend": count, "skip_download": True}) as y:
        return [e for e in (y.extract_info(source, download=False) or {}).get("entries", []) if e and e.get("id")]


def _yt_detail(vid: str) -> dict | None:
    import yt_dlp
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as y:
        i = y.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False, process=False)
    return i if i and i.get("timestamp") else None  # no timestamp: a premiere or live stream not yet out


async def _youtube(http, feed, first, seen, limit, progress):
    """YouTube's feed holds only the latest 15. yt-dlp lists the channel's Videos tab, then one request per video for its
    exact date and description (about 1.5 s each)."""
    source = _yt_source(feed["url"])
    if not source:
        return []
    have = {g for g in seen if g.startswith("yt:video:")}
    out = []
    for e in await asyncio.to_thread(_yt_list, source, len(have) + limit + 20):
        if len(out) >= limit:
            break
        guid = f"yt:video:{e['id']}"
        if guid in have:
            continue
        progress["checked"] += 1
        try:
            d = await asyncio.to_thread(_yt_detail, e["id"])
        except Exception:
            continue
        await asyncio.sleep(0.5)
        if not d:
            continue
        out.append({"id": guid, "yt_videoid": e["id"], "link": f"https://www.youtube.com/watch?v={e['id']}", "title": d.get("title") or e.get("title") or "",
                    "author": d.get("channel") or d.get("uploader") or "", "summary": d.get("description") or "",
                    "published_parsed": datetime.fromtimestamp(d["timestamp"], timezone.utc).timetuple(),
                    "media_thumbnail": [{"url": f"https://i.ytimg.com/vi/{e['id']}/hqdefault.jpg"}]})
    return out


METHODS = (("YouTube channel", _youtube), ("archived feed pages", _archive_links), ("Substack archive", _substack),
           ("paged feed", _paged), ("sitemap", _sitemap))


async def run(conn, cfg: Config, feed, limit: int, progress: dict) -> dict:
    """Store older entries (marked read, so the unread counts don't balloon). Returns {"method", "new", "message"}."""
    limit = max(1, min(limit, 200))
    seen: set[str] = set()
    for row in conn.execute("SELECT url, guid FROM entries WHERE feed_id=?", (feed["id"],)):
        seen |= {_key(row["url"]), row["guid"]} - {""}
    errors = []
    async with feeds.client(cfg) as http:
        r = await http.get(feed["url"])
        r.raise_for_status()
        first = feeds.parse(r.content, feed["url"])
        for name, fn in METHODS:
            try:
                found = await fn(http, feed, first, seen, limit, progress)
            except Exception as exc:  # a method failing just means try the next
                errors.append(f"{name}: {exc}")
                continue
            if found:
                n = feeds._store_entries(conn, feed["id"], SimpleNamespace(entries=found), feed["url"], read=True)
                conn.commit()
                return {"method": name, "new": n, "message": ""}
    msg = "Nothing older found. This site doesn't offer more than the feed."
    return {"method": None, "new": 0, "message": msg + (f" ({'; '.join(errors)[:150]})" if errors else "")}
