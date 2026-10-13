import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from forage import clip, extract, opml
from forage.app import create_app
from forage.config import Config

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
    from forage import feeds

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
        again = client.post(f"/api/entries/{eid}/save").json()  # saving is untracked: a repeat writes a numbered copy
        assert again["path"] == "Hello-World-1 2.md" and (c.inbox_dir / again["path"]).exists()
        cl = client.get(f"/api/entries/{eid}/clip").json()
        assert cl["filename"] == "Hello-World-1.md" and cl["markdown"].startswith("---\ntitle:") and "saved_path" not in cl
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


def test_platform_candidates_and_dedupe():
    from forage import search
    assert search.platform_candidates("https://github.com/o/r") == ["https://github.com/o/r/releases.atom", "https://github.com/o/r/commits.atom"]
    assert search.platform_candidates("https://www.reddit.com/r/selfhosted/top") == ["https://www.reddit.com/r/selfhosted/.rss"]
    assert search.platform_candidates("https://foo.substack.com/p/x") == ["https://foo.substack.com/feed"]
    assert search.platform_candidates("https://example.com/") == []
    assert search.norm("https://www.Example.com/feed/") == search.norm("http://example.com/feed")
    assert search.looks_like_url("simonwillison.net") and not search.looks_like_url("ethan mollick")


def test_folder_rename_merge_delete(tmp_path):
    c = cfg(tmp_path)
    with TestClient(create_app(c)) as client:
        import sqlite3
        db = sqlite3.connect(c.db_path)
        for i, f in enumerate(["Dev", "dev", "Dev", ""]):
            db.execute("INSERT INTO feeds (url, title, folder) VALUES (?,?,?)", (f"https://x/{i}", f"F{i}", f))
        db.commit()
        assert client.post("/api/folders/rename", json={"from": "dev", "to": "Dev"}).json() == {"moved": 1}
        folders = sorted({f["folder"] for f in client.get("/api/feeds").json()})
        assert folders == ["", "Dev"]
        assert client.post("/api/folders/delete", json={"name": "Dev"}).json() == {"unfiled": 3}
        assert {f["folder"] for f in client.get("/api/feeds").json()} == {""}
        fid = client.get("/api/feeds").json()[0]["id"]
        client.patch(f"/api/feeds/{fid}", json={"title": "New", "folder": "X"})
        assert [f["folder"] for f in client.get("/api/feeds").json() if f["id"] == fid] == ["X"]


def test_entities_decoded_in_summary_and_title(tmp_path, monkeypatch):
    import httpx
    from forage import feeds
    rss = b"<?xml version='1.0'?><rss version='2.0'><channel><title>T</title><item><title>It&amp;#8217;s</title><link>https://e/1</link><guid>1</guid><description>Here&amp;#8217;s what</description></item></channel></rss>"
    async def fake_discover(http, url): return url
    async def fake_get(self, url, **kw): return httpx.Response(200, content=rss, request=httpx.Request("GET", url))
    monkeypatch.setattr(feeds, "discover", fake_discover)
    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)
    with TestClient(create_app(cfg(tmp_path))) as client:
        client.post("/api/feeds", json={"url": "https://e/feed"})
        e = client.get("/api/entries").json()[0]
        assert "&#" not in e["summary"] and "\u2019" in e["summary"] and "\u2019" in e["title"]


def test_settings_roundtrip_and_save_uses_them(tmp_path, monkeypatch):
    import httpx
    from forage import feeds
    async def fake_discover(http, url): return url
    async def fake_get(self, url, **kw): return httpx.Response(200, content=RSS, headers={"content-type": "application/rss+xml"}, request=httpx.Request("GET", url))
    monkeypatch.setattr(feeds, "discover", fake_discover)
    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)
    c = cfg(tmp_path)
    with TestClient(create_app(c)) as client:
        st = client.get("/api/settings").json()
        assert st["inbox_dir"] == str(c.inbox_dir) and st["git_push"] is False and st["sync_script"] is False
        for bad in ({"inbox_dir": "relative/path"}, {"refresh_minutes": 2}, {"refresh_minutes": "abc"}):
            assert client.put("/api/settings", json=bad).status_code == 422
        new = tmp_path / "my vault" / "inbox"
        st = client.put("/api/settings", json={"inbox_dir": str(new), "wikilink_author": False, "refresh_minutes": 60}).json()
        assert st["inbox_dir"] == str(new) and new.is_dir() and st["refresh_minutes"] == 60 and st["wikilink_author"] is False
        client.post("/api/feeds", json={"url": "https://ex.test/feed"})
        eid = client.get("/api/entries").json()[0]["id"]
        path = client.post(f"/api/entries/{eid}/save").json()["path"]
        text = (new / path).read_text()
        assert 'author:\n  - "Jane Doe"' in text and "[[" not in text


