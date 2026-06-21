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


def test_search_ranks_gst(tmp_path):
    conn = _kb(tmp_path)
    rag.ingest(conn, tmp_path, embedder=fake_embed)
    res = rag.search(conn, "gst registration", k=1, embedder=fake_embed)
    assert res and "GST" in res[0]["text"]


def test_search_ranks_dues(tmp_path):
    conn = _kb(tmp_path)
    rag.ingest(conn, tmp_path, embedder=fake_embed)
    res = rag.search(conn, "how to recover dues from customers", k=1, embedder=fake_embed)
    assert "udhaar" in res[0]["text"].lower()


def test_search_empty_kb_returns_nothing():
    conn = db.get_connection(":memory:")
    db.init_db(conn)
    assert rag.search(conn, "anything", embedder=fake_embed) == []
