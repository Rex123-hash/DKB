"""Hybrid RAG retrieval and grounding utilities for DukanBook.

Pipeline: markdown knowledge notes -> section-aware chunks with overlap ->
multilingual embeddings plus SQLite lexical search -> reciprocal-rank fusion ->
lightweight reranking -> grounded snippets with auditable citations.

The embedding backend remains provider-neutral: local fastembed is preferred
when installed, while Gemini embeddings and the prebuilt vector cache continue
to support small cloud deployments. Embedders are injectable for deterministic
tests and evaluation.
"""
from __future__ import annotations

import glob
import importlib.util
import json
import os
import re
import time
import uuid
from pathlib import Path

import numpy as np

from app import db as db_layer

MODEL_NAME = os.environ.get("EMBED_MODEL", "intfloat/multilingual-e5-large")
KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "data" / "knowledge"
KB_CACHE = Path(__file__).resolve().parent.parent / "data" / "kb_vectors.npz"

DEFAULT_MIN_SCORE = 0.2
RRF_K = 50
_CHUNK_TARGET_CHARS = 900
_CHUNK_OVERLAP_BLOCKS = 1
_TOKEN_RE = re.compile(r"[A-Za-z0-9_\u0900-\u097f]+")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_STOP_WORDS = {
    "a", "an", "and", "are", "for", "from", "how", "in", "is", "it",
    "ka", "ke", "ki", "kya", "me", "mein", "of", "on", "or", "the",
    "to", "what", "when", "where", "which", "who", "why", "with",
}
_QUERY_REWRITES = {
    "udhaar": "credit dues receivables",
    "udhar": "credit dues receivables",
    "baaki": "outstanding balance dues",
    "baki": "outstanding balance dues",
    "jama": "payment received deposit",
    "gst": "gst goods and services tax",
    "itr": "income tax return itr",
    "fssai": "fssai food license",
    "gumasta": "gumasta shop establishment license",
    "mausam": "weather",
}

_model = None

# Gemini's embedding endpoint keeps deployed containers small. Local fastembed
# remains the default whenever it is installed.
GEMINI_EMBED_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:batchEmbedContents"
)
GEMINI_EMBED_MODEL = os.environ.get("GEMINI_EMBED_MODEL", "gemini-embedding-001")
GEMINI_BATCH = int(os.environ.get("GEMINI_EMBED_BATCH", "16"))
GEMINI_PACE = float(os.environ.get("GEMINI_EMBED_PACE", "1.5"))
GEMINI_EMBED_DIM = int(os.environ.get("GEMINI_EMBED_DIM", "768"))


def _use_gemini() -> bool:
    forced = os.environ.get("EMBED_BACKEND")
    if forced == "local":
        return False
    if forced == "cloud":
        return True
    if importlib.util.find_spec("fastembed") is not None:
        return False
    return bool(os.environ.get("GEMINI_API_KEY"))


def _backend_id() -> str:
    if _use_gemini():
        return f"gemini:{GEMINI_EMBED_MODEL}:{GEMINI_EMBED_DIM}"
    return f"local:{os.environ.get('EMBED_MODEL', MODEL_NAME)}"


def _gemini_embed(texts: list[str], kind: str) -> np.ndarray:
    import requests

    key = os.environ["GEMINI_API_KEY"]
    url = GEMINI_EMBED_URL.format(model=GEMINI_EMBED_MODEL)
    task = "RETRIEVAL_QUERY" if kind == "query" else "RETRIEVAL_DOCUMENT"
    out: list[list[float]] = []
    for index in range(0, len(texts), GEMINI_BATCH):
        if index and GEMINI_PACE:
            time.sleep(GEMINI_PACE)
        batch = texts[index : index + GEMINI_BATCH]
        body = {
            "requests": [
                {
                    "model": f"models/{GEMINI_EMBED_MODEL}",
                    "content": {"parts": [{"text": text}]},
                    "taskType": task,
                    "outputDimensionality": GEMINI_EMBED_DIM,
                }
                for text in batch
            ]
        }
        for attempt in range(6):
            response = requests.post(
                url, params={"key": key}, json=body, timeout=60
            )
            if response.status_code == 429:
                time.sleep(2**attempt)
                continue
            response.raise_for_status()
            break
        else:
            response.raise_for_status()
        out.extend(item["values"] for item in response.json()["embeddings"])
    return np.array(out, dtype=np.float32)


