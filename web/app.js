/* ===== DukanBook web replica — talks to the FastAPI backend (same origin) ===== */
const API = ""; // same origin as FastAPI

const state = {
  tab: "customer",      // customer | supplier
  nav: "home",          // home | stock | bills | menu
  billTab: "sale",      // sale | purchase
  activeDraftId: null,
  parties: [],
  search: "",
  sessionId: localStorage.getItem("db_sid") || (() => {
    const id = "web-" + Math.random().toString(36).slice(2);
    localStorage.setItem("db_sid", id); return id;
  })(),
};
// A bill conversation starts only after a fresh upload. Drafts remain safely
// stored in the backend, but an old draft does not take over the chatbot when
// the app is reopened.
localStorage.removeItem("db_bill_draft");

/* ---------- API helpers ---------- */
async function api(path, opts) {
  const res = await fetch(API + path, opts);
  if (!res.ok) throw new Error((await res.text()) || res.status);
  const ct = res.headers.get("content-type") || "";
  return ct.includes("json") ? res.json() : res.text();
}
const getParties = () => api("/parties");
const getDetail = (id) => api("/parties/" + id);
const getReminders = (s = "pending") => api("/reminders?status=" + s);
const postJSON = (path, body) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const putJSON = (path, body) =>
  api(path, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const getBills = (type) => api("/bills" + (type ? "?type=" + encodeURIComponent(type) : ""));
const getBillSummary = () => api("/bills/summary");
const getStock = () => api("/stock");

/* ---------- helpers ---------- */
const $ = (sel) => document.querySelector(sel);
const view = () => $("#view");
const fmt = (n) => "₹" + Number(Math.abs(n)).toLocaleString("en-IN", { maximumFractionDigits: 0 });
const fmtPaise = (n) => "₹" + (Number(n || 0) / 100).toLocaleString("en-IN", {
  minimumFractionDigits: 2, maximumFractionDigits: 2,
});
const initials = (name) => (name || "?").trim().slice(0, 1).toUpperCase();
const esc = (s) => (s || "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const linkify = (s) =>
  esc(s).replace(/(https?:\/\/[^\s]+|tel:\+?[0-9]+)/g, (u) => `<a href="${u}" target="_blank">${u.length > 34 ? "link" : u}</a>`);

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), 2200);
}

/* ---------- data load ---------- */
async function loadData() {
  try {
    state.parties = await getParties();
    const rem = await getReminders("pending");
    $("#remBadge").textContent = rem.length;
  } catch (e) {
    state.parties = [];
  }
  try { const h = await api("/health"); state.voice = !!h.voice; } catch {}
}
// Refresh data AND redraw the current nav screen (use only when you want to navigate).
async function refresh() { await loadData(); render(); }

/* ---------- bottom-nav highlight (follows the selected tab) ---------- */
function setActive(name) {
  document.querySelectorAll(".bn-item").forEach((b) =>
    b.classList.toggle("active", b.dataset.nav === name));
  $("#navAI").classList.toggle("active", name === "ai");
}

/* ---------- render router ---------- */
function render() {
  setActive(state.nav);
  // tabs only relevant on home
  $("#tabs").style.display = state.nav === "home" ? "flex" : "none";
  if (state.nav === "home") return renderHome();
  if (state.nav === "stock") return renderStock();
  if (state.nav === "bills") return renderBills();
  if (state.nav === "menu") return renderMenu();
}

/* ---------- HOME (Customers / Suppliers ledger) ---------- */
function renderHome() {
  const parties = state.parties.filter((p) => p.type === state.tab);
  const get = parties.filter((p) => p.balance > 0).reduce((s, p) => s + p.balance, 0);
  const give = parties.filter((p) => p.balance < 0).reduce((s, p) => s - p.balance, 0);
  const term = state.search.toLowerCase();
  const shown = parties.filter((p) => p.name.toLowerCase().includes(term));
  const noun = state.tab === "customer" ? "Customers" : "Suppliers";

  view().innerHTML = `
    <div class="pill">
      <div class="col get"><div class="amt">${fmt(get)}</div><div class="lbl">You will get</div></div>
      <div class="col give"><div class="amt">${fmt(give)}</div><div class="lbl">You will give</div></div>
    </div>
    <div class="searchrow">
      <div class="search"><span class="mag">🔍</span>
        <input id="searchBox" placeholder="Search ${noun}" value="${esc(state.search)}" />
      </div>
      <button class="fab" id="addParty">+</button>
    </div>
    <div class="section-title">All Transactions</div>
    <div class="list" id="partyList">
      ${shown.length ? shown.map(rowHTML).join("") : emptyHTML(noun)}
    </div>`;

  $("#searchBox").addEventListener("input", (e) => { state.search = e.target.value; renderHome(); });
  $("#searchBox").focus && setCaretEnd($("#searchBox"));
  $("#addParty").onclick = () => addPartyModal();
  document.querySelectorAll("[data-pid]").forEach((el) =>
    el.onclick = () => openDetail(Number(el.dataset.pid)));
}

function rowHTML(p) {
  const bal = p.balance;
  const cls = bal > 0 ? "get" : bal < 0 ? "give" : "zero";
  const label = bal > 0 ? "you will get" : bal < 0 ? "you will give" : "settled";
  const phone = p.phone ? " · 📞 " + p.phone : "";
  return `<div class="row" data-pid="${p.id}">
      <div class="avatar">${initials(p.name)}</div>
      <div class="meta"><div class="nm">${esc(p.name)}</div>
        <div class="sub">${label}${phone}</div></div>
      <div class="bal ${cls}">${fmt(bal)}</div>
    </div>`;
}
function emptyHTML(noun) {
  return `<div class="empty"><div class="em-ico">🧑‍🤝‍🧑</div>
    <div class="em-title">No ${noun} yet</div>
    <div class="em-sub">Tap the + button to add your first ${noun.toLowerCase().replace(/s$/, "")}.</div></div>`;
}
function setCaretEnd(input){ const v=input.value; input.value=""; input.value=v; }

