"""Deterministic evaluation harness for DukanBook's retrieval pipeline."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app import config as _config  # noqa: F401 - loads the project's .env
from app import db, rag

DATASET_PATH = Path("data/evals/retrieval_eval.json")


def load_dataset(path: Path = DATASET_PATH) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def ensure_kb(conn) -> int:
    db.init_db(conn)
    existing = rag.count(conn)
    if existing:
        return existing
    try:
        loaded = rag.load_kb(conn)
        return loaded or rag.ingest(conn)
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Knowledge base is empty and embedding dependencies are missing. "
            "Install project requirements, then rerun this evaluation."
        ) from exc


def run_eval(
    conn, dataset: list[dict], top_k: int = 5
) -> tuple[list[dict], dict]:
    rows = []
    hit1 = hit3 = hit5 = 0
    reciprocal_rank = precision3 = recall5 = groundedness = 0.0

    for item in dataset:
        query = item["question"]
        expected = set(item["expected_sources"])
        hits = rag.search(conn, query, k=top_k)
        # Evaluate source documents, not repeated chunks from the same document.
        # Hybrid retrieval may legitimately return several sections of one file.
        sources = list(dict.fromkeys(hit["source"] for hit in hits))
        rank = next(
            (index + 1 for index, source in enumerate(sources) if source in expected),
            None,
        )
        relevant_top3 = sum(source in expected for source in sources[:3])
        relevant_top5 = sum(source in expected for source in sources[:5])
        answer = rag.grounded_answer(query, hits) or ""
        support = rag.supported_sentence_ratio(answer, hits)

        if rank:
            reciprocal_rank += 1.0 / rank
            hit1 += rank <= 1
            hit3 += rank <= 3
            hit5 += rank <= 5
        precision3 += relevant_top3 / min(3, max(len(sources), 1))
        recall5 += relevant_top5 / len(expected)
        groundedness += support
        rows.append(
            {
                "question": query,
                "expected_sources": sorted(expected),
                "top_sources": sources,
                "rank": rank,
                "answer": answer,
                "supported_sentence_ratio": support,
            }
        )

    total = max(len(dataset), 1)
    metrics = {
        "questions": len(dataset),
        "hit_at_1": hit1 / total,
        "hit_at_3": hit3 / total,
        "hit_at_5": hit5 / total,
        "mrr": reciprocal_rank / total,
        "precision_at_3": precision3 / total,
        "recall_at_5": recall5 / total,
        "grounded_sentence_ratio": groundedness / total,
    }
    return rows, metrics


def print_report(rows: list[dict], metrics: dict) -> None:
    print(f"Knowledge eval questions: {metrics['questions']}")
    print(f"Hit@1: {metrics['hit_at_1']:.0%}")
    print(f"Hit@3: {metrics['hit_at_3']:.0%}")
    print(f"Hit@5: {metrics['hit_at_5']:.0%}")
    print(f"MRR: {metrics['mrr']:.3f}")
    print(f"Precision@3: {metrics['precision_at_3']:.3f}")
    print(f"Recall@5: {metrics['recall_at_5']:.3f}")
    print(f"Grounded sentence ratio: {metrics['grounded_sentence_ratio']:.3f}")
    print()
    print(f"{'Rank':<6}{'Top source':<30}Question")
    print("-" * 96)
    for row in rows:
        top = row["top_sources"][0] if row["top_sources"] else "-"
        print(f"{str(row['rank'] or '-'): <6}{top:<30}{row['question']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-hit-at-3", type=float, default=None)
    args = parser.parse_args(argv)

    conn = db.get_connection()
    try:
        chunk_count = ensure_kb(conn)
        rows, metrics = run_eval(conn, load_dataset())
    finally:
        conn.close()

    print(f"Knowledge base chunks: {chunk_count}")
    print_report(rows, metrics)
    if args.min_hit_at_3 is not None and metrics["hit_at_3"] < args.min_hit_at_3:
        print(
            f"\nRegression check failed: Hit@3 {metrics['hit_at_3']:.3f} "
            f"< {args.min_hit_at_3:.3f}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
