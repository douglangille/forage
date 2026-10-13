"use strict";
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const S = {
  ai: false, undo: false, feeds: [], entries: [], sel: null, cur: null, loadingMore: false, done: false,
  scope: { type: "today" },         // today | all | starred | folder | feed
  unreadOnly: true, q: "",
  get narrow() { return matchMedia("(max-width: 899px)").matches; },
};
try { S.unreadOnly = localStorage.getItem("forage.unreadOnly") !== "0"; } catch {}

// Stroke icons (Lucide shapes), drawn at 24x24 and recoloured by currentColor. Buttons name theirs with data-icon.
const ICONS = {
  menu: "M4 6h16M4 12h16M4 18h16",
  refresh: "M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8M21 3v5h-5",
  settings: "M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
  pencil: "M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5zM15 5l4 4",
  "check-all": "M18 6 7 17l-5-5M22 10l-7.5 7.5L13 16",
  "chevron-left": "m15 18-6-6 6-6",
  "chevron-right": "m9 18 6-6-6-6",
  "chevron-up": "m18 15-6-6-6 6",
  "chevron-down": "m6 9 6 6 6-6",
  maximize: "M8 3H5a2 2 0 0 0-2 2v3M21 8V5a2 2 0 0 0-2-2h-3M3 16v3a2 2 0 0 0 2 2h3M16 21h3a2 2 0 0 0 2-2v-3",
  minimize: "M8 3v3a2 2 0 0 1-2 2H3M21 8h-3a2 2 0 0 1-2-2V3M3 16h3a2 2 0 0 1 2 2v3M16 21v-3a2 2 0 0 1 2-2h3",
  star: "M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01z",
  mail: "M4 4h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zM22 7l-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7",
  "mail-open": "M21.2 8.4c.5.38.8.97.8 1.6v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V10a2 2 0 0 1 .8-1.6l8-6a2 2 0 0 1 2.4 0zM22 10l-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 10",
  external: "M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6",
  list: "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01",
  share: "M4 12v7a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-7M16 6l-4-4-4 4M12 2v13",
  save: "M12 17V3M6 11l6 6 6-6M19 21H5",  // down to a line: into the inbox
};
const svg = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${ICONS[name]}"/></svg>`;
function setIcon(el, name) { if (el.dataset.icon !== name || !el.firstChild) { el.dataset.icon = name; el.innerHTML = svg(name); } }
document.querySelectorAll("[data-icon]").forEach((el) => setIcon(el, el.dataset.icon));

async function api(path, opts = {}) {
  const r = await fetch("/api" + path, {
    method: opts.method || "GET",
    headers: opts.body ? { "content-type": "application/json" } : undefined,
    body: opts.body ? JSON.stringify(opts.body) : opts.raw,
  });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return r.json();
}

let toastTimer;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2400);
}

function ago(iso) {
  const s = (Date.now() - new Date(iso)) / 1000;
  if (s < 3600) return Math.max(1, Math.round(s / 60)) + "m";
  if (s < 86400) return Math.round(s / 3600) + "h";
  if (s < 86400 * 30) return Math.round(s / 86400) + "d";
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: s > 86400 * 330 ? "numeric" : undefined });
}

/* ---------- sidebar ---------- */
function scopeKey(sc) { return sc.type + ":" + (sc.id ?? sc.name ?? ""); }

function folderNames() { return [...new Set(S.feeds.map((f) => f.folder).filter(Boolean))].sort((x, y) => x.localeCompare(y)); }
function folderOptions(cur = "") {
  return `<option value="">Unfiled</option>` + folderNames().map((n) => `<option value="${esc(n)}" ${n === cur ? "selected" : ""}>${esc(n)}</option>`).join("") + `<option value="__new__">New folder…</option>`;
}
function folderPick(sel, input) { return sel.value === "__new__" ? input.value.trim() : sel.value; }

let collapsed = new Set();
try { collapsed = new Set(JSON.parse(localStorage.getItem("forage.collapsed") || "[]")); } catch {}
function saveCollapsed() { try { localStorage.setItem("forage.collapsed", JSON.stringify([...collapsed])); } catch {} }

function renderNav() {
  const total = S.feeds.reduce((a, f) => a + f.unread, 0);
  const folders = {};
  S.feeds.forEach((f) => (folders[f.folder] ||= []).push(f));
  const item = (sc, label, n, cls = "", err = false) =>
    `<button class="nav-item ${cls} ${err ? "err" : ""} ${scopeKey(S.scope) === scopeKey(sc) ? "sel" : ""}" data-scope='${esc(JSON.stringify(sc))}'><span class="t">${esc(label)}</span>${n ? `<span class="n">${n}</span>` : ""}</button>`;
  let h = item({ type: "today" }, "Today", 0, "today") + item({ type: "all" }, "All items", total) + item({ type: "starred" }, "Starred", 0);
  const names = Object.keys(folders).sort((a, b) => (!a - !b) || a.localeCompare(b));  // folders by name, unfiled last
  for (const name of names) {
    const feedsHtml = folders[name].map((f) => item({ type: "feed", id: f.id }, f.title || f.url, f.unread, name ? "nav-feed" : "", !!f.error)).join("");
    if (name) {
      const n = folders[name].reduce((a, f) => a + f.unread, 0);
      const closed = collapsed.has(name);
      h += `<div class="nav-fold"><button class="caret" data-fold="${esc(name)}" aria-label="${closed ? "Expand" : "Collapse"} ${esc(name)}" aria-expanded="${!closed}">${closed ? "▸" : "▾"}</button>`
        + item({ type: "folder", name }, name, n, "folder") + `</div>` + (closed ? "" : feedsHtml);
    } else {
      if (names.length > 1) h += `<div class="nav-folder">Unfiled</div>`;
      h += feedsHtml;
    }
  }
  $("#nav").innerHTML = h;
  document.title = total ? `(${total}) Forage` : "Forage";
  try { navigator.setAppBadge?.(total); } catch {}
}

async function loadFeeds() { S.feeds = await api("/feeds"); renderNav(); }

function scopeTitle() {
  const sc = S.scope;
  if (sc.type === "today") return "Today";
  if (sc.type === "starred") return "Starred";
  if (sc.type === "folder") return sc.name;
  if (sc.type === "feed") return S.feeds.find((f) => f.id === sc.id)?.title || "Feed";
  return "All items";
}

/* ---------- entry list ---------- */
function query(before) {
  const p = new URLSearchParams({ limit: "50" });
  const sc = S.scope;
  if (sc.type === "feed") p.set("feed", sc.id);
  if (sc.type === "folder") p.set("folder", sc.name);
  if (sc.type === "today") { p.set("since_hours", String(TODAY_HOURS)); p.set("cards", "true"); }
  if (sc.type === "starred") p.set("starred", "true");
  else if (S.unreadOnly) p.set("unread", "true");
  if (S.q) p.set("q", S.q);
  if (before) p.set("before", before);
  return "/entries?" + p;
}