/* ---------- DETAIL ---------- */
async function openDetail(id) {
  $("#tabs").style.display = "none";
  view().innerHTML = `<div class="spinner"></div>`;
  let d;
  try { d = await getDetail(id); } catch { toast("Could not load"); return; }
  const p = d.party, bal = d.balance;
  const cls = bal > 0 ? "get" : bal < 0 ? "give" : "zero";
  const label = bal > 0 ? "You will get" : bal < 0 ? "You will give" : "Settled";
  const phoneBlock = p.phone
    ? `<a class="chip" href="tel:+91${p.phone}">📞 Call ${p.phone}</a>
       <a class="chip" href="https://wa.me/91${p.phone}" target="_blank">💬 WhatsApp</a>`
    : `<button class="chip" id="addPhone">📞 Add phone</button>`;

  view().innerHTML = `
    <div class="detail-head">
      <button class="back" id="back">←</button>
      <div class="avatar">${initials(p.name)}</div>
      <div><div class="nm" style="font-weight:700">${esc(p.name)}</div>
        <div class="sub" style="color:var(--muted);font-size:.78rem">${esc(p.type)}</div></div>
    </div>
    <div class="detail-bal"><div class="v ${cls}">${fmt(bal)}</div><div class="l">${label}</div></div>
    <div class="chip-row">
      <button class="chip solid" id="giveBtn">＋ You gave (udhaar)</button>
      <button class="chip solid" id="getBtn">＋ You got (payment)</button>
    </div>
    <div class="chip-row">${phoneBlock}
      <button class="chip" id="remindBtn">⏰ Set reminder</button>
    </div>
    <div class="section-title">History</div>
    <div class="list">${d.transactions.length
      ? d.transactions.slice().reverse().map(txnHTML).join("")
      : `<div class="empty"><div class="em-sub">No transactions yet.</div></div>`}</div>`;

  $("#back").onclick = () => { state.nav = "home"; render(); };
  $("#giveBtn").onclick = () => txnModal(p, "credit");   // you gave udhaar -> they owe you (get)
  $("#getBtn").onclick = () => txnModal(p, "debit");     // you received -> reduces what they owe
  $("#remindBtn").onclick = () => reminderModal(p);
  if ($("#addPhone")) $("#addPhone").onclick = () => phoneModal(p);
}
function txnHTML(t) {
  const cls = t.type === "credit" ? "get" : "give";
  const sign = t.type === "credit" ? "+" : "−";
  const note = t.note ? " · " + esc(t.note) : "";
  return `<div class="txn"><div><div>${t.type === "credit" ? "Udhaar" : "Payment"}${note}</div>
      <div class="d">${(t.txn_date || "").slice(0, 10)}</div></div>
      <div class="a ${cls}">${sign}${fmt(t.amount)}</div></div>`;
}

/* ---------- STOCK (updated automatically by confirmed bills) ---------- */
async function renderStock() {
  view().innerHTML = `<div class="spinner"></div>`;
  let products = [];
  try { products = await getStock(); } catch { return toast("Stock could not be loaded"); }
  const low = products.filter((p) => Number(p.quantity) <= 5).length;
  view().innerHTML = `
    <div class="appbar-row" style="color:var(--ink);padding:4px 2px 12px">
      <div style="font-size:1.2rem;font-weight:700">Stock Management</div></div>
    <div class="stats">
      <div class="col"><div class="n">${products.length}</div><div class="t">Products</div></div>
      <div class="col"><div class="n red">${low}</div><div class="t">Low Stock</div></div>
    </div>
    ${products.length ? `<div class="list">${products.map((p) => `
      <div class="row">
        <div class="avatar">📦</div>
        <div class="meta"><div class="nm">${esc(p.name)}</div>
          <div class="sub">Updated by confirmed purchase/sale bills</div></div>
        <div class="bal ${Number(p.quantity) < 0 ? "give" : "get"}">${esc(String(p.quantity))} ${esc(p.unit || "")}</div>
      </div>`).join("")}</div>` : `<div class="empty"><div class="em-ico">📦</div>
      <div class="em-title">No products added</div>
      <div class="em-sub">Scan a purchase bill in the AI Assistant to add stock.</div></div>`}`;
}

/* ---------- BILLS: management screen backed by the existing AI Assistant ---------- */
async function renderBills() {
  view().innerHTML = `<div class="spinner"></div>`;
  let summary, bills;
  try {
    [summary, bills] = await Promise.all([getBillSummary(), getBills(state.billTab)]);
  } catch (error) {
    console.error("Bills load failed", error);
    view().innerHTML = `<div class="empty"><div class="em-ico">⚠️</div>
      <div class="em-title">Bills could not be loaded</div>
      <div class="em-sub">Please check the connection and try again.</div></div>`;
    return;
  }
  const tabLabel = state.billTab === "sale" ? "Sale" : "Purchase";
  view().innerHTML = `
    <div class="bill-head">
      <div><div class="screen-title">Manage Bills</div>
        <div class="screen-sub">Created and verified with the DukanBook AI Assistant</div></div>
      <button class="scan-cta" id="scanFromBills">📷 Scan bill</button>
    </div>
    <div class="stats">
      <div class="col"><div class="n get">${fmtPaise(summary.total_sales_paise)}</div><div class="t">Total Sales</div></div>
      <div class="col"><div class="n red">${fmtPaise(summary.total_purchases_paise)}</div><div class="t">Total Purchases</div></div>
    </div>
    <div class="cash-strip">
      <span><b>${fmtPaise(summary.total_in_paise)}</b><small>Cash IN</small></span>
      <span><b>${fmtPaise(summary.total_out_paise)}</b><small>Cash OUT</small></span>
      <button id="cashbookBtn">Cashbook ›</button>
    </div>
    <div class="bill-tabs">
      <button class="${state.billTab === "sale" ? "active" : ""}" data-btab="sale">Sales</button>
      <button class="${state.billTab === "purchase" ? "active" : ""}" data-btab="purchase">Purchases</button>
    </div>
    ${bills.length ? `<div class="list bill-list">${bills.map(billRowHTML).join("")}</div>` :
      `<div class="empty bill-empty"><div class="em-ico">✉️</div>
        <div class="em-title">No ${tabLabel} Bills Added</div>
        <div class="em-sub">Open the AI Assistant and photograph a handwritten bill.</div>
        <button class="btn-primary compact" id="emptyScan">Scan with AI</button></div>`}`;
  $("#scanFromBills").onclick = openBillScannerInChat;
  if ($("#emptyScan")) $("#emptyScan").onclick = openBillScannerInChat;
  $("#cashbookBtn").onclick = openCashbook;
  document.querySelectorAll("[data-btab]").forEach((button) =>
    button.onclick = () => { state.billTab = button.dataset.btab; renderBills(); });
  document.querySelectorAll("[data-bill-id]").forEach((row) =>
    row.onclick = () => openBillDetail(Number(row.dataset.billId)));
}

