# Forage

A self-hosted RSS reader where the reader view is the main screen, and one tap saves the article to your Obsidian vault.

It's a small Python server plus a progressive web app. Install the app to the Home Screen on an iPhone or iPad, or open it in a desktop browser. There's no native app, no account and no cloud service. It's built for one person on a home server, reached over a private network such as [Tailscale](https://tailscale.com).

Not affiliated with Obsidian, Readwise or any feed service.

## What it does

- **Reading first.** Open an article and get a clean, typeset page. If the feed only carries a summary, Forage fetches the page and extracts the readable text ([trafilatura](https://github.com/adbar/trafilatura)), caches it, and sanitizes it.
- **One-tap save to Obsidian.** The checkbox in the article toolbar writes a markdown note to a folder in your vault, in the shape of Obsidian Web Clipper's default template: frontmatter (`title`, `source`, `author`, `published`, `created`, `description`, `tags: clippings`), then `# Title` and the article body.
- **Optional commit and push.** If the vault is a git checkout, a save can also commit just that file and push it, so phones and other machines pick it up through whatever git sync you already use.
- **Find feeds.** Paste a site, page or feed address to list every feed behind it, with built-in rules for Substack, GitHub (releases and commits), Reddit, Medium, YouTube and Bluesky. Or type keywords to search Substack and Feedly.
- **Folders.** Rename, merge and delete folders, move feeds between them, and collapse them in the sidebar.
- **OPML** import and export.
- **Keyboard:** `j`/`k` next and previous, `s` save, `m` toggle read, `f` star, `o` open the original, `u` unread only, `r` refresh, `/` search.
- **Looks:** [Rosé Pine](https://rosepinetheme.com) in dark, Rosé Pine Dawn in light, following the system setting. Reading text is set in [Libron](https://github.com/nicoverbruggen/libron), the interface in Source Sans 3, and code in JetBrainsMono Nerd Font Mono. All are self-hosted, so iOS has them.
- **Installable.** Home Screen app on iOS, with an app-shell cache and recently opened articles available offline.

## Quick start

Python 3.12 or newer.

```sh
git clone https://github.com/douglangille/forage.git
cd forage
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
FORAGE_DATA_DIR=data FORAGE_INBOX_DIR=/path/to/your/vault/inbox .venv/bin/python -m forage
```

Open <http://127.0.0.1:8787>, choose **+ Find feeds**, and go. Without `FORAGE_INBOX_DIR`, saved clips go to `data/inbox`.

Run the tests with `.venv/bin/pytest`.

## Configuration

All settings are environment variables.

| Variable | Default | Meaning |
|---|---|---|
| `FORAGE_DATA_DIR` | `data` | Where the SQLite database lives |
| `FORAGE_INBOX_DIR` | `$FORAGE_DATA_DIR/inbox` | Folder saved clips are written to |
| `FORAGE_HOST` / `FORAGE_PORT` | `127.0.0.1` / `8787` | Listen address |
| `FORAGE_REFRESH_MINUTES` | `30` | Background polling interval |
| `FORAGE_WIKILINK_AUTHOR` | `1` | Write `author: - "[[Name]]"` as Web Clipper does; `0` for plain text |
| `FORAGE_GIT_ROOT` | unset | If set, a save also commits the clip in this git checkout |
| `FORAGE_SYNC_SCRIPT` | unset | Script to run after the commit to rebase and push; unset means plain `git pull --rebase` then `git push` |
| `FORAGE_USER_AGENT` | a generic UA | Sent when fetching feeds and pages |

When `FORAGE_GIT_ROOT` is set, the commit is path-limited to the saved file, so anything else you have mid-edit in that checkout is left alone. A git failure never fails the save: the file is already written, and the error goes to the log.

## Running it on a home server

`deploy/deploy.sh user@host` copies the code to `~/forage` on the server, builds a venv, installs the service as a **systemd user service**, and restarts it. Put per-host settings in `~/.config/forage/forage.env` on the server (plain `KEY=value` lines, absolute paths):

```
FORAGE_INBOX_DIR=/home/you/vault/inbox
FORAGE_GIT_ROOT=/home/you/vault
FORAGE_SYNC_SCRIPT=/home/you/vault/scripts/sync.sh
```

To start at boot without anyone logged in, run `loginctl enable-linger $USER` once.

### HTTPS and the private network

iOS only installs and caches a PWA properly over HTTPS. With Tailscale, enable HTTPS certificates for your tailnet, then on the server:

```sh
sudo tailscale serve --bg --https=443 http://127.0.0.1:8787
```

Open the `https://<host>.<tailnet>.ts.net` address on each device and choose Add to Home Screen. The app inlines its touch icon in the page because iOS can fail to fetch a separately linked icon from a private-network host and then shows a letter tile.

## Security

**Forage has no login, by design.** Anyone who can reach the port can read your feeds, mark things read, delete feeds and write files into the inbox folder. Bind it to localhost (the default) and expose it only through a private network or an authenticating reverse proxy. Never put it on the public internet as it is.

It also fetches whatever address you give it, from the server's own network position. That's the point of the "find feeds" box, but on a shared network it means anyone who can reach Forage can make it request internal URLs. Keep it single-user.

Feed and article HTML is sanitized (`nh3`) before it's stored or shown, with scripts, event handlers and unknown tags stripped.

## Limits worth knowing

- **Paywalled and logged-in content.** Page fetches are anonymous, so a paid post gives you the teaser at best. Forage doesn't read email newsletters either. Use the newsletter's RSS feed where it has one.
- **Extraction is heuristic.** It occasionally keeps a "related posts" block or drops a section.
- **Unofficial search sources.** The Feedly and Substack keyword search use undocumented endpoints and may break. URL discovery doesn't depend on them.
- **YouTube** feed endpoints are flaky. A channel URL may resolve to a feed that then fails to verify.
- **Backups.** Saved clips live in your vault. The database holds your feed list and read and starred state. Exporting OPML from **Manage** is the cheap backup of the feeds.
- **iOS PWAs are not native apps.** Offline reading covers recently opened articles, not everything.

## How it's built

FastAPI and uvicorn, SQLite, `feedparser` and `httpx` for fetching (ETag aware), `trafilatura` for extraction, `nh3` for sanitizing, `markdownify` for the clip. The front end is plain JavaScript and CSS with no build step, in `forage/static/`.

## Credits and licences

Forage is MIT licensed (see `LICENSE`). The bundled fonts are SIL Open Font License 1.1, with their licence texts in `forage/static/fonts/`:

- [Libron](https://github.com/nicoverbruggen/libron) by Nico Verbruggen, v0.31 web build
- [Source Sans 3](https://github.com/adobe-fonts/source-sans) by Adobe, 3.052R, subset
- [JetBrains Mono](https://www.jetbrains.com/lp/mono/) via [Nerd Fonts](https://github.com/ryanoasis/nerd-fonts) 3.5.1, subset

The palette is [Rosé Pine](https://rosepinetheme.com).