const TODAY_HOURS = 24;
let loadTok = 0;
async function loadEntries(append = false) {
  if (append && S.loadingMore) return;
  const tok = append ? loadTok : ++loadTok;  // a fresh load supersedes anything still in flight
  if (append) S.loadingMore = true;
  try {
    const last = S.entries[S.entries.length - 1];
    const rows = await api(query(append && last ? `${last.published}|${last.id}` : ""));
    if (tok !== loadTok) return;
    S.entries = append ? S.entries.concat(rows) : rows;
    S.done = rows.length < 50;
    renderList(append);
  } catch (e) { if (tok === loadTok) toast("Couldn't load: " + e.message); }
  finally { if (append) S.loadingMore = false; }
}

function renderList(append) {
  $("#app").dataset.scope = S.scope.type;
  $("#list-title").textContent = scopeTitle() + (S.scope.type === "today" && S.entries.length ? ` · ${S.entries.length}${S.done ? "" : "+"}` : "");
  $("#btn-filter").classList.toggle("on", S.unreadOnly);
  $("#btn-filter").hidden = S.scope.type === "starred";
  $("#btn-editfeed").hidden = S.scope.type !== "feed";
  const ul = $("#entries");
  const keepTop = ul.scrollTop;
  if (append && ul.children.length && ul.children.length <= S.entries.length) {
    const from = ul.children.length;
    ul.insertAdjacentHTML("beforeend", S.entries.slice(from).map((e, i) => rowHtml(e, from + i)).join(""));  // only the new rows
  } else {
    ul.innerHTML = S.entries.map((e, i) => rowHtml(e, i)).join("");
    if (append) ul.scrollTop = keepTop;
  }
  layoutCards();
  $("#list-empty").hidden = S.entries.length > 0;
  $("#list-empty").textContent = S.feeds.length ? "Nothing here. Go outside." : "No feeds yet. Add one, or import an OPML file from Manage.";
}

function cardHtml(e) {
  return `<li data-id="${e.id}" tabindex="0" role="button" class="card ${e.card ? "" : "bare"} ${e.is_read ? "" : "unread"} ${S.cur === e.id ? "sel" : ""}">
    ${e.card ? `<img class="pic" loading="lazy" decoding="async" src="${esc(e.card)}" style="aspect-ratio: 1 / ${e.card_ratio}" alt="" onerror="this.closest('li').classList.add('bare');this.remove();layoutCards()">` : ""}
    <div class="cbody"><div class="sub"><span class="dot"></span>${esc(e.feed_title)} · ${ago(e.published)}</div>
      <div class="ttl">${esc(e.title)}</div>
      ${e.summary ? `<div class="snip">${esc(e.summary.slice(0, 200))}</div>` : ""}</div></li>`;
}

// Masonry for Today: columns of at least CARD_MIN px fill the width and each card goes where the shortest column is.
// Heights are read after every width is set, so it's one reflow.
const CARD_MIN = 250, CARD_GAP = 14;
let layoutQueued = false;
function layoutCards() {
  if (layoutQueued) return;
  layoutQueued = true;
  requestAnimationFrame(() => {
    layoutQueued = false;
    const ul = $("#entries");
    if (S.scope.type !== "today" || !ul.clientWidth) return;
    const cs = getComputedStyle(ul), padL = parseFloat(cs.paddingLeft), padT = parseFloat(cs.paddingTop);
    const W = ul.clientWidth - padL - parseFloat(cs.paddingRight);
    const n = Math.max(1, Math.floor((W + CARD_GAP) / (CARD_MIN + CARD_GAP)));
    const cw = (W - CARD_GAP * (n - 1)) / n;
    const cards = [...ul.children];
    cards.forEach((li) => { li.style.width = cw + "px"; });
    const heights = cards.map((li) => li.offsetHeight);
    const col = Array(n).fill(0);
    cards.forEach((li, i) => {
      const at = col.indexOf(Math.min(...col)), top = col[at];
      li.style.left = padL + at * (cw + CARD_GAP) + "px";
      li.style.top = padT + top + "px";
      col[at] = top + heights[i] + CARD_GAP;
    });
    ul.style.setProperty("--mason-h", Math.max(0, ...col) - CARD_GAP + "px");
  });
}
new ResizeObserver(layoutCards).observe($("#entries"));

function rowHtml(e, i) {
  if (S.scope.type === "today") return cardHtml(e, i);
  return `<li data-id="${e.id}" tabindex="0" role="button" class="${e.is_read ? "" : "unread"} ${S.cur === e.id ? "sel" : ""}">
    <span class="dot"></span>
    <div class="txt"><div class="ttl">${esc(e.title)}</div>
      <div class="sub">${esc(e.feed_title)} · ${ago(e.published)}</div>
      ${e.summary ? `<div class="snip">${esc(e.summary.slice(0, 160))}</div>` : ""}</div>
    ${e.thumb ? `<img class="thumb" loading="lazy" decoding="async" width="56" height="56" src="${esc(e.thumb)}" alt="" onerror="this.remove()">` : ""}</li>`;
}

function patchRow(e) {
  const li = $(`#entries li[data-id="${e.id}"]`);
  if (li) { li.outerHTML = rowHtml(e, S.entries.indexOf(e)); layoutCards(); }
}

/* ---------- reader ---------- */
async function openEntry(id, { fromList = true } = {}) {
  S.cur = id;
  const e = S.entries.find((x) => x.id === id);
  document.querySelectorAll("#entries li.sel").forEach((n) => n.classList.remove("sel"));
  $(`#entries li[data-id="${id}"]`)?.classList.add("sel");
  $("#reader-empty").hidden = true;
  $("#article").hidden = false;
  $("#art-title").textContent = e?.title || "";
  $("#art-meta").textContent = e ? e.feed_title : "";
  $("#art-content").innerHTML = '<p class="meta">Loading…</p>';
  renderSummary("");
  $("#art-scroll").scrollTop = 0;
  S.anchor = null;
  syncFocus();
  if (S.narrow && fromList) { history.pushState({ view: "reader" }, ""); setView("reader"); }
  else if (!S.narrow) { $("#app").dataset.tview = S.scope.type === "today" ? "read" : ""; syncFocus(); }
  try {
    const a = await api("/entries/" + id);
    if (S.cur !== id) return;
    S.art = a;
    $("#art-title").textContent = a.title;
    $("#art-meta").textContent = [a.feed_title, a.author, new Date(a.published).toLocaleDateString(undefined, { dateStyle: "medium" })].filter(Boolean).join(" · ");
    $("#art-content").innerHTML = a.html;
    $("#btn-summary").hidden = !a.summary_on;
    renderSummary(a.summary, a.summary_model);
    $("#art-content").querySelectorAll("a").forEach((l) => (l.target = "_blank"));
    requestAnimationFrame(() => {
      layoutPaging();
    });
    $("#btn-open").hidden = !a.url;
    $("#btn-share").hidden = !a.url;
    if (a.url) $("#btn-open").href = a.url;
    $("#btn-star").classList.toggle("on", !!a.starred);
    if (e) { const was = !e.is_read; e.is_read = 1; e.starred = a.starred; patchRow(e); if (was) bump(e.feed_id, -1); }
    setReadBtn(true);
  } catch (err) {
    $("#art-content").innerHTML = `<p class="meta">Couldn't load this one: ${esc(err.message)}</p>`;
  }
}

