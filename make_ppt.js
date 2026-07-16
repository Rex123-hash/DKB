const PptxGenJS = require("pptxgenjs");
const p = new PptxGenJS();
p.defineLayout({ name: "W", width: 10, height: 5.625 });
p.layout = "W";

const GREEN = "1F6E43", INK = "222222", MUT = "6B6B6B", BOX = "F4F4F2", LINE = "DDDDD7";
const TITLE_F = "Georgia", BODY_F = "Calibri";

function slide() { const s = p.addSlide(); s.background = { color: "FFFFFF" }; return s; }
function title(s, t) {
  s.addText(t, { x: 0.6, y: 0.5, w: 8.8, h: 0.8, fontFace: TITLE_F, fontSize: 30, bold: true, color: GREEN, align: "left" });
}

// 1. Title
let s = slide();
s.addText("DukanBook AI VoiceBot", { x: 0.7, y: 1.9, w: 8.6, h: 1.0, fontFace: TITLE_F, fontSize: 42, bold: true, color: GREEN });
s.addText("A voice and text assistant for local shopkeepers", { x: 0.7, y: 2.95, w: 8.6, h: 0.5, fontFace: BODY_F, fontSize: 19, color: INK });
s.addText("Hindi  ·  English  ·  Hinglish", { x: 0.7, y: 3.5, w: 8.6, h: 0.4, fontFace: BODY_F, fontSize: 15, color: MUT });
s.addText("Amaan  ·  Srishti", { x: 0.7, y: 4.6, w: 8.6, h: 0.4, fontFace: BODY_F, fontSize: 14, color: MUT });

// 2. Problem
s = slide(); title(s, "The problem");
s.addText("Most small shops still keep their accounts on a paper register.", { x: 0.6, y: 1.45, w: 8.8, h: 0.5, fontFace: BODY_F, fontSize: 19, color: INK });
const probs = [
  "India has about 200 million shops, and only a small share use any accounting software.",
  "The software that exists is costly for a small shop and hard to use.",
  "Many shopkeepers are far more comfortable speaking than typing.",
];
s.addText(probs.map(t => ({ text: t, options: { bullet: { code: "2022" }, paraSpaceAfter: 12 } })),
  { x: 0.7, y: 2.25, w: 8.6, h: 2.5, fontFace: BODY_F, fontSize: 16.5, color: INK, lineSpacingMultiple: 1.1 });

// 3. What we built
s = slide(); title(s, "What we built");
s.addText("A simple AI munshi for the shop.", { x: 0.6, y: 1.45, w: 8.8, h: 0.5, fontFace: BODY_F, fontSize: 19, color: INK });
s.addText("The shopkeeper just talks or types, in Hindi, English or Hinglish, and the assistant takes care of the daily money work.", { x: 0.6, y: 2.05, w: 8.8, h: 0.8, fontFace: BODY_F, fontSize: 16.5, color: MUT });
const did = [
  "Writes the khata — udhaar given and payments received",
  "Tells the balance — how much each customer owes",
  "Sets payment and call reminders",
  "Answers GST, tax, loan and business questions",
];
s.addText(did.map(t => ({ text: t, options: { bullet: { code: "2022" }, paraSpaceAfter: 10 } })),
  { x: 0.7, y: 3.0, w: 8.6, h: 2.2, fontFace: BODY_F, fontSize: 16.5, color: INK });

// 4. Four parts (2x2 grid)
s = slide(); title(s, "The four parts");
const cards = [
  ["Khata (Ledger)", "Add customers and suppliers, record credit or debit, see balance and history."],
  ["AI Assistant", "Type or speak in Hindi, English or Hinglish. It understands and does the job."],
  ["Reminders", "Payment and call reminders, by date and time."],
  ["Knowledge answers", "GST, income tax, loans, licences and business advice."],
];
const gx = [0.6, 5.1], gy = [1.55, 3.45], gw = 4.3, gh = 1.75;
cards.forEach((c, i) => {
  const x = gx[i % 2], y = gy[Math.floor(i / 2)];
  s.addShape(p.ShapeType.rect, { x, y, w: gw, h: gh, fill: { color: BOX }, line: { color: LINE, width: 1 } });
  s.addText(c[0], { x: x + 0.25, y: y + 0.22, w: gw - 0.5, h: 0.4, fontFace: TITLE_F, fontSize: 16, bold: true, color: GREEN });
  s.addText(c[1], { x: x + 0.25, y: y + 0.7, w: gw - 0.5, h: 0.9, fontFace: BODY_F, fontSize: 13.5, color: INK });
});