function billRowHTML(bill) {
  const due = Number(bill.due_rupees || 0);
  const typeClass = bill.type === "sale" ? "get" : "give";
  return `<div class="row bill-row" data-bill-id="${bill.id}">
    <div class="avatar">${bill.type === "sale" ? "S" : "P"}</div>
    <div class="meta"><div class="nm">${esc(bill.party_name)}</div>
      <div class="sub">${esc(bill.bill_number)} · ${esc(bill.bill_date || "")} · ${bill.gst_mode === "gst" ? "GST" : "Non-GST"}</div></div>
    <div class="bill-amount"><b class="${typeClass}">${fmtPaise(bill.grand_total_paise)}</b>
      <small>${due ? fmt(due) + " due" : "Paid"}</small></div>
  </div>`;
}

async function openBillDetail(id) {
  $("#tabs").style.display = "none";
  view().innerHTML = `<div class="spinner"></div>`;
  try {
    const bill = await api("/bills/" + id);
    view().innerHTML = `
      <div class="detail-head"><button class="back" id="back">←</button>
        <div><div class="screen-title">DukanBook ${bill.type === "sale" ? "Sale" : "Purchase"} Bill</div>
          <div class="screen-sub">${esc(bill.bill_number)}</div></div></div>
      <div class="digital-bill">
        <div class="digital-brand">DukanBook <span>AI verified</span></div>
        <div class="digital-meta"><b>${esc(bill.party_name)}</b><span>${esc(bill.bill_date)}</span></div>
        ${bill.party_phone ? `<div class="screen-sub">${esc(bill.party_phone)}</div>` : ""}
        <div class="bill-items">${bill.items.map((item) => `
          <div><span>${esc(item.name)} × ${esc(item.quantity)} ${esc(item.unit || "")}</span>
            <b>${fmtPaise(item.line_total_paise)}</b></div>`).join("")}</div>
        <div class="bill-total"><span>Verified total</span><b>${fmtPaise(bill.grand_total_paise)}</b></div>
        <div class="screen-sub">${bill.gst_mode === "gst" ? `GST · ${esc(bill.gst_rate || "0")}%` : "Non-GST"} · ${esc(bill.payment_status)}</div>
      </div>
      <a class="btn-primary download-bill" href="/bills/${bill.id}/pdf" download>Download professional PDF</a>
      <button class="btn-ghost" id="anotherBill">Scan another bill with AI</button>`;
    $("#back").onclick = () => { state.nav = "bills"; renderBills(); };
    $("#anotherBill").onclick = openBillScannerInChat;
  } catch { toast("Bill could not be loaded"); }
}

async function openCashbook() {
  $("#tabs").style.display = "none";
  view().innerHTML = `<div class="spinner"></div>`;
  let entries = [];
  try { entries = await api("/cashbook"); } catch {}
  view().innerHTML = `<div class="detail-head"><button class="back" id="back">←</button>
      <div class="screen-title">Cashbook</div></div>
    ${entries.length ? `<div class="list">${entries.map((entry) => `
      <div class="txn"><div><div>${esc(entry.note || entry.bill_number || "Bill")}</div>
        <div class="d">${esc(entry.entry_date || "")}</div></div>
        <div class="a ${entry.direction === "in" ? "get" : "give"}">${entry.direction === "in" ? "+" : "−"}${fmtPaise(entry.amount_paise)}</div>
      </div>`).join("")}</div>` : `<div class="empty"><div class="em-ico">🧮</div>
      <div class="em-title">No cash entries</div><div class="em-sub">Paid bills appear here automatically.</div></div>`}`;
  $("#back").onclick = () => { state.nav = "bills"; renderBills(); };
}

/* ---------- MENU ---------- */
function renderMenu() {
  const items = [
    ["👤", "Profile"], ["📒", "Cashbook"], ["⏰", "Reminders / Call requests", () => openReminders()],
    ["💬", "AI Assistant", () => openChat()], ["🗑️", "Bin"], ["📇", "Visiting Cards"],
    ["📞", "Call Us"], ["✉️", "Mail Us"],
  ];
  view().innerHTML = `
    <div class="menu-head"><div class="mav"></div>
      <div><div class="mn">DukanBook</div><div class="me">Aapka digital khata</div></div></div>
    ${items.map((it, i) => `<div class="menu-item" data-mi="${i}">
        <span class="mi">${it[0]}</span><span>${it[1]}</span></div>`).join("")}`;
  document.querySelectorAll("[data-mi]").forEach((el) => {
    const it = items[Number(el.dataset.mi)];
    el.onclick = it[2] || (() => toast(it[1] + " — coming soon"));
  });
}

