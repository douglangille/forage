"""YouTube entries: a player and a tidy description to read, the transcript only when saved."""
import html
import re
from urllib.parse import parse_qs, urlsplit

_ID = re.compile(r"^[\w-]{11}$")
_URL = re.compile(r"(https?://[^\s<>\"]+?)([.,;:!?)\]]*(?:\s|$))")
PARA_SENTENCE = 4  # seconds before a paragraph may break at a caption that ends a sentence
PARA_MAX = 30      # seconds after which it breaks anyway, mid-sentence if need be (fitted to the Web Clipper's output)


def video_id(guid: str, url: str) -> str:
    """The video id for a YouTube feed entry, or "" if it isn't one."""
    if guid.startswith("yt:video:") and _ID.match(guid[9:]):
        return guid[9:]
    p = urlsplit(url or "")
    host = p.netloc.lower().removeprefix("www.").removeprefix("m.")
    if host == "youtu.be":
        vid = p.path.strip("/")
    elif host in ("youtube.com", "music.youtube.com"):
        vid = (parse_qs(p.query).get("v") or [""])[0] or (p.path.split("/")[2] if p.path.startswith(("/shorts/", "/live/", "/embed/")) and p.path.count("/") >= 2 else "")
    else:
        return ""
    return vid if _ID.match(vid) else ""


def is_short(url: str) -> bool:
    p = urlsplit(url or "")
    return p.netloc.lower().removeprefix("www.").removeprefix("m.") == "youtube.com" and p.path.startswith("/shorts/")


def watch_url(vid: str) -> str:
    return f"https://www.youtube.com/watch?v={vid}"


def description_html(text: str) -> str:
    """The feed's plain-text description as paragraphs with line breaks and clickable links."""
    out = []
    for para in re.split(r"\n\s*\n", html.unescape(text or "").strip()):
        lines = [_linkify(html.escape(l.strip())) for l in para.splitlines() if l.strip()]
        if lines:
            out.append("<p>" + "<br>".join(lines) + "</p>")
    return "".join(out)


def _linkify(escaped: str) -> str:
    return _URL.sub(lambda m: f'<a href="{m.group(1)}">{m.group(1)}</a>{m.group(2)}', escaped)


def reader_description(feed_html: str) -> str:
    """The stored description as paragraphs. Entries stored before the formatting existed hold the bare text, newlines and all."""
    if not feed_html or "<p>" in feed_html:
        return feed_html or ""
    return description_html(description_text(feed_html))


EMBED_TOKEN = "[[forage-yt-embed:%s]]"  # plain text stands in for an embed while the HTML is stored and sanitized: an extractor drops short links and images
_IFRAME = re.compile(r"<iframe\b[^>]*?\b(?:data-)?src\s*=\s*[\"']([^\"']+)[\"'][^>]*>(?:\s*</iframe>)?", re.I | re.S)
_EMBED_ID = re.compile(r"(?:youtube(?:-nocookie)?\.com/embed/|youtu\.be/)([\w-]{11})")
_MARKER = re.compile(r"(?:<p>\s*)?\[\[forage-yt-embed:([\w-]{11})\]\](?:\s*</p>)?")


def mark_embeds(html_in: str) -> str:
    """YouTube iframes in feed or page HTML become a text token, since the sanitizer would drop the iframe."""
    def swap(m):
        v = _EMBED_ID.search(m.group(1))
        return f"<p>{EMBED_TOKEN % v.group(1)}</p>" if v else m.group(0)
    return _IFRAME.sub(swap, html_in or "")


def show_embeds(html_in: str) -> str:
    """Tokens back into players, for the reader."""
    return _MARKER.sub(lambda m: player_html(m.group(1)), html_in or "")


def clip_embeds(html_in: str) -> str:
    """Tokens into bare video images, which markdown turns into the Web Clipper's `![](watch url)`."""
    return _MARKER.sub(lambda m: f'<p><img src="{watch_url(m.group(1))}" alt=""></p>', html_in or "")


