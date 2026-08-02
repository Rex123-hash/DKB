/* ===== DukanBook web replica — talks to the FastAPI backend (same origin) ===== */
const API = ""; // same origin as FastAPI

const state = {
  tab: "customer",      // customer | supplier
  nav: "home",          // home | stock | bills | menu
  billTab: "sale",      // sale | purchase
  activeDraftId: null,
  // Speak-while-typing. A spoken question is always answered aloud
  // regardless of this switch; it only governs typed messages.
  speakReplies: localStorage.getItem("db_speak_replies") === "1",
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
const postJSON = (path, body, options = {}) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), ...options });
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
const FA_ICONS = {
  back: ["fa-solid", "fa-arrow-left"], camera: ["fa-solid", "fa-camera"],
  mic: ["fa-solid", "fa-microphone-lines"], stop: ["fa-solid", "fa-stop"],
  send: ["fa-solid", "fa-paper-plane"], shield: ["fa-solid", "fa-shield-halved"],
  search: ["fa-solid", "fa-magnifying-glass"], phone: ["fa-solid", "fa-phone"],
  whatsapp: ["fa-brands", "fa-whatsapp"], users: ["fa-solid", "fa-user-group"],
  box: ["fa-solid", "fa-box"], warning: ["fa-solid", "fa-triangle-exclamation"],
  envelope: ["fa-solid", "fa-envelope-open-text"], calculator: ["fa-solid", "fa-calculator"],
  scan: ["fa-solid", "fa-file-circle-plus"], receipt: ["fa-solid", "fa-receipt"],
  sale: ["fa-solid", "fa-arrow-up-from-bracket"], purchase: ["fa-solid", "fa-arrow-down"],
  check: ["fa-solid", "fa-circle-check"], pen: ["fa-solid", "fa-pen-to-square"],
  profile: ["fa-solid", "fa-user"], book: ["fa-solid", "fa-book-open"],
  clock: ["fa-solid", "fa-clock"], trash: ["fa-solid", "fa-trash-can"],
  card: ["fa-solid", "fa-address-card"], message: ["fa-solid", "fa-message"],
  gst: ["fa-solid", "fa-indian-rupee-sign"], image: ["fa-solid", "fa-image"],
  speakerOn: ["fa-solid", "fa-volume-high"], speakerOff: ["fa-solid", "fa-volume-xmark"],
};
const chatIcon = (name) => {
  const classes = FA_ICONS[name];
  return classes ? `<i class="${classes.join(" ")}" aria-hidden="true"></i>` : "";
};

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), 2200);
}

let activeResponseAbort = null;
let activeResponseAudio = null;
let activeResponseAudioResolve = null;
let activeSpeechFetchAbort = null;
let responseSpeechQueue = [];
let responseSpeechQueueRunning = false;
let responseSpeechEpoch = 0;
const wasAborted = (error) => !!error && (
  error.name === "AbortError" || error.code === 20 || /abort|cancel/i.test(error.message || "")
);
function setStopResponseVisible(visible) {
  const stop = $("#stopResponseBtn");
  const safe = $("#privateIndicator");
  if (stop) stop.classList.toggle("hidden", !visible);
  if (safe) safe.classList.toggle("hidden", visible);
}
// Speak text the browser composed itself (bill replies). `force` is used when
// the shopkeeper asked by voice, which is always answered aloud whatever the
// speaker toggle says.
function speakText(text, force = false) {
  if (!force && !state.speakReplies) return;
  if (!text) return;
  responseSpeechQueue.push({
    text: String(text).slice(0, 2000),
    epoch: responseSpeechEpoch,
  });
  drainResponseSpeechQueue();
}

function queueReplyAudio(base64) {
  if (!base64) return;
  responseSpeechQueue.push({ audioBase64: base64, epoch: responseSpeechEpoch });
  drainResponseSpeechQueue();
}

async function drainResponseSpeechQueue() {
  if (responseSpeechQueueRunning) return;
  responseSpeechQueueRunning = true;
  try {
    while (responseSpeechQueue.length) {
      const job = responseSpeechQueue.shift();
      if (!job || job.epoch !== responseSpeechEpoch) continue;
      setStopResponseVisible(true);
      let base64 = job.audioBase64;
      if (!base64 && job.text) {
        activeSpeechFetchAbort = new AbortController();
        try {
          const res = await postJSON(
            "/speak",
            { text: job.text },
            { signal: activeSpeechFetchAbort.signal },
          );
          base64 = res && res.audio_b64;
        } catch (error) {
          if (!wasAborted(error)) {
            // Keep the written reply when speech synthesis is unavailable.
          }
        } finally {
          activeSpeechFetchAbort = null;
        }
      }
      if (base64 && job.epoch === responseSpeechEpoch) {
        await playReplyAudio(base64, job.epoch);
      }
    }
  } finally {
    responseSpeechQueueRunning = false;
    if (!activeResponseAbort && !activeResponseAudio && !responseSpeechQueue.length) {
      setStopResponseVisible(false);
    }
  }
}

