"""RAG tests using a deterministic fake embedder (no model download)."""
import numpy as np

from app import db, rag


def fake_embed(texts):
    """Tiny keyword-based embedding: [gst, dues, stock] counts."""
    def vec(t):
        t = t.lower()
        return [
            float(t.count("gst")),
            float(t.count("udhaar") + t.count("dues") + t.count("recover")),
            float(t.count("stock")),
        ]
    return np.array([vec(t) for t in texts], dtype=np.float32)


def test_vertex_embed_uses_adc_and_retrieval_task(monkeypatch):
    from app import config, gcp

    monkeypatch.setattr(config, "GCP_ENABLED", True)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "atlasaccess")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(gcp, "project_id", lambda: "atlasaccess")
    monkeypatch.setattr(gcp, "access_token", lambda: "adc-token")
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"predictions": [{"embeddings": {"values": [0.1, 0.2, 0.3]}}]}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append((url, headers, json, timeout))
        return Response()

    monkeypatch.setattr("requests.post", fake_post)
    vectors = rag._vertex_embed(["GST kya hai?"], "query")

    assert vectors.shape == (1, 3)
    assert calls[0][1]["Authorization"] == "Bearer adc-token"
    assert calls[0][2]["instances"][0]["task_type"] == "RETRIEVAL_QUERY"
    assert "gemini-embedding-001:predict" in calls[0][0]


def _kb(tmp_path):
    (tmp_path / "a.md").write_text(
        "GST is a tax. GST registration threshold is 40 lakh for goods.", encoding="utf-8")
    (tmp_path / "b.md").write_text(
        "Recovering udhaar dues works with gentle reminders to customers.", encoding="utf-8")
    (tmp_path / "c.md").write_text(
        "Manage stock so you never run out of fast moving items in stock.", encoding="utf-8")
    conn = db.get_connection(":memory:")
    db.init_db(conn)
    return conn


def test_ingest_counts_chunks(tmp_path):
    conn = _kb(tmp_path)
    n = rag.ingest(conn, tmp_path, embedder=fake_embed)
    assert n == 3
    assert rag.count(conn) == 3


def test_ingest_stores_section_and_line_metadata(tmp_path):
    conn = _kb(tmp_path)
    rag.ingest(conn, tmp_path, embedder=fake_embed)

    row = conn.execute(
        "SELECT title, section, line_from, line_to, chunk_key "
        "FROM kb_chunk ORDER BY id LIMIT 1"
    ).fetchone()

    assert row["title"]
    assert row["section"]
    assert row["line_from"] >= 1
    assert row["line_to"] >= row["line_from"]
    assert row["chunk_key"]


def test_search_ranks_gst(tmp_path):
    conn = _kb(tmp_path)
    rag.ingest(conn, tmp_path, embedder=fake_embed)
    res = rag.search(conn, "gst registration", k=1, embedder=fake_embed)
    assert res and "GST" in res[0]["text"]
    assert res[0]["citation"].startswith("[a.md")


def test_search_ranks_dues(tmp_path):
    conn = _kb(tmp_path)
    rag.ingest(conn, tmp_path, embedder=fake_embed)
    res = rag.search(conn, "how to recover dues from customers", k=1, embedder=fake_embed)
    assert "udhaar" in res[0]["text"].lower()


def test_search_empty_kb_returns_nothing():
    conn = db.get_connection(":memory:")
    db.init_db(conn)
    assert rag.search(conn, "anything", embedder=fake_embed) == []


def test_hinglish_query_rewrite_and_grounded_answer(tmp_path):
    conn = _kb(tmp_path)
    rag.ingest(conn, tmp_path, embedder=fake_embed)

    hits = rag.search(conn, "udhaar kaise recover karein", k=2, embedder=fake_embed)
    answer = rag.grounded_answer("udhaar kaise recover karein", hits)

    assert hits and hits[0]["source"] == "b.md"
    assert answer is not None
    assert "[b.md" in answer
    assert rag.supported_sentence_ratio(answer, hits) > 0


def test_vector_cache_roundtrip_preserves_rag_metadata(tmp_path):
    conn = _kb(tmp_path)
    rag.ingest(conn, tmp_path, embedder=fake_embed)
    cache_path = tmp_path / "kb.npz"

    assert rag.export_kb(conn, cache_path) == 3
    conn.execute("DELETE FROM kb_chunk")
    conn.commit()
    assert rag.load_kb(conn, cache_path) == 3

    row = conn.execute(
        "SELECT title, section, chunk_key FROM kb_chunk ORDER BY id LIMIT 1"
    ).fetchone()
    assert row["title"] and row["section"] and row["chunk_key"]


def test_retrieval_writes_an_auditable_trace(tmp_path):
    conn = _kb(tmp_path)
    rag.ingest(conn, tmp_path, embedder=fake_embed)

    rag.search(conn, "gst registration", k=1, embedder=fake_embed, run_id="run-1")
    trace = db.list_traces(conn, limit=1)[0]

    assert trace["run_id"] == "run-1"
    assert trace["event_type"] == "retrieval"
    assert "gst registration" in trace["payload_json"]
