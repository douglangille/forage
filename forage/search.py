"""Find feeds: discover every feed behind a URL, or search directories by keyword."""
import asyncio
import re
from urllib.parse import quote, urljoin, urlparse

import feedparser
import httpx
from lxml import html as lxml_html

from . import feeds

FEED_RE = re.compile(rb"<(rss|feed|rdf)\b", re.I)


def norm(url: str) -> str:
    """Key for dedupe: scheme, leading www and trailing slash don't make a different feed."""
    u = re.sub(r"^https?://(www\.)?", "", url.strip().lower())
    return u.rstrip("/")


def looks_like_url(q: str) -> bool:
    q = q.strip()
    return bool(re.match(r"^https?://", q)) or (" " not in q and "." in q and not q.endswith("."))


def platform_candidates(url: str) -> list[str]:
    """Feed URLs that follow from the address alone, for platforms that don't advertise them."""
    p = urlparse(url if "://" in url else "https://" + url)
    host = (p.hostname or "").lower().removeprefix("www.")
    path = p.path.rstrip("/")
    origin = f"{p.scheme}://{p.netloc}"
    out: list[str] = []
    if host.endswith(".substack.com") or host == "substack.com" and path.startswith("/@"):
        out.append(f"{origin}/feed" if host != "substack.com" else "")
    if host == "youtube.com" or host == "m.youtube.com":
        m = re.match(r"^/channel/([\w-]+)", path)
        if m:
            out.append(f"https://www.youtube.com/feeds/videos.xml?channel_id={m.group(1)}")
        m = re.match(r"^/playlist", path)
        if m and "list=" in (p.query or ""):
            out.append("https://www.youtube.com/feeds/videos.xml?playlist_id=" + re.search(r"list=([\w-]+)", p.query).group(1))
    if host == "reddit.com" or host.endswith(".reddit.com"):
        m = re.match(r"^/(r|user)/([\w-]+)", path)
        if m:
            out.append(f"https://www.reddit.com/{m.group(1)}/{m.group(2)}/.rss")
    if host == "github.com":
        m = re.match(r"^/([\w.-]+)/([\w.-]+)", path)
        if m:
            base = f"https://github.com/{m.group(1)}/{m.group(2)}"
            out += [f"{base}/releases.atom", f"{base}/commits.atom"]
        elif re.match(r"^/([\w.-]+)$", path):
            out.append(f"https://github.com{path}.atom")
    if host == "medium.com":
        m = re.match(r"^/(@[\w.-]+|[\w-]+)", path)
        if m:
            out.append(f"https://medium.com/feed/{m.group(1)}")
    if host == "bsky.app":
        m = re.match(r"^/profile/([\w.:-]+)", path)
        if m:
            out.append(f"https://bsky.app/profile/{m.group(1)}/rss")
    return [u for u in out if u]


async def probe(http: httpx.AsyncClient, url: str, source: str = "", hint: dict | None = None) -> dict | None:
    """Fetch a candidate and confirm it parses as a feed; return what the picker needs to show."""
    try:
        r = await http.get(url, timeout=12)
        if r.status_code != 200 or not FEED_RE.search(r.content[:3000]):
            return None
        d = feedparser.parse(r.content)
        if not d.entries and d.bozo:
            return None
        desc = re.sub(r"<[^>]+>", "", d.feed.get("subtitle", "") or (hint or {}).get("description", "")).strip()
        latest = ""
        if d.entries:
            t = d.entries[0].get("published") or d.entries[0].get("updated") or ""
            latest = t
        return {
            "url": str(r.url),
            "title": (d.feed.get("title") or (hint or {}).get("title") or url).strip(),
            "site_url": d.feed.get("link", "") or (hint or {}).get("site_url", ""),
            "description": desc[:200],
            "items": len(d.entries),
            "latest": latest,
            "source": source,
            "subscribers": (hint or {}).get("subscribers"),
        }
    except Exception:
        return None