function playReplyAudio(base64, epoch = responseSpeechEpoch) {
  return new Promise((resolve) => {
    if (!base64 || epoch !== responseSpeechEpoch) {
      resolve();
      return;
    }
    let audio = null;
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      if (activeResponseAudioResolve === finish) activeResponseAudioResolve = null;
      if (activeResponseAudio === audio) activeResponseAudio = null;
      resolve();
    };
    try {
      audio = new Audio("data:audio/mp3;base64," + base64);
      activeResponseAudio = audio;
      activeResponseAudioResolve = finish;
      audio.onended = finish;
      audio.onerror = finish;
      setStopResponseVisible(true);
      audio.play().catch(() => {
        finish();
        toast("Browser ne awaaz block ki — screen par ek baar tap karke dobara try kijiye");
      });
    } catch {
      finish();
    }
  });
}

function cancelResponseSpeech() {
  const hadSpeech = !!(
    activeResponseAudio || activeSpeechFetchAbort || responseSpeechQueue.length
  );
  responseSpeechEpoch += 1;
  responseSpeechQueue = [];
  if (activeSpeechFetchAbort) {
    activeSpeechFetchAbort.abort();
    activeSpeechFetchAbort = null;
  }
  if (activeResponseAudio) {
    activeResponseAudio.pause();
    activeResponseAudio.currentTime = 0;
    activeResponseAudio = null;
  }
  if (activeResponseAudioResolve) {
    const resolveCurrent = activeResponseAudioResolve;
    activeResponseAudioResolve = null;
    resolveCurrent();
  }
  return hadSpeech;
}