def _get_model():
    global _model
    if _model is None:
        from fastembed import TextEmbedding

        name = os.environ.get("EMBED_MODEL", MODEL_NAME)
        _model = TextEmbedding(model_name=name)
    return _model


def embed(texts: list[str], kind: str = "passage") -> np.ndarray:
    """Embed texts into a float32 matrix for passage or query retrieval."""
    if _use_gemini():
        return _gemini_embed(texts, kind)
    prefixed = [f"{kind}: {text}" for text in texts]
    return np.array(list(_get_model().embed(prefixed)), dtype=np.float32)


def _passage_embedder(texts):
    return embed(texts, "passage")


def _query_embedder(texts):
    return embed(texts, "query")


def _tokenize(text: str) -> list[str]:
    return [
        token
        for token in (value.lower() for value in _TOKEN_RE.findall(text or ""))
        if len(token) > 1 and token not in _STOP_WORDS
    ]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "chunk"


def _display_title(path: str | Path) -> str:
    stem = Path(path).stem.replace("_", " ").replace("-", " ").strip()
    return stem.title() if stem else "Knowledge Note"


def _clean_block(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip()


def _iter_blocks(text: str, source: str) -> tuple[str, list[dict]]:
    lines = text.splitlines()
    title = _display_title(source)
    current_section = title
    paragraph_lines: list[str] = []
    paragraph_start = 1
    blocks: list[dict] = []

    def flush(end_line: int) -> None:
        nonlocal paragraph_lines, paragraph_start
        if not paragraph_lines:
            return
        clean = _clean_block("\n".join(paragraph_lines).strip())
        if len(clean) >= 40:
            blocks.append(
                {
                    "text": clean,
                    "section": current_section,
                    "line_from": paragraph_start,
                    "line_to": end_line,
                }
            )
        paragraph_lines = []

    for line_number, line in enumerate(lines, 1):
        heading = _HEADING_RE.match(line)
        if heading:
            flush(line_number - 1)
            heading_text = heading.group(2).strip()
            if heading.group(1) == "#":
                title = heading_text
            current_section = heading_text
            continue
        if not line.strip():
            flush(line_number - 1)
            paragraph_start = line_number + 1
            continue
        if not paragraph_lines:
            paragraph_start = line_number
        paragraph_lines.append(line)

    flush(len(lines))
    return title, blocks


def chunk_text(text: str, source: str = "knowledge.md") -> list[dict]:
    """Split markdown into section-aware chunks with one-block overlap."""
    title, blocks = _iter_blocks(text, source)
    if not blocks:
        return []

    chunks: list[dict] = []
    window: list[dict] = []
    window_chars = 0
    chunk_index = 0

    def emit(items: list[dict]) -> None:
        nonlocal chunk_index
        section = items[0]["section"] if items else title
        chunks.append(
            {
                "text": "\n\n".join(item["text"] for item in items),
                "source": os.path.basename(source),
                "title": title,
                "section": section,
                "line_from": items[0]["line_from"],
                "line_to": items[-1]["line_to"],
                "chunk_key": f"{Path(source).stem}#{_slug(section)}-{chunk_index}",
            }
        )
        chunk_index += 1

    for block in blocks:
        block_length = len(block["text"])
        if window and window_chars + block_length > _CHUNK_TARGET_CHARS:
            emit(window)
            window = window[-_CHUNK_OVERLAP_BLOCKS:]
            window_chars = sum(len(item["text"]) for item in window)
        window.append(block)
        window_chars += block_length
    if window:
        emit(window)
    return chunks


def ingest(conn, docs_dir: str | Path = KNOWLEDGE_DIR, embedder=None) -> int:
    """Rebuild the knowledge base from markdown files."""
    db_layer.init_db(conn)
    embedder = embedder or _passage_embedder
    conn.execute("DELETE FROM kb_chunk")
    conn.commit()

    chunks: list[dict] = []
    for path in sorted(glob.glob(os.path.join(str(docs_dir), "*.md"))):
        text = Path(path).read_text(encoding="utf-8")
        chunks.extend(chunk_text(text, source=path))
    if not chunks:
        db_layer.replace_kb_fts(conn)
        return 0

    vectors = embedder([chunk["text"] for chunk in chunks])
    for chunk, vector in zip(chunks, vectors):
        conn.execute(
            """
            INSERT INTO kb_chunk (
                text, source, title, section, line_from, line_to, chunk_key,
                embedding
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chunk["text"],
                chunk["source"],
                chunk["title"],
                chunk["section"],
                chunk["line_from"],
                chunk["line_to"],
                chunk["chunk_key"],
                np.asarray(vector, dtype=np.float32).tobytes(),
            ),
        )
    conn.commit()
    db_layer.replace_kb_fts(conn)
    return len(chunks)


def count(conn) -> int:
    db_layer.init_db(conn)
    return int(conn.execute("SELECT COUNT(*) AS c FROM kb_chunk").fetchone()["c"])


def export_kb(conn, path: str | Path = KB_CACHE) -> int:
    """Dump vectors and chunk metadata to a provider-specific cache."""
    db_layer.init_db(conn)
    rows = conn.execute(
        """
        SELECT text, source, title, section, line_from, line_to, chunk_key,
               embedding
        FROM kb_chunk
        WHERE embedding IS NOT NULL
        ORDER BY id
        """
    ).fetchall()
    if not rows:
        return 0
    np.savez_compressed(
        path,
        texts=np.array([row["text"] for row in rows], dtype=object),
        sources=np.array([row["source"] for row in rows], dtype=object),
        titles=np.array([row["title"] for row in rows], dtype=object),
        sections=np.array([row["section"] for row in rows], dtype=object),
        line_from=np.array([row["line_from"] or 0 for row in rows]),
        line_to=np.array([row["line_to"] or 0 for row in rows]),
        chunk_keys=np.array([row["chunk_key"] for row in rows], dtype=object),
        vectors=np.vstack(
            [np.frombuffer(row["embedding"], dtype=np.float32) for row in rows]
        ),
        backend=np.array(_backend_id()),
        schema_version=np.array(2),
    )
    return len(rows)


def _cache_array(data, name: str, fallback: list) -> np.ndarray:
    return data[name] if name in data.files else np.array(fallback, dtype=object)


def load_kb(conn, path: str | Path = KB_CACHE) -> int:
    """Load a matching vector cache, including legacy two-field caches."""
    db_layer.init_db(conn)
    path = Path(path)
    if not path.exists():
        return 0
    data = np.load(path, allow_pickle=True)
    cached_backend = str(data["backend"]) if "backend" in data.files else "unknown"
    if cached_backend != _backend_id():
        print(
            f"[rag] cache built by {cached_backend}, active backend is "
            f"{_backend_id()}; rebuilding"
        )
        return 0

    texts = data["texts"]
    sources = data["sources"]
    vectors = data["vectors"]
    default_titles = [_display_title(str(source)) for source in sources]
    titles = _cache_array(data, "titles", default_titles)
    sections = _cache_array(data, "sections", default_titles)
    line_from = _cache_array(data, "line_from", [0] * len(texts))
    line_to = _cache_array(data, "line_to", [0] * len(texts))
    default_keys = [f"{Path(str(source)).stem}#cached-{index}" for index, source in enumerate(sources)]
    chunk_keys = _cache_array(data, "chunk_keys", default_keys)

    conn.execute("DELETE FROM kb_chunk")
    for text, source, title, section, first, last, key, vector in zip(
        texts,
        sources,
        titles,
        sections,
        line_from,
        line_to,
        chunk_keys,
        vectors,
    ):
        first_value = int(first) if int(first) > 0 else None
        last_value = int(last) if int(last) > 0 else None
        conn.execute(
            """
            INSERT INTO kb_chunk (
                text, source, title, section, line_from, line_to, chunk_key,
                embedding
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(text),
                str(source),
                str(title or _display_title(str(source))),
                str(section or title or _display_title(str(source))),
                first_value,
                last_value,
                str(key),
                np.asarray(vector, dtype=np.float32).tobytes(),
            ),
        )
    conn.commit()
    db_layer.replace_kb_fts(conn)
    return len(texts)


def _load_rows(conn):
    db_layer.init_db(conn)
    return conn.execute(
        """
        SELECT id, text, source, title, section, line_from, line_to, chunk_key,
               embedding
        FROM kb_chunk
        WHERE embedding IS NOT NULL
        """
    ).fetchall()


def _load_matrix(conn):
    rows = _load_rows(conn)
    if not rows:
        return [], None
    matrix = np.vstack(
        [np.frombuffer(row["embedding"], dtype=np.float32) for row in rows]
    )
    return rows, matrix


def _build_query_variants(query: str) -> list[str]:
    base = (query or "").strip()
    if not base:
        return []
    expanded_parts = [base]
    for word in _tokenize(base):
        replacement = _QUERY_REWRITES.get(word)
        if replacement:
            expanded_parts.append(replacement)
    expanded = " ".join(expanded_parts).strip()
    return list(dict.fromkeys([base, expanded]))


def _dense_rank(
    rows, matrix, queries: list[str], limit: int, embedder=None
) -> dict[int, dict]:
    if not rows or matrix is None or not queries:
        return {}
    embedder = embedder or _query_embedder
    query_vectors = np.asarray(embedder(queries), dtype=np.float32)
    if query_vectors.ndim == 1:
        query_vectors = query_vectors.reshape(1, -1)
    if matrix.shape[1] != query_vectors.shape[1]:
        return {}

    row_norms = np.linalg.norm(matrix, axis=1)
    ranking: dict[int, dict] = {}
    for variant, query_vector in zip(queries, query_vectors):
        query_norm = float(np.linalg.norm(query_vector))
        if query_norm <= 1e-9:
            continue
        similarities = (matrix @ query_vector) / (
            row_norms * (query_norm + 1e-9) + 1e-9
        )
        for rank, index in enumerate(np.argsort(-similarities)[:limit], 1):
            row_id = int(rows[index]["id"])
            score = float(similarities[index])
            previous = ranking.get(row_id)
            if previous is None or score > previous["dense_score"]:
                ranking[row_id] = {
                    "dense_score": score,
                    "dense_rank": rank,
                    "dense_query": variant,
                }
    return ranking


def _fts_rank(conn, query: str, limit: int) -> list[dict]:
    if not db_layer.has_fts(conn):
        return []
    tokens = list(dict.fromkeys(_tokenize(query)))
    if not tokens:
        return []
    try:
        rows = conn.execute(
            """
            SELECT rowid AS id, bm25(kb_chunk_fts) AS score
            FROM kb_chunk_fts
            WHERE kb_chunk_fts MATCH ?
            ORDER BY score
            LIMIT ?
            """,
            (" OR ".join(tokens), limit),
        ).fetchall()
    except Exception:
        return []
    return [
        {"id": int(row["id"]), "score": float(row["score"])} for row in rows
    ]


def _fallback_lexical(rows, query: str, limit: int) -> list[dict]:
    query_tokens = set(_tokenize(query))
    scored = []
    for row in rows:
        text_tokens = set(
            _tokenize(
                " ".join(
                    filter(None, [row["title"], row["section"], row["text"]])
                )
            )
        )
        overlap = len(query_tokens & text_tokens)
        if overlap:
            scored.append({"id": int(row["id"]), "score": -float(overlap)})
    scored.sort(key=lambda item: item["score"])
    return scored[:limit]


def _lexical_rank(conn, rows, queries: list[str], limit: int) -> dict[int, dict]:
    ranking: dict[int, dict] = {}
    for query in queries:
        hits = _fts_rank(conn, query, limit) or _fallback_lexical(
            rows, query, limit
        )
        for rank, item in enumerate(hits, 1):
            row_id = item["id"]
            previous = ranking.get(row_id)
            if previous is None or rank < previous["lexical_rank"]:
                ranking[row_id] = {
                    "lexical_rank": rank,
                    "lexical_score": item["score"],
                    "lexical_query": query,
                }
    return ranking


def _overlap_score(query: str, row) -> float:
    query_tokens = set(_tokenize(query))
    if not query_tokens:
        return 0.0
    document_tokens = set(
        _tokenize(
            " ".join(filter(None, [row["title"], row["section"], row["text"]]))
        )
    )
    return len(query_tokens & document_tokens) / len(query_tokens)


def _rerank(query: str, row, data: dict) -> float:
    dense = max(data.get("dense_score", 0.0), 0.0)
    overlap = _overlap_score(query, row)
    lexical_rank = data.get("lexical_rank")
    lexical_boost = 1.0 / (1 + lexical_rank) if lexical_rank else 0.0
    reciprocal_rank = data.get("rrf_score", 0.0)
    section = (row["section"] or "").lower()
    exact = 0.1 if any(token in section for token in _tokenize(query)) else 0.0
    return (
        dense * 0.45
        + overlap * 0.25
        + lexical_boost * 0.15
        + reciprocal_rank * 0.15
        + exact
    )


def format_citation(hit: dict) -> str:
    section = hit.get("section") or hit.get("title") or hit.get("source") or "note"
    source = hit.get("source") or "knowledge"
    line_from = hit.get("line_from")
    line_to = hit.get("line_to")
    if line_from and line_to:
        return f"[{source} - {section}, lines {line_from}-{line_to}]"
    return f"[{source} - {section}]"


def search(
    conn,
    query: str,
    k: int = 3,
    embedder=None,
    run_id: str | None = None,
) -> list[dict]:
    """Return top controlled passages using hybrid retrieval and reranking."""
    query = (query or "").strip()
    if not query:
        return []
    k = max(1, min(int(k), 10))
    rows, matrix = _load_matrix(conn)
    if not rows:
        return []

    started = time.perf_counter()
    queries = _build_query_variants(query)
    candidate_limit = max(k * 5, 10)
    dense = _dense_rank(
        rows, matrix, queries, candidate_limit, embedder=embedder
    )
    lexical = _lexical_rank(conn, rows, queries, candidate_limit)

    by_id = {int(row["id"]): row for row in rows}
    candidates: dict[int, dict] = {}
    for row_id, info in dense.items():
        candidates.setdefault(row_id, {}).update(info)
    for row_id, info in lexical.items():
        candidates.setdefault(row_id, {}).update(info)

    merged = []
    for row_id, info in candidates.items():
        reciprocal_rank = 0.0
        if info.get("dense_rank"):
            reciprocal_rank += 1.0 / (RRF_K + info["dense_rank"])
        if info.get("lexical_rank"):
            reciprocal_rank += 1.0 / (RRF_K + info["lexical_rank"])
        info["rrf_score"] = reciprocal_rank
        row = by_id[row_id]
        merged.append((row, info, _rerank(query, row, info)))
    merged.sort(key=lambda item: item[2], reverse=True)

    hits = []
    for row, info, final_score in merged[:k]:
        row_data = dict(row)
        hits.append(
            {
                "id": int(row["id"]),
                "text": row["text"],
                "source": row["source"],
                "title": row["title"],
                "section": row["section"],
                "line_from": row["line_from"],
                "line_to": row["line_to"],
                "chunk_key": row["chunk_key"],
                "score": float(final_score),
                "dense_score": float(info.get("dense_score", 0.0)),
                "rrf_score": float(info.get("rrf_score", 0.0)),
                "overlap": float(_overlap_score(query, row)),
                "citation": format_citation(row_data),
            }
        )

    try:
        db_layer.log_trace(
            conn,
            run_id or str(uuid.uuid4()),
            "retrieval",
            json.dumps(
                {
                    "query": query,
                    "query_variants": queries,
                    "top_hits": [
                        {
                            "source": hit["source"],
                            "section": hit["section"],
                            "score": hit["score"],
                            "citation": hit["citation"],
                        }
                        for hit in hits
                    ],
                },
                ensure_ascii=False,
            ),
            question=query,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
    except Exception:
        pass
    return hits


def grounded_answer(
    query: str, hits: list[dict], min_score: float = DEFAULT_MIN_SCORE
) -> str | None:
    """Compose a concise extractive reply with citations from retrieved chunks."""
    if not hits:
        return None
    usable = [
        hit
        for hit in hits
        if hit["score"] >= min_score or hit["overlap"] >= 0.2
    ]
    if not usable:
        return None

    lines = ["Mere notes ke hisaab se:"]
    for hit in usable[:2]:
        snippet = re.split(r"(?<=[.!?])\s+", hit["text"].strip())[0]
        lines.append(f"- {snippet} {hit['citation']}")
    if len(usable) > 1:
        lines.append(
            "Agar aap chahein to main isko aur seedhe shabdon mein samjha sakta hoon."
        )
    return "\n".join(lines)


def supported_sentence_ratio(answer: str, hits: list[dict]) -> float:
    """Measure lexical support for answer sentences against retrieved text."""
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", answer or "")
        if sentence.strip()
    ]
    if not sentences:
        return 0.0
    corpus_tokens = set(_tokenize(" ".join(hit["text"] for hit in hits)))
    supported = 0
    for sentence in sentences:
        tokens = set(_tokenize(sentence))
        if not tokens:
            continue
        overlap = len(tokens & corpus_tokens) / len(tokens)
        if overlap >= 0.45 or "[" in sentence:
            supported += 1
    return supported / len(sentences)


def knowledge_summary(conn) -> dict:
    db_layer.init_db(conn)
    row = conn.execute(
        """
        SELECT COUNT(*) AS chunk_count,
               COUNT(DISTINCT source) AS source_count,
               COUNT(DISTINCT COALESCE(section, source)) AS section_count
        FROM kb_chunk
        """
    ).fetchone()
    trace_count = conn.execute("SELECT COUNT(*) AS c FROM trace_event").fetchone()["c"]
    return {
        "chunk_count": int(row["chunk_count"]),
        "source_count": int(row["source_count"]),
        "section_count": int(row["section_count"]),
        "fts": db_layer.has_fts(conn),
        "trace_count": int(trace_count),
        "embedding_backend": _backend_id(),
    }


if __name__ == "__main__":
    import sys

    from app import config, db  # noqa: F401

    print(f"embedding backend: {'gemini' if _use_gemini() else 'local'}")
    connection = db.get_connection()
    db.init_db(connection)
    chunk_count = ingest(connection)
    print(f"Ingested {chunk_count} chunks from {KNOWLEDGE_DIR}")
    if "build" in sys.argv:
        print(f"Wrote {export_kb(connection)} vectors to {KB_CACHE}")