def player_html(vid: str) -> str:
    return (f'<div class="yt-embed"><iframe src="https://www.youtube-nocookie.com/embed/{vid}" title="YouTube video player" '
            'allow="accelerometer; encrypted-media; picture-in-picture; fullscreen" referrerpolicy="strict-origin-when-cross-origin" '
            'allowfullscreen loading="lazy"></iframe></div>')


def fetch_transcript(vid: str) -> list[tuple[float, str]]:
    """(start seconds, text) snippets, or [] when the video has no captions or YouTube won't hand them over."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        snippets = YouTubeTranscriptApi().fetch(vid, languages=["en"]).snippets
    except Exception:
        return []
    return [(s.start, re.sub(r"\s+", " ", s.text).strip()) for s in snippets if s.text.strip()]


def _stamp(sec: float) -> str:
    s = int(sec)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


_CHAPTER = re.compile(r"^\(?((?:\d+:)?\d{1,2}:\d{2})\)?[\s\-–—:]+(\S.*)$")


def _secs(stamp: str) -> int:
    n = 0
    for part in stamp.split(":"):
        n = n * 60 + int(part)
    return n


def chapters(description: str) -> list[tuple[int, str]]:
    """(start seconds, title) from the description's timestamp lines, the way YouTube reads them: three or more, the first at 0:00."""
    found = []
    for line in description.splitlines():
        m = _CHAPTER.match(line.strip())
        if m:
            found.append((_secs(m.group(1)), m.group(2).strip()))
    ok = len(found) >= 3 and found[0][0] == 0 and all(a[0] < b[0] for a, b in zip(found, found[1:]))
    return found if ok else []


def description_text(feed_html: str) -> str:
    """The plain text back out of the description HTML the feed was stored as."""
    t = re.sub(r"<br\s*/?>", "\n", feed_html or "")
    t = re.sub(r"</p>\s*<p>", "\n\n", t)
    return html.unescape(re.sub(r"<[^>]+>", "", t)).strip()


def _clean(text: str) -> str:
    text = re.sub(r"\s*>>\s*", " ", text)  # captions mark a speaker change with >>
    return re.sub(r"([\[\]])", r"\\\1", re.sub(r"\s+", " ", text)).strip()


def transcript_markdown(snips: list[tuple[float, str]], chaps: list[tuple[int, str]] = ()) -> str:
    """Web Clipper's shape: **m:ss** · text paragraphs under ### chapter headings. A paragraph breaks at a caption that ends a
    sentence once PARA_SENTENCE seconds have run, or after PARA_MAX regardless, and always where a chapter starts."""
    out, buf, start, nxt = [], [], 0.0, 0

    def flush():
        nonlocal buf
        if buf:
            out.append(f"**{_stamp(start)}** · " + _clean(" ".join(buf)))
            buf = []

    for i, (t, text) in enumerate(snips):
        new_chapter = nxt < len(chaps) and t >= chaps[nxt][0]
        elapsed = t - start
        prev_ends = i > 0 and re.search(r"[.!?][\"')\]]*$", snips[i - 1][1])
        if buf and (new_chapter or (prev_ends and elapsed >= PARA_SENTENCE) or elapsed >= PARA_MAX):
            flush()
        if new_chapter:
            out.append(f"### {chaps[nxt][1]}")
            nxt += 1
        if not buf:
            start = t
        buf.append(text)
    flush()
    return "\n\n".join(out)


def clip_body(vid: str, snips: list[tuple[float, str]], description: str = "") -> str:
    """The clip below its frontmatter, as the Web Clipper writes a video: the player, the description (a hard break at
    each line end), then the transcript. clip.render puts the H1 above it."""
    body = f"![]({watch_url(vid)})\n"
    if description:
        body += "\n" + "  \n".join(description.splitlines()) + "\n"
    if snips:
        body += "\n## Transcript\n\n" + transcript_markdown(snips, chapters(description)) + "\n"
    return body