function stopActiveAIResponse() {
  if (activeResponseAbort) activeResponseAbort.abort();
  if (cancelResponseSpeech()) {
    addBubble("Voice response stopped. Aap sawaal ko correct karke dobara pooch sakte hain.", "bot");
  }
  setStopResponseVisible(false);
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
  view().classList.remove("chat-screen");
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
      <div class="search"><span class="mag">${chatIcon("search")}</span>
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
  const phone = p.phone ? ` · ${chatIcon("phone")} ${esc(p.phone)}` : "";
  return `<div class="row" data-pid="${p.id}">
      <div class="avatar">${initials(p.name)}</div>
      <div class="meta"><div class="nm">${esc(p.name)}</div>
        <div class="sub">${label}${phone}</div></div>
      <div class="bal ${cls}">${fmt(bal)}</div>
    </div>`;
}
function emptyHTML(noun) {
  return `<div class="empty"><div class="em-ico">${chatIcon("users")}</div>
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
    ? `<a class="chip" href="tel:+91${p.phone}">${chatIcon("phone")} Call ${p.phone}</a>
       <a class="chip" href="https://wa.me/91${p.phone}" target="_blank">${chatIcon("whatsapp")} WhatsApp</a>`
    : `<button class="chip" id="addPhone">${chatIcon("phone")} Add phone</button>`;

  view().innerHTML = `
    <div class="detail-head">
      <button class="back" id="back" aria-label="Back">${chatIcon("back")}</button>
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
      <button class="chip" id="remindBtn">${chatIcon("clock")} Set reminder</button>
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
        <div class="avatar">${chatIcon("box")}</div>
        <div class="meta"><div class="nm">${esc(p.name)}</div>
          <div class="sub">Updated by confirmed purchase/sale bills</div></div>
        <div class="bal ${Number(p.quantity) < 0 ? "give" : "get"}">${esc(String(p.quantity))} ${esc(p.unit || "")}</div>
      </div>`).join("")}</div>` : `<div class="empty"><div class="em-ico">${chatIcon("box")}</div>
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
    view().innerHTML = `<div class="empty"><div class="em-ico">${chatIcon("warning")}</div>
      <div class="em-title">Bills could not be loaded</div>
      <div class="em-sub">Please check the connection and try again.</div></div>`;
    return;
  }
  const tabLabel = state.billTab === "sale" ? "Sale" : "Purchase";
  view().innerHTML = `
    <div class="bill-head">
      <div><div class="screen-title">Manage Bills</div>
        <div class="screen-sub">Created and verified with the DukanBook AI Assistant</div></div>
      <button class="scan-cta" id="scanFromBills">${chatIcon("scan")} Scan bill</button>
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
      `<div class="empty bill-empty"><div class="em-ico">${chatIcon("envelope")}</div>
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
      <div class="detail-head"><button class="back" id="back" aria-label="Back">${chatIcon("back")}</button>
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
  view().innerHTML = `<div class="detail-head"><button class="back" id="back" aria-label="Back">${chatIcon("back")}</button>
      <div class="screen-title">Cashbook</div></div>
    ${entries.length ? `<div class="list">${entries.map((entry) => `
      <div class="txn"><div><div>${esc(entry.note || entry.bill_number || "Bill")}</div>
        <div class="d">${esc(entry.entry_date || "")}</div></div>
        <div class="a ${entry.direction === "in" ? "get" : "give"}">${entry.direction === "in" ? "+" : "−"}${fmtPaise(entry.amount_paise)}</div>
      </div>`).join("")}</div>` : `<div class="empty"><div class="em-ico">${chatIcon("calculator")}</div>
      <div class="em-title">No cash entries</div><div class="em-sub">Paid bills appear here automatically.</div></div>`}`;
  $("#back").onclick = () => { state.nav = "bills"; renderBills(); };
}

/* ---------- MENU ---------- */
function renderMenu() {
  const items = [
    ["profile", "Profile"], ["book", "Cashbook"], ["clock", "Reminders / Call requests", () => openReminders()],
    ["message", "AI Assistant", () => openChat()], ["trash", "Bin"], ["card", "Visiting Cards"],
    ["phone", "Call Us"], ["envelope", "Mail Us"],
  ];
  view().innerHTML = `
    <div class="menu-head"><div class="mav"></div>
      <div><div class="mn">DukanBook</div><div class="me">Aapka digital khata</div></div></div>
    ${items.map((it, i) => `<div class="menu-item" data-mi="${i}">
        <span class="mi">${chatIcon(it[0])}</span><span>${it[1]}</span></div>`).join("")}`;
  document.querySelectorAll("[data-mi]").forEach((el) => {
    const it = items[Number(el.dataset.mi)];
    el.onclick = it[2] || (() => toast(it[1] + " — coming soon"));
  });
}

/* ---------- REMINDERS ---------- */
async function openReminders() {
  view().classList.remove("chat-screen");
  $("#tabs").style.display = "none";
  view().innerHTML = `<div class="spinner"></div>`;
  let rem = [];
  try { rem = await getReminders("pending"); } catch {}
  view().innerHTML = `
    <div class="detail-head"><button class="back" id="back" aria-label="Back">${chatIcon("back")}</button>
      <div style="font-size:1.2rem;font-weight:700">Reminders & Call requests</div></div>
    <div class="list">${rem.length ? rem.map(remHTML).join("")
      : `<div class="empty"><div class="em-ico">${chatIcon("clock")}</div><div class="em-title">No pending reminders</div>
         <div class="em-sub">Ask the AI: "kal Rahul ko 500 ke liye call karna".</div></div>`}</div>`;
  $("#back").onclick = () => { state.nav = "menu"; render(); };
  document.querySelectorAll("[data-done]").forEach((b) =>
    b.onclick = async () => { await postJSON(`/reminders/${b.dataset.done}/done`, {}); toast("Marked done"); openReminders(); });
}
function remHTML(r) {
  const amt = r.amount ? " · " + fmt(r.amount) : "";
  const wa = r.whatsapp_link ? `<a class="chip" href="${r.whatsapp_link}" target="_blank">${chatIcon("whatsapp")} WhatsApp</a>` : "";
  const call = r.call_link ? `<a class="chip" href="${r.call_link}">${chatIcon("phone")} Call</a>` : "";
  return `<div style="border-bottom:1px solid var(--line);padding:10px 4px">
    <div style="display:flex;justify-content:space-between">
      <div><b>${esc(r.party_name)}</b>${amt}</div>
      <div class="d" style="font-size:.74rem;color:var(--muted)">${(r.due_at || "").replace("T", " ").slice(0, 16)}</div></div>
    ${r.message ? `<div class="sub" style="font-size:.82rem;color:var(--muted);margin:3px 0">${esc(r.message)}</div>` : ""}
    <div class="chip-row" style="margin-top:6px">${call}${wa}
      <button class="chip" data-done="${r.id}">${chatIcon("check")} Done</button></div></div>`;
}

/* ---------- CHAT (the same AI Assistant handles khata + scanned bills) ---------- */
async function openChat() {
  $("#tabs").style.display = "none";
  view().classList.add("chat-screen");
  setActive("ai");
  const micBtn = state.voice
    ? `<button id="micBtn" class="composer-tool" type="button" aria-label="Speak your message" title="Speak in Hindi, English or Hinglish">${chatIcon("mic")}</button>` : "";
  view().innerHTML = `
    <div class="chat-wrap">
      <header class="chat-head">
        <button class="chat-back" id="back" type="button" aria-label="Back to menu">${chatIcon("back")}</button>
        <div class="assistant-avatar" aria-hidden="true"><span>AI</span></div>
        <div class="assistant-identity">
          <div class="assistant-name">DukanBook Assistant</div>
          <div class="assistant-status"><span></span> Online · Hindi, English & Hinglish</div>
        </div>
        <button class="speak-toggle${state.speakReplies ? " on" : ""}" id="speakToggle" type="button"
          aria-pressed="${state.speakReplies}"
          title="${state.speakReplies ? "Assistant bolega bhi" : "Sirf likhega; voice sawal ka jawab phir bhi bolega"}">
          ${chatIcon(state.speakReplies ? "speakerOn" : "speakerOff")}
        </button>
        <div class="private-chip" id="privateIndicator" title="Bills are posted only after your confirmation">
          ${chatIcon("shield")}<span>Safe</span>
        </div>
        <button class="stop-response hidden" id="stopResponseBtn" type="button" aria-label="Stop AI response">
          ${chatIcon("stop")}<span>Stop</span>
        </button>
      </header>
      <div class="chat-log" id="log" role="log" aria-live="polite" aria-label="Conversation with DukanBook Assistant">
        <div class="chat-date"><span>Today</span></div>
        <div class="message-row bot">
          <div class="message-avatar" aria-hidden="true">AI</div>
          <div class="message-content">
            <div class="bubble bot"><b>Namaste! Main aapka DukanBook AI hoon.</b>
Khata update karein, reminder banayein, business sawal poochhein, ya handwritten bill scan karein. Main details verify karke confirmation ke baad hi accounts aur stock update karunga.</div>
            <div class="message-meta">DukanBook AI · now</div>
          </div>
        </div>
        <div class="quick-actions" id="quickActions" aria-label="Suggested actions">
          <button type="button" data-chat-action="scan"><span>${chatIcon("scan")}</span> Scan a bill</button>
          <button type="button" data-suggest="Mere pending reminders dikhao"><span>${chatIcon("clock")}</span> Check reminders</button>
          <button type="button" data-suggest="GST registration kab zaroori hoti hai?"><span>${chatIcon("gst")}</span> Ask about GST</button>
        </div>
      </div>
      <div class="composer-shell" id="composerShell">
        <div class="composer-note">${chatIcon("shield")} Nothing is posted without your confirmation</div>
        <div class="chat-input">
        <button id="cameraBtn" class="composer-tool" type="button" aria-label="Scan a handwritten bill" title="Scan handwritten bill">${chatIcon("camera")}</button>
        ${micBtn}
        <input id="billImage" class="hidden" type="file" accept="image/jpeg,image/png,image/webp" capture="environment" />
        <input id="chatBox" aria-label="Message DukanBook Assistant" autocomplete="off" placeholder="Ask or enter bill details..." />
        <button id="sendBtn" class="send-button" type="button" aria-label="Send message" disabled>${chatIcon("send")}</button>
        </div>
      </div>
    </div>`;
  $("#back").onclick = () => { state.nav = "menu"; render(); };
  $("#stopResponseBtn").onclick = stopActiveAIResponse;
  $("#speakToggle").onclick = (event) => {
    state.speakReplies = !state.speakReplies;
    localStorage.setItem("db_speak_replies", state.speakReplies ? "1" : "0");
    const button = event.currentTarget;
    button.classList.toggle("on", state.speakReplies);
    button.setAttribute("aria-pressed", String(state.speakReplies));
    button.innerHTML = chatIcon(state.speakReplies ? "speakerOn" : "speakerOff");
    if (!state.speakReplies) {
      cancelResponseSpeech();
      setStopResponseVisible(false);
    }
    toast(state.speakReplies
      ? "Awaaz on — main likhe hue sawaal ka jawab bhi bolunga"
      : "Awaaz off — voice se poochenge to phir bhi bolunga");
  };
  let sending = false;
  const syncComposer = () => {
    const hasMessage = !!$("#chatBox").value.trim();
    $("#sendBtn").disabled = sending || !hasMessage;
    $("#composerShell").classList.toggle("has-text", hasMessage);
  };
  const send = async () => {
    const box = $("#chatBox"); const msg = box.value.trim();
    if (!msg || sending) return;
    sending = true; syncComposer();
    $("#composerShell").classList.add("busy");
    $("#log").setAttribute("aria-busy", "true");
    activeResponseAbort = new AbortController();
    setStopResponseVisible(true);
    addBubble(msg, "user"); box.value = ""; box.focus();
    syncComposer();
    const typing = addBubble("", "bot");
    setBubbleLoading(typing, "Thinking");
    try {
      if (state.activeDraftId) {
        if (/^(cancel|exit|stop).*(bill)?$/i.test(msg)) {
          clearActiveDraft();
          stopBubbleLoading(typing);
          typing.textContent = "Bill mode closed. The draft is still saved in Bills history for later review.";
          speakText(typing.textContent);
        } else {
          const draft = await postJSON(`/bill-drafts/${state.activeDraftId}/answer`, { answer: msg }, { signal: activeResponseAbort.signal });
          stopBubbleLoading(typing);
          const billReply = billAssistantReply(draft);
          typing.textContent = billReply;
          appendDraftCard(draft);
          speakText(billReply);
        }
      } else {
        const r = await postJSON("/chat", {
          message: msg, session_id: state.sessionId, speak: state.speakReplies,
        }, { signal: activeResponseAbort.signal });
        stopBubbleLoading(typing);
        typing.innerHTML = linkify(r.reply);
        if (r.audio_b64) queueReplyAudio(r.audio_b64);
      }
    } catch (error) {
      stopBubbleLoading(typing);
      typing.textContent = wasAborted(error)
        ? "Response stopped. Aap mujhe correct karke dobara pooch sakte hain."
        : "Backend se baat nahi ho payi. Please try again.";
    }
    finally {
      stopBubbleLoading(typing);
      activeResponseAbort = null;
      if (!activeResponseAudio && !responseSpeechQueueRunning) {
        setStopResponseVisible(false);
      }
      sending = false;
      $("#composerShell").classList.remove("busy");
      $("#log").setAttribute("aria-busy", "false");
      syncComposer();
    }
    $("#log").scrollTop = $("#log").scrollHeight;
    loadData();  // ledger may have changed — refresh data but stay in chat
  };
  $("#sendBtn").onclick = send;
  ["input", "change", "keyup"].forEach((eventName) =>
    $("#chatBox").addEventListener(eventName, syncComposer));
  $("#chatBox").addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });
  document.querySelectorAll("[data-suggest]").forEach((button) => {
    button.onclick = () => {
      $("#chatBox").value = button.dataset.suggest;
      syncComposer();
      $("#chatBox").focus();
    };
  });
  $("[data-chat-action='scan']").onclick = () => $("#billImage").click();
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
  const row = document.createElement("div");
  row.className = "message-row " + who;
  if (who === "bot") {
    const avatar = document.createElement("div");
    avatar.className = "message-avatar";
    avatar.setAttribute("aria-hidden", "true");
    avatar.textContent = "AI";
    row.appendChild(avatar);
  }
  const content = document.createElement("div");
  content.className = "message-content";
  const b = document.createElement("div");
  b.className = "bubble " + who; b.textContent = text;
  const meta = document.createElement("div");
  meta.className = "message-meta";
  meta.textContent = who === "bot" ? "DukanBook AI · now" : "You · now";
  content.appendChild(b); content.appendChild(meta); row.appendChild(content);
  $("#log").appendChild(row); $("#log").scrollTop = $("#log").scrollHeight;
  return b;
}


function setBubbleLoading(bubble, label) {
  bubble.classList.add("loading");
  bubble.setAttribute("aria-busy", "true");
  bubble.innerHTML = `<span class="chat-loader" aria-hidden="true"></span><span>${esc(label)}</span>`;
}

function stopBubbleLoading(bubble) {
  bubble.classList.remove("loading");
  bubble.removeAttribute("aria-busy");
}

function openBillScannerInChat() {
  openChat().then(() => {
    addBubble("Please photograph the full handwritten bill in good light. Keep all four corners visible.", "bot");
    $("#billImage").click();
  });
}

async function scanBillInChat(file) {
  const uploaded = addBubble(file.name, "user");
  uploaded.innerHTML = `${chatIcon("image")} ${esc(file.name)}`;
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
    typing.textContent = (draft.duplicate && !draft.reprocessed)
      ? "Yehi scan pehle se saved tha, wahi khol diya."
      : billAssistantReply(draft);
    appendDraftCard(draft);
  } catch (e) {
    // A busy or slow AI service is not an unreadable bill. Saying so sends the
    // shopkeeper off to re-photograph a photo that was fine.
    const detail = String((e && e.message) || "");
    typing.textContent = /extraction failed|502|429|timeout|timed out/i.test(detail)
      ? "AI service abhi busy hai, bill padha nahi ja saka. Thodi der baad dobara bhejiye — photo theek hai."
      : "Ye image padhi nahi ja saki. Saaf JPEG, PNG ya WebP photo bhejiye jisme poora bill dikhe.";
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
  bill_type: "Ye aapki sale hai ya purchase?",
  bill_date: "Bill par date kya hai? (jaise 18 Jan 2026, 18/01/2026, ya 'kal')",
  "party.name": "Party ka naam kya likhun?",
  gst_mode: "Ye GST bill hai ya non-GST?",
  gst_rate: "GST kitne % ka hai?",
  tax_scheme: "Same state ka hai (CGST+SGST) ya doosre state ka (IGST)?",
  payment_status: "Payment ho gayi, udhaar hai, ya thodi si aayi hai?",
  paid_amount_paise: "Kitna paisa mila hai?",
  items: "Ek bhi item nahi padh paaya — Review mein add kar dijiye.",
  "items.quantities": "Item ki quantity saaf nahi dikhi. Sab 1-1 hain? 'all 1' likh dijiye, warna Review mein bhar dijiye.",
  "items.prices": "Naam to mil gaye, par rate saaf nahi dikha. Review mein rate bhar dijiye.",
};
function missingQuestion(path) {
  if (missingLabels[path]) return missingLabels[path];
  if (/items\.\d+\.name/.test(path)) return "Ek item ka naam nahi padha — kya likhun?";
  if (/items\.\d+\.quantity/.test(path)) return "Ek item ki quantity nahi dikhi — kitni hai?";
  if (/items\.\d+\.unit_price_paise/.test(path)) return "Ek item ka rate nahi dikha — kitna hai?";
  return `Zara ${path} confirm kar dijiye.`;
}

// Short noun-phrases so several gaps can be asked for in one natural sentence
// instead of interrogating the shopkeeper one field at a time.
const missingShort = {
  bill_type: "sale ya purchase",
  bill_date: "bill ki date",
  "party.name": "party ka naam",
  gst_mode: "GST ya non-GST",
  gst_rate: "GST %",
  tax_scheme: "CGST+SGST ya IGST",
  payment_status: "paid, udhaar ya partial",
  paid_amount_paise: "kitna paid hua",
  items: "kam se kam ek item",
  "items.quantities": "items ki quantity",
  "items.prices": "items ka rate",
};
function missingShortLabel(path) {
  if (missingShort[path]) return missingShort[path];
  if (/items\.\d+\.name/.test(path)) return "item ka naam";
  if (/items\.\d+\.quantity/.test(path)) return "item ki quantity";
  if (/items\.\d+\.unit_price_paise/.test(path)) return "item ka rate";
  return path;
}
// Ask for exactly one thing at a time. Listing every gap at once reads like a
// form, not a conversation.
function missingAsk(fields) {
  const pending = (fields || []).filter(Boolean);
  return pending.length ? missingQuestion(pending[0]) : "";
}

const appliedLabels = {
  bill_type: (d) => d.bill_type === "purchase" ? "Purchase" : "Sale",
  bill_date: (d) => d.bill_date,
  gst_mode: (d) => d.gst_mode === "gst" ? "GST" : "Non-GST",
  gst_rate: (d) => `${d.gst_rate}% GST`,
  tax_scheme: (d) => d.tax_scheme === "igst" ? "IGST" : "CGST+SGST",
  payment_status: (d) => ({ paid: "Paid", credit: "Udhaar", partial: "Partial" })[d.payment_status],
  paid_amount_paise: (d) => `${fmtPaise(d.paid_amount_paise)} paid`,
  "party.name": (d) => (d.party || {}).name,
  "party.phone": (d) => (d.party || {}).phone,
  "party.gstin": (d) => "GSTIN",
};
// "Sale aur udhaar note kar liya." — proof it actually heard the last message.
function appliedNote(draft) {
  const data = draft.data || {};
  const parts = (draft.applied_fields || [])
    .map((field) => appliedLabels[field] && appliedLabels[field](data))
    .filter(Boolean);
  if (!parts.length) return "";
  const shown = parts.slice(0, 3);
  const list = shown.length > 1
    ? shown.slice(0, -1).join(", ") + " aur " + shown[shown.length - 1]
    : shown[0];
  return `${list} note kar liya.`;
}
function billAssistantReply(draft) {
  const calc = draft.calculation || {};
  if (draft.answer_applied === false) {
    const ask = missingAsk(calc.missing_fields);
    return ask
      ? `Ye main samajh nahi paaya. ${ask}`
      : "Ye main samajh nahi paaya. Review kholkar detail seedhe edit kar lijiye.";
  }
  const errors = (calc.warnings || []).filter((w) => w.severity === "error");
  const cautions = (calc.warnings || []).filter((w) => w.severity === "warning");
  const notBill = errors.find((warning) =>
    warning.code === "not_a_bill" || warning.code === "uncertain_document");
  if (notBill) {
    return `This image cannot be processed as a bill: ${notBill.message} Please upload a clear sale or purchase bill.`;
  }
  if ((calc.missing_fields || []).length) {
    const note = appliedNote(draft);
    if (note) return `${note} ${missingAsk(calc.missing_fields)}`;
    const read = (draft.data || {}).items || [];
    const opener = read.length
      ? `Bill padh liya — ${read.length} item, total ${fmtPaise(calc.subtotal_paise)}.`
      : "Scan save kar liya.";
    return `${opener} ${missingAsk(calc.missing_fields)}`;
  }
  if (errors.length) {
    return `Hisaab mein farak hai: ${errors[0].message} Neeche "Use DukanBook's maths" tap kijiye, ya Review mein khud theek kar lijiye.`;
  }
  if (cautions.length) {
    return `I read the bill, but one value needs a careful look: ${cautions[0].message} Please check it in Review before confirming.`;
  }
  return `The bill is ready for review. I independently verified the total as ${fmtPaise(calc.grand_total_paise)}. Please review before confirming.`;
}


// One tap to accept DukanBook's own arithmetic in place of what the bill says.
function withVerifiedMaths(draft) {
  const data = JSON.parse(JSON.stringify(draft.data || {}));
  const calc = draft.calculation || {};
  (data.items || []).forEach((item, index) => {
    const line = (calc.lines || [])[index] || {};
    if (line.calculated_total_paise != null) item.written_total_paise = line.calculated_total_paise;
  });
  data.written_subtotal_paise = calc.subtotal_paise;
  data.written_grand_total_paise = calc.grand_total_paise;
  return data;
}
const hasMathsIssue = (draft) =>
  ((draft.calculation || {}).warnings || []).some((w) =>
    w.severity === "error" && /mismatch/.test(w.code || ""));

async function acceptVerifiedMaths(draft) {
  try {
    const saved = await putJSON(`/bill-drafts/${draft.id}`, { data: withVerifiedMaths(draft) });
    addBubble("Theek hai — maine apna verified hisaab laga diya. Ab review karke confirm kar dijiye.", "bot");
    appendDraftCard(saved);
    speakText("Maine apna verified hisaab laga diya.");
  } catch { toast("Verified totals apply nahi ho paye"); }
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
    ? missingAsk(calc.missing_fields) : null;
  card.innerHTML = `
    <div class="draft-card-top"><span>${data.bill_type === "purchase" ? `${chatIcon("purchase")} Purchase` : data.bill_type === "sale" ? `${chatIcon("sale")} Sale` : `${chatIcon("receipt")} Bill draft`}</span>
      <b>${fmtPaise(calc.grand_total_paise)}</b></div>
    <div class="draft-card-party">${esc((data.party || {}).name || "Party not identified")} · ${(data.items || []).length} item(s)</div>
    <div class="draft-status ${draft.status === "ready_for_review" ? "ready" : ""}">
      ${draft.status === "ready_for_review" ? `${chatIcon("check")} Ready to review` :
        `${(calc.missing_fields || []).length} missing · ${errors.length} maths issue(s)`}</div>
    ${nextQuestion ? `<div class="draft-question"><b>AI asks:</b> ${esc(nextQuestion)}</div>` : ""}
    <div class="draft-actions">
      ${nextQuestion ? `<button data-answer>${chatIcon("mic")} Type or speak answer</button>` : ""}
      ${!nextQuestion && hasMathsIssue(draft) ? `<button data-accept-maths>${chatIcon("calculator")} Use DukanBook's maths</button>` : ""}
      <button data-review>${nextQuestion ? `${chatIcon("pen")} Fill details manually` : `${chatIcon("check")} Review & confirm`}</button>
    </div>
    <button data-exit class="draft-exit">Exit bill</button>`;
  log.appendChild(card);
  if (card.querySelector("[data-answer]")) {
    card.querySelector("[data-answer]").onclick = () => {
      const input = $("#chatBox");
      const pending = (calc.missing_fields || []).map(missingShortLabel);
      input.placeholder = pending.length > 1
        ? `${pending.slice(0, 3).join(", ")}…`
        : nextQuestion;
      input.focus();
      toast(state.voice
        ? "Type below, or tap the microphone and speak"
        : "Type your answer below");
    };
  }
  if (card.querySelector("[data-accept-maths]")) {
    card.querySelector("[data-accept-maths]").onclick = () => acceptVerifiedMaths(draft);
  }
  card.querySelector("[data-review]").onclick = () => openBillDraftEditor(draft);
  card.querySelector("[data-exit]").onclick = () => {
    clearActiveDraft(); card.remove(); addBubble("Bill mode closed. You can continue using the assistant normally.", "bot");
  };
  log.scrollTop = log.scrollHeight;
}

const rupeesValue = (paise) => paise == null ? "" : (Number(paise) / 100).toFixed(2).replace(/\.00$/, "");
function toPaise(value, nullable = true) {
  // Shopkeepers type "1,200" and "₹1200"; both must mean 1200, never 0.
  const text = String(value ?? "").replace(/[₹,\s]|Rs\.?/gi, "").trim();
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
    `<div class="math-warning ${warning.severity}">${chatIcon("warning")} ${esc(warning.message)}</div>`).join("");
  showModal(`<div class="bill-review">
    <div class="review-title"><div><h3>Review AI bill</h3>
      <div class="screen-sub">Nothing is posted until you confirm.</div></div>
      <button class="close-x" id="cancel">×</button></div>
    ${warningHTML}
    ${(calc.missing_fields || []).length ? `<div class="missing-box"><b>AI still needs:</b>
      ${esc((calc.missing_fields || []).map(missingShortLabel).join(", "))}</div>` : ""}
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
      `<button class="btn-ghost math-accept" id="acceptMath">Use DukanBook's maths instead of the written figures</button>` : ""}
    <div class="review-actions">
      <button class="btn-ghost" id="saveDraft">Save & recheck</button>
      <button class="btn-primary" id="confirmDraft">Confirm & post bill</button>
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
      (bill.stock_alerts || []).forEach((alert) =>
        addBubble(`Heads up: ${alert}`, "bot"));
      loadData();
    } catch (e) { toast("Bill could not be posted: review the remaining issue"); }
  };
}

