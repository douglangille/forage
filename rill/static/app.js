"use strict";
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const S = {
  feeds: [], entries: [], sel: null, cur: null, loadingMore: false, done: false,
  scope: { type: "all" },           // all | starred | folder | feed
  unreadOnly: true, q: "",
  get narrow() { return matchMedia("(max-width: 899px)").matches; },
};
try { S.unreadOnly = localStorage.getItem("rill.unreadOnly") !== "0"; } catch {}

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

function renderNav() {
  const total = S.feeds.reduce((a, f) => a + f.unread, 0);
  const folders = {};
  S.feeds.forEach((f) => (folders[f.folder] ||= []).push(f));
  const item = (sc, label, n, cls = "", err = false) =>
    `<button class="nav-item ${cls} ${err ? "err" : ""} ${scopeKey(S.scope) === scopeKey(sc) ? "sel" : ""}" data-scope='${esc(JSON.stringify(sc))}'><span class="t">${esc(label)}</span>${n ? `<span class="n">${n}</span>` : ""}</button>`;
  let h = item({ type: "all" }, "All items", total) + item({ type: "starred" }, "Starred", 0);
  const names = Object.keys(folders).sort((a, b) => a.localeCompare(b));
  for (const name of names) {
    if (name) {
      const n = folders[name].reduce((a, f) => a + f.unread, 0);
      h += `<div class="nav-folder">${esc(name)}</div>` + item({ type: "folder", name }, "All in " + name, n);
    } else if (names.length > 1) h += `<div class="nav-folder">Unfiled</div>`;
    h += folders[name].map((f) => item({ type: "feed", id: f.id }, f.title || f.url, f.unread, "nav-feed", !!f.error)).join("");
  }
  $("#nav").innerHTML = h;
  $("#folders").innerHTML = names.filter(Boolean).map((n) => `<option value="${esc(n)}">`).join("");
  document.title = total ? `(${total}) Rill` : "Rill";
  try { navigator.setAppBadge?.(total); } catch {}
}

async function loadFeeds() { S.feeds = await api("/feeds"); renderNav(); }

function scopeTitle() {
  const sc = S.scope;
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
  if (sc.type === "starred") p.set("starred", "true");
  else if (S.unreadOnly) p.set("unread", "true");
  if (S.q) p.set("q", S.q);
  if (before) p.set("before", before);
  return "/entries?" + p;
}

async function loadEntries(append = false) {
  if (S.loadingMore) return;
  S.loadingMore = true;
  try {
    const last = S.entries[S.entries.length - 1];
    const rows = await api(query(append && last ? `${last.published}|${last.id}` : ""));
    S.entries = append ? S.entries.concat(rows) : rows;
    S.done = rows.length < 50;
    renderList(append);
  } catch (e) { toast("Couldn't load: " + e.message); }
  S.loadingMore = false;
}

function renderList(append) {
  $("#list-title").textContent = scopeTitle();
  $("#btn-filter").classList.toggle("on", S.unreadOnly);
  $("#btn-filter").hidden = S.scope.type === "starred";
  const ul = $("#entries");
  const keepTop = ul.scrollTop;
  ul.innerHTML = S.entries.map(rowHtml).join("");
  if (append) ul.scrollTop = keepTop;
  $("#list-empty").hidden = S.entries.length > 0;
  $("#list-empty").textContent = S.feeds.length ? "Nothing here. Go outside." : "No feeds yet. Add one, or import an OPML file from Manage.";
}

function rowHtml(e) {
  return `<li data-id="${e.id}" class="${e.is_read ? "" : "unread"} ${S.cur === e.id ? "sel" : ""}">
    <span class="dot"></span>
    <div class="txt"><div class="ttl">${esc(e.title)}</div>
      <div class="sub ${e.saved_path ? "saved" : ""}">${esc(e.feed_title)} · ${ago(e.published)}</div>
      ${e.summary ? `<div class="snip">${esc(e.summary.slice(0, 160))}</div>` : ""}</div>
    ${e.image ? `<img class="thumb" loading="lazy" src="${esc(e.image)}" alt="" onerror="this.remove()">` : ""}</li>`;
}