function bump(feedId, d) {
  const f = S.feeds.find((x) => x.id === feedId);
  if (f) { f.unread = Math.max(0, f.unread + d); renderNav(); }
}

// Saving is a one-way push to the inbox, not state the app keeps: the button is always ready, and every press writes a new clip.
function setSaveBtn(busy = false) {
  document.querySelectorAll(".save-btn").forEach((b) => {
    b.disabled = busy;
    setIcon(b, "save");
    b.title = "Save a clip to the Obsidian inbox (s)";
  });
}

async function save() {
  if (!S.cur || S.saving) return;
  S.saving = true;
  setSaveBtn(true);
  try {
    const r = await api(`/entries/${S.cur}/save`, { method: "POST" });
    toast("Saved to inbox: " + r.path);
  } catch (err) { toast("Save failed: " + err.message); }
  S.saving = false;
  setSaveBtn();
}

// The system share sheet for the article's own address (not a clip of it). Browsers without one get the link copied.
async function share() {
  const a = S.art; if (!a?.url) return;
  try {
    if (navigator.share) await navigator.share({ title: a.title, url: a.url });
    else { await navigator.clipboard.writeText(a.url); toast("Link copied"); }
  } catch (err) { if (err.name !== "AbortError") toast("Couldn't share: " + err.message); }
}

// An on-demand summary of the open article: kept by the server, shown above the text. The model's output is escaped, never HTML.
function renderSummary(text, model, note = "") {
  const box = $("#art-summary");
  if (!text) { box.hidden = true; box.innerHTML = ""; return; }
  const bullets = text.split("\n").map((l) => l.replace(/^-\s+/, "").trim()).filter(Boolean)
    .map((l) => `<li>${esc(l).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")}</li>`).join("");
  box.innerHTML = `<details open><summary>Summary · AI-written${model ? " by " + esc(model) : ""}</summary><ul>${bullets}</ul>`
    + (note ? `<p class="redo">${esc(note)}</p>` : "") + `<p class="redo"><button type="button" class="linkish" id="sum-redo">Summarize again</button></p></details>`;
  box.hidden = false;
  $("#sum-redo").onclick = () => summarizeNow(true);
}
async function summarizeNow(force = false) {
  const id = S.cur, b = $("#btn-summary");
  if (!id || b.classList.contains("busy")) return;
  const box = $("#art-summary");
  if (!force && !box.hidden) { box.querySelector("details").open = !box.querySelector("details").open; return; }  // already there: fold it
  b.classList.add("busy"); b.title = "Summarizing…";
  if (box.hidden) { box.innerHTML = '<p class="redo">Summarizing…</p>'; box.hidden = false; }
  try {
    const r = await api(`/entries/${id}/summarize`, { method: "POST", body: { force } });
    if (S.cur !== id) return;
    renderSummary(r.summary, r.model, [r.note, r.truncated ? "Only the first part of a long article was summarized." : ""].filter(Boolean).join(" "));
  } catch (err) {
    if (S.cur === id) { if (S.art?.summary) renderSummary(S.art.summary, S.art.summary_model); else { box.hidden = true; box.innerHTML = ""; } }
    toast(err.message);
  } finally { b.classList.remove("busy"); b.title = "Summarize this article"; }
}

async function toggleStar() {
  if (!S.art) return;
  const v = !S.art.starred;
  try { await api(`/entries/${S.cur}/state`, { method: "POST", body: { starred: v } }); }
  catch (err) { return toast("Couldn't update: " + err.message); }
  S.art.starred = v ? 1 : 0;
  const e = S.entries.find((x) => x.id === S.cur);
  if (e) { e.starred = S.art.starred; }
  $("#btn-star").classList.toggle("on", v);
}

function setReadBtn(isRead) {
  const b = $("#btn-unread");
  setIcon(b, isRead ? "mail-open" : "mail");
  b.classList.toggle("on", !isRead);
  b.title = isRead ? "Mark unread (m)" : "Mark read (m)";
  b.setAttribute("aria-pressed", !isRead);
}

async function toggleRead() {
  const e = S.entries.find((x) => x.id === S.cur);
  if (!e) return;
  const nowRead = !e.is_read;
  try { await api(`/entries/${S.cur}/state`, { method: "POST", body: { read: nowRead } }); }
  catch (err) { return toast("Couldn't update: " + err.message); }
  e.is_read = nowRead ? 1 : 0;
  if (S.art) S.art.is_read = e.is_read;
  patchRow(e); bump(e.feed_id, nowRead ? -1 : 1); setReadBtn(nowRead);
}

function step(d) {
  const i = S.entries.findIndex((x) => x.id === S.cur);
  const n = S.entries[Math.min(S.entries.length - 1, Math.max(0, i + d))];
  if (n && n.id !== S.cur) {
    openEntry(n.id, { fromList: false });
    $(`#entries li[data-id="${n.id}"]`)?.scrollIntoView({ block: "nearest" });
    if (i + d >= S.entries.length - 5 && !S.done) loadEntries(true);
  }
}

