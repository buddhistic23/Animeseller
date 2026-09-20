const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const money = (v) => v == null ? "—" : "$" + Number(v).toFixed(2);
const pct = (v) => v == null ? "—" : Number(v).toFixed(0) + "%";
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

let mode = "keyword";
let current = null;      // current scan id
let results = [];        // opportunities for current scan
let sortKey = "net", sortDir = -1;
let pollTimer = null;
let settings = {};
let watch = new Set();

// ---- tabs ----
$$(".tab").forEach(b => b.addEventListener("click", () => {
  mode = b.dataset.mode;
  $$(".tab").forEach(t => t.classList.toggle("active", t === b));
  $$(".mode").forEach(m => m.classList.toggle("hidden", !m.classList.contains("mode-" + mode)));
}));

// ---- settings ----
async function loadSettings() {
  settings = await (await fetch("/api/settings")).json();
  const chips = [
    ["Member discount", pct(settings.kino_member_discount * 100)],
    ["Kino sales tax", pct(settings.kino_sales_tax * 100)],
    ["Kino ship/item", money(settings.kino_shipping_per_item)],
    ["eBay FVF", pct(settings.ebay_fvf_rate * 100) + " + " + money(settings.ebay_per_order_fee)],
    ["Your ship cost", money(settings.ebay_ship_cost)],
    ["Buyer tax (FVF base)", pct(settings.buyer_tax_rate * 100)],
  ];
  $("#assumption-list").innerHTML = chips.map(([k, v]) => `<span class="chip">${k}: <b>${v}</b></span>`).join("");
  const banner = $("#banner");
  const msgs = [];
  if (settings.mock_mode) msgs.push("MOCK MODE: showing fake data. Set MOCK_MODE=0 in .env for live scans.");
  if (!settings.mock_mode && !settings.ebay_configured) msgs.push("eBay API keys not set (EBAY_CLIENT_ID / EBAY_CLIENT_SECRET). Comps will be empty.");
  if (!settings.mock_mode && !settings.ebay_sold_scrape) msgs.push("Sold comps disabled (EBAY_SOLD_SCRAPE=0); targets use the lowest active listing.");
  banner.textContent = msgs.join("  ·  ");
  banner.classList.toggle("hidden", msgs.length === 0);
  $("input[name=max_items]").value = Math.min(settings.max_items_per_scan, 40);
}

// ---- scans ----
$("#scan-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const body = { member: f.member.checked, max_items: Number(f.max_items.value) || undefined };
  if (mode === "keyword") body.keyword = f.keyword.value.trim();
  if (mode === "url") body.url = f.url.value.trim();
  if (mode === "isbns") body.isbns = f.isbns.value.split(/\n/);
  const r = await fetch("/api/scans", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) { alert((await r.json()).detail || "Scan failed"); return; }
  const { id } = await r.json();
  await loadScans();
  selectScan(id);
});

async function loadScans() {
  const scans = await (await fetch("/api/scans")).json();
  $("#scan-list").innerHTML = scans.map(s => `
    <li data-id="${s.id}" class="${s.id === current ? "active" : ""}">
      <span class="src" title="${esc(s.source)}">#${s.id} ${esc(s.source)}</span>
      <span class="sub">${s.status === "running" ? `${s.done}/${s.total}` : s.status}</span>
      <button class="ghost small del" data-id="${s.id}" title="delete">×</button>
    </li>`).join("") || `<li class="sub">No scans yet.</li>`;
  $$("#scan-list li[data-id]").forEach(li => li.addEventListener("click", () => selectScan(Number(li.dataset.id))));
  $$("#scan-list .del").forEach(b => b.addEventListener("click", async (e) => {
    e.stopPropagation();
    await fetch("/api/scans/" + b.dataset.id, { method: "DELETE" });
    if (current === Number(b.dataset.id)) { current = null; results = []; render(); }
    loadScans();
  }));
}

function selectScan(id) {
  current = id;
  $("#export").href = `/api/scans/${id}/export.csv`;
  clearInterval(pollTimer);
  poll();
  pollTimer = setInterval(poll, 1500);
  loadScans();
}