/* ---------- REMINDERS ---------- */
async function openReminders() {
  $("#tabs").style.display = "none";
  view().innerHTML = `<div class="spinner"></div>`;
  let rem = [];
  try { rem = await getReminders("pending"); } catch {}
  view().innerHTML = `
    <div class="detail-head"><button class="back" id="back">←</button>
      <div style="font-size:1.2rem;font-weight:700">Reminders & Call requests</div></div>
    <div class="list">${rem.length ? rem.map(remHTML).join("")
      : `<div class="empty"><div class="em-ico">⏰</div><div class="em-title">No pending reminders</div>
         <div class="em-sub">Ask the AI: "kal Rahul ko 500 ke liye call karna".</div></div>`}</div>`;
  $("#back").onclick = () => { state.nav = "menu"; render(); };
  document.querySelectorAll("[data-done]").forEach((b) =>
    b.onclick = async () => { await postJSON(`/reminders/${b.dataset.done}/done`, {}); toast("Marked done"); openReminders(); });
}
function remHTML(r) {
  const amt = r.amount ? " · " + fmt(r.amount) : "";
  const wa = r.whatsapp_link ? `<a class="chip" href="${r.whatsapp_link}" target="_blank">💬 WhatsApp</a>` : "";
  const call = r.call_link ? `<a class="chip" href="${r.call_link}">📞 Call</a>` : "";
  return `<div style="border-bottom:1px solid var(--line);padding:10px 4px">
    <div style="display:flex;justify-content:space-between">
      <div><b>${esc(r.party_name)}</b>${amt}</div>
      <div class="d" style="font-size:.74rem;color:var(--muted)">${(r.due_at || "").replace("T", " ").slice(0, 16)}</div></div>
    ${r.message ? `<div class="sub" style="font-size:.82rem;color:var(--muted);margin:3px 0">${esc(r.message)}</div>` : ""}
    <div class="chip-row" style="margin-top:6px">${call}${wa}
      <button class="chip" data-done="${r.id}">✓ Done</button></div></div>`;
}

/* ---------- CHAT (the same AI Assistant handles khata + scanned bills) ---------- */
async function openChat() {
  $("#tabs").style.display = "none";
  setActive("ai");
  const micBtn = state.voice
    ? `<button id="micBtn" class="mic" title="Boliye (Hindi / English / Hinglish)">🎙️</button>` : "";
  view().innerHTML = `
    <div class="chat-wrap">
      <div class="detail-head"><button class="back" id="back">←</button>
        <div style="font-size:1.15rem;font-weight:700">🤖 AI Assistant</div></div>
      <div class="chat-log" id="log">
        <div class="bubble bot">Namaste! Main aapka DukanBook AI Assistant hoon.
Khata update karein, reminder banayein, ya 📷 se handwritten bill scan karein. Main missing details poochunga aur confirmation ke baad hi stock/accounts update honge.</div>
      </div>
      <div class="chat-input">
        ${micBtn}
        <button id="cameraBtn" class="mic" title="Handwritten bill scan karein">📷</button>
        <input id="billImage" class="hidden" type="file" accept="image/jpeg,image/png,image/webp" capture="environment" />
        <input id="chatBox" placeholder="Message..." />
        <button id="sendBtn">Send</button>
      </div>
    </div>`;
  $("#back").onclick = () => { state.nav = "menu"; render(); };
  const send = async () => {
    const box = $("#chatBox"); const msg = box.value.trim(); if (!msg) return;
    addBubble(msg, "user"); box.value = ""; box.focus();
    const typing = addBubble("…", "bot");
    try {
      if (state.activeDraftId) {
        if (/^(cancel|exit|stop).*(bill)?$/i.test(msg)) {
          clearActiveDraft();
          typing.textContent = "Bill mode closed. The draft is still saved in Bills history for later review.";
        } else {
          const draft = await postJSON(`/bill-drafts/${state.activeDraftId}/answer`, { answer: msg });
          typing.textContent = billAssistantReply(draft);
          appendDraftCard(draft);
        }
      } else {
        const r = await postJSON("/chat", { message: msg, session_id: state.sessionId });
        typing.innerHTML = linkify(r.reply);
      }
    } catch { typing.textContent = "Backend se baat nahi ho payi."; }
    $("#log").scrollTop = $("#log").scrollHeight;
    loadData();  // ledger may have changed — refresh data but stay in chat
  };
  $("#sendBtn").onclick = send;
  $("#chatBox").addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });
  if (state.voice) $("#micBtn").onclick = (e) => toggleMic(e.currentTarget);
  $("#cameraBtn").onclick = () => $("#billImage").click();
  $("#billImage").onchange = (e) => {
    const file = e.target.files && e.target.files[0];
    if (file) scanBillInChat(file);
  };
  if (state.activeDraftId) {
    try {
      const draft = await api(`/bill-drafts/${state.activeDraftId}`);
      addBubble("Your bill draft is still active. Continue answering here or review it below.", "bot");
      appendDraftCard(draft);
    } catch { clearActiveDraft(); }
  }
}
function addBubble(text, who) {
  const b = document.createElement("div");
  b.className = "bubble " + who; b.textContent = text;
  $("#log").appendChild(b); $("#log").scrollTop = $("#log").scrollHeight;
  return b;
}

function openBillScannerInChat() {
  openChat().then(() => {
    addBubble("Please photograph the full handwritten bill in good light. Keep all four corners visible.", "bot");
    $("#billImage").click();
  });
}