/* ---------- focus mode: paged columns ---------- */
// The article becomes horizontal pages of columns up to 66ch wide. The browser lays out the columns and scrolls natively (swipe, trackpad);
// this block turns pages, snaps to a page boundary, shows "n / N", and keeps your place across reflows (resize, images
// loading) by remembering the text point at the top-left of the page.
S.focus = false;
try { S.focus = localStorage.getItem("forage.focus") === "1"; } catch {}
const focusOn = () => $("#app").dataset.focus === "1" && (!S.narrow || $("#app").dataset.view === "reader");
const reduceMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
function pagerMetrics() {
  const sc = $("#art-scroll"), body = document.querySelector(".art-body");
  const bodyRect = body.getBoundingClientRect();
  const gap = parseFloat(getComputedStyle(body).columnGap) || 0;
  const stride = bodyRect.width + gap;
  // The last content box tells us how many pages there are, and it can't create a blank page the way a trailing sentinel can.
  const rg = document.createRange();
  rg.selectNodeContents(body);
  let x = 0;
  for (const r of rg.getClientRects()) if (r.width > 0 || r.height > 0) x = Math.max(x, r.left - bodyRect.left);
  const pages = Math.max(1, Math.floor((x + 2) / stride) + 1);
  const cs = getComputedStyle(sc);
  return { sc, body, bodyRect, gap, stride, pages, padL: parseFloat(cs.paddingLeft) || 0, padR: parseFloat(cs.paddingRight) || 0, padT: parseFloat(cs.paddingTop) || 0 };
}
const clampPage = (m, p) => Math.min(m.pages - 1, Math.max(0, p));
const pageOfScroll = (m) => clampPage(m, Math.round(m.sc.scrollLeft / m.stride));
function updatePager() {
  if (!focusOn()) return;
  const m = pagerMetrics();
  $("#pg-n").textContent = `${(S.target ?? pageOfScroll(m)) + 1} / ${m.pages}`;
}
// The anchor is a collapsed Range at the text point under the page's top-left corner (or {el} for a block when there is none).
function saveAnchor(m) {
  const x = m.sc.getBoundingClientRect().left + m.padL + 2, y = m.sc.getBoundingClientRect().top + m.padT + 8;
  let r = null;
  if (document.caretRangeFromPoint) r = document.caretRangeFromPoint(x, y);
  else if (document.caretPositionFromPoint) {
    const c = document.caretPositionFromPoint(x, y);
    if (c) { r = document.createRange(); r.setStart(c.offsetNode, c.offset); r.collapse(true); }
  }
  S.anchor = r && m.body.contains(r.startContainer) ? { range: r } : null;
}
// Vertical (non-focus) reading position: the text point just under the top edge of the article.
function saveVerticalAnchor() {
  const sc = $("#art-scroll"), sr = sc.getBoundingClientRect(), br = document.querySelector(".art-body").getBoundingClientRect();
  const x = br.left + 4, y = sr.top + (parseFloat(getComputedStyle(sc).paddingTop) || 0) + 4;
  let r = null;
  if (document.caretRangeFromPoint) r = document.caretRangeFromPoint(x, y);
  else if (document.caretPositionFromPoint) {
    const c = document.caretPositionFromPoint(x, y);
    if (c) { r = document.createRange(); r.setStart(c.offsetNode, c.offset); r.collapse(true); }
  }
  S.anchor = r && document.querySelector(".art-body").contains(r.startContainer) ? { range: r } : null;
}
function anchorEl() {
  const a = S.anchor;
  if (a?.el?.isConnected) return a.el;
  if (a?.range?.startContainer.isConnected) { const n = a.range.startContainer; return n.nodeType === 1 ? n : n.parentElement; }
  return null;
}
function anchorRect() {
  const a = S.anchor;
  if (a?.range?.startContainer.isConnected) {
    const q = a.range.cloneRange(), n = q.startContainer;
    if (n.nodeType === 3 && q.startOffset < n.length) q.setEnd(n, q.startOffset + 1);
    const r = q.getClientRects()[0];
    if (r) return r;
  }
  return anchorEl()?.getClientRects()[0] || null;
}
// A column is never wider than 66ch (the line-length cap, same as the scrolling view) nor narrower than 45ch. The column count is the
// fewest that keeps every column under the cap, and when there's room to spare the whole block is capped and centered.
function setColumns() {
  const body = document.querySelector(".art-body");
  const gap = parseFloat(getComputedStyle(body).columnGap) || 0;
  const probe = document.createElement("div");
  probe.style.cssText = "position:absolute;visibility:hidden;width:66ch";
  body.appendChild(probe);
  const cap = probe.offsetWidth;
  probe.remove();
  body.style.maxWidth = "none";
  const w = body.getBoundingClientRect().width;
  let n = Math.max(1, Math.ceil((w + gap) / (cap + gap)));
  while (n > 1 && (w - (n - 1) * gap) / n < cap * 0.68) n--;
  const col = (w - (n - 1) * gap) / n;
  body.style.maxWidth = col > cap ? n * cap + (n - 1) * gap + "px" : "none";
  body.style.setProperty("--cols", String(n));
}
function layoutPaging() {
  if (!focusOn()) return;
  setColumns();
  const m = pagerMetrics();
  // The scroller is wider than a page once the block is capped and centred, so the neighbouring pages would show in the margins.
  // Clip the scroller to the page itself: the body sits centred between the padding, one page wide.
  const bw = m.body.getBoundingClientRect().width, left = m.padL + (m.sc.clientWidth - m.padL - m.padR - bw) / 2;
  m.sc.style.clipPath = `inset(0 ${Math.max(0, m.sc.clientWidth - left - bw)}px 0 ${Math.max(0, left)}px)`;
  // Without this, a short last page can't be scrolled to its own start.
  // The block is centred, so scrolling to the last page also needs the centring margin on both sides of it.
  const spare = Math.max(0, m.sc.clientWidth - m.padL - m.padR - bw);
  $("#art-pad").style.left = (m.padL + m.pages * m.stride - m.gap + m.padR - 1 + spare) + "px";
  if (!S.moving) {   // never fight a page turn in flight; settle() re-anchors when it ends
    const r = S.anchor && anchorRect(), was = m.sc.scrollLeft;
    // Reflow scrolls (ours or the browser's clamping) must not re-anchor: only a page turn or the reader's own scroll does (S.saveOK).
    m.sc.scrollLeft = r ? clampPage(m, Math.floor((r.left - m.bodyRect.left + 2) / m.stride)) * m.stride : Math.min(was, (m.pages - 1) * m.stride);

  }
  updatePager();
}
let settleT;
function settle() {
  clearTimeout(settleT);
  if (!focusOn()) { S.moving = false; S.target = null; S.snapped = false; return; }
  const m = pagerMetrics(), p = S.target != null ? clampPage(m, S.target) : pageOfScroll(m);
  S.target = null;
  if (!S.snapped && Math.abs(m.sc.scrollLeft - p * m.stride) > 1) {
    // Off a page boundary: glide there, then look again. The anchor is only saved once things have stopped moving.
    S.snapped = true; S.moving = true; S.target = p;
    m.sc.scrollTo({ left: p * m.stride, behavior: reduceMotion() ? "auto" : "smooth" });
    settleT = setTimeout(settle, reduceMotion() ? 50 : 450);
    return;
  }
  S.snapped = false; S.moving = false;
  if (S.saveOK) { S.saveOK = false; saveAnchor(m); }
  updatePager();
}
function goPage(p, smooth = true) {
  S.saveOK = true;
  const m = pagerMetrics();
  S.target = clampPage(m, p);
  S.moving = true;
  m.sc.scrollTo({ left: S.target * m.stride, behavior: smooth && !reduceMotion() ? "smooth" : "auto" });
  updatePager();
  clearTimeout(settleT);
  settleT = setTimeout(settle, smooth ? 450 : 50);
}
function turn(d) {
  const m = pagerMetrics(), t = (S.target ?? pageOfScroll(m)) + d;
  if (t >= 0 && t < m.pages) { goPage(t); return; }
  // The first and last page are the ends: Next/Previous (j/k) move between articles, paging never does.
}
function syncFocus() {
  // On a wide screen Today's cards are their own screen: focus mode belongs to the article, not to them.
  const onCards = !S.narrow && S.scope.type === "today" && $("#app").dataset.tview !== "read";
  const on = S.focus && !$("#article").hidden && !onCards;
  $("#app").dataset.focus = on ? "1" : "";
  $("#btn-focus").classList.toggle("on", S.focus);
  $("#btn-focus").setAttribute("aria-pressed", S.focus ? "true" : "false");
  setIcon($("#btn-focus"), S.focus ? "minimize" : "maximize");
  $("#art-scroll").scrollLeft = 0;
  if (!on) { const b = document.querySelector(".art-body"); b.style.maxWidth = ""; b.style.removeProperty("--cols"); $("#art-scroll").style.clipPath = ""; }
  S.target = null; S.moving = false;
  requestAnimationFrame(() => layoutPaging());
}
// Toggling keeps your place: vertical position becomes a page, and a page becomes a vertical position.
function setFocus(on) {
  const m = focusOn() ? pagerMetrics() : null;
  if (m) saveAnchor(m);
  else saveVerticalAnchor();
  S.focus = on;
  try { localStorage.setItem("forage.focus", on ? "1" : "0"); } catch {}
  syncFocus();
  if (!on) requestAnimationFrame(() => {
    const r = anchorRect(), sc = $("#art-scroll");
    if (r) sc.scrollTop = Math.max(0, r.top - sc.getBoundingClientRect().top + sc.scrollTop - 24);
    S.anchor = null;
  });
}
{
  const sc = $("#art-scroll");
  let wheelAt = 0;
  sc.addEventListener("scroll", () => {
    if (!focusOn()) return;
    updatePager();
    clearTimeout(settleT);
    settleT = setTimeout(settle, S.moving ? 450 : 140);
  }, { passive: true });
  // Touching the page takes over from a programmatic turn.
  sc.addEventListener("touchstart", () => { S.target = null; S.moving = false; S.ptr = "touch"; S.saveOK = true; }, { passive: true });
  sc.addEventListener("pointerdown", (e) => { S.ptr = e.pointerType; }, { passive: true });
  // A mouse wheel or trackpad flick turns one page. Momentum events keep refreshing wheelAt, so one flick is one page.
  sc.addEventListener("wheel", (e) => {
    S.saveOK = true;
    if (!focusOn() || e.ctrlKey || e.metaKey || Math.abs(e.deltaY) <= Math.abs(e.deltaX) || Math.abs(e.deltaY) < 4) return;
    const inner = e.target.closest?.("pre, table");
    if (inner && (inner.scrollHeight > inner.clientHeight + 1 || inner.scrollWidth > inner.clientWidth + 1)) return;
    e.preventDefault();
    if (e.timeStamp - wheelAt > 220) turn(e.deltaY > 0 ? 1 : -1);
    wheelAt = e.timeStamp;
  }, { passive: false });
  // Touch: tap the left or right quarter to turn. A trackpad or mouse click does not (S.ptr).
  sc.addEventListener("click", (e) => {
    if (!focusOn() || (S.ptr !== "touch" && S.ptr !== "pen") || e.target.closest("a, button, img, pre, table, input, video, audio, iframe, summary, label, details")) return;
    const r = sc.getBoundingClientRect(), f = (e.clientX - r.left) / r.width;
    if (f < 0.25) turn(-1); else if (f > 0.75) turn(1);
  });
  $("#art-content").addEventListener("load", () => requestAnimationFrame(() => layoutPaging()), true);
  new ResizeObserver(() => layoutPaging()).observe(sc);
  $("#btn-focus").onclick = () => setFocus(!S.focus);
  $("#pg-prev").onclick = () => turn(-1);
  $("#pg-next").onclick = () => turn(1);
}

