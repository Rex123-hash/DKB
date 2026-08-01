# DukanBook AI Assistant

DukanBook helps a local shopkeeper keep accounts without a paper register. On top of that
digital khata it adds a voice and text AI assistant. The shopkeeper can simply talk to it in
Hindi, English, or a mix of the two, and it writes the khata, answers everyday business
questions, and sets payment and call reminders.

## Why we are building this

Most small shops in India still keep their hisaab in a paper book. The accounting apps that
already exist are costly and hard to use, and many shopkeepers are far more comfortable
speaking than typing. We wanted something that feels as easy as talking to a munshi, so a
shopkeeper does not have to learn complicated software to run their own accounts.

## What it can do

- Opens new accounts by voice or text, one at a time or several at once, and can ask for each
  one's phone number.
- Records udhaar and payments for every customer and supplier, with a running balance and a
  clear "you will get / you will give" summary.
- Shows and updates a phone number on each account, with one-tap Call and WhatsApp buttons.
- Creates call and payment reminders by voice, for example "kal Rahul ko 5000 ke payment ke
  liye call karna", storing the name, amount, message and time, and giving ready WhatsApp and
  click-to-call links to act on.
- Answers questions about GST, income tax, loans, licences, stock and general business, using
  a set of official reference notes so the information stays reliable.
- Works by voice and by text, in Hindi, English and Hinglish, and speaks the reply back.
- Scans handwritten sale and purchase bills inside the existing AI Assistant, asks for
  unreadable or missing details by text or voice, and keeps a persistent draft during review.
- Independently recalculates every item, subtotal, GST split and grand total using integer
  paise; handwritten arithmetic mismatches must be resolved before confirmation.
- Produces GST or non-GST DukanBook-branded digital bills and downloadable PDFs.
- Posts a confirmed purchase or sale atomically to bills, stock, party ledger and cashbook.
  Scanning and AI extraction alone never change the accounts.
- When it is not sure about something, it says so instead of giving a wrong answer.

## How it is built

The app has three layers. The frontends talk only to the backend; the backend holds all the
logic and is the only layer that touches the database, so there is a single source of truth.

- **Frontend:** a branded web app built in HTML, CSS and JavaScript that matches the
  nestdukanbook.com product, plus the original Streamlit app used during development. Both
  share the same backend.
- **Backend:** FastAPI (Python), which serves the API, the AI brain and tool-calling, the
  ledger and reminder logic, the knowledge engine and voice.
- **Database:** SQLite, storing parties, transactions, reminders, bill drafts, finalized bills,
  products, stock movements, cashbook entries and the knowledge base.

The assistant's intelligence runs on a local Ollama model, so no paid API key is needed.
Business answers use Retrieval-Augmented Generation over prepared reference notes. Voice uses
faster-whisper for speech to text and edge-tts for the spoken reply, both key-free.

## Technology stack

| Area | Technology |
|------|------------|
| Backend | FastAPI + Uvicorn (Python) |
| Database | SQLite |
| Local LLM | Ollama (OpenAI-compatible), e.g. `qwen3:8b`; optional Gemini/Groq |
| Bill vision | Gemini structured vision for accuracy; Ollama (`gemma3:4b`) fallback |
| Knowledge (RAG) | fastembed (multilingual-e5-large) + NumPy cosine search |
| Voice | faster-whisper (speech to text), edge-tts (text to speech) |
| Web frontend | HTML, CSS, vanilla JavaScript |
| Dev frontend | Streamlit |
| Tests | pytest |

## Project structure

```
app/        FastAPI backend: main, brain, llm, parser, tools, rag, voice, db, config
web/        The branded HTML/CSS/JS web app (served by the backend at /app)
ui/         The Streamlit app (dashboard, khata, assistant, reminders)
data/       The knowledge reference notes
tests/      The pytest suite
```

## Running it

Install the requirements once:

```
pip install -r requirements.txt
```

Then start the app:

```
python run.py
```

This starts the backend and the screens together. The branded web app is available at
`http://localhost:8000/app/`. The voice part runs on local libraries, so it needs no key. To
turn on the smart assistant, run Ollama and set `OLLAMA_MODEL` in a `.env` file (a sample is
in `.env.example`); a cloud key (`GEMINI_API_KEY` or `GROQ_API_KEY`) also works.

