"""Write an article into the vault inbox in Obsidian Web Clipper's default-template shape."""
import re
import subprocess
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


def render(entry: dict, article_html: str, wikilink_author: bool, today: str, body_md: str | None = None) -> str:
    """body_md replaces the converted article HTML (a video clip is the player plus its transcript)."""
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
        fm.append(f"description: {_q(entry['summary'][:200].rstrip())}")
    fm += ["tags:", '  - "clippings"', "---", ""]
    return "\n".join(fm) + f"# {entry['title']}\n\n" + (body_md if body_md is not None else extract.to_markdown(article_html))


def write(inbox: Path, entry: dict, article_html: str, wikilink_author: bool, body_md: str | None = None) -> Path:
    inbox.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    text = render(entry, article_html, wikilink_author, today, body_md)
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


def commit_and_push(git_root: Path, path: Path, title: str, sync_script: str | None) -> str:
    """Commit just the clip, then let the vault's own sync script rebase and push.
    Returns "" on success or a short reason. Never raises: the file is already saved."""
    def run(*cmd, timeout=60):
        return subprocess.run(cmd, cwd=git_root, capture_output=True, text=True, timeout=timeout)

    try:
        rel = str(path.relative_to(git_root))
        if run("git", "add", "--", rel).returncode:
            return "git add failed"
        # Path-limited, so anything else mid-edit in the tree is not swept into the commit.
        r = run("git", "commit", "-q", "-m", f"Clip: {title[:100]}", "--", rel)
        if r.returncode:
            return "commit failed: " + (r.stderr or r.stdout).strip()[:200]
        if sync_script:
            r = run("bash", sync_script, timeout=120)
            if r.returncode:
                return "sync failed: " + (r.stderr or r.stdout).strip()[:200]
        else:
            r = run("git", "pull", "--rebase", "--autostash", "-q", timeout=120)
            if r.returncode:
                return "pull failed: " + r.stderr.strip()[:200]
            r = run("git", "push", "-q", timeout=120)
            if r.returncode:
                return "push failed: " + r.stderr.strip()[:200]
    except Exception as exc:
        return str(exc)[:200]
    return ""


def git_toplevel(path: Path) -> Path | None:
    """The git checkout containing path (or its nearest existing parent), if any."""
    p = path
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        r = subprocess.run(["git", "-C", str(p), "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    return Path(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None