function closeBillModal() {
  $("#modalCard").classList.remove("bill-modal");
  closeModal();
}
function billItemEditorHTML(item, index) {
  // Carry the AI-read per-item GST rate on the row so an exact edit elsewhere
  // in the form never silently discards it.
  return `<div class="bill-item-editor" data-item-row data-gst-rate="${esc(item.gst_rate || "")}">
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
      gst_rate: row.dataset.gstRate || null,
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
let _rec = null, _chunks = [], _recording = false, _recordingStartedAt = 0, _recordingTimer = null;
function supportedRecordingOptions() {
  const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];
  const mimeType = candidates.find((type) => MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(type));
  return mimeType ? { mimeType } : undefined;
}
async function toggleMic(btn) {
  if (_recording && _rec) { _rec.stop(); return; }
  if (!navigator.mediaDevices || !window.MediaRecorder) return toast("Browser mic supported nahi");
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    _rec = new MediaRecorder(stream, supportedRecordingOptions());
    _chunks = [];
    _rec.ondataavailable = (e) => { if (e.data.size) _chunks.push(e.data); };
    _rec.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      clearTimeout(_recordingTimer);
      const durationMs = Math.min(30_000, Math.max(0, Date.now() - _recordingStartedAt));
      _recording = false; btn.classList.remove("rec"); btn.innerHTML = chatIcon("mic");
      await sendVoice(new Blob(_chunks, { type: _rec.mimeType || "audio/webm" }), durationMs);
    };
    _rec.start();
    _recordingStartedAt = Date.now();
    _recordingTimer = setTimeout(() => {
      if (_rec && _rec.state === "recording") _rec.stop();
    }, 30_000);
    _recording = true; btn.classList.add("rec"); btn.innerHTML = chatIcon("stop");
    toast("Sun raha hoon… stop ke liye dobara tap karein");
  } catch { toast("Mic access nahi mila"); }
}
async function sendVoice(blob, durationMs = 0) {
  const youSaid = addBubble("", "user");
  setBubbleLoading(youSaid, "Processing voice");
  const botSaid = addBubble("", "bot");
  setBubbleLoading(botSaid, "Understanding");
  activeResponseAbort = new AbortController();
  setStopResponseVisible(true);
  try {
    const fd = new FormData();
    fd.append("file", blob, "audio.webm");
    fd.append("duration_ms", String(Math.round(durationMs)));
    if (!state.activeDraftId) fd.append("session_id", state.sessionId);
    const path = state.activeDraftId
      ? `/bill-drafts/${state.activeDraftId}/voice-answer`
      : "/voice/chat";
    const res = await fetch(path, { method: "POST", body: fd, signal: activeResponseAbort.signal });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    const transcript = typeof data.transcript === "object"
      ? data.transcript.text : data.transcript;
    stopBubbleLoading(youSaid);
    youSaid.innerHTML = `${chatIcon("mic")} ${esc(transcript || "Voice not detected")}`;
    stopBubbleLoading(botSaid);
    if (state.activeDraftId) {
      const draft = data.draft;
      const spokenReply = billAssistantReply(draft);
      botSaid.textContent = spokenReply;
      appendDraftCard(draft);
      // Asked by voice, answered by voice - whatever the speaker toggle says.
      speakText(spokenReply, true);
    } else {
      botSaid.innerHTML = linkify(data.reply || "");
      if (data.audio_b64) queueReplyAudio(data.audio_b64);
    }
  } catch (e) {
    stopBubbleLoading(youSaid);
    youSaid.innerHTML = `${chatIcon("mic")} Voice not detected`;
    stopBubbleLoading(botSaid);
    botSaid.textContent = wasAborted(e)
      ? "Voice response stopped."
      : "Voice error — phir se boliye.";
  } finally {
    stopBubbleLoading(youSaid);
    stopBubbleLoading(botSaid);
    activeResponseAbort = null;
    if (!activeResponseAudio && !responseSpeechQueueRunning) {
      setStopResponseVisible(false);
    }
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
  const phoneFields = p.phone
    ? `<div class="field"><label>Mobile</label><div>${esc(p.phone)}</div></div>`
    : `<div class="field"><label>Mobile (required)</label><input id="rPhone" inputmode="numeric" placeholder="10-digit mobile" /></div>
       <label class="check-row"><input id="skipPhone" type="checkbox" /> Save without phone (explicit skip)</label>`;
  showModal(`<h3>Reminder — ${esc(p.name)}</h3>
    ${phoneFields}
    <div class="field"><label>When</label><input id="due" type="datetime-local" value="${def}" /></div>
    <div class="field"><label>Amount ₹ (required)</label><input id="rAmt" type="number" min="1" placeholder="500" /></div>
    <div class="field"><label>Channel</label>
      <div class="seg" id="ch"><button class="on" data-c="call">${chatIcon("phone")} Call</button><button data-c="whatsapp">${chatIcon("whatsapp")} WhatsApp</button></div></div>
    <div class="field"><label>Note</label><input id="rMsg" placeholder="payment ke liye yaad dilao" /></div>
    <button class="btn-primary" id="saveRem">Set reminder</button>
    <button class="btn-ghost" id="cancel">Cancel</button>`);
  let channel = "call";
  document.querySelectorAll("#ch button").forEach((b) =>
    b.onclick = () => { channel = b.dataset.c; document.querySelectorAll("#ch button").forEach((x) => x.classList.toggle("on", x === b)); });
  $("#cancel").onclick = closeModal;
  $("#saveRem").onclick = async () => {
    const due = $("#due").value; if (!due) return toast("Time daaliye");
    const amount = parseFloat($("#rAmt").value);
    if (!(amount > 0)) return toast("Positive amount daaliye");
    try {
      const enteredPhone = $("#rPhone")?.value.trim() || "";
      const skipPhone = Boolean($("#skipPhone")?.checked);
      if (!p.phone && !enteredPhone && !skipPhone) return toast("10-digit mobile daaliye ya Skip chuniye");
      if (enteredPhone) await postJSON(`/parties/${p.id}/phone`, { phone: enteredPhone });
      await postJSON("/reminders", { party_id: p.id, due_at: due + ":00", message: $("#rMsg").value.trim() || null, amount, channel, skip_phone: skipPhone });
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
