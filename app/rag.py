"""RAG engine: embed a knowledge corpus and retrieve relevant passages.

Pipeline: markdown files in data/knowledge -> split into paragraph chunks ->
multilingual embeddings (fastembed) -> stored as BLOBs in the kb_chunk table ->
cosine-similarity search at query time. Exposed to the brain via the
`search_knowledge` tool so answers are grounded in real text, not guessed.

The embedder is injectable (``embedder=`` arg) so tests can run without
downloading the model.
"""
from __future__ import annotations

import glob
import os
import re
import time
from pathlib import Path

import os

import numpy as np

# multilingual-e5-large gives strong Hindi/English/Hinglish retrieval. e5 models
# expect "query:"/"passage:" prefixes — handled in embed().
MODEL_NAME = os.environ.get("EMBED_MODEL", "intfloat/multilingual-e5-large")
KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "data" / "knowledge"

_model = None

# Gemini's embedding endpoint. Used when GEMINI_API_KEY is set, which keeps the
# deployed container small — the local fastembed model is ~2.2 GB and will not
# fit in a free-tier container, so cloud embeddings are the production path.
GEMINI_EMBED_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents"
)
GEMINI_EMBED_MODEL = os.environ.get("GEMINI_EMBED_MODEL", "gemini-embedding-001")
# Gemini accepts up to 100 per batch, but the free tier rate-limits well below
# that, so keep batches small and back off on 429.
GEMINI_BATCH = int(os.environ.get("GEMINI_EMBED_BATCH", "16"))
# Seconds to pause between batches. Only matters for bulk ingest; a single
# query embedding is one batch and never sleeps.
GEMINI_PACE = float(os.environ.get("GEMINI_EMBED_PACE", "1.5"))
# gemini-embedding-001 defaults to 3072 dims; 768 keeps the stored BLOBs small
# and is plenty for a 300-chunk corpus.
GEMINI_EMBED_DIM = int(os.environ.get("GEMINI_EMBED_DIM", "768"))


def _use_gemini() -> bool:
    """Prefer cloud embeddings unless explicitly told to embed locally."""
    if os.environ.get("EMBED_BACKEND") == "local":
        return False
    return bool(os.environ.get("GEMINI_API_KEY"))


def _gemini_embed(texts: list[str], kind: str) -> np.ndarray:
    """Embed via Gemini. `kind` maps to Gemini's task_type for better retrieval.

    Batched, so ingesting the whole knowledge base is a handful of round-trips
    rather than one per chunk.
    """
    import requests  # local import keeps this module importable without network deps

    key = os.environ["GEMINI_API_KEY"]
    url = GEMINI_EMBED_URL.format(model=GEMINI_EMBED_MODEL)
    task = "RETRIEVAL_QUERY" if kind == "query" else "RETRIEVAL_DOCUMENT"
    out: list[list[float]] = []
    for i in range(0, len(texts), GEMINI_BATCH):
        if i and GEMINI_PACE:  # stay under the per-minute quota while ingesting
            time.sleep(GEMINI_PACE)
        batch = texts[i : i + GEMINI_BATCH]
        body = {
            "requests": [
                {
                    "model": f"models/{GEMINI_EMBED_MODEL}",
                    "content": {"parts": [{"text": t}]},
                    "taskType": task,
                    "outputDimensionality": GEMINI_EMBED_DIM,
                }
                for t in batch
            ]
        }
        for attempt in range(6):
            resp = requests.post(url, params={"key": key}, json=body, timeout=60)
            if resp.status_code == 429:  # free-tier quota: wait and retry
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            break
        else:
            resp.raise_for_status()  # out of retries, surface the 429
        out.extend(e["values"] for e in resp.json()["embeddings"])
    return np.array(out, dtype=np.float32)


def _get_model():
    global _model
    if _model is None:
        from fastembed import TextEmbedding  # imported lazily (heavy)
        name = os.environ.get("EMBED_MODEL", MODEL_NAME)  # resolved at call time
        _model = TextEmbedding(model_name=name)
    return _model


def embed(texts: list[str], kind: str = "passage") -> np.ndarray:
    """Embed texts -> (n, dim) float32 array. `kind` is 'passage' or 'query'."""
    if _use_gemini():
        return _gemini_embed(texts, kind)
    prefixed = [f"{kind}: {t}" for t in texts]
    vecs = list(_get_model().embed(prefixed))
    return np.array(vecs, dtype=np.float32)


def _passage_embedder(texts):
    return embed(texts, "passage")


def _query_embedder(texts):
    return embed(texts, "query")


