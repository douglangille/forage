import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rill import clip, extract, opml
from rill.app import create_app
from rill.config import Config

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Test Feed</title><link>https://ex.test/</link>
<item><title>Hello: "World"/1</title><link>https://ex.test/a</link><guid>a</guid><author>Jane Doe</author>
<pubDate>Wed, 08 Oct 2026 10:00:00 GMT</pubDate><description>short</description>
<content:encoded xmlns:content="http://purl.org/rss/1.0/modules/content/"><![CDATA[<p>%s</p><script>x</script><img src="/i.png">]]></content:encoded></item>
</channel></rss>""" % (b"word " * 400)


def cfg(tmp_path) -> Config:
    return Config(tmp_path / "d", tmp_path / "inbox", "127.0.0.1", 0, 30, True, "t")


def test_filename_strips_illegal():
    assert clip.filename('Hello: "World"/1 #tag [x]') == "Hello-World-1 tag x"
    assert clip.filename("???") == "Untitled"


def test_render_matches_clipper_default():
    out = clip.render({"title": 'A "q"', "url": "https://x/y", "author": "Jane", "published": "2026-10-08T10:00:00Z", "summary": "d"},
                      "<p>Hi <a href='https://a'>l</a></p>", True, "2026-10-10")
    assert out.startswith('---\ntitle: "A \\"q\\""\nsource: "https://x/y"\nauthor:\n  - "[[Jane]]"\npublished: 2026-10-08\ncreated: 2026-10-10\n')
    assert 'tags:\n  - "clippings"\n---\n' in out
    assert "Hi [l](https://a)" in out
    assert out.split("---\n")[2].startswith('# A "q"\n\nHi')


def test_sanitize_strips_script_and_absolutizes():
    out = extract.sanitize('<p onclick="x">a</p><script>x</script><img src="/i.png">', "https://ex.test/p")
    assert "script" not in out and "onclick" not in out and 'src="https://ex.test/i.png"' in out


def test_opml_roundtrip():
    items = opml.parse_opml(b'<opml><body><outline text="Dev"><outline xmlUrl="https://a/f" text="A"/></outline><outline xmlUrl="https://b/f" text="B"/></body></opml>')
    assert items == [{"url": "https://a/f", "title": "A", "folder": "Dev"}, {"url": "https://b/f", "title": "B", "folder": ""}]


def test_api_flow(tmp_path, monkeypatch):
    from rill import feeds

    async def fake_discover(http, url): return url
    async def fake_get(self, url, **kw):
        import httpx
        return httpx.Response(200, content=RSS, headers={"content-type": "application/rss+xml"}, request=httpx.Request("GET", url))
    monkeypatch.setattr(feeds, "discover", fake_discover)
    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)
    c = cfg(tmp_path)
    with TestClient(create_app(c)) as client:
        r = client.post("/api/feeds", json={"url": "https://ex.test/feed", "folder": "Dev"})
        assert r.status_code == 200 and r.json()["title"] == "Test Feed"
        es = client.get("/api/entries?unread=true").json()
        assert len(es) == 1 and es[0]["author"] == "Jane Doe"
        eid = es[0]["id"]
        art = client.get(f"/api/entries/{eid}").json()
        assert "script" not in art["html"] and art["is_read"] == 1
        s = client.post(f"/api/entries/{eid}/save").json()
        assert s["path"] == 'Hello-World-1.md'
        text = (c.inbox_dir / s["path"]).read_text()
        assert 'source: "https://ex.test/a"' in text and "word word" in text
        assert client.post(f"/api/entries/{eid}/save").json()["already"] is True
        assert client.get("/api/opml").text.count("xmlUrl") == 1
        assert client.get("/api/feeds").json()[0]["unread"] == 0


def test_commit_and_push(tmp_path):
    import subprocess
    def git(*a, cwd): return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout
    bare, work = tmp_path / "bare.git", tmp_path / "work"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    subprocess.run(["git", "clone", "-q", str(bare), str(work)], check=True, capture_output=True)
    git("config", "user.email", "t@t", cwd=work); git("config", "user.name", "t", cwd=work)
    (work / "other.md").write_text("x"); git("add", "-A", cwd=work); git("commit", "-qm", "init", cwd=work); git("push", "-q", "-u", "origin", "main", cwd=work)
    (work / "other.md").write_text("mid-edit")  # must not be swept into the clip commit
    p = clip.write(work / "inbox", {"title": "T", "url": "https://x", "author": "", "published": "", "summary": ""}, "<p>b</p>", True)
    assert clip.commit_and_push(work, p, "T", None) == ""
    assert git("log", "-1", "--format=%s", cwd=bare) .strip() == "Clip: T"
    assert "other.md" in git("status", "--porcelain", cwd=work)
