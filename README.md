# Forage

A self-hosted RSS reader where the reader view is the main screen, and one tap saves the article to your Obsidian vault.

It's a small Python server plus a progressive web app. Install the app to the Home Screen on an iPhone or iPad, or open it in a desktop browser. There's no native app, no account and no cloud service. It's built for one person on a home server.

> ## Read this first
>
> **WHAT THIS IS:** a single-user app for one person, on a private network you control.
>
> **WHAT THIS IS NOT:** a multi-user service, a hosted product, or anything safe to put on the public internet.
>
> **THERE IS NO LOGIN. NONE.** Anyone who can reach the port can read your feeds, change or delete them, make the server fetch any URL, and write files into your vault's inbox folder. **Your security is entirely a matter of who can reach it.**
>
> **How I run it:** on a home server, bound to localhost, exposed only to my own devices over [Tailscale](https://tailscale.com). Other ways to host it are listed [below](#other-ways-to-host-it). Pick one that keeps strangers out, or don't run it.

Not affiliated with Obsidian, Readwise or any feed service.

## What it does

- **Reading first.** Open an article and get a clean, typeset page. If the feed only carries a summary, Forage fetches the page and extracts the readable text ([trafilatura](https://github.com/adbar/trafilatura)), caches it, and sanitizes it.
- **One-tap save to Obsidian.** The checkbox in the article toolbar writes a markdown note to a folder in your vault, in the shape of Obsidian Web Clipper's default template: frontmatter (`title`, `source`, `author`, `published`, `created`, `description`, `tags: clippings`), then `# Title` and the article body.
- **Optional commit and push.** If the vault is a git checkout, a save can also commit just that file and push it, so phones and other machines pick it up through whatever git sync you already use.
- **Settings in the app.** Inbox folder, commit-and-push, author style and refresh interval are set from a gear button, with no config file.
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
.venv/bin/python -m forage
```

Open <http://127.0.0.1:8787>, choose **+ Find feeds**, then open **Settings** (the gear) and point **Obsidian inbox folder** at a folder in your vault. Until you do, saved clips go to `data/inbox`.

Run the tests with `.venv/bin/pytest`.

## Configuration

**Settings (the gear in the sidebar)** are saved in the database, so there's no file to edit:

- **Obsidian inbox folder:** where saved clips are written. Forage creates it if it doesn't exist.
- **Commit and push each saved clip:** if that folder is inside a git checkout, a save also commits just the clip and pushes it, so phones and other machines get it through whatever git sync you already use. The commit is limited to the saved file, so anything else you have mid-edit in the checkout is left alone. A git failure never fails the save: the file is already written, and the error goes to the log.
- **Write the author as a [[wikilink]]:** Web Clipper's default does; turn it off for plain text.
- **Check for new articles every N minutes:** 5 to 1440.

**Environment variables** only cover what has to be known before the app starts, plus optional first-run defaults for the settings above (a value saved in Settings wins):

| Variable | Default | Meaning |
|---|---|---|
| `FORAGE_DATA_DIR` | `data` | Where the SQLite database lives |
| `FORAGE_HOST` / `FORAGE_PORT` | `127.0.0.1` / `8787` | Listen address |
| `FORAGE_USER_AGENT` | a generic UA | Sent when fetching feeds and pages |
| `FORAGE_INBOX_DIR`, `FORAGE_WIKILINK_AUTHOR`, `FORAGE_REFRESH_MINUTES` | see Settings | First-run defaults |
| `FORAGE_GIT_ROOT` | unset | First-run default: turns on commit-and-push for this checkout |
| `FORAGE_SYNC_SCRIPT` | unset | **Server-side only.** A script to run after the commit to rebase and push, instead of plain `git pull --rebase` and `git push` |

The sync script is deliberately not a setting. Forage has no login, so a script path editable from the UI would let anyone who can reach it run a command on the server.

## Running it on a home server

`deploy/deploy.sh user@host` copies the code to `~/forage` on the server, builds a venv, installs the service as a **systemd user service**, and restarts it. Everything day-to-day is in Settings. If you need the environment variables above, put them as plain `KEY=value` lines (absolute paths) in `~/.config/forage/forage.env` on the server, which the service unit reads.

To start at boot without anyone logged in, run `loginctl enable-linger $USER` once.

### How I run it: Tailscale

iOS only installs and caches a PWA properly over HTTPS. With Tailscale, enable HTTPS certificates for your tailnet, then on the server:

```sh
sudo tailscale serve --bg --https=443 http://127.0.0.1:8787
```

Open the `https://<host>.<tailnet>.ts.net` address on each device and choose Add to Home Screen. The app inlines its touch icon in the page because iOS can fail to fetch a separately linked icon from a private-network host and then shows a letter tile.

### Other ways to host it

Forage only needs something in front of it that decides who gets in. Tailscale is what I use and the only one I've tested. These are the other common shapes, described and **not tested by me**:

- **Another private network** (WireGuard, ZeroTier, a VPN you already run). Same idea as Tailscale: nothing public, only your devices can connect. You still need a valid HTTPS certificate for iOS to install and cache the app.
- **A reverse proxy that does the authentication** (Caddy or nginx with basic auth, or an auth layer such as Authelia). Forage stays on `127.0.0.1` and the proxy faces the network. Check that installing to the iOS Home Screen still works behind your auth, since PWAs and login prompts don't always get along.
- **Cloudflare Tunnel with Access**, or a similar identity-aware gateway, in front of localhost.
- **Localhost only**, with an SSH tunnel when you want it. Fine for a desktop, no use for an installed phone app.

Whatever you choose, bind Forage to `127.0.0.1` (the default) and let the front layer be the only way in.

## Security

**Forage has no login, by design.** Anyone who can reach the port can read your feeds, mark things read, delete feeds and write files into the inbox folder. **Never put it on the public internet as it is.**

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