def chunk_text(text: str) -> list[str]:
    """Split a document into paragraph chunks (blank-line separated)."""
    parts = re.split(r"\n\s*\n", text)
    out = []
    for p in parts:
        p = p.strip()
        if p.startswith("#"):  # drop a lone heading line, keep heading+body otherwise
            lines = [ln for ln in p.splitlines() if not ln.strip().startswith("#")]
            p = "\n".join(lines).strip()
        if len(p) >= 40:  # skip tiny fragments
            out.append(p)
    return out


def ingest(conn, docs_dir: str | Path = KNOWLEDGE_DIR, embedder=None) -> int:
    """(Re)build the knowledge base from markdown files. Returns chunk count."""
    embedder = embedder or _passage_embedder
    conn.execute("DELETE FROM kb_chunk")
    conn.commit()
    chunks: list[str] = []
    sources: list[str] = []
    for path in sorted(glob.glob(os.path.join(str(docs_dir), "*.md"))):
        text = Path(path).read_text(encoding="utf-8")
        for c in chunk_text(text):
            chunks.append(c)
            sources.append(os.path.basename(path))
    if not chunks:
        return 0
    vecs = embedder(chunks)
    for c, s, v in zip(chunks, sources, vecs):
        conn.execute(
            "INSERT INTO kb_chunk (text, source, embedding) VALUES (?, ?, ?)",
            (c, s, np.asarray(v, dtype=np.float32).tobytes()),
        )
    conn.commit()
    return len(chunks)


def count(conn) -> int:
    return int(conn.execute("SELECT COUNT(*) AS c FROM kb_chunk").fetchone()["c"])


# A prebuilt vector cache committed to the repo. Building the knowledge base
# needs ~320 embedding calls, which a fresh container should not spend on every
# cold start (and which the free API tier rate-limits). Build it once with
# `python -m app.rag build`, commit the file, and deployments just load it.
KB_CACHE = Path(__file__).resolve().parent.parent / "data" / "kb_vectors.npz"


def export_kb(conn, path: str | Path = KB_CACHE) -> int:
    """Dump the ingested knowledge base to a .npz cache."""
    rows = conn.execute("SELECT text, source, embedding FROM kb_chunk").fetchall()
    if not rows:
        return 0
    np.savez_compressed(
        path,
        texts=np.array([r["text"] for r in rows], dtype=object),
        sources=np.array([r["source"] for r in rows], dtype=object),
        vectors=np.vstack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows]),
    )
    return len(rows)


def load_kb(conn, path: str | Path = KB_CACHE) -> int:
    """Load the prebuilt cache into kb_chunk. Returns 0 if there is no cache."""
    path = Path(path)
    if not path.exists():
        return 0
    data = np.load(path, allow_pickle=True)
    texts, sources, vectors = data["texts"], data["sources"], data["vectors"]
    conn.execute("DELETE FROM kb_chunk")
    for t, s, v in zip(texts, sources, vectors):
        conn.execute(
            "INSERT INTO kb_chunk (text, source, embedding) VALUES (?, ?, ?)",
            (str(t), str(s), np.asarray(v, dtype=np.float32).tobytes()),
        )
    conn.commit()
    return len(texts)


def _load_matrix(conn):
    rows = conn.execute(
        "SELECT text, source, embedding FROM kb_chunk WHERE embedding IS NOT NULL"
    ).fetchall()
    if not rows:
        return [], None
    mat = np.vstack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
    return rows, mat


def search(conn, query: str, k: int = 3, embedder=None) -> list[dict]:
    """Return the top-k most similar passages to the query."""
    embedder = embedder or _query_embedder
    rows, mat = _load_matrix(conn)
    if not rows:
        return []
    qv = embedder([query])[0]
    if mat.shape[1] != qv.shape[0]:
        # Stored vectors came from a different embedding model. Rebuild rather
        # than crash on the dot product.
        ingest(conn)
        rows, mat = _load_matrix(conn)
        if not rows:
            return []
    sims = (mat @ qv) / (
        np.linalg.norm(mat, axis=1) * (np.linalg.norm(qv) + 1e-9) + 1e-9
    )
    order = np.argsort(-sims)[:k]
    return [
        {"text": rows[i]["text"], "source": rows[i]["source"], "score": float(sims[i])}
        for i in order
    ]


if __name__ == "__main__":  # `python -m app.rag [build]` rebuilds the KB
    import sys

    from app import config, db  # noqa: F401  (config loads .env -> picks the
    #                              same embedding backend production will use)

    print(f"embedding backend: {'gemini' if _use_gemini() else 'local'}")
    conn = db.get_connection()
    db.init_db(conn)
    n = ingest(conn)
    print(f"Ingested {n} chunks from {KNOWLEDGE_DIR}")
    if "build" in sys.argv:  # also write the cache shipped to deployments
        print(f"Wrote {export_kb(conn)} vectors to {KB_CACHE}")
