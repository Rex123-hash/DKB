# Dukanbook AI — Demo Script

A 5-minute walkthrough to show all features. Speak in Hinglish, like a real shopkeeper.

## 0. Start (one command)
```bash
python run.py          # or double-click start.bat
```
Backend → http://127.0.0.1:8000 · UI opens at http://localhost:8501
On the **Dashboard** sidebar → click **🌱 Load sample data** (gives 2 customers, 1 supplier, a reminder).

## One-line pitch
> "A voice + text AI munshi for shopkeepers — manages the udhaar khata, answers GST/business
> questions, and sets payment reminders, in Hindi, English and Hinglish. Runs on local Python
> libraries with **no paid voice keys** — only one LLM key for the brain."

## Walkthrough (follow the sidebar — each feature is its OWN module)

**1. Dashboard** — point out totals: *Lene hain ₹1500*, *Dene hain ₹3000*, *Reminders 1*.
   "Everything is separate modules, not one chatbot — like the real app."

**2. 📒 Khata (Ledger)** — the structured credits/debits feature (no AI):
   - *Transaction* tab → Ramesh → credit ₹100 → **Likho**.
   - *Khaata dekho* → show running balance + history (credit green / debit red).
   - "Customers **and** suppliers — two-party ledger."

**3. 🤖 AI Assistant** — the smart, separate module. Show it does many things:
   - Ledger by text: `Suresh ne 500 jama kiye` → balance updates.
   - Ledger by **voice** 🎙️: press mic, say *"Ramesh ko do sau udhaar likho"* → it transcribes,
     records, and **speaks the reply back**. (STT = offline Whisper, TTS = edge-tts, no key.)
   - **RAG** (required pipeline): `GST ke liye kab register karna padta hai?` → grounded answer
     from the knowledge base (won't invent rules).
   - Reminder by voice/text: `Ramesh ko kal subah 10 baje payment ke liye yaad dilana`.
   - General: `Kanpur ka mausam?` (live, no key) · `2340 ko 12 se divide karo`.
   - Honesty: `cricket score?` → it says it doesn't have live scores (doesn't fake it).

**4. ⏰ Reminders** — open the tab: the reminder you set shows here, 🔴 DUE when its time passes.
   Click **Done** to clear. "Payment & call reminders — the scheduling requirement."

## What makes it strong (say these)
- **Separate modules**, shared database — structured Khata is distinct from the AI.
- **Hinglish-native**: we keep the user's actual words instead of translating to English and back
  (the reference video's approach) — so *"bhaiya ko 500 ka maal"* just works.
- **Minimal keys / local libraries**: voice, weather, maths, RAG all run with **no paid API** —
  only one Groq key for the LLM brain.
- **Grounded RAG** so GST/tax answers are factual, not hallucinated.
- **44 automated tests**; warm bahi-khata design tuned for shopkeeper trust.

## Reset between demos
Dashboard sidebar → **🧹 Reset** clears all ledger data (knowledge base stays).

## Feature checklist (from the brief)
- [x] Credits & debits — customers and retailers
- [x] Scheduling of calls / payment reminders
- [x] General assistant — weather, maths (cricket = optional key)
- [x] Hindi / English / Hinglish — text **and** voice
- [x] RAG pipeline (required)