/* ---------- navigation ---------- */
function setView(v) { $("#app").dataset.view = v; requestAnimationFrame(() => layoutPaging()); }
function setMenu(open) { $("#app").dataset.menu = open ? "open" : ""; }
// View state lives in history.state, not the URL hash: a reload used to leave a stale "#e…" that made Back a no-op.
history.replaceState(null, "", location.pathname);
addEventListener("popstate", () => setView(history.state?.view === "reader" ? "reader" : "list"));

async function setScope(sc) {
  S.scope = sc; S.q = ""; $("#q").value = "";
  $("#app").dataset.tview = "";
  syncFocus();
  setMenu(false); setView("list");
  renderNav();
  await loadEntries();
  $("#entries").scrollTop = 0;
}

async function refreshAll() {
  const b = $("#btn-refresh");
  b.disabled = true; toast("Refreshing…");
  try {
    const r = await api("/refresh", { method: "POST" });
    await loadFeeds();
    await loadEntries();
    toast(r.busy ? "Already checking…" : r.new ? `${r.new} new` : "Nothing new");
  } catch (e) { toast("Refresh failed: " + e.message); }
  b.disabled = false;
}

/* ---------- dialogs ---------- */
const QUIET_DAYS = 365;
function quietNote(f) {  // a feed that fetches fine but hasn't posted in a year or more
  const t = Date.parse(f.last_post || "");
  if (!t || Date.now() - t < QUIET_DAYS * 864e5) return "";
  return `<br><span class="e q">Quiet since ${new Date(t).toLocaleDateString(undefined, { month: "short", year: "numeric" })}</span>`;
}
function renderManage() {
  const groups = {};
  S.feeds.forEach((f) => (groups[f.folder] ||= []).push(f));
  const names = Object.keys(groups).sort((x, y) => x.localeCompare(y));
  let h = "";
  for (const name of names) {
    const count = groups[name].length;
    if (S.edit?.type === "folder" && S.edit.name === name) {
      h += `<li class="m-head editing"><input class="e-name" type="text" value="${esc(name)}" autocapitalize="words"><button data-cancel>Cancel</button><button class="primary" data-saveren="${esc(name)}">Save</button></li>`;
    } else {
      h += `<li class="m-head"><span class="t">${esc(name || "Unfiled")} <span class="n">${count}</span></span>`
        + (name ? `<button data-ren="${esc(name)}">Rename</button><button class="danger" data-delf="${esc(name)}">Delete</button>` : "") + `</li>`;
    }
    for (const f of groups[name]) {
      if (S.edit?.type === "feed" && S.edit.id === f.id) {
        h += `<li class="m-edit" data-id="${f.id}"><input class="e-title" type="text" value="${esc(f.title)}" placeholder="${esc(f.url)}">
          <select class="e-folder">${folderOptions(f.folder)}</select><input class="e-new" type="text" placeholder="New folder name" autocapitalize="words" hidden>
          <div class="row"><button data-cancel>Cancel</button><button class="primary" data-savefeed="${f.id}">Save</button></div></li>`;
      } else {
        h += `<li class="m-feed" data-id="${f.id}"><span class="t">${esc(f.title || f.url)}${f.error ? `<br><span class="e">${esc(f.error)}</span>` : quietNote(f)}</span><button data-edit="${f.id}">Edit</button><button class="danger" data-del="${f.id}">Remove</button></li>`;
      }
    }
  }
  $("#manage-list").innerHTML = h || "<li>No feeds yet.</li>";
}

