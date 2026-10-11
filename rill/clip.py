"""Write an article into the vault inbox in Obsidian Web Clipper's default-template shape."""
import re
from datetime import datetime
from pathlib import Path

from . import extract

_ILLEGAL = re.compile(r'[\\/:*?"<>|#^\[\]]')


def filename(title: str) -> str:
    name = re.sub(r"\s*[\\/:]\s*", "-", title)  # path-ish separators become hyphens, the rest is dropped
    name = _ILLEGAL.sub("", name).replace("\n", " ")
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:120].rstrip(" .") or "Untitled"


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def render(entry: dict, article_html: str, wikilink_author: bool, today: str) -> str:
    author = (entry.get("author") or "").strip()
    fm = ["---", f"title: {_q(entry['title'])}", f"source: {_q(entry['url'])}"]
    if author:
        fm.append("author:")
        fm.append(f"  - {_q('[[' + author + ']]') if wikilink_author else _q(author)}")
    published = (entry.get("published") or "")[:10]
    if published:
        fm.append(f"published: {published}")
    fm.append(f"created: {today}")
    if entry.get("summary"):
        fm.append(f"description: {_q(entry['summary'][:300])}")
    fm += ["tags:", '  - "clippings"', "---", ""]
    return "\n".join(fm) + extract.to_markdown(article_html)


def write(inbox: Path, entry: dict, article_html: str, wikilink_author: bool) -> Path:
    inbox.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    text = render(entry, article_html, wikilink_author, today)
    base = filename(entry["title"])
    path = inbox / f"{base}.md"
    n = 2
    while path.exists():
        path = inbox / f"{base} {n}.md"
        n += 1
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.rename(path)  # vault-autosync never sees a half-written file
    return path