async function scanBillInChat(file) {
  addBubble(`📷 ${file.name}`, "user");
  const typing = addBubble("Reading handwriting and extracting bill details…", "bot");
  const fd = new FormData();
  fd.append("file", file, file.name);
  fd.append("session_id", state.sessionId);
  try {
    const draft = await api("/bill-drafts/scan", { method: "POST", body: fd });
    const documentKind = (draft.data || {}).document_kind;
    if (documentKind === "not_bill" || documentKind === "uncertain") {
      clearActiveDraft();
      typing.textContent = documentKind === "not_bill"
        ? `This image does not look like a bill. ${(draft.data || {}).document_reason || "Please upload a handwritten or printed sale/purchase bill."}`
        : "I cannot clearly identify this image as a bill. Please upload a clearer photo with the full bill visible.";
      return;
    }
    setActiveDraft(draft.id);
    typing.textContent = draft.duplicate
      ? "I found the same saved scan, so I reopened it without creating a duplicate."
      : billAssistantReply(draft);
    appendDraftCard(draft);
  } catch (e) {
    typing.textContent = "I could not read this image. Please use a clear JPEG, PNG, or WebP photo and try again.";
  }
}

function setActiveDraft(id) {
  state.activeDraftId = id;
}
function clearActiveDraft() {
  state.activeDraftId = null;
  localStorage.removeItem("db_bill_draft");
}

const missingLabels = {
  bill_type: "Is this a sale or purchase bill?",
  bill_date: "What is the bill date? (YYYY-MM-DD)",
  "party.name": "What is the customer or supplier name?",
  gst_mode: "Should I make this GST or non-GST?",
  gst_rate: "What GST percentage applies?",
  tax_scheme: "Is it CGST + SGST or IGST?",
  payment_status: "Was it paid, on credit, or partially paid?",
  paid_amount_paise: "How much was paid?",
  items: "Please add at least one item in Review.",
  "items.quantities": "I found the item rows but could not safely read their quantities. Are all quantities 1? Reply 'all 1', or fill them manually.",
  "items.prices": "I found the item names but could not safely read all prices. Please fill the prices in Review.",
};
function missingQuestion(path) {
  if (missingLabels[path]) return missingLabels[path];
  if (/items\.\d+\.name/.test(path)) return "What is the missing item name?";
  if (/items\.\d+\.quantity/.test(path)) return "What is the missing item quantity?";
  if (/items\.\d+\.unit_price_paise/.test(path)) return "What is the missing item price?";
  return `Please confirm ${path}.`;
}
function billAssistantReply(draft) {
  const calc = draft.calculation || {};
  const errors = (calc.warnings || []).filter((w) => w.severity === "error");
  const cautions = (calc.warnings || []).filter((w) => w.severity === "warning");
  const notBill = errors.find((warning) =>
    warning.code === "not_a_bill" || warning.code === "uncertain_document");
  if (notBill) {
    return `This image cannot be processed as a bill: ${notBill.message} Please upload a clear sale or purchase bill.`;
  }
  if ((calc.missing_fields || []).length) {
    return `I saved the scan. ${missingQuestion(calc.missing_fields[0])} You can type or speak the answer.`;
  }
  if (errors.length) {
    return `I found a calculation mismatch: ${errors[0].message} Open Review to compare and accept or correct it.`;
  }
  if (cautions.length) {
    return `I read the bill, but one value needs a careful look: ${cautions[0].message} Please check it in Review before confirming.`;
  }
  return `The bill is ready for review. I independently verified the total as ${fmtPaise(calc.grand_total_paise)}. Please review before confirming.`;
}

function appendDraftCard(draft) {
  const log = $("#log");
  if (!log) return;
  log.querySelectorAll(".draft-card").forEach((card) => card.remove());
  const data = draft.data || {}, calc = draft.calculation || {};
  const errors = (calc.warnings || []).filter((w) => w.severity === "error");
  const card = document.createElement("div");
  card.className = "draft-card";
  const nextQuestion = (calc.missing_fields || []).length
    ? missingQuestion(calc.missing_fields[0]) : null;
  card.innerHTML = `
    <div class="draft-card-top"><span>${data.bill_type === "purchase" ? "📥 Purchase" : data.bill_type === "sale" ? "📤 Sale" : "🧾 Bill draft"}</span>
      <b>${fmtPaise(calc.grand_total_paise)}</b></div>
    <div class="draft-card-party">${esc((data.party || {}).name || "Party not identified")} · ${(data.items || []).length} item(s)</div>
    <div class="draft-status ${draft.status === "ready_for_review" ? "ready" : ""}">
      ${draft.status === "ready_for_review" ? "✓ Ready to review" :
        `${(calc.missing_fields || []).length} missing · ${errors.length} maths issue(s)`}</div>
    ${nextQuestion ? `<div class="draft-question"><b>AI asks:</b> ${esc(nextQuestion)}</div>` : ""}
    <div class="draft-actions">
      ${nextQuestion ? `<button data-answer>🎙 Type or speak answer</button>` : ""}
      <button data-review>${nextQuestion ? "✍ Fill details manually" : "Review & confirm"}</button>
    </div>
    <button data-exit class="draft-exit">Exit bill</button>`;
  log.appendChild(card);
  if (card.querySelector("[data-answer]")) {
    card.querySelector("[data-answer]").onclick = () => {
      const input = $("#chatBox");
      input.placeholder = nextQuestion;
      input.focus();
      toast(state.voice
        ? "Type below, or tap the microphone and speak"
        : "Type your answer below");
    };
  }
  card.querySelector("[data-review]").onclick = () => openBillDraftEditor(draft);
  card.querySelector("[data-exit]").onclick = () => {
    clearActiveDraft(); card.remove(); addBubble("Bill mode closed. You can continue using the assistant normally.", "bot");
  };
  log.scrollTop = log.scrollHeight;
}

const rupeesValue = (paise) => paise == null ? "" : (Number(paise) / 100).toFixed(2).replace(/\.00$/, "");
function toPaise(value, nullable = true) {
  const text = String(value ?? "").trim();
  if (!text) return nullable ? null : 0;
  const number = Number(text);
  if (!Number.isFinite(number)) return nullable ? null : 0;
  return Math.round(number * 100);
}
function option(value, current, label) {
  return `<option value="${value}" ${value === current ? "selected" : ""}>${label}</option>`;
}

