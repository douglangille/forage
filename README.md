# Forage

Self-hosted RSS reader. Reader view is the main screen; one tap saves the article to an Obsidian vault's `inbox/` as a Web Clipper–style note (default-template frontmatter, `tags: clippings`). A PWA, so it runs on macOS, iPhone and iPad from one codebase. Single user, no auth: bind it to localhost and reach it over Tailscale.

## Run locally

    python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
    FORAGE_DATA_DIR=data .venv/bin/python -m forage     # http://127.0.0.1:8787
    .venv/bin/pytest

## Config (environment)

| Var | Default | |
|---|---|---|
| `FORAGE_DATA_DIR` | `data` | SQLite lives here |
| `FORAGE_INBOX_DIR` | `$FORAGE_DATA_DIR/inbox` | where saved clips are written |
| `FORAGE_HOST` / `FORAGE_PORT` | `127.0.0.1` / `8787` | |
| `FORAGE_REFRESH_MINUTES` | `30` | background poll |
| `FORAGE_WIKILINK_AUTHOR` | `1` | `author: - "[[Name]]"` like the clipper default |

## Deploy (ubik)

    deploy/deploy.sh            # rsync, venv, systemd user service
    sudo tailscale serve --bg --https=443 http://127.0.0.1:8787   # once; HTTPS is what lets the PWA install and cache offline

Then open `https://ubik.<tailnet>.ts.net` on each device and Add to Home Screen. The saved file reaches the phone through the vault's own git sync (ubik autosync, GitSync on mobile).

## Keys

`j`/`k` next/prev, `s` save, `m` mark unread, `f` star, `o` open original, `u` unread-only, `r` refresh, `/` search.

## How it works

`feedparser` + `httpx` poll feeds (ETag aware). A page is fetched and cleaned with `trafilatura` on first open unless the feed already carries the full text; HTML is sanitized with `nh3`; the clip is `markdownify` of that HTML.
