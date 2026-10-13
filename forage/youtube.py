"""YouTube entries: a player and a tidy description to read, the transcript only when saved."""
import html
import re
from urllib.parse import parse_qs, urlsplit

_ID = re.compile(r"^[\w-]{11}$")
_URL = re.compile(r"(https?://[^\s<>\"]+?)([.,;:!?)\]]*(?:\s|$))")
PARA_MIN = 20  # seconds of speech before a paragraph may break at a sentence end


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


def transcript_markdown(snips: list[tuple[float, str]]) -> str:
    """Web Clipper's shape: paragraphs of **m:ss** · text, broken at a sentence end once PARA_MIN seconds have passed."""
    paras, start, buf = [], 0.0, []
    for t, text in snips:
        if not buf:
            start = t
        buf.append(text)
        if t - start >= PARA_MIN and re.search(r"[.!?][\"')\]]*$", text):
            paras.append((start, " ".join(buf)))
            buf = []
    if buf:
        paras.append((start, " ".join(buf)))
    return "\n\n".join(f"**{_stamp(s)}** · {p}" for s, p in paras)


def clip_body(vid: str, snips: list[tuple[float, str]]) -> str:
    body = f"![]({watch_url(vid)})\n"
    return body + ("\n## Transcript\n\n" + transcript_markdown(snips) + "\n" if snips else "")