The bill assistant defaults to the deterministic `fake` extractor so its full scan → review
→ confirm workflow can be tested without a vision model. To read actual handwritten photos
locally:

```
ollama pull gemma3:4b
```

Then set:

```
BILL_AI_BACKEND=ollama
BILL_OLLAMA_MODEL=gemma3:4b
```

Open the existing AI Assistant and tap the camera button. The Bills tab is the history and
management view; it does not create a separate chatbot.

The extraction boundary is deliberately provider-neutral. The implemented GCP adapter keeps
the same canonical draft, deterministic calculator, confirmation rules and posting transaction.

### Private Google Cloud mode

Google Cloud cannot be enabled from the browser or any API. The repository owner must edit the
private constant in `app/config.py`, changing `GCP_ENABLED = False` to `True`, and restart the
server. Changing it back to `False` restores the existing local provider order without changing
the database or bill workflow. `GCP_ALLOW_LOCAL_FALLBACK` is a second code-only policy switch.

GCP mode uses:

- Vertex AI Gemini for the existing assistant's tool-calling loop.
- Vertex AI Gemini vision for structured bill extraction, optionally enriched by a Document AI
  OCR processor. The original scan remains authoritative and calculations remain deterministic.
- Speech-to-Text V2 Chirp 3 for Hindi, Indian English and Hinglish input.
- Text-to-Speech Chirp 3 HD for the spoken Indian voice.

Enable the required APIs and authenticate locally with Application Default Credentials:

```
gcloud services enable aiplatform.googleapis.com speech.googleapis.com texttospeech.googleapis.com
gcloud auth application-default login
```

Document AI is optional:

```
gcloud services enable documentai.googleapis.com
```

Set `GOOGLE_CLOUD_PROJECT` and the optional `GCP_*` model, region, voice and processor values
shown in `.env.example`. No API key or service-account JSON is read by the GCP adapter. On Cloud
Run, attach a least-privilege service account and let ADC obtain its short-lived identity.

### RAG retrieval evaluation

The business-knowledge assistant uses section-aware chunks, dense and lexical retrieval,
reciprocal-rank fusion, lightweight reranking, inline source citations and retrieval traces.
Run the committed Hinglish/English regression set with:

```
uv run --no-project --python .venv/Scripts/python.exe python eval_rag.py --min-hit-at-3 0.8
```

## Deploying it

The app ships as a container (`Dockerfile`), with `render.yaml` describing a Render
web service. It runs the FastAPI backend and serves the branded web app, so the deployed
root URL opens the shop app directly.

Running locally is unchanged: as long as Ollama and the offline libraries are installed, the
app uses them and needs no key. Each backend picks itself — the local library wins whenever
it is present, and the cloud is used only where it is absent, which is exactly the case
inside the container. Nothing to switch by hand.

A container cannot carry the offline models — the embedding model alone is about 2.2 GB —
so the deployed build swaps them for hosted equivalents and drops Streamlit:

- **Knowledge (RAG):** query embeddings come from the Gemini API. The passage vectors are
  prebuilt into `data/kb_vectors.npz` and loaded at startup, so a fresh container makes no
  embedding calls and starts instantly. Rebuild them with `python -m app.rag build` after
  editing anything in `data/knowledge`.
- **Speech to text:** Groq's hosted Whisper instead of faster-whisper.
- **Text to speech:** still edge-tts, which needs no key.

Set `GEMINI_API_KEY` and `GROQ_API_KEY` in the host's environment. Both are used: Gemini
answers first and Groq takes over if it is rate-limited.

To deploy on Render: push the repo, create a Blueprint from `render.yaml`, and add the two
keys as environment variables. Note that the free plan has no persistent disk, so the
SQLite file resets whenever the service restarts; `SEED_ON_START=1` means it comes back
with the demo shop rather than empty. For real persistence, use a paid instance with a
disk mounted at `/data` and set `DUKANBOOK_DB=/data/dukanbook.db` (both are commented into
`render.yaml`).

To run the container locally:

```
docker build -t dukanbook .
docker run -p 8000:8000 -e GEMINI_API_KEY=... -e GROQ_API_KEY=... dukanbook
```

## Documentation

- `DukanBook_Technical_Documentation.pdf` — the full technical reference (architecture, stack,
  modules, database, LLM, RAG, voice, frontends, testing and scaling).
- `DukanBook_Presentation.pptx` — a presentation of the features and the work done.
