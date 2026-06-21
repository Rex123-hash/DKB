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
from pathlib import Path

import os

import numpy as np

# multilingual-e5-large gives strong Hindi/English/Hinglish retrieval. e5 models
# expect "query:"/"passage:" prefixes — handled in embed().
MODEL_NAME = os.environ.get("EMBED_MODEL", "intfloat/multilingual-e5-large")
KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "data" / "knowledge"

_model = None


def _get_model():
    global _model
    if _model is None:
        from fastembed import TextEmbedding  # imported lazily (heavy)
        name = os.environ.get("EMBED_MODEL", MODEL_NAME)  # resolved at call time
        _model = TextEmbedding(model_name=name)
    return _model


def embed(texts: list[str], kind: str = "passage") -> np.ndarray:
    """Embed texts -> (n, dim) float32 array. `kind` is 'passage' or 'query'."""
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
    sims = (mat @ qv) / (
        np.linalg.norm(mat, axis=1) * (np.linalg.norm(qv) + 1e-9) + 1e-9
    )
    order = np.argsort(-sims)[:k]
    return [
        {"text": rows[i]["text"], "source": rows[i]["source"], "score": float(sims[i])}
        for i in order
    ]


if __name__ == "__main__":  # `python -m app.rag` rebuilds the KB
    from app import db

    conn = db.get_connection()
    db.init_db(conn)
    n = ingest(conn)
    print(f"Ingested {n} chunks from {KNOWLEDGE_DIR}")