def test_settings_cannot_set_a_script(tmp_path):
    with TestClient(create_app(cfg(tmp_path))) as client:
        st = client.put("/api/settings", json={"sync_script": "/bin/evil", "git_root": "/", "inbox_dir": str(tmp_path / "i")}).json()
        assert "sync_script" in st and st["sync_script"] is False  # only reports whether the server configured one
        import sqlite3
        keys = {r[0] for r in sqlite3.connect(cfg(tmp_path).db_path).execute("select key from settings")}
        assert keys == {"inbox_dir"}


def _rss(*items):
    body = "".join(
        f"<item><title>{t}</title><link>https://ex.test/{g}</link><guid>{g}</guid><pubDate>{d}</pubDate><description>s</description></item>"
        for g, t, d in items
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>T</title><link>https://ex.test/</link>{body}</channel></rss>'.encode()


def _backfill_client(tmp_path, monkeypatch, handler):
    import httpx
    from forage import backfill, feeds

    monkeypatch.setattr(backfill, "PAGE_DELAY", 0)
    monkeypatch.setattr(backfill, "ARTICLE_DELAY", 0)
    monkeypatch.setattr(feeds, "client", lambda cfg: httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True))
    client = TestClient(create_app(cfg(tmp_path)))
    client.__enter__()
    return client


def _run_backfill(client, feed_url="https://ex.test/feed"):
    import time

    c = client
    c.post("/api/opml", content=f'<opml><body><outline xmlns="" type="rss" text="T" xmlUrl="{feed_url}"/></body></opml>'.encode())
    time.sleep(0.3)
    fid = c.get("/api/feeds").json()[0]["id"]
    c.post(f"/api/feeds/{fid}/backfill", json={"limit": 10})
    for _ in range(100):
        st = c.get(f"/api/feeds/{fid}/backfill").json()
        if st["state"] != "running":
            return st
        time.sleep(0.1)
    raise AssertionError("backfill never finished")


def test_backfill_paged_feed(tmp_path, monkeypatch):
    import httpx

    def handler(req: httpx.Request):
        n = req.url.params.get("paged")
        if n is None:
            return httpx.Response(200, content=_rss(("a", "A", "Wed, 08 Oct 2026 10:00:00 GMT")))
        if n == "2":
            return httpx.Response(200, content=_rss(("b", "B", "Tue, 07 Oct 2026 10:00:00 GMT")))
        return httpx.Response(404)

    c = _backfill_client(tmp_path, monkeypatch, handler)
    st = _run_backfill(c)
    assert st["state"] == "done" and st["method"] == "paged feed" and st["new"] == 1
    entries = c.get("/api/entries").json()
    assert {e["title"] for e in entries} == {"A", "B"}
    assert [e["is_read"] for e in entries if e["title"] == "B"] == [1]  # older ones arrive read


def test_backfill_ignored_paged_param_and_sitemap(tmp_path, monkeypatch):
    import httpx

    page = '<html><head><meta property="og:image" content="/feature.jpg"></head><body><article><h1>Old</h1>' + "<p>" + "word " * 120 + "</p>" * 1 + "<p>" + "more " * 120 + "</p></article></body></html>"

    def handler(req: httpx.Request):
        path = req.url.path
        if path == "/feed":
            return httpx.Response(200, content=_rss(("a", "A", "Wed, 08 Oct 2026 10:00:00 GMT")))  # ignores ?paged, serves page 1
        if path == "/robots.txt":
            return httpx.Response(404)
        if path == "/sitemap.xml":
            return httpx.Response(200, content=(
                b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                b"<url><loc>https://ex.test/a</loc><lastmod>2026-10-08</lastmod></url>"
                b"<url><loc>https://ex.test/old-post</loc><lastmod>2026-01-02</lastmod></url>"
                b"<url><loc>https://ex.test/tag/x</loc><lastmod>2026-01-03</lastmod></url></urlset>"))
        if path == "/old-post":
            return httpx.Response(200, text=page, headers={"content-type": "text/html"})
        return httpx.Response(404)

    c = _backfill_client(tmp_path, monkeypatch, handler)
    st = _run_backfill(c)
    assert st["state"] == "done" and st["method"] == "sitemap" and st["new"] == 1
    urls = {e["url"] for e in c.get("/api/entries").json()}
    assert urls == {"https://ex.test/a", "https://ex.test/old-post"}
    assert any(e["image"] == "https://ex.test/feature.jpg" for e in c.get("/api/entries").json())