// 5. How it works (flow)
s = slide(); title(s, "How it works");
const steps = ["Voice or text", "Understand\nthe request", "Do the job\n(ledger / answer / reminder)", "Reply in the\nsame language"];
const sw = 1.95, sh = 1.1, sy = 2.1, sx0 = 0.6, gap = 0.42;
steps.forEach((t, i) => {
  const x = sx0 + i * (sw + gap);
  s.addShape(p.ShapeType.rect, { x, y: sy, w: sw, h: sh, fill: { color: GREEN }, line: { type: "none" } });
  s.addText(t, { x, y: sy, w: sw, h: sh, fontFace: BODY_F, fontSize: 12.5, bold: true, color: "FFFFFF", align: "center", valign: "middle" });
  if (i < steps.length - 1) s.addText(">", { x: x + sw, y: sy, w: gap, h: sh, fontFace: BODY_F, fontSize: 20, bold: true, color: MUT, align: "center", valign: "middle" });
});
s.addText("Voice runs on the computer itself: speech-to-text and text-to-speech, both with no paid key.", { x: 0.6, y: 3.7, w: 8.8, h: 0.5, fontFace: BODY_F, fontSize: 15, color: MUT });

// 6. Knowledge engine
s = slide(); title(s, "The knowledge engine");
s.addText("Reliable answers, not guesses.", { x: 0.6, y: 1.45, w: 8.8, h: 0.5, fontFace: BODY_F, fontSize: 19, color: INK });
const rag = [
  "About 320 reference notes, prepared from official sources: the Income Tax Department, the GST portal, the Ministry of MSME, MUDRA, PM SVANidhi, FSSAI and NPCI.",
  "For a question, the assistant finds the most relevant notes and answers only from them.",
  "If it is not sure, it says so instead of inventing a rule.",
];
s.addText(rag.map(t => ({ text: t, options: { bullet: { code: "2022" }, paraSpaceAfter: 12 } })),
  { x: 0.7, y: 2.2, w: 8.6, h: 2.6, fontFace: BODY_F, fontSize: 16, color: INK, lineSpacingMultiple: 1.1 });

// 7. Technology
s = slide(); title(s, "Technology we used");
const tech = [
  ["Python (FastAPI) and SQLite", "Runs the logic and stores the khata."],
  ["Streamlit", "The screens the shopkeeper uses."],
  ["Local AI model (Ollama)", "Understands the commands. Runs offline, no paid key."],
  ["faster-whisper and edge-tts", "Voice in and voice out. No paid key."],
];
tech.forEach((t, i) => {
  const y = 1.5 + i * 0.92;
  s.addText(t[0], { x: 0.7, y, w: 3.9, h: 0.8, fontFace: TITLE_F, fontSize: 15, bold: true, color: GREEN, valign: "top" });
  s.addText(t[1], { x: 4.8, y, w: 4.5, h: 0.8, fontFace: BODY_F, fontSize: 14.5, color: INK, valign: "top" });
});
s.addText("Built to run with minimal keys — the whole thing can run on a laptop.", { x: 0.7, y: 5.05, w: 8.6, h: 0.4, fontFace: BODY_F, fontSize: 13.5, italic: true, color: MUT });

// 8. Status
s = slide(); title(s, "Where it stands");
const done = [
  "Working: ledger (credits and debits), reminders, knowledge answers",
  "Voice and text, in Hindi, English and Hinglish",
  "Checked with automated tests",
];
s.addText(done.map(t => ({ text: t, options: { bullet: { code: "2713" }, paraSpaceAfter: 10 } })),
  { x: 0.7, y: 1.5, w: 8.6, h: 2.0, fontFace: BODY_F, fontSize: 16.5, color: INK });
s.addText("Next", { x: 0.6, y: 3.5, w: 8.8, h: 0.4, fontFace: TITLE_F, fontSize: 16, bold: true, color: GREEN });
const next = ["A bigger knowledge base", "Faster voice", "Put it online so it opens with a web link"];
s.addText(next.map(t => ({ text: t, options: { bullet: { code: "2022" }, paraSpaceAfter: 8 } })),
  { x: 0.7, y: 3.95, w: 8.6, h: 1.4, fontFace: BODY_F, fontSize: 16, color: INK });

p.writeFile({ fileName: "DukanBook_AI_Presentation.pptx" }).then(() => console.log("written"));
