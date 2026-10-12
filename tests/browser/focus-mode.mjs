// Browser smoke test for focus mode (paged columns), in Chromium and WebKit via Playwright. Optional, not part of pytest.
//
//   FORAGE_DATA_DIR=/tmp/fdata FORAGE_PORT=8799 .venv/bin/python -m forage &     # a throwaway instance
//   curl -X POST localhost:8799/api/feeds -H 'content-type: application/json' -d '{"url":"https://daringfireball.net/feeds/main"}'
//   mkdir /tmp/pw && cd /tmp/pw && npm i playwright && npx playwright install chromium webkit
//   cp <repo>/tests/browser/focus-mode.mjs . && node focus-mode.mjs ./screens     # FORAGE_URL overrides the address
//
// Needs at least three long-form articles (over ~6000 characters of HTML) and two of them adjacent in the list. Exits 1 if it can't find them.
import { chromium, webkit, devices } from "playwright";
const URL = process.env.FORAGE_URL || "http://127.0.0.1:8799/";
const results = [];
let ENG = "";
const check = (name, ok, detail = "") => { results.push({ eng: ENG, name, ok: !!ok, detail }); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Pick long articles through the API.
const list = await (await fetch(URL + "api/entries?limit=40")).json();
const lens = new Map();
for (const e of list) { const a = await (await fetch(URL + "api/entries/" + e.id)).json(); lens.set(e.id, (a.html || "").length); }
const long = [...lens.entries()].filter(([, n]) => n > 6000).sort((a, b) => b[1] - a[1]).map(([id]) => id).slice(0, 4);
if (long.length < 3) { console.log("not enough long entries", long.length); process.exit(1); }
let pair = null;
for (let i = 1; i < list.length; i++) if (lens.get(list[i].id) > 6000 && lens.get(list[i - 1].id) > 6000) { pair = list[i].id; break; }
console.log("long:", long, "pair:", pair);

const HELPERS = () => {
  window.__snip = () => {
    const sc = document.querySelector("#art-scroll"), r = sc.getBoundingClientRect(), cs = getComputedStyle(sc);
    const x = r.left + parseFloat(cs.paddingLeft) + 2, y = r.top + parseFloat(cs.paddingTop) + 8;
    const cr = document.caretRangeFromPoint(x, y);
    if (!cr || cr.startContainer.nodeType !== 3) return null;
    return cr.startContainer.data.slice(cr.startOffset, cr.startOffset + 40);
  };
  window.__pos = (snip) => {
    const w = document.createTreeWalker(document.querySelector(".art-body"), NodeFilter.SHOW_TEXT);
    const sc = document.querySelector("#art-scroll"), sr = sc.getBoundingClientRect(), cs = getComputedStyle(sc);
    while (w.nextNode()) {
      const i = w.currentNode.data.indexOf(snip);
      if (i < 0) continue;
      const rg = document.createRange(); rg.setStart(w.currentNode, i); rg.setEnd(w.currentNode, i + 1);
      const r = rg.getClientRects()[0]; if (!r) continue;
      return { left: r.left - sr.left - parseFloat(cs.paddingLeft), top: r.top - sr.top - parseFloat(cs.paddingTop), w: sr.width, h: sr.height };
    }
    return null;
  };
  window.__pg = () => document.querySelector("#pg-n").textContent;
  window.__stride = () => { const b = document.querySelector(".art-body"); return b.getBoundingClientRect().width + (parseFloat(getComputedStyle(b).columnGap) || 0); };
  window.__lastVisible = () => {
    const sc = document.querySelector("#art-scroll"), sr = sc.getBoundingClientRect(); const rg = document.createRange(); rg.selectNodeContents(document.querySelector(".art-body"));
    return [...rg.getClientRects()].some((r) => r.width > 0 && r.left >= sr.left && r.left < sr.right);
  };
};

async function open(pg, id) {
  await pg.evaluate((id) => openEntry(id, { fromList: false }), id);
  await pg.waitForFunction(() => !document.querySelector("#art-content").textContent.startsWith("Loading"));
  await pg.evaluate(() => Promise.all([...document.querySelectorAll("#art-content img")].map((i) => i.complete ? 0 : new Promise((r) => { i.onload = i.onerror = r; setTimeout(r, 4000); }))));
  await sleep(700);
}
const pgN = async (pg) => { const t = await pg.evaluate(() => __pg()); const [a, b] = t.split("/").map((x) => parseInt(x)); return { cur: a, n: b }; };
async function fresh(browser, opts = {}, init) {
  const ctx = await browser.newContext({ viewport: { width: 1180, height: 820 }, ...opts });
  await ctx.addInitScript(() => { try { localStorage.setItem("forage.unreadOnly", "0"); } catch {} });
  if (init) await ctx.addInitScript(init);
  const pg = await ctx.newPage();
  pg.errs = []; pg.on("pageerror", (e) => pg.errs.push(e.message));
  await pg.goto(URL); await pg.waitForSelector("#entries li");
  await pg.evaluate(HELPERS);
  return { ctx, pg };
}

async function run(name, eng) {
  ENG = name;
  const browser = await eng.launch();
  // ---- desktop group
  {
    const { ctx, pg } = await fresh(browser);
    await open(pg, long[0]);
    check("vertical article loads, no errors", pg.errs.length === 0, pg.errs.join("|"));
    await pg.keyboard.press("z"); await sleep(700);
    let st = await pg.evaluate(() => ({ f: document.querySelector("#app").dataset.focus, cols: getComputedStyle(document.querySelector(".art-body")).columnCount, side: getComputedStyle(document.querySelector("#side")).display, aria: document.querySelector("#btn-focus").getAttribute("aria-pressed") }));
    check("z enters focus, panes hidden, aria-pressed", st.f === "1" && st.side === "none" && st.aria === "true", JSON.stringify(st));
    check("two columns at 1180px", st.cols === "2", "cols=" + st.cols);
    let p = await pgN(pg);
    check("pager shows 1 / N with N>=2", p.cur === 1 && p.n >= 2, JSON.stringify(p));
    const N = p.n;

    await pg.keyboard.press("ArrowRight"); await sleep(800);
    p = await pgN(pg);
    let al = await pg.evaluate(() => { const sl = document.querySelector("#art-scroll").scrollLeft, s = __stride(); return { off: Math.abs(sl - s * Math.round(sl / s)), page: Math.round(sl / s) }; });
    check("→ goes to page 2, aligned", p.cur === 2 && al.off < 2 && al.page === 1, JSON.stringify({ p, al }));
    await pg.keyboard.press("ArrowLeft"); await sleep(800);
    check("← back to page 1", (await pgN(pg)).cur === 1);

    // quick presses
    for (let i = 0; i < Math.min(3, N - 1); i++) await pg.keyboard.press("ArrowRight");
    await sleep(1200);
    p = await pgN(pg);
    check("rapid → presses all counted", p.cur === Math.min(4, N), JSON.stringify({ p, N }));
    await pg.keyboard.press("Space"); await sleep(700);
    const afterSpace = await pgN(pg);
    check("Space advances (or stays at the end)", afterSpace.cur === Math.min(p.cur + 1, N), JSON.stringify({ p, afterSpace }));
    await pg.keyboard.press("Shift+Space"); await sleep(700);
    check("Shift+Space goes back", (await pgN(pg)).cur === Math.max(1, afterSpace.cur - 1));
    await pg.keyboard.press("PageUp"); await sleep(600);
    await pg.keyboard.press("PageDown"); await sleep(600);

    // anchor survives resize / font size
    await pg.evaluate(() => goPage(1, false)); await sleep(500);
    let snip = await pg.evaluate(() => __snip());
    const near = (pos) => pos && pos.left >= -2 && pos.left < pos.w - 20 && pos.top >= -2 && pos.top < pos.h;
    await pg.setViewportSize({ width: 1000, height: 820 }); await sleep(900);
    let pos = await pg.evaluate((s) => __pos(s), snip);
    check("resize 1180→1000 keeps place (on current page)", near(pos), JSON.stringify({ snip, pos }));
    await pg.setViewportSize({ width: 1500, height: 900 }); await sleep(900);
    pos = await pg.evaluate((s) => __pos(s), snip);
    check("resize →1500 keeps place", near(pos), JSON.stringify(pos));
    const cols1500 = await pg.evaluate(() => getComputedStyle(document.querySelector(".art-body")).columnCount);
    check("1500px gives 2-3 columns", ["2", "3"].includes(cols1500), "cols=" + cols1500);
    await pg.setViewportSize({ width: 1180, height: 820 }); await sleep(900);
    await pg.evaluate(() => goPage(1, false)); await sleep(500);
    snip = await pg.evaluate(() => __snip());
    await pg.keyboard.press("+"); await pg.keyboard.press("+"); await pg.keyboard.press("+"); await sleep(900);
    pos = await pg.evaluate((s) => __pos(s), snip);
    check("A+ x3 keeps place", near(pos), JSON.stringify(pos));
    await pg.keyboard.press("-"); await pg.keyboard.press("-"); await pg.keyboard.press("-"); await sleep(900);
    pos = await pg.evaluate((s) => __pos(s), snip);
    check("A- x3 keeps place", near(pos), JSON.stringify(pos));

    // native swipe snaps
    await pg.evaluate(() => { document.querySelector("#art-scroll").scrollLeft = __stride() * 0.62; }); await sleep(1100);
    al = await pg.evaluate(() => { const sl = document.querySelector("#art-scroll").scrollLeft, s = __stride(); return { off: Math.abs(sl - s * Math.round(sl / s)), page: Math.round(sl / s) }; });
    check("free scroll snaps to nearest page", al.off < 2 && al.page === 1, JSON.stringify(al));

    // last page: reachable, not blank, edge pause
    await pg.evaluate(() => goPage(Infinity, false)); await sleep(600);
    p = await pgN(pg);
    const lv = await pg.evaluate(() => __lastVisible());
    al = await pg.evaluate(() => { const sc = document.querySelector("#art-scroll"), s = __stride(); return Math.abs(sc.scrollLeft - s * Math.round(sc.scrollLeft / s)); });
    check("last page: N/N, aligned, has content", p.cur === p.n && lv && al < 2, JSON.stringify({ p, lv, al }));
    const idx = await pg.evaluate(() => S.entries.findIndex((x) => x.id === S.cur));
    const curBefore = await pg.evaluate(() => S.cur);
    await pg.evaluate(() => document.querySelector("#toast").textContent = "");
    await pg.keyboard.press("ArrowRight"); await sleep(400);
    let toast = await pg.evaluate(() => document.querySelector("#toast").textContent);
    check("first → at end only toasts", toast.startsWith("End. Again for next") && (await pg.evaluate(() => S.cur)) === curBefore, toast);
    await pg.keyboard.press("ArrowRight"); await sleep(1500);
    const curAfter = await pg.evaluate(() => S.cur);
    check("second → opens next article at page 1", curAfter !== curBefore && (await pgN(pg)).cur === 1, `${curBefore}→${curAfter} ${JSON.stringify(await pgN(pg))}`);

    // go back past start lands on previous article's last page
    await open(pg, pair);
    await pg.keyboard.press("ArrowLeft"); await sleep(400);
    toast = await pg.evaluate(() => document.querySelector("#toast").textContent);
    const here = await pg.evaluate(() => S.cur);
    check("← at start toasts first", toast.startsWith("Start. Again for previous") && (await pg.evaluate(() => S.cur)) === here, toast);
    await pg.keyboard.press("ArrowLeft"); await sleep(1500);
    p = await pgN(pg);
    const there = await pg.evaluate(() => S.cur);
    check("second ← opens previous on its LAST page", there !== here && p.cur === p.n && p.n >= 2, JSON.stringify({ here, there, p }));
    await pg.keyboard.press("j"); await sleep(1500);
    p = await pgN(pg);
    check("j afterwards opens next on page 1 (no flag leak)", p.cur === 1, JSON.stringify(p));
    // click-away race: arm pendingLast then open another entry
    await pg.evaluate((id) => { S.pendingLast = 999999; return openEntry(id, { fromList: false }); }, long[2]);
    await sleep(1800);
    p = await pgN(pg);
    check("stale pendingLast does not leak", p.cur === 1 && (await pg.evaluate(() => S.pendingLast)) === null, JSON.stringify(p));

    // wheel
    await open(pg, long[0]);
    const box = await pg.evaluate(() => { const r = document.querySelector("#art-scroll").getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; });
    await pg.mouse.move(box.x, box.y);
    for (let i = 0; i < 5; i++) { await pg.mouse.wheel(0, 90); await sleep(25); }
    await sleep(1000);
    check("one flick (5 wheel events) = one page", (await pgN(pg)).cur === 2, JSON.stringify(await pgN(pg)));
    await sleep(300);
    await pg.mouse.wheel(0, 90); await sleep(1000);
    check("second flick = next page", (await pgN(pg)).cur === 3 || (await pgN(pg)).n === 2, JSON.stringify(await pgN(pg)));
    const ctrl = await pg.evaluate(() => { const sc = document.querySelector("#art-scroll"), before = document.querySelector("#pg-n").textContent; const ev = new WheelEvent("wheel", { deltaY: 120, ctrlKey: true, bubbles: true, cancelable: true }); sc.dispatchEvent(ev); return { prevented: ev.defaultPrevented, same: before === document.querySelector("#pg-n").textContent }; });
    check("ctrl+wheel (pinch) not hijacked", !ctrl.prevented && ctrl.same, JSON.stringify(ctrl));

    // mouse click in edge zone doesn't turn page
    await pg.evaluate(() => goPage(0, false)); await sleep(500);
    const pt = await pg.evaluate(() => { const sc = document.querySelector("#art-scroll"), r = sc.getBoundingClientRect(); const x = r.left + r.width * 0.9; for (let y = r.top + 30; y < r.bottom - 30; y += 17) { const el = document.elementFromPoint(x, y); if (el && !el.closest("a, img, pre, table, button, input")) return { x, y }; } return null; });
    await pg.mouse.click(pt.x, pt.y); await sleep(900);
    check("mouse click in right quarter does not turn page", (await pgN(pg)).cur === 1, JSON.stringify(await pgN(pg)));

    // z toggle keeps place both ways
    await pg.keyboard.press("Escape"); await sleep(700);
    check("Esc exits focus, panes back", (await pg.evaluate(() => document.querySelector("#app").dataset.focus)) === "" && (await pg.evaluate(() => getComputedStyle(document.querySelector("#side")).display)) !== "none");
    await pg.evaluate(() => { document.querySelector("#art-scroll").scrollTop = 1600; }); await sleep(300);
    const vsnip = await pg.evaluate(() => { const sc = document.querySelector("#art-scroll"), r = sc.getBoundingClientRect(); const cr = document.caretRangeFromPoint(r.left + r.width / 2 - 100, r.top + 60); return cr && cr.startContainer.nodeType === 3 ? cr.startContainer.data.slice(cr.startOffset, cr.startOffset + 40) : null; });
    await pg.keyboard.press("z"); await sleep(900);
    const vis = await pg.evaluate((s) => { const p = __pos(s); return p && p.left >= -2 && p.left < p.w && p.top > -50 && p.top < p.h; }, vsnip);
    const pageNow = await pgN(pg);
    check("z mid-article: same text is on the current page", vis && pageNow.cur > 1, JSON.stringify({ vsnip, vis, pageNow }));
    await pg.keyboard.press("z"); await sleep(900);
    const back = await pg.evaluate((s) => { const p = __pos(s); return p && p.top > -10 && p.top < p.h; }, vsnip);
    const sT = await pg.evaluate(() => document.querySelector("#art-scroll").scrollTop);
    check("z back to vertical: same text in view", back && sT > 100, JSON.stringify({ back, sT }));

    // persistence
    await pg.keyboard.press("z"); await sleep(400);
    await pg.reload(); await pg.waitForSelector("#entries li"); await pg.evaluate(HELPERS);
    await open(pg, long[0]);
    check("focus persists across reload", (await pg.evaluate(() => S.focus)) === true && (await pg.evaluate(() => document.querySelector("#app").dataset.focus)) === "1");
    check("no page errors on desktop run", pg.errs.length === 0, pg.errs.join("|"));
    await pg.screenshot({ path: `${process.argv[2] || "."}/final-${name}.png` });
    await ctx.close();
  }
  // ---- reduced motion
  {
    const { ctx, pg } = await fresh(browser, { reducedMotion: "reduce" }, () => localStorage.setItem("forage.focus", "1"));
    await open(pg, long[0]);
    await pg.keyboard.press("ArrowRight"); await pg.keyboard.press("ArrowRight"); await sleep(900);
    const p = await pgN(pg);
    check("reduced-motion paging works", p.cur === Math.min(3, p.n), JSON.stringify(p));
    await ctx.close();
  }
  // ---- iPad landscape, touch
  {
    const dev = devices["iPad (gen 7) landscape"];
    const { ctx, pg } = await fresh(browser, { ...dev }, () => localStorage.setItem("forage.focus", "1"));
    await open(pg, long[0]);
    const info = await pg.evaluate(() => ({ cols: getComputedStyle(document.querySelector(".art-body")).columnCount, n: document.querySelector("#pg-n").textContent, w: innerWidth }));
    check("iPad landscape: 2 columns", info.cols === "2", JSON.stringify(info));
    const pt = async (f) => pg.evaluate((f) => { const sc = document.querySelector("#art-scroll"), r = sc.getBoundingClientRect(); const x = r.left + r.width * f; for (let y = r.top + 30; y < r.bottom - 30; y += 13) { const el = document.elementFromPoint(x, y); if (el && !el.closest("a, img, pre, table, button, input")) return { x, y }; } return null; }, f);
    let q = await pt(0.93);
    await pg.touchscreen.tap(q.x, q.y); await sleep(900);
    check("touch tap right quarter → next page", (await pgN(pg)).cur === 2, JSON.stringify(await pgN(pg)));
    q = await pt(0.06);
    await pg.touchscreen.tap(q.x, q.y); await sleep(900);
    check("touch tap left quarter → previous page", (await pgN(pg)).cur === 1, JSON.stringify(await pgN(pg)));
    await ctx.close();
  }
  // ---- iPad portrait (narrow) and phone: list/back guard
  for (const dn of ["iPad (gen 7)", "iPhone 13"]) {
    const dev = devices[dn];
    const { ctx, pg } = await fresh(browser, { ...dev }, () => localStorage.setItem("forage.focus", "1"));
    await pg.evaluate(() => openEntry(S.entries[0].id)); // fromList=true on narrow → reader view
    await pg.waitForFunction(() => !document.querySelector("#art-content").textContent.startsWith("Loading"));
    await sleep(1200);
    const i = await pg.evaluate(() => ({ view: document.querySelector("#app").dataset.view, cols: getComputedStyle(document.querySelector(".art-body")).columnCount, n: document.querySelector("#pg-n").textContent }));
    check(`${dn}: reader view, focus pages`, i.view === "reader" && i.n.includes("/"), JSON.stringify(i));
    const wid = await pg.evaluate(() => { const b = document.querySelector(".art-body"), cols = +getComputedStyle(b).columnCount, gap = parseFloat(getComputedStyle(b).columnGap); const pr = document.createElement("div"); pr.style.cssText = "position:absolute;visibility:hidden;width:66ch"; b.appendChild(pr); const cap = pr.offsetWidth; pr.remove(); return { cols, colW: (b.getBoundingClientRect().width - (cols - 1) * gap) / cols, cap }; });
    check(`${dn}: every column <= 66ch`, wid.colW <= wid.cap + 1, JSON.stringify(wid));
    await pg.keyboard.press("ArrowRight"); await sleep(800);
    const moved = (await pgN(pg)).cur;
    check(`${dn}: → pages in reader`, moved === 2 || (await pgN(pg)).n === 1, JSON.stringify(await pgN(pg)));
    await pg.click("#btn-back"); await sleep(700);
    const v = await pg.evaluate(() => document.querySelector("#app").dataset.view);
    const before = await pg.evaluate(() => ({ sl: document.querySelector("#art-scroll").scrollLeft, cur: S.cur, armed: S.edgeArmed }));
    await pg.evaluate(() => document.querySelector("#toast").textContent = "");
    for (let k = 0; k < 4; k++) { await pg.keyboard.press("ArrowRight"); await sleep(150); }
    await sleep(500);
    const after = await pg.evaluate(() => ({ sl: document.querySelector("#art-scroll").scrollLeft, cur: S.cur, armed: S.edgeArmed, toast: document.querySelector("#toast").textContent }));
    check(`${dn}: keys ignored while list is showing`, v === "list" && before.sl === after.sl && before.cur === after.cur && after.toast === "", JSON.stringify({ v, before, after }));
    check(`${dn}: no page errors`, pg.errs.length === 0, pg.errs.join("|"));
    await ctx.close();
  }
  await browser.close();
}
await run("chromium", chromium);
await run("webkit", webkit);
const fails = results.filter((r) => !r.ok);
for (const r of results) console.log(`${r.ok ? "PASS" : "FAIL"} [${r.eng}] ${r.name}${r.ok ? "" : "  -> " + r.detail}`);
console.log(`\n${results.length - fails.length}/${results.length} passed`);
