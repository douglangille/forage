"""Regression tests for the issues found in the 10/11 review."""
import httpx
from fastapi.testclient import TestClient

from forage import feeds
from forage.app import create_app
from forage.config import Config

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Feed One</title><link>https://ex.test/</link>
<item><title>Short one</title><link>https://ex.test/a</link><guid>a</guid><pubDate>Wed, 08 Oct 2026 10:00:00 GMT</pubDate><description>just a summary</description></item>
<item><title>Bad link</title><link>javascript:alert(1)</link><guid>b</guid><pubDate>Tue, 07 Oct 2026 10:00:00 GMT</pubDate><description>x &lt;script&gt;boom&lt;/script&gt;</description></item>
</channel></rss>"""
PAGE = "<html><body><article><h1>Short one</h1>" + "<p>" + "full text here " * 80 + "</p></article></body></html>"


def cfg(tmp_path) -> Config:
    return Config(tmp_path / "d", tmp_path / "inbox", "127.0.0.1", 0, 30, True, "t")


def boot(tmp_path, monkeypatch, page_ok=True):
    state = {"page_ok": page_ok}

    async def fake_discover(http, url): return url

    async def fake_get(self, url, **kw):
        url = str(url)
        if url.endswith("/feed"):
            return httpx.Response(200, content=RSS, headers={"content-type": "application/rss+xml"}, request=httpx.Request("GET", url))
        if not state["page_ok"]:
            raise httpx.ConnectTimeout("slow")
        return httpx.Response(200, content=PAGE.encode(), headers={"content-type": "text/html"}, request=httpx.Request("GET", url))

    monkeypatch.setattr(feeds, "discover", fake_discover)
    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)
    return state


def add_feed(client):
    return client.post("/api/feeds", json={"url": "https://ex.test/feed"}).json()


def test_failed_fetch_is_not_cached_as_the_article(tmp_path, monkeypatch):
    state = boot(tmp_path, monkeypatch, page_ok=False)
    with TestClient(create_app(cfg(tmp_path))) as c:
        add_feed(c)
        eid = next(e["id"] for e in c.get("/api/entries").json() if e["title"] == "Short one")
        assert "just a summary" in c.get(f"/api/entries/{eid}").json()["html"]
        state["page_ok"] = True
        # a failed fetch is remembered for a few minutes (not cached as the article); jump past that window
        import time
        monkeypatch.setattr(time, "time", lambda: 1e12)
        assert "full text here" in c.get(f"/api/entries/{eid}").json()["html"]


def test_links_and_summary_are_untrusted(tmp_path, monkeypatch):
    boot(tmp_path, monkeypatch)
    with TestClient(create_app(cfg(tmp_path))) as c:
        add_feed(c)
        rows = {e["title"]: e for e in c.get("/api/entries").json()}
        assert rows["Bad link"]["url"] == ""
        art = c.get(f"/api/entries/{rows['Bad link']['id']}").json()
        assert art["url"] == "" and "<script>" not in art["html"]


def test_rename_survives_refresh(tmp_path, monkeypatch):
    boot(tmp_path, monkeypatch)
    with TestClient(create_app(cfg(tmp_path))) as c:
        fid = add_feed(c)["id"]
        c.patch(f"/api/feeds/{fid}", json={"title": "My Name"})
        c.post("/api/refresh")
        assert c.get("/api/feeds").json()[0]["title"] == "My Name"


def test_bad_cursor_and_cross_site_writes(tmp_path, monkeypatch):
    boot(tmp_path, monkeypatch)
    with TestClient(create_app(cfg(tmp_path))) as c:
        add_feed(c)
        assert c.get("/api/entries?before=x|notanumber").status_code == 400
        assert c.post("/api/refresh", headers={"sec-fetch-site": "cross-site"}).status_code == 403
        assert c.post("/api/refresh", headers={"sec-fetch-site": "same-origin"}).status_code == 200
        assert c.post("/api/refresh", headers={"sec-fetch-site": "cross-site", "x-forage": "1"}).status_code == 200
        assert c.get("/api/feeds", headers={"sec-fetch-site": "cross-site"}).status_code == 200


def test_concurrent_saves_write_one_file(tmp_path, monkeypatch):
    import asyncio
    boot(tmp_path, monkeypatch)
    conf = cfg(tmp_path)
    app = create_app(conf)

    # Seed through the sync client, then race the saves on one event loop (as uvicorn runs them).
    with TestClient(app) as sync:
        add_feed(sync)
        eid = next(e["id"] for e in sync.get("/api/entries").json() if e["title"] == "Short one")

    async def race():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            return [r.json() for r in await asyncio.gather(*(c.post(f"/api/entries/{eid}/save") for _ in range(5)))]

    res = asyncio.run(race())
    assert len({r["path"] for r in res}) == 5  # every save writes its own file, none overwrites another
    assert len(list(conf.inbox_dir.glob("*.md"))) == 5



def test_private_hosts_are_refused():
    import asyncio
    async def go():
        for host in ("127.0.0.1", "10.0.0.5", "100.100.1.1", "169.254.169.254"):
            try:
                await feeds._no_private_hosts(httpx.Request("GET", f"http://{host}/x"))
            except httpx.RequestError:
                continue
            raise AssertionError(host)
    asyncio.run(go())