/* ---------- edit one feed ---------- */
function currentFeed() { return S.scope.type === "feed" ? S.feeds.find((f) => f.id === S.scope.id) : null; }
$("#btn-editfeed").onclick = () => {
  const f = currentFeed(); if (!f) return;
  $("#feed-title").value = f.title || ""; $("#feed-title").placeholder = f.url;
  $("#feed-folder").innerHTML = folderOptions(f.folder);
  $("#feed-folder-new").hidden = true; $("#feed-folder-new").value = "";
  const u = $("#feed-url"); u.classList.toggle("bad", !!f.error);
  u.textContent = f.error ? `${f.url}\nLast fetch failed: ${f.error}` : f.url; u.style.whiteSpace = "pre-line";
  $("#feed-msg").textContent = "";
  $("#dlg-feed").showModal();
};
$("#feed-folder").addEventListener("change", (e) => { const n = $("#feed-folder-new"); n.hidden = e.target.value !== "__new__"; if (!n.hidden) n.focus(); });
$("#feed-cancel").onclick = () => $("#dlg-feed").close();
$("#form-feed").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = currentFeed(); if (!f) return;
  const folder = folderPick($("#feed-folder"), $("#feed-folder-new"));
  if ($("#feed-folder").value === "__new__" && !folder) { $("#feed-msg").textContent = "Name the new folder"; return; }
  const b = $("#feed-save"); b.disabled = true;
  try {
    await api("/feeds/" + f.id, { method: "PATCH", body: { title: $("#feed-title").value, folder } });
    await loadFeeds(); renderList(); $("#dlg-feed").close(); toast("Feed updated");
  } catch (e) { $("#feed-msg").textContent = e.message; }
  b.disabled = false;
});
/* Long-running per-feed jobs: start, poll until done, report. */
async function runFeedJob(path, startBody, busy, done) {
  const f = currentFeed(); if (!f) return;
  const btns = document.querySelectorAll("#feed-backfill"), msg = $("#feed-msg");
  btns.forEach((b) => (b.disabled = true)); msg.textContent = busy;
  try {
    let st = await api(`/feeds/${f.id}/${path}`, { method: "POST", body: startBody });
    while (st.state === "running") {
      msg.textContent = st.new ? `${busy.replace("…", "")}: ${st.new} so far…` : busy;
      await new Promise((r) => setTimeout(r, 1500));
      st = await api(`/feeds/${f.id}/${path}`);
    }
    if (st.state === "error") throw new Error(st.message);
    msg.textContent = st.new ? done(st) : st.message;
    if (st.new) { await loadFeeds(); await loadEntries(); }
  } catch (e) { msg.textContent = e.message; }
  btns.forEach((b) => (b.disabled = false));
}
$("#feed-backfill").onclick = () => runFeedJob("backfill", { limit: 50 }, "Looking for older articles…",
  (st) => `Added ${st.new} older articles (${st.method}), marked read.`);
$("#feed-remove").onclick = async () => {
  const f = currentFeed(); if (!f) return;
  if (!confirm(`Remove "${f.title || f.url}" and its articles? Anything you saved to Obsidian stays.`)) return;
  try {
    await api("/feeds/" + f.id, { method: "DELETE" });
    $("#dlg-feed").close(); S.scope = { type: "all" }; await loadFeeds(); await loadEntries(); toast("Feed removed");
  } catch (e) { $("#feed-msg").textContent = e.message; }
};

/* ---------- settings ---------- */
function fillSettings(st) {
  $("#set-inbox").value = st.inbox_dir;
  $("#set-git").checked = st.git_push;
  $("#set-wiki").checked = st.wikilink_author;
  $("#set-refresh").value = st.refresh_minutes;
  $("#set-org-row").hidden = !st.ai_on;
  $("#set-org").value = st.organize_model; $("#set-org").placeholder = st.default_model;
  const n = $("#set-inbox-note");
  n.classList.toggle("bad", !st.inbox_writable);
  n.textContent = !st.inbox_exists ? "Doesn't exist yet. Forage will create it when you save settings." : st.inbox_writable ? "Saved clips are written here." : "Forage can't write to this folder.";
  $("#set-git-note").textContent = st.git_root
    ? `Git checkout: ${st.git_root}.${st.sync_script ? " Pushing through the server's sync script." : " Pushes with plain git."}`
    : "That folder isn't inside a git checkout, so there's nothing to commit to.";
  if (st.publish_error) $("#set-git-note").textContent += ` The last push failed (${st.publish_error}); the clip is saved but not yet pushed.`;
}
$("#btn-settings").onclick = async () => {
  setMenu(false); $("#set-msg").textContent = "";
  try { fillSettings(await api("/settings")); $("#dlg-settings").showModal(); }
  catch (e) { toast("Couldn't load settings: " + e.message); }
};
$("#set-cancel").onclick = () => $("#dlg-settings").close();
$("#form-settings").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const b = $("#set-save"); b.disabled = true; $("#set-msg").textContent = "";
  try {
    const st = await api("/settings", { method: "PUT", body: {
      inbox_dir: $("#set-inbox").value, git_push: $("#set-git").checked,
      wikilink_author: $("#set-wiki").checked, refresh_minutes: +$("#set-refresh").value,
      ...(!$("#set-org-row").hidden && { organize_model: $("#set-org").value }) } });
    fillSettings(st); $("#dlg-settings").close(); toast("Settings saved");
  } catch (e) { $("#set-msg").textContent = e.message; }
  b.disabled = false;
});