function openBillDraftEditor(draft) {
  const data = draft.data || {}, party = data.party || {}, calc = draft.calculation || {};
  const items = (data.items && data.items.length ? data.items : [{}]);
  const warningHTML = (calc.warnings || []).map((warning) =>
    `<div class="math-warning ${warning.severity}">⚠ ${esc(warning.message)}</div>`).join("");
  showModal(`<div class="bill-review">
    <div class="review-title"><div><h3>Review AI bill</h3>
      <div class="screen-sub">Nothing is posted until you confirm.</div></div>
      <button class="close-x" id="cancel">×</button></div>
    ${warningHTML}
    ${(calc.missing_fields || []).length ? `<div class="missing-box"><b>AI still needs:</b>
      ${esc((calc.missing_fields || []).map(missingQuestion).join(" "))}</div>` : ""}
    <div class="review-grid">
      <div class="field"><label>Bill type *</label><select id="bdType">
        <option value="">Choose</option>${option("sale", data.bill_type, "Sale")}${option("purchase", data.bill_type, "Purchase")}
      </select></div>
      <div class="field"><label>Bill date *</label><input id="bdDate" type="date" value="${esc(data.bill_date || "")}" /></div>
      <div class="field"><label>Bill number</label><input id="bdNumber" value="${esc(data.bill_number || "")}" /></div>
      <div class="field"><label>Party name *</label><input id="bdParty" value="${esc(party.name || "")}" /></div>
      <div class="field"><label>Phone</label><input id="bdPhone" value="${esc(party.phone || "")}" /></div>
      <div class="field"><label>GSTIN</label><input id="bdGstin" value="${esc(party.gstin || "")}" /></div>
      <div class="field"><label>Bill taxation *</label><select id="bdGstMode">
        <option value="">Choose</option>${option("non_gst", data.gst_mode, "Non-GST")}${option("gst", data.gst_mode, "GST")}
      </select></div>
      <div class="field"><label>GST rate %</label><input id="bdGstRate" type="number" step=".01" value="${esc(data.gst_rate || "")}" /></div>
      <div class="field"><label>Tax scheme</label><select id="bdTaxScheme">
        <option value="">Choose</option>${option("cgst_sgst", data.tax_scheme, "CGST + SGST")}${option("igst", data.tax_scheme, "IGST")}
      </select></div>
      <div class="field"><label>Payment *</label><select id="bdPayment">
        <option value="">Choose</option>${option("paid", data.payment_status, "Paid")}${option("credit", data.payment_status, "Credit")}${option("partial", data.payment_status, "Partially paid")}
      </select></div>
      <div class="field"><label>Paid amount ₹</label><input id="bdPaid" type="number" step=".01" value="${rupeesValue(data.paid_amount_paise)}" /></div>
    </div>
    <div class="items-title"><b>Items</b><button id="addBillItem">+ Add item</button></div>
    <div id="billItemRows">${items.map((item, index) => billItemEditorHTML(item, index)).join("")}</div>
    <div class="review-grid money-grid">
      <div class="field"><label>Discount ₹</label><input id="bdDiscount" type="number" step=".01" value="${rupeesValue(data.discount_paise || 0)}" /></div>
      <div class="field"><label>Extra charge ₹</label><input id="bdExtra" type="number" step=".01" value="${rupeesValue(data.extra_charge_paise || 0)}" /></div>
      <div class="field"><label>Round off ₹</label><input id="bdRound" type="number" step=".01" value="${rupeesValue(data.round_off_paise || 0)}" /></div>
    </div>
    <div class="verified-summary">
      <div><span>Subtotal</span><b>${fmtPaise(calc.subtotal_paise)}</b></div>
      <div><span>GST</span><b>${fmtPaise(calc.gst_paise)}</b></div>
      <div class="grand"><span>AI-verified total</span><b>${fmtPaise(calc.grand_total_paise)}</b></div>
    </div>
    ${(calc.warnings || []).some((w) => w.severity === "error") ?
      `<button class="btn-ghost math-accept" id="acceptMath">Use the independently verified maths</button>` : ""}
    <div class="review-actions">
      <button class="btn-ghost" id="saveDraft">Save & recheck</button>
      <button class="btn-primary" id="confirmDraft" ${draft.status !== "ready_for_review" ? "disabled" : ""}>Confirm & post bill</button>
    </div>
  </div>`);
  $("#modalCard").classList.add("bill-modal");
  $("#cancel").onclick = closeBillModal;
  $("#addBillItem").onclick = () => {
    const index = document.querySelectorAll(".bill-item-editor").length;
    $("#billItemRows").insertAdjacentHTML("beforeend", billItemEditorHTML({}, index));
    wireRemoveBillItems();
  };
  wireRemoveBillItems();
  $("#saveDraft").onclick = async () => {
    try {
      const saved = await putJSON(`/bill-drafts/${draft.id}`, { data: collectBillDraft(data) });
      setActiveDraft(saved.id);
      closeBillModal();
      addBubble(billAssistantReply(saved), "bot");
      appendDraftCard(saved);
    } catch (e) { toast("Please check the highlighted bill details"); }
  };
  if ($("#acceptMath")) $("#acceptMath").onclick = async () => {
    const corrected = collectBillDraft(data);
    corrected.items.forEach((item, index) => {
      item.written_total_paise = (calc.lines[index] || {}).calculated_total_paise;
    });
    corrected.written_subtotal_paise = calc.subtotal_paise;
    corrected.written_grand_total_paise = calc.grand_total_paise;
    try {
      const saved = await putJSON(`/bill-drafts/${draft.id}`, { data: corrected });
      closeBillModal();
      addBubble("I replaced the handwritten arithmetic with the independently verified totals.", "bot");
      appendDraftCard(saved);
    } catch { toast("Could not apply verified totals"); }
  };
  $("#confirmDraft").onclick = async () => {
    try {
      const saved = await putJSON(`/bill-drafts/${draft.id}`, { data: collectBillDraft(data) });
      if (saved.status !== "ready_for_review") {
        closeBillModal(); addBubble(billAssistantReply(saved), "bot"); appendDraftCard(saved); return;
      }
      const bill = await postJSON(`/bill-drafts/${draft.id}/confirm`, {});
      clearActiveDraft(); closeBillModal();
      document.querySelectorAll(".draft-card").forEach((card) => card.remove());
      addBubble(`Confirmed. I created DukanBook bill ${bill.bill_number} for ${fmtPaise(bill.grand_total_paise)} and updated ${bill.type === "purchase" ? "purchase stock, supplier ledger and cashbook" : "sales stock, customer ledger and cashbook"}.`, "bot");
      loadData();
    } catch (e) { toast("Bill could not be posted: review the remaining issue"); }
  };
}