async function poll() {
  if (current == null) return;
  const r = await fetch("/api/scans/" + current);
  if (!r.ok) { clearInterval(pollTimer); return; }
  const s = await r.json();
  results = s.results;
  $("#results-title").textContent = `#${s.id} · ${s.source}`;
  $("#status").textContent = `${s.status} · ${s.done}/${s.total} · ${s.message}`;
  $("#bar").style.width = s.total ? (100 * s.done / s.total) + "%" : "0";
  render();
  if (s.status === "done" || s.status === "error") { clearInterval(pollTimer); loadScans(); }
}

// ---- results table ----
const getters = {
  verdict: o => ({ buy: 3, maybe: 2, pass: 1, unknown: 0 })[o.verdict],
  title: o => o.product.title.toLowerCase(),
  kino: o => o.product.sale_price ?? o.product.price,
  cost: o => o.cost_basis,
  target: o => o.target_price ?? -1,
  net: o => o.net_profit ?? -1e9,
  roi: o => o.roi_pct ?? -1e9,
  sold: o => o.comps.sold_median ?? -1,
  active: o => o.comps.active_min ?? -1,
};
$$("#results th[data-k]").forEach(th => th.addEventListener("click", () => {
  const k = th.dataset.k;
  if (sortKey === k) sortDir = -sortDir; else { sortKey = k; sortDir = k === "title" ? 1 : -1; }
  render();
}));
["#f-verdict", "#f-roi", "#f-profit", "#f-stock", "#f-text"].forEach(s => $(s).addEventListener("input", render));

function filtered() {
  const v = $("#f-verdict").value, roi = Number($("#f-roi").value || -1e9), prof = $("#f-profit").value;
  const stock = $("#f-stock").checked, txt = $("#f-text").value.toLowerCase();
  return results.filter(o =>
    (!v || o.verdict === v) &&
    (o.verdict === "unknown" || (o.roi_pct ?? -1e9) >= roi) &&
    (prof === "" || (o.net_profit ?? -1e9) >= Number(prof)) &&
    (!stock || o.product.in_stock) &&
    (!txt || o.product.title.toLowerCase().includes(txt) || o.product.isbn.includes(txt))
  ).sort((a, b) => {
    const x = getters[sortKey](a), y = getters[sortKey](b);
    return (x < y ? -1 : x > y ? 1 : 0) * sortDir;
  });
}

function render() {
  const rows = filtered();
  $("#results tbody").innerHTML = rows.map(o => {
    const p = o.product, c = o.comps;
    const kino = p.sale_price != null
      ? `<span class="sale">${money(p.sale_price)}</span> <s class="sub">${money(p.price)}</s>`
      : money(p.price);
    const netCls = o.net_profit == null ? "" : o.net_profit > 0 ? "pos" : "neg";
    return `
    <tr class="${p.in_stock ? "" : "oos"}" data-isbn="${p.isbn}">
      <td><span class="badge ${o.verdict}">${o.verdict}</span></td>
      <td class="title"><a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.title)}</a>
        <div class="sub">${esc(p.isbn)}${p.author ? " · " + esc(p.author) : ""}${p.in_stock ? "" : " · OUT OF STOCK"}</div></td>
      <td class="num">${kino}</td>
      <td class="num">${money(o.cost_basis)}</td>
      <td class="num">${money(o.target_price)}<div class="sub">${o.target_source.replace("_", " ")}</div></td>
      <td class="num ${netCls}">${money(o.net_profit)}</td>
      <td class="num ${netCls}">${pct(o.roi_pct)}</td>
      <td class="num">${money(c.sold_median)}<div class="sub">n=${c.sold_count}</div></td>
      <td class="num">${money(c.active_min)}<div class="sub">n=${c.active_count}</div></td>
      <td><button class="ghost small toggle">▾</button>
          <button class="ghost small watch">${watch.has(p.isbn) ? "★" : "☆"}</button></td>
    </tr>
    <tr class="detail hidden"><td colspan="10">${detail(o)}</td></tr>`;
  }).join("") || `<tr><td colspan="10" class="sub">Nothing to show.</td></tr>`;

  $$("#results .toggle").forEach(b => b.addEventListener("click", () => {
    b.closest("tr").nextElementSibling.classList.toggle("hidden");
  }));
  $$("#results .watch").forEach(b => b.addEventListener("click", async () => {
    const tr = b.closest("tr"), isbn = tr.dataset.isbn;
    const o = results.find(r => r.product.isbn === isbn);
    if (watch.has(isbn)) await fetch("/api/watchlist/" + isbn, { method: "DELETE" });
    else await fetch("/api/watchlist", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ isbn, title: o.product.title }) });
    await loadWatchlist(); render();
  }));
}