/* ---------- wiring ---------- */
$("#nav").addEventListener("click", (e) => {
  const c = e.target.closest("[data-fold]");
  if (c) { const n = c.dataset.fold; collapsed.has(n) ? collapsed.delete(n) : collapsed.add(n); saveCollapsed(); renderNav(); return; }
  const b = e.target.closest("[data-scope]");
  if (b) setScope(JSON.parse(b.dataset.scope));
});
$("#entries").addEventListener("click", (e) => { const li = e.target.closest("li[data-id]"); if (li) openEntry(+li.dataset.id); });
$("#entries").addEventListener("keydown", (e) => {
  if (e.key !== "Enter" && e.key !== " ") return;
  const li = e.target.closest("li[data-id]"); if (li) { e.preventDefault(); openEntry(+li.dataset.id); }
});
$("#entries").addEventListener("scroll", (e) => {
  const el = e.target;
  if (!S.done && el.scrollTop + el.clientHeight > el.scrollHeight - 400) loadEntries(true);
});
$("#btn-menu").onclick = () => setMenu(true);
$("#scrim").onclick = () => setMenu(false);
function backToCards() { $("#app").dataset.tview = ""; syncFocus(); $(`#entries li[data-id="${S.cur}"]`)?.scrollIntoView({ block: "nearest" }); }
$("#btn-back").onclick = () => {
  if (!S.narrow && S.scope.type === "today") backToCards();
  else if (history.state?.view === "reader") history.back(); else setView("list");
};
$("#btn-refresh").onclick = refreshAll;
document.querySelectorAll(".save-btn").forEach((b) => (b.onclick = save));
$("#btn-star").onclick = toggleStar;
$("#btn-share").onclick = share;
$("#btn-summary").onclick = () => summarizeNow(false);
$("#btn-unread").onclick = toggleRead;
$("#btn-prev").onclick = () => step(-1);
$("#btn-next").onclick = () => step(1);
$("#btn-filter").onclick = () => {
  S.unreadOnly = !S.unreadOnly;
  try { localStorage.setItem("forage.unreadOnly", S.unreadOnly ? "1" : "0"); } catch {}
  loadEntries();
};
$("#btn-markall").onclick = async () => {
  const sc = S.scope;
  if (sc.type === "starred") return toast("Mark all read doesn't apply to Starred");
  if (S.q) return toast("Clear the search first: this marks the whole view, not just the matches");
  if (!confirm(`Mark everything in "${scopeTitle()}" as read?`)) return;
  const body = {};
  if (sc.type === "feed") body.feed = sc.id;
  if (sc.type === "folder") body.folder = sc.name;
  if (sc.type === "today") body.since_hours = TODAY_HOURS;
  const newest = S.entries[0]?.published;
  if (newest) body.older_than = newest;
  try {
    const r = await api("/mark-read", { method: "POST", body });
    toast(`${r.marked} marked read`);
    await loadFeeds(); await loadEntries();
  } catch (err) { toast("Couldn't mark read: " + err.message); }
};
let qTimer;
$("#q").addEventListener("input", (e) => { clearTimeout(qTimer); qTimer = setTimeout(() => { S.q = e.target.value.trim(); loadEntries(); }, 250); });

function openFind() { setMenu(false); $("#add-folder").innerHTML = folderOptions("") + (S.ai ? `<option value="__ai__">Let AI pick</option>` : ""); $("#add-folder-new").hidden = true; $("#add-folder-new").value = ""; $("#add-msg").textContent = ""; $("#results").innerHTML = ""; $("#dlg-add").showModal(); $("#add-url").focus(); }
$("#btn-add").onclick = openFind;
$("#manage-find").onclick = () => { $("#dlg-manage").close(); openFind(); };
$("#add-cancel").onclick = () => $("#dlg-add").close();
$("#add-folder").addEventListener("change", (e) => { const n = $("#add-folder-new"); n.hidden = e.target.value !== "__new__"; if (!n.hidden) n.focus(); });
let found = [];
function resultHtml(r, i) {
  const d = new Date(r.latest);
  const bits = [r.source, r.items ? `${r.items} posts` : "", isNaN(d) ? "" : "latest " + ago(d.toISOString()), r.subscribers ? r.subscribers.toLocaleString() + " readers" : ""].filter(Boolean);
  return `<li><div class="info"><div class="ttl">${esc(r.title)}</div><div class="sub">${esc(bits.join(" · "))}</div>${r.description ? `<div class="desc">${esc(r.description)}</div>` : ""}</div>
    <button type="button" data-i="${i}" ${r.subscribed ? "disabled" : ""}>${r.subscribed ? "Added ✓" : "Add"}</button></li>`;
}
$("#form-add").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const go = $("#add-go"); go.disabled = true; $("#add-msg").textContent = "Searching…"; $("#results").innerHTML = "";
  try {
    found = await api("/find?q=" + encodeURIComponent($("#add-url").value));
    $("#add-msg").textContent = found.length ? "" : "Nothing found. Try the site's address, or different words.";
    $("#results").innerHTML = found.map(resultHtml).join("");
  } catch (e) { $("#add-msg").textContent = e.message; }
  go.disabled = false;
});
$("#results").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-i]"); if (!b) return;
  const r = found[+b.dataset.i];
  b.disabled = true; b.textContent = "Adding…";
  try {
    const pick = folderPick($("#add-folder"), $("#add-folder-new"));
    const added = await api("/feeds", { method: "POST", body: pick === "__ai__" ? { url: r.url, suggest: true } : { url: r.url, folder: pick } });
    r.subscribed = true; b.textContent = pick === "__ai__" && added.folder ? `Added to ${added.folder} ✓` : "Added ✓";
    await loadFeeds(); await loadEntries();
  } catch (err) { b.disabled = false; b.textContent = "Add"; $("#add-msg").textContent = err.message; }
});
$("#manage-close").onclick = () => $("#dlg-manage").close();
async function manageRefresh() { await loadFeeds(); renderManage(); loadEntries(); }
$("#manage-list").addEventListener("click", async (e) => {
  const d = e.target.dataset;
  try {
    if (d.edit) { S.edit = { type: "feed", id: +d.edit }; renderManage(); }
    else if (d.ren !== undefined) { S.edit = { type: "folder", name: d.ren }; renderManage(); }
    else if ("cancel" in d) { S.edit = null; renderManage(); }
    else if (d.savefeed) {
      const li = e.target.closest("li");
      const folder = folderPick(li.querySelector(".e-folder"), li.querySelector(".e-new"));
      if (li.querySelector(".e-folder").value === "__new__" && !folder) return toast("Name the new folder");
      await api("/feeds/" + d.savefeed, { method: "PATCH", body: { title: li.querySelector(".e-title").value, folder } });
      S.edit = null; await manageRefresh();
    } else if (d.saveren) {
      const to = e.target.closest("li").querySelector(".e-name").value.trim();
      if (!to) return toast("Folder needs a name");
      const merging = to !== d.saveren && folderNames().includes(to);
      await api("/folders/rename", { method: "POST", body: { from: d.saveren, to } });
      if (collapsed.delete(d.saveren)) saveCollapsed();
      if (S.scope.type === "folder" && S.scope.name === d.saveren) S.scope = { type: "folder", name: to };
      S.edit = null; await manageRefresh();
      if (merging) toast(`Merged into ${to}`);
    } else if (d.delf) {
      if (!confirm(`Delete folder "${d.delf}"? Its feeds move to Unfiled.`)) return;
      await api("/folders/delete", { method: "POST", body: { name: d.delf } });
      if (S.scope.type === "folder" && S.scope.name === d.delf) S.scope = { type: "all" };
      await manageRefresh();
    } else if (d.del) {
      if (!confirm("Remove this feed and its articles?")) return;
      await api("/feeds/" + d.del, { method: "DELETE" });
      if (S.scope.type === "feed" && S.scope.id === +d.del) S.scope = { type: "all" };
      await manageRefresh();
    }
  } catch (err) { toast(err.message); }
});
$("#manage-list").addEventListener("change", (e) => {
  if (e.target.classList.contains("e-folder")) {
    const input = e.target.closest("li").querySelector(".e-new");
    input.hidden = e.target.value !== "__new__";
    if (!input.hidden) input.focus();
  }
});
async function loadAi() {
  try { const st = await api("/settings"); S.ai = st.ai_on; S.undo = st.folders_undo; } catch {}
  $("#manage-organize").hidden = !S.ai; $("#manage-undo").hidden = !S.undo;
}
$("#btn-manage").onclick = () => { setMenu(false); S.edit = null; renderManage(); $("#dlg-manage").showModal(); loadAi(); };