def test_backfill_substack_skips_paid(tmp_path, monkeypatch):
    import httpx

    def handler(req: httpx.Request):
        if req.url.path == "/feed":
            return httpx.Response(200, content=_rss(("a", "A", "Wed, 08 Oct 2026 10:00:00 GMT")))
        if req.url.path == "/api/v1/archive":
            if req.url.params.get("offset") != "0":
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=[
                {"canonical_url": "https://ex.test/a", "title": "A", "post_date": "2026-10-08T10:00:00.000Z", "audience": "everyone"},
                {"canonical_url": "https://ex.test/p/free", "title": "Free", "post_date": "2026-09-01T10:00:00.000Z", "audience": "everyone", "subtitle": "sub"},
                {"canonical_url": "https://ex.test/p/paid", "title": "Paid", "post_date": "2026-08-01T10:00:00.000Z", "audience": "only_paid"},
            ])
        return httpx.Response(404)

    c = _backfill_client(tmp_path, monkeypatch, handler)
    st = _run_backfill(c, "https://x.substack.com/feed")
    assert st["state"] == "done" and st["method"] == "Substack archive" and st["new"] == 1


def test_backfill_rfc5005_archive_links(tmp_path, monkeypatch):
    import httpx

    def handler(req: httpx.Request):
        if req.url.path == "/feed":
            body = _rss(("a", "A", "Wed, 08 Oct 2026 10:00:00 GMT")).replace(
                b"<item>", b'<atom:link xmlns:atom="http://www.w3.org/2005/Atom" rel="prev-archive" href="/archive1"/><item>', 1)
            return httpx.Response(200, content=body)
        if req.url.path == "/archive1":
            return httpx.Response(200, content=_rss(("z", "Z", "Mon, 01 Jun 2026 10:00:00 GMT")))
        return httpx.Response(404)

    c = _backfill_client(tmp_path, monkeypatch, handler)
    st = _run_backfill(c)
    assert st["method"] == "archived feed pages" and st["new"] == 1



def test_thumbnail_is_small_webp_and_cached(tmp_path, monkeypatch):
    import io, time
    import httpx
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (800, 500), (200, 50, 50)).save(buf, "PNG")
    hits = []
    feed = _rss(("a", "A", "Wed, 08 Oct 2026 10:00:00 GMT")).replace(
        b"<description>s</description>", b'<description>&lt;img src="https://ex.test/big.png"&gt;</description>')

    def handler(req: httpx.Request):
        if req.url.path == "/feed":
            return httpx.Response(200, content=feed)
        hits.append(req.url.path)
        return httpx.Response(200, content=buf.getvalue(), headers={"content-type": "image/png"})

    c = _backfill_client(tmp_path, monkeypatch, handler)
    c.post("/api/opml", content=b'<opml><body><outline type="rss" text="T" xmlUrl="https://ex.test/feed"/></body></opml>')
    time.sleep(0.3)
    e = c.get("/api/entries").json()[0]
    assert e["thumb"].startswith(f"/thumb/{e['id']}?v=")
    r = c.get(e["thumb"])
    assert r.headers["content-type"] == "image/webp" and "immutable" in r.headers["cache-control"]
    assert Image.open(io.BytesIO(r.content)).size == (112, 112) and len(r.content) < len(buf.getvalue())
    c.get(e["thumb"])
    assert hits == ["/big.png"]  # second request served from disk


def test_youtube_ids_and_clip_body():
    from forage import youtube
    assert youtube.video_id("yt:video:IBVmenOroPs", "") == "IBVmenOroPs"
    assert youtube.video_id("x", "https://www.youtube.com/shorts/IBVmenOroPs") == "IBVmenOroPs"
    assert youtube.video_id("x", "https://youtu.be/IBVmenOroPs?t=3") == "IBVmenOroPs"
    assert youtube.video_id("x", "https://example.com/watch?v=IBVmenOroPs") == ""
    snips = [(0.0, "Hello there."), (5.0, "Second bit [music]"), (31.0, "Done. >> Yes."), (40.0, "Last.")]
    desc = "About it.\n\nChapters\n(0:00) Intro\n(0:31) Middle\n(0:40) End"
    body = youtube.clip_body("IBVmenOroPs", snips, desc)
    assert body.startswith("![](https://www.youtube.com/watch?v=IBVmenOroPs)\n\nAbout it.  \n  \nChapters  \n(0:00) Intro  \n")
    assert "\n## Transcript\n\n### Intro\n\n**0:00** · Hello there.\n\n**0:05** · Second bit \\[music\\]\n\n### Middle\n\n**0:31** · Done. Yes.\n\n### End\n\n**0:40** · Last.\n" in body
    assert "## Transcript" not in youtube.clip_body("IBVmenOroPs", [], desc)
    assert youtube.chapters("a\n0:00 One\n1:10 - Two\n2:20 Three") == [(0, "One"), (70, "Two"), (140, "Three")]
    assert youtube.chapters("0:00 One\n1:10 Two") == []  # fewer than three isn't a chapter list
    assert youtube.description_text('<p>a<br>b</p><p>c &amp; d</p>') == "a\nb\n\nc & d"
    assert youtube.description_html("a\nhttps://x.co/a.\n\nb") == '<p>a<br><a href="https://x.co/a">https://x.co/a</a>.</p><p>b</p>'


