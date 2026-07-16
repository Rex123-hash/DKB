/* ===== DukanBook web replica — talks to the FastAPI backend (same origin) ===== */
const API = ""; // same origin as FastAPI

const state = {
  tab: "customer",      // customer | supplier
  nav: "home",          // home | stock | bills | menu
  parties: [],
  search: "",
  sessionId: localStorage.getItem("db_sid") || (() => {
    const id = "web-" + Math.random().toString(36).slice(2);
    localStorage.setItem("db_sid", id); return id;
  })(),
};

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

/* ---------- helpers ---------- */
const $ = (sel) => document.querySelector(sel);
const view = () => $("#view");
const fmt = (n) => "₹" + Number(Math.abs(n)).toLocaleString("en-IN", { maximumFractionDigits: 0 });
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

/* ---------- STOCK (branded, like the real app) ---------- */
function renderStock() {
  view().innerHTML = `
    <div class="appbar-row" style="color:var(--ink);padding:4px 2px 12px">
      <div style="font-size:1.2rem;font-weight:700">Stock Management</div></div>
    <div class="stats">
      <div class="col"><div class="n">0</div><div class="t">Products</div></div>
      <div class="col"><div class="n red">0</div><div class="t">Low Stock</div></div>
      <div class="col"><div class="n">₹0</div><div class="t">Purchase Value</div></div>
    </div>
    <div class="empty"><div class="em-ico">📦</div>
      <div class="em-title">No products added</div>
      <div class="em-sub">Stock module is part of the roadmap.<br/>Ledger, reminders and the AI assistant are live.</div></div>`;
}

/* ---------- BILLS (branded) ---------- */
function renderBills() {
  view().innerHTML = `
    <div class="appbar-row" style="color:var(--ink);padding:4px 2px 12px">
      <div style="font-size:1.2rem;font-weight:700">Manage Bills</div></div>
    <div class="stats">
      <div class="col"><div class="n get">₹0</div><div class="t">Total Sales</div></div>
      <div class="col"><div class="n red">₹0</div><div class="t">Total Purchases</div></div>
    </div>
    <div class="empty"><div class="em-ico">🧾</div>
      <div class="em-title">No bills added</div>
      <div class="em-sub">Billing module is part of the roadmap.</div></div>`;
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

/* ---------- CHAT (AI Assistant) — text + voice ---------- */
function openChat() {
  $("#tabs").style.display = "none";
  setActive("ai");
  const micBtn = state.voice
    ? `<button id="micBtn" class="mic" title="Boliye (Hindi / English / Hinglish)">🎙️</button>` : "";
  view().innerHTML = `
    <div class="chat-wrap">
      <div class="detail-head"><button class="back" id="back">←</button>
        <div style="font-size:1.15rem;font-weight:700">🤖 AI Assistant</div></div>
      <div class="chat-log" id="log">
        <div class="bubble bot">Namaste! Khata, reminders ya business sawaal — boliye ya likhiye.
Jaise: "Rahul ko 500 udhaar diye" ya "kal Sita ko payment ke liye call karna".</div>
      </div>
      <div class="chat-input">
        ${micBtn}
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
      const r = await postJSON("/chat", { message: msg, session_id: state.sessionId });
      typing.innerHTML = linkify(r.reply);
    } catch { typing.textContent = "Backend se baat nahi ho payi."; }
    $("#log").scrollTop = $("#log").scrollHeight;
    loadData();  // ledger may have changed — refresh data but stay in chat
  };
  $("#sendBtn").onclick = send;
  $("#chatBox").addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });
  if (state.voice) $("#micBtn").onclick = (e) => toggleMic(e.currentTarget);
}
function addBubble(text, who) {
  const b = document.createElement("div");
  b.className = "bubble " + who; b.textContent = text;
  $("#log").appendChild(b); $("#log").scrollTop = $("#log").scrollHeight;
  return b;
}

/* ---------- voice: record -> /voice/chat -> show transcript + speak reply ---------- */
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
    fd.append("session_id", state.sessionId);
    const res = await fetch("/voice/chat", { method: "POST", body: fd });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    youSaid.textContent = "🎙️ " + (data.transcript || "…");
    botSaid.innerHTML = linkify(data.reply || "");
    if (data.audio_b64) { try { new Audio("data:audio/mp3;base64," + data.audio_b64).play(); } catch {} }
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
function closeModal() { $("#modalRoot").classList.add("hidden"); }
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