function detail(o) {
  const p = o.product, c = o.comps;
  const list = (arr) => arr.length
    ? `<ul>${arr.slice(0, 8).map(l => `<li><a href="${esc(l.url)}" target="_blank" rel="noopener">${money(l.price)}${l.shipping ? " + " + money(l.shipping) + " ship" : ""}</a> ${esc(l.condition || "")} <span class="sub">${esc(l.title).slice(0, 70)}</span></li>`).join("")}</ul>`
    : `<div class="sub">none</div>`;
  const ebayQ = `https://www.ebay.com/sch/i.html?_nkw=${p.isbn}&LH_Sold=1&LH_Complete=1`;
  return `<div class="breakdown">
    <div><b>Cost breakdown</b>
      <ul>
        <li>Kino ${p.sale_price != null ? "sale" : "list"} price: ${money(p.sale_price ?? p.price)}</li>
        <li>All-in cost (discount, tax, ship): <b>${money(o.cost_basis)}</b></li>
        <li>Sell at (${o.target_source.replace("_", " ")}): ${money(o.target_price)}</li>
        <li>eBay fees: ${money(o.ebay_fees)}</li>
        <li>Your shipping: ${money(settings.ebay_ship_cost)}</li>
        <li>Net: <b>${money(o.net_profit)}</b> · margin ${pct(o.margin_pct)} · ROI ${pct(o.roi_pct)}</li>
      </ul>
      ${c.error ? `<div class="neg">eBay: ${esc(c.error)}</div>` : ""}
      <div><a href="${ebayQ}" target="_blank" rel="noopener">Open sold comps on eBay ↗</a></div>
    </div>
    <div><b>Sold (median ${money(c.sold_median)})</b>${list(c.sold)}</div>
    <div><b>Active (min ${money(c.active_min)}, median ${money(c.active_median)})</b>${list(c.active)}</div>
  </div>`;
}

// ---- watchlist ----
async function loadWatchlist() {
  const items = await (await fetch("/api/watchlist")).json();
  watch = new Set(items.map(i => i.isbn));
  $("#watchlist").innerHTML = items.map(i => `
    <li><span class="src">${esc(i.title || i.isbn)} <span class="sub">${i.isbn}</span></span>
      <button class="ghost small rescan" data-isbn="${i.isbn}">rescan</button>
      <button class="ghost small unwatch" data-isbn="${i.isbn}">×</button></li>`).join("")
    || `<li class="sub">Star items in results to track them here.</li>`;
  $$("#watchlist .unwatch").forEach(b => b.addEventListener("click", async () => {
    await fetch("/api/watchlist/" + b.dataset.isbn, { method: "DELETE" }); await loadWatchlist(); render();
  }));
  $$("#watchlist .rescan").forEach(b => b.addEventListener("click", async () => {
    const r = await fetch("/api/scans", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ isbns: [b.dataset.isbn], member: true }) });
    const { id } = await r.json(); await loadScans(); selectScan(id);
  }));
}

(async () => {
  await loadSettings();
  await loadWatchlist();
  await loadScans();
  const scans = await (await fetch("/api/scans")).json();
  if (scans.length) selectScan(scans[0].id);
})();
