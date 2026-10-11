"use strict";
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const S = {
  feeds: [], entries: [], sel: null, cur: null, loadingMore: false, done: false,
  scope: { type: "all" },           // all | starred | folder | feed
  unreadOnly: true, q: "",
  get narrow() { return matchMedia("(max-width: 899px)").matches; },
};
try { S.unreadOnly = localStorage.getItem("forage.unreadOnly") !== "0"; } catch {}

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
  let h = item({ type: "all" }, "All items", total) + item({ type: "starred" }, "Starred", 0);
  const names = Object.keys(folders).sort((a, b) => a.localeCompare(b));
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
  $("#btn-editfeed").hidden = S.scope.type !== "feed";
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
        h += `<li class="m-feed" data-id="${f.id}"><span class="t">${esc(f.title || f.url)}${f.error ? `<br><span class="e">${esc(f.error)}</span>` : ""}</span><button data-edit="${f.id}">Edit</button><button class="danger" data-del="${f.id}">Remove</button></li>`;
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
  const btns = document.querySelectorAll("#feed-backfill, #feed-images"), msg = $("#feed-msg");
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
$("#feed-images").onclick = () => runFeedJob("images", {}, "Looking for images…",
  (st) => `Filled in ${st.new} images.`);
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
  const n = $("#set-inbox-note");
  n.classList.toggle("bad", !st.inbox_writable);
  n.textContent = !st.inbox_exists ? "Doesn't exist yet. Forage will create it when you save settings." : st.inbox_writable ? "Saved clips are written here." : "Forage can't write to this folder.";
  $("#set-git-note").textContent = st.git_root
    ? `Git checkout: ${st.git_root}.${st.sync_script ? " Pushing through the server's sync script." : " Pushes with plain git."}`
    : "That folder isn't inside a git checkout, so there's nothing to commit to.";
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
      wikilink_author: $("#set-wiki").checked, refresh_minutes: +$("#set-refresh").value } });
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
  try { localStorage.setItem("forage.unreadOnly", S.unreadOnly ? "1" : "0"); } catch {}
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

function openFind() { setMenu(false); $("#add-folder").innerHTML = folderOptions(""); $("#add-folder-new").hidden = true; $("#add-folder-new").value = ""; $("#add-msg").textContent = ""; $("#results").innerHTML = ""; $("#dlg-add").showModal(); $("#add-url").focus(); }
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
    await api("/feeds", { method: "POST", body: { url: r.url, folder: folderPick($("#add-folder"), $("#add-folder-new")) } });
    r.subscribed = true; b.textContent = "Added ✓";
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
$("#btn-manage").onclick = () => { setMenu(false); S.edit = null; renderManage(); $("#dlg-manage").showModal(); };
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

setSaveBtn(false);

(async function init() {
  await loadFeeds();
  await loadEntries();
})();