function closeBillModal() {
  $("#modalCard").classList.remove("bill-modal");
  closeModal();
}
function billItemEditorHTML(item, index) {
  return `<div class="bill-item-editor" data-item-row>
    <div class="item-row-head"><span>Item ${index + 1}</span><button data-remove-item title="Remove">×</button></div>
    <div class="review-grid">
      <div class="field wide"><label>Name *</label><input data-item="name" value="${esc(item.name || "")}" /></div>
      <div class="field"><label>Quantity *</label><input data-item="quantity" type="number" step=".001" value="${esc(item.quantity || "")}" /></div>
      <div class="field"><label>Unit</label><input data-item="unit" value="${esc(item.unit || "")}" placeholder="pcs, kg" /></div>
      <div class="field"><label>Rate ₹ *</label><input data-item="price" type="number" step=".01" value="${rupeesValue(item.unit_price_paise)}" /></div>
      <div class="field"><label>Written total ₹</label><input data-item="written" type="number" step=".01" value="${rupeesValue(item.written_total_paise)}" /></div>
      <div class="field"><label>HSN</label><input data-item="hsn" value="${esc(item.hsn || "")}" /></div>
    </div>
  </div>`;
}
function wireRemoveBillItems() {
  document.querySelectorAll("[data-remove-item]").forEach((button) =>
    button.onclick = () => {
      if (document.querySelectorAll(".bill-item-editor").length === 1) return toast("At least one item is required");
      button.closest(".bill-item-editor").remove();
      document.querySelectorAll(".item-row-head span").forEach((label, index) => label.textContent = `Item ${index + 1}`);
    });
}
function collectBillDraft(original) {
  const value = (selector) => ($(selector).value || "").trim() || null;
  const items = Array.from(document.querySelectorAll("[data-item-row]")).map((row) => {
    const itemValue = (name) => (row.querySelector(`[data-item="${name}"]`).value || "").trim() || null;
    return {
      name: itemValue("name"),
      quantity: itemValue("quantity"),
      unit: itemValue("unit"),
      unit_price_paise: toPaise(itemValue("price")),
      written_total_paise: toPaise(itemValue("written")),
      hsn: itemValue("hsn"),
      gst_rate: null,
      confidence: {},
    };
  });
  return {
    ...original,
    bill_type: value("#bdType"),
    bill_number: value("#bdNumber"),
    bill_date: value("#bdDate"),
    party: { name: value("#bdParty"), phone: value("#bdPhone"), gstin: value("#bdGstin") },
    gst_mode: value("#bdGstMode"),
    tax_scheme: value("#bdTaxScheme"),
    gst_rate: value("#bdGstRate"),
    payment_status: value("#bdPayment"),
    paid_amount_paise: toPaise(value("#bdPaid")),
    discount_paise: toPaise(value("#bdDiscount"), false),
    extra_charge_paise: toPaise(value("#bdExtra"), false),
    round_off_paise: toPaise(value("#bdRound"), false),
    items,
  };
}

/* ---------- voice: normal assistant or active bill clarification ---------- */
let _rec = null, _chunks = [], _recording = false;
async function toggleMic(btn) {
  if (_recording && _rec) { _rec.stop(); return; }
  if (!navigator.mediaDevices || !window.MediaRecorder) return toast("Browser mic supported nahi");
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    _rec = new MediaRecorder(stream);
    _chunks = [];
    _rec.ondataavailable = (e) => { if (e.data.size) _chunks.push(e.data); };
    _rec.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      _recording = false; btn.classList.remove("rec"); btn.textContent = "🎙️";
      await sendVoice(new Blob(_chunks, { type: _rec.mimeType || "audio/webm" }));
    };
    _rec.start();
    _recording = true; btn.classList.add("rec"); btn.textContent = "⏹";
    toast("Sun raha hoon… stop ke liye dobara tap karein");
  } catch { toast("Mic access nahi mila"); }
}
async function sendVoice(blob) {
  const youSaid = addBubble("🎙️ …", "user");
  const botSaid = addBubble("…", "bot");
  try {
    const fd = new FormData();
    fd.append("file", blob, "audio.webm");
    if (!state.activeDraftId) fd.append("session_id", state.sessionId);
    const path = state.activeDraftId
      ? `/bill-drafts/${state.activeDraftId}/voice-answer`
      : "/voice/chat";
    const res = await fetch(path, { method: "POST", body: fd });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    const transcript = typeof data.transcript === "object"
      ? data.transcript.text : data.transcript;
    youSaid.textContent = "🎙️ " + (transcript || "…");
    if (state.activeDraftId) {
      const draft = data.draft;
      botSaid.textContent = billAssistantReply(draft);
      appendDraftCard(draft);
    } else {
      botSaid.innerHTML = linkify(data.reply || "");
      if (data.audio_b64) { try { new Audio("data:audio/mp3;base64," + data.audio_b64).play(); } catch {} }
    }
  } catch (e) {
    botSaid.textContent = "Voice error — phir se boliye.";
  }
  $("#log").scrollTop = $("#log").scrollHeight;
  loadData();  // stay in chat
}