/* ---------- organize with AI: propose, look, apply (and undo) ---------- */
let proposal = null;
async function runOrganize() {
  proposal = null;
  $("#org-list").innerHTML = ""; $("#org-apply").hidden = $("#org-again").hidden = true;
  $("#org-msg").textContent = "Reading your feeds and sorting them. This can take half a minute.";
  if (!$("#dlg-organize").open) $("#dlg-organize").showModal();
  try {
    proposal = await api("/folders/organize", { method: "POST" });
    $("#org-msg").textContent = `Nothing changes until you apply. Sorted by ${proposal.model}.`;
    $("#org-list").innerHTML = proposal.folders.map((f) =>
      `<li class="m-head"><span class="t">${esc(f.name || "Unfiled")} <span class="n">${f.feeds.length}</span></span></li>`
      + f.feeds.map((x) => `<li class="m-feed"><span class="t">${esc(x.title)}</span></li>`).join("")).join("");
    $("#org-apply").hidden = false;
  } catch (e) { $("#org-msg").textContent = e.message; }
  $("#org-again").hidden = false;
}
$("#manage-organize").onclick = () => { $("#dlg-manage").close(); runOrganize(); };
$("#org-again").onclick = runOrganize;
$("#org-cancel").onclick = () => { $("#dlg-organize").close(); $("#dlg-manage").showModal(); };
$("#org-apply").onclick = async () => {
  if (!proposal) return;
  try {
    const r = await api("/folders/apply", { method: "POST", body: { assign: proposal.assign } });
    S.scope = { type: "today" };
    await loadFeeds(); await loadEntries(); renderManage();
    toast(`${r.moved} feeds moved. Undo is in Manage.`);
    $("#dlg-organize").close(); $("#dlg-manage").showModal(); loadAi();
  } catch (e) { $("#org-msg").textContent = e.message; }
};
$("#manage-undo").onclick = async () => {
  try {
    const r = await api("/folders/undo", { method: "POST" });
    S.scope = { type: "all" };
    await manageRefresh(); toast(`${r.restored} feeds put back`); loadAi();
  } catch (e) { toast(e.message); }
};
$("#opml-file").addEventListener("change", async (e) => {
  const f = e.target.files[0]; if (!f) return;
  try {
    const r = await api("/opml", { method: "POST", raw: await f.text() });
    toast(`Imported ${r.added} new of ${r.total}. Fetching…`);
    setTimeout(async () => { await loadFeeds(); await loadEntries(); renderManage(); }, 6000);
    await loadFeeds(); renderManage();
  } catch (err) { toast("Import failed: " + err.message); }
  e.target.value = "";
});

addEventListener("keydown", (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey || /input|textarea/i.test(e.target.tagName) || document.querySelector("dialog[open]")) return;
  const k = e.key;
  if (k === "z") { setFocus(!S.focus); return; }
  if (focusOn()) {
    if (k === " " && e.target.tagName === "BUTTON") return;
    if (k === "ArrowRight" || k === "PageDown" || (k === " " && !e.shiftKey)) { e.preventDefault(); turn(1); return; }
    if (k === "ArrowLeft" || k === "PageUp" || (k === " " && e.shiftKey)) { e.preventDefault(); turn(-1); return; }
    if (k === "Escape") { setFocus(false); return; }
  }
  if (k === "Escape" && !S.narrow && S.scope.type === "today" && $("#app").dataset.tview === "read") backToCards();
  else if (k === "j") step(1); else if (k === "k") step(-1);
  else if (k === "s") save(); else if (k === "m") toggleRead();
  else if (k === "o" && S.art?.url) window.open(S.art.url, "_blank", "noopener");
  else if (k === "r") refreshAll(); else if (k === "u") $("#btn-filter").click();
  else if (k === "f") toggleStar(); else if (k === "/") { e.preventDefault(); $("#q").focus(); }
  else return;
});

// iOS can keep stale layout/hit-test geometry after a rotation; resetting scroll and nudging a reflow clears it.
let rotTimer;
function relayout() {
  clearTimeout(rotTimer);
  rotTimer = setTimeout(() => {
    window.scrollTo(0, 0);
    const app = $("#app"); app.style.transform = "translateZ(0)"; void app.offsetHeight; app.style.transform = "";
  }, 250);
}
addEventListener("orientationchange", relayout);
addEventListener("resize", relayout);

document.addEventListener("visibilitychange", () => { if (!document.hidden) loadFeeds().catch(() => {}); });

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});

setSaveBtn();

(async function init() {
  await loadFeeds();
  await loadEntries();
})();