function patchRow(e) {
  const li = $(`#entries li[data-id="${e.id}"]`);
  if (li) li.outerHTML = rowHtml(e);
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
  $("#art-scroll").scrollTop = 0;
  if (S.narrow && fromList) { history.pushState({ view: "reader" }, ""); setView("reader"); }
  try {
    const a = await api("/entries/" + id);
    if (S.cur !== id) return;
    S.art = a;
    $("#art-title").textContent = a.title;
    $("#art-meta").textContent = [a.feed_title, a.author, new Date(a.published).toLocaleDateString(undefined, { dateStyle: "medium" })].filter(Boolean).join(" · ");
    $("#art-content").innerHTML = a.html;
    $("#art-content").querySelectorAll("a").forEach((l) => (l.target = "_blank"));
    $("#btn-open").href = a.url;
    setSaveBtn(!!a.saved_path);
    $("#btn-star").textContent = a.starred ? "★" : "☆";
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

const ICON_SAVE = '<svg viewBox="0 0 24 24"><rect x="4" y="4" width="16" height="16" rx="3.5"/></svg>';
const ICON_DONE = '<svg viewBox="0 0 24 24"><rect x="4" y="4" width="16" height="16" rx="3.5"/><path d="m8.5 12.3 2.4 2.4 4.6-5"/></svg>';
function setSaveBtn(saved, busy = false) {
  document.querySelectorAll(".save-btn").forEach((b) => {
    b.disabled = busy;
    b.classList.toggle("done", saved);
    b.innerHTML = saved ? ICON_DONE : ICON_SAVE;
    b.title = saved ? "Saved to inbox" : "Save to Obsidian inbox (s)";
    b.setAttribute("aria-pressed", saved);
  });
}

async function save() {
  if (!S.cur) return;
  if ($("#btn-save").classList.contains("done")) return;
  setSaveBtn(false, true);
  try {
    const r = await api(`/entries/${S.cur}/save`, { method: "POST" });
    setSaveBtn(true);
    const e = S.entries.find((x) => x.id === S.cur);
    if (e) { e.saved_path = r.path; patchRow(e); }
  } catch (err) { setSaveBtn(false); toast("Save failed: " + err.message); }
}

async function toggleStar() {
  if (!S.art) return;
  const v = !S.art.starred;
  await api(`/entries/${S.cur}/state`, { method: "POST", body: { starred: v } });
  S.art.starred = v ? 1 : 0;
  const e = S.entries.find((x) => x.id === S.cur);
  if (e) { e.starred = S.art.starred; }
  $("#btn-star").textContent = v ? "★" : "☆";
  $("#btn-star").classList.toggle("on", v);
}

function setReadBtn(isRead) {
  const b = $("#btn-unread");
  b.textContent = isRead ? "○" : "●";
  b.classList.toggle("on", !isRead);
  b.title = isRead ? "Mark unread (m)" : "Mark read (m)";
  b.setAttribute("aria-pressed", !isRead);
}

async function toggleRead() {
  const e = S.entries.find((x) => x.id === S.cur);
  if (!e) return;
  const nowRead = !e.is_read;
  await api(`/entries/${S.cur}/state`, { method: "POST", body: { read: nowRead } });
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

/* ---------- navigation ---------- */
function setView(v) { $("#app").dataset.view = v; }
function setMenu(open) { $("#app").dataset.menu = open ? "open" : ""; }
// View state lives in history.state, not the URL hash: a reload used to leave a stale "#e…" that made Back a no-op.
history.replaceState(null, "", location.pathname);
addEventListener("popstate", () => setView(history.state?.view === "reader" ? "reader" : "list"));

async function setScope(sc) {
  S.scope = sc; S.q = ""; $("#q").value = "";
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
    toast(r.new ? `${r.new} new` : "Nothing new");
  } catch (e) { toast("Refresh failed: " + e.message); }
  b.disabled = false;
}

/* ---------- dialogs ---------- */
function renderManage() {
  $("#manage-list").innerHTML = S.feeds.map((f) => `<li data-id="${f.id}"><span class="t">${esc(f.title || f.url)}${f.error ? `<br><span class="e">${esc(f.error)}</span>` : ""}</span><button data-del="${f.id}">Remove</button></li>`).join("") || "<li>No feeds yet.</li>";
}

/* ---------- wiring ---------- */
$("#nav").addEventListener("click", (e) => { const b = e.target.closest("[data-scope]"); if (b) setScope(JSON.parse(b.dataset.scope)); });
$("#entries").addEventListener("click", (e) => { const li = e.target.closest("li[data-id]"); if (li) openEntry(+li.dataset.id); });
$("#entries").addEventListener("scroll", (e) => {
  const el = e.target;
  if (!S.done && el.scrollTop + el.clientHeight > el.scrollHeight - 400) loadEntries(true);
});
$("#btn-menu").onclick = () => setMenu(true);
$("#scrim").onclick = () => setMenu(false);
$("#btn-back").onclick = () => { if (history.state?.view === "reader") history.back(); else setView("list"); };
$("#btn-refresh").onclick = refreshAll;
document.querySelectorAll(".save-btn").forEach((b) => (b.onclick = save));
$("#btn-star").onclick = toggleStar;
$("#btn-unread").onclick = toggleRead;
$("#btn-prev").onclick = () => step(-1);
$("#btn-next").onclick = () => step(1);
$("#btn-filter").onclick = () => {
  S.unreadOnly = !S.unreadOnly;
  try { localStorage.setItem("rill.unreadOnly", S.unreadOnly ? "1" : "0"); } catch {}
  loadEntries();
};
$("#btn-markall").onclick = async () => {
  const sc = S.scope;
  if (!confirm(`Mark everything in "${scopeTitle()}" as read?`)) return;
  const body = {};
  if (sc.type === "feed") body.feed = sc.id;
  if (sc.type === "folder") body.folder = sc.name;
  const newest = S.entries[0]?.published;
  if (newest) body.older_than = newest;
  const r = await api("/mark-read", { method: "POST", body });
  toast(`${r.marked} marked read`);
  await loadFeeds(); await loadEntries();
};
let qTimer;
$("#q").addEventListener("input", (e) => { clearTimeout(qTimer); qTimer = setTimeout(() => { S.q = e.target.value.trim(); loadEntries(); }, 250); });

$("#btn-add").onclick = () => { setMenu(false); $("#add-msg").textContent = ""; $("#add-url").value = ""; $("#dlg-add").showModal(); $("#add-url").focus(); };
$("#add-cancel").onclick = () => $("#dlg-add").close();
$("#form-add").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const go = $("#add-go"); go.disabled = true; $("#add-msg").textContent = "Looking for a feed…";
  try {
    const f = await api("/feeds", { method: "POST", body: { url: $("#add-url").value, folder: $("#add-folder").value } });
    $("#dlg-add").close();
    toast(f.added ? "Added " + (f.title || f.url) : "Already subscribed");
    await loadFeeds(); await loadEntries();
  } catch (e) { $("#add-msg").textContent = e.message; }
  go.disabled = false;
});
$("#btn-manage").onclick = () => { setMenu(false); renderManage(); $("#dlg-manage").showModal(); };
$("#manage-close").onclick = () => $("#dlg-manage").close();
$("#manage-list").addEventListener("click", async (e) => {
  const id = e.target.dataset.del;
  if (!id || !confirm("Remove this feed and its articles?")) return;
  await api("/feeds/" + id, { method: "DELETE" });
  if (S.scope.type === "feed" && S.scope.id === +id) S.scope = { type: "all" };
  await loadFeeds(); renderManage(); loadEntries();
});
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
  if (k === "j") step(1); else if (k === "k") step(-1);
  else if (k === "s") save(); else if (k === "m") toggleRead();
  else if (k === "o" && S.art?.url) window.open(S.art.url, "_blank", "noopener");
  else if (k === "r") refreshAll(); else if (k === "u") $("#btn-filter").click();
  else if (k === "f") toggleStar(); else if (k === "/") { e.preventDefault(); $("#q").focus(); }
  else return;
});

document.addEventListener("visibilitychange", () => { if (!document.hidden) loadFeeds().catch(() => {}); });

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});

setSaveBtn(false);

(async function init() {
  await loadFeeds();
  await loadEntries();
})();