async def discover_all(http: httpx.AsyncClient, q: str) -> list[dict]:
    """Every feed behind a site or page address, verified."""
    url = q.strip()
    if not re.match(r"^https?://", url):
        url = "https://" + url
    cands: list[tuple[str, str]] = [(u, "platform") for u in platform_candidates(url)]
    try:
        r = await http.get(url)
        if FEED_RE.search(r.content[:3000]):
            cands.insert(0, (str(r.url), "this address"))
        else:
            try:
                doc = lxml_html.fromstring(r.content)
                for link in doc.xpath('//link[@rel="alternate"]'):
                    if (link.get("type") or "").lower() in feeds.FEED_TYPES and link.get("href"):
                        cands.append((urljoin(str(r.url), link.get("href")), "page"))
            except Exception:
                pass
            if not any(s == "page" for _, s in cands):
                base = f"{urlparse(str(r.url)).scheme}://{urlparse(str(r.url)).netloc}"
                cands += [(base + g, "guess") for g in ("/feed", "/feed.xml", "/rss", "/rss.xml", "/atom.xml", "/index.xml")]
        for u in platform_candidates(str(r.url)):
            cands.append((u, "platform"))
    except httpx.HTTPError:
        pass  # platform guesses may still work for a page that blocks us
    seen, uniq = set(), []
    for u, s in cands:
        if norm(u) not in seen:
            seen.add(norm(u)); uniq.append((u, s))
    found = await asyncio.gather(*(probe(http, u, s) for u, s in uniq[:12]))
    return [f for f in found if f]


async def _feedly(http: httpx.AsyncClient, q: str) -> list[dict]:
    """Feedly's public search. Unofficial and unauthenticated: expect it to break someday."""
    try:
        r = await http.get("https://cloud.feedly.com/v3/search/feeds", params={"query": q, "count": 10}, timeout=10)
        out = []
        for x in r.json().get("results", []):
            fid = x.get("feedId", "")
            if fid.startswith("feed/"):
                out.append({"url": fid[5:], "title": x.get("title", ""), "site_url": x.get("website", ""),
                            "description": x.get("description", "") or " · ".join(x.get("topics", [])[:3]),
                            "subscribers": x.get("subscribers"), "source": "Feedly"})
        return out
    except Exception:
        return []


async def _substack(http: httpx.AsyncClient, q: str) -> list[dict]:
    """Substack's site search (unofficial): publications behind matching profiles and posts."""
    try:
        r = await http.get("https://substack.com/api/v1/top/search", params={"query": q}, timeout=10)
        pubs: dict[str, dict] = {}
        for item in r.json().get("items", []):
            cand = []
            if item.get("type") == "profileSearchResults":
                for prof in item.get("results", []):
                    for pu in prof.get("publicationUsers", []):
                        if pu.get("is_primary") and pu.get("publication"):
                            cand.append(pu["publication"])
            elif item.get("publication"):
                cand.append(item["publication"])
            for p in cand:
                sub = p.get("subdomain")
                if sub and sub not in pubs:
                    pubs[sub] = {"url": f"https://{sub}.substack.com/feed", "title": p.get("name", sub),
                                 "site_url": f"https://{p.get('custom_domain') or sub + '.substack.com'}",
                                 "description": p.get("hero_text", "") or "", "source": "Substack"}
        return list(pubs.values())[:8]
    except Exception:
        return []


async def search(http: httpx.AsyncClient, q: str) -> list[dict]:
    q = q.strip()
    if not q:
        return []
    if looks_like_url(q):
        return await discover_all(http, q)
    lists = await asyncio.gather(_substack(http, q), _feedly(http, q))
    merged, seen = [], set()
    for lst in lists:
        for x in lst:
            if norm(x["url"]) not in seen:
                seen.add(norm(x["url"])); merged.append(x)
    # Verify so dead directory entries drop out and each result has real title and recency.
    checked = await asyncio.gather(*(probe(http, x["url"], x["source"], x) for x in merged[:20]))
    return [c for c in checked if c]
