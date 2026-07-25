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
- When it is not sure about something, it says so instead of giving a wrong answer.

## How it is built

The app has three layers. The frontends talk only to the backend; the backend holds all the
logic and is the only layer that touches the database, so there is a single source of truth.

- **Frontend:** a branded web app built in HTML, CSS and JavaScript that matches the
  nestdukanbook.com product, plus the original Streamlit app used during development. Both
  share the same backend.
- **Backend:** FastAPI (Python), which serves the API, the AI brain and tool-calling, the
  ledger and reminder logic, the knowledge engine and voice.
- **Database:** SQLite, storing parties, transactions, reminders and the knowledge base.

The assistant's intelligence runs on a local Ollama model, so no paid API key is needed.
Business answers use Retrieval-Augmented Generation over prepared reference notes. Voice uses
faster-whisper for speech to text and edge-tts for the spoken reply, both key-free.

## Technology stack

| Area | Technology |
|------|------------|
| Backend | FastAPI + Uvicorn (Python) |
| Database | SQLite |
| Local LLM | Ollama (OpenAI-compatible), e.g. `qwen3:8b`; optional Gemini/Groq |
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

## Deploying it

The app ships as a container (`Dockerfile`), with `render.yaml` describing a Render
web service. It runs the FastAPI backend and serves the branded web app, so the deployed
root URL opens the shop app directly.

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