def test_strip_leading_title_unescapes():
    from forage import extract
    assert extract.strip_leading_title("<h1>A &gt; B</h1><p>x</p>", "A > B") == "<p>x</p>"


def test_youtube_old_description_gets_paragraphs():
    from forage import youtube
    old = "Intro line.\n\nSee https://x.co/a &amp; more\nnext line"
    assert youtube.reader_description(old) == '<p>Intro line.</p><p>See <a href="https://x.co/a">https://x.co/a</a> &amp; more<br>next line</p>'
    assert youtube.reader_description("<p>done</p>") == "<p>done</p>"



def test_summarize_clean_and_text():
    from forage import summarize
    assert summarize.clean("<think>hm</think>Sure!\n* One\n2. Two\n```") == "- One\n- Two"
    assert summarize.clean("") == ""
    assert summarize.clean("Safety: Unsafe\nCategories: S1") == ""  # a classifier's verdict is not a summary
    assert summarize.plain_text("<p>a &amp; b</p><p>c</p>").split() == ["a", "&", "b", "c"]


def test_summarize_endpoint(tmp_path, monkeypatch):
    import dataclasses
    from forage import feeds, summarize
    calls = []

    async def fake_summarize(key, model, text, ua, allow_training=False):
        calls.append((key, model))
        return "- point one\n- point two", "some/model:free"

    monkeypatch.setattr(summarize, "summarize", fake_summarize)

    async def fake_discover(http, url): return url
    async def fake_get(self, url, **kw):
        import httpx
        return httpx.Response(200, content=RSS, request=httpx.Request("GET", url))
    monkeypatch.setattr(feeds, "discover", fake_discover)
    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)

    off = create_app(cfg(tmp_path))
    with TestClient(off) as c:
        c.post("/api/feeds", json={"url": "https://ex.test/feed"})
        eid = c.get("/api/entries").json()[0]["id"]
        assert c.post(f"/api/entries/{eid}/summarize").status_code == 404  # no key, no feature
        assert c.get(f"/api/entries/{eid}").json()["summary_on"] is False

    on = create_app(dataclasses.replace(cfg(tmp_path), summary_key="k", summary_daily_cap=2))
    with TestClient(on) as c:
        eid = c.get("/api/entries").json()[0]["id"]
        art = c.get(f"/api/entries/{eid}").json()
        assert art["summary_on"] is True and art["summary"] == ""
        r = c.post(f"/api/entries/{eid}/summarize").json()
        assert r["summary"] == "- point one\n- point two" and r["model"] == "some/model:free" and r["cached"] is False
        assert c.post(f"/api/entries/{eid}/summarize").json()["cached"] is True and len(calls) == 1  # kept: no second call
        assert c.get(f"/api/entries/{eid}").json()["summary_model"] == "some/model:free"
        assert c.post(f"/api/entries/{eid}/summarize", json={"force": True}).json()["cached"] is False and len(calls) == 2
        assert c.post(f"/api/entries/{eid}/summarize", json={"force": True}).status_code == 429  # the daily cap of 2 is spent


def test_summarize_skips_safety_models(monkeypatch):
    import asyncio
    import httpx
    from forage import summarize
    answers = iter([("nvidia/nemotron-3.5-content-safety:free", "- looks like a bullet"), ("some/real:free", "- one\n- two")])

    async def fake_post(self, url, **kw):
        model, text = next(answers)
        return httpx.Response(200, json={"model": model, "choices": [{"message": {"content": text}}]}, request=httpx.Request("POST", url))

    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)
    out, model = asyncio.run(summarize.summarize("k", "openrouter/free", "text", "ua"))
    assert model == "some/real:free" and out == "- one\n- two"  # the safety model's answer was thrown away and the call retried