/* ---------- MODALS ---------- */
function showModal(html) {
  $("#modalCard").innerHTML = html;
  $("#modalRoot").classList.remove("hidden");
}
function closeModal() {
  $("#modalRoot").classList.add("hidden");
  $("#modalCard").classList.remove("bill-modal");
}
$("#modalBackdrop").onclick = closeModal;

function addPartyModal() {
  let type = state.tab;
  showModal(`<h3>New ${type === "customer" ? "Customer" : "Supplier"}</h3>
    <div class="field"><label>Type</label>
      <div class="seg" id="segType">
        <button class="${type === "customer" ? "on" : ""}" data-t="customer">Customer</button>
        <button class="${type === "supplier" ? "on" : ""}" data-t="supplier">Supplier</button>
      </div></div>
    <div class="field"><label>Name</label><input id="pName" placeholder="e.g. Rahul Kirana" /></div>
    <div class="field"><label>Phone (optional)</label><input id="pPhone" placeholder="10-digit mobile" inputmode="numeric" /></div>
    <button class="btn-primary" id="saveParty">Create account</button>
    <button class="btn-ghost" id="cancel">Cancel</button>`);
  document.querySelectorAll("#segType button").forEach((b) =>
    b.onclick = () => { type = b.dataset.t; document.querySelectorAll("#segType button").forEach((x) => x.classList.toggle("on", x === b)); });
  $("#cancel").onclick = closeModal;
  $("#saveParty").onclick = async () => {
    const name = $("#pName").value.trim(); if (!name) return toast("Name daaliye");
    const phone = $("#pPhone").value.trim() || null;
    try { await postJSON("/parties", { name, type, phone }); closeModal(); toast(name + " ka khaata ban gaya"); state.tab = type; refresh(); }
    catch (e) { toast("Error: " + e.message); }
  };
}

function txnModal(p, txnType) {
  const title = txnType === "credit" ? "You gave (udhaar diya)" : "You got (payment mila)";
  showModal(`<h3>${title}</h3>
    <div class="field"><label>${esc(p.name)} · Amount ₹</label>
      <input id="amt" type="number" inputmode="decimal" placeholder="0" /></div>
    <div class="field"><label>Note (optional)</label><input id="note" placeholder="e.g. doodh, kirana" /></div>
    <button class="btn-primary" id="saveTxn">Save</button>
    <button class="btn-ghost" id="cancel">Cancel</button>`);
  $("#cancel").onclick = closeModal;
  $("#amt").focus();
  $("#saveTxn").onclick = async () => {
    const amount = parseFloat($("#amt").value); if (!(amount > 0)) return toast("Amount daaliye");
    try {
      await postJSON("/transactions", { party_id: p.id, type: txnType, amount, note: $("#note").value.trim() || null });
      closeModal(); toast("Saved"); await loadData(); openDetail(p.id);
    } catch (e) { toast("Error: " + e.message); }
  };
}

function phoneModal(p) {
  showModal(`<h3>Add phone — ${esc(p.name)}</h3>
    <div class="field"><label>Phone (10-digit)</label><input id="ph" inputmode="numeric" placeholder="9876543210" /></div>
    <button class="btn-primary" id="savePh">Save</button>
    <button class="btn-ghost" id="cancel">Cancel</button>`);
  $("#cancel").onclick = closeModal; $("#ph").focus();
  $("#savePh").onclick = async () => {
    try { await postJSON(`/parties/${p.id}/phone`, { phone: $("#ph").value.trim() }); closeModal(); toast("Phone saved"); await loadData(); openDetail(p.id); }
    catch { toast("10-digit mobile daaliye"); }
  };
}

function reminderModal(p) {
  const tomorrow = new Date(Date.now() + 864e5);
  const def = tomorrow.toISOString().slice(0, 10) + "T10:00";
  showModal(`<h3>Reminder — ${esc(p.name)}</h3>
    <div class="field"><label>When</label><input id="due" type="datetime-local" value="${def}" /></div>
    <div class="field"><label>Amount ₹ (optional)</label><input id="rAmt" type="number" placeholder="0" /></div>
    <div class="field"><label>Channel</label>
      <div class="seg" id="ch"><button class="on" data-c="call">📞 Call</button><button data-c="whatsapp">💬 WhatsApp</button></div></div>
    <div class="field"><label>Note</label><input id="rMsg" placeholder="payment ke liye yaad dilao" /></div>
    <button class="btn-primary" id="saveRem">Set reminder</button>
    <button class="btn-ghost" id="cancel">Cancel</button>`);
  let channel = "call";
  document.querySelectorAll("#ch button").forEach((b) =>
    b.onclick = () => { channel = b.dataset.c; document.querySelectorAll("#ch button").forEach((x) => x.classList.toggle("on", x === b)); });
  $("#cancel").onclick = closeModal;
  $("#saveRem").onclick = async () => {
    const due = $("#due").value; if (!due) return toast("Time daaliye");
    const amount = parseFloat($("#rAmt").value) || null;
    try {
      await postJSON("/reminders", { party_id: p.id, due_at: due + ":00", message: $("#rMsg").value.trim() || null, amount, channel });
      closeModal(); toast("Reminder set"); loadData();
    } catch (e) { toast("Error: " + e.message); }
  };
}

/* ---------- wire static nav ---------- */
document.querySelectorAll(".tab").forEach((t) =>
  t.onclick = () => { state.tab = t.dataset.tab; document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === t)); renderHome(); });
document.querySelectorAll(".bn-item").forEach((b) =>
  b.onclick = () => { state.nav = b.dataset.nav; state.search = ""; render(); });
$("#navChat").onclick = () => openChat();
$("#navAI").onclick = () => openChat();
$("#navReminders").onclick = () => openReminders();
$("#navTxns").onclick = () => { state.nav = "home"; render(); };

/* ---------- go ---------- */
refresh();
