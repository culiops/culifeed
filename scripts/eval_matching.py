"""Evaluate v2 topic matching against a hand-labeled CSV.

CSV format: article_id,expected_topic_name (header on first line).

Reads existing pipeline_version='v2' rows from the database — does NOT
run the pipeline itself.  Outputs:

  - Per-topic precision, recall, F1
  - Top-1 accuracy
  - Confusion matrix (top-10 mismatches)

Usage::

    python scripts/eval_matching.py --csv labeled.csv --db data/culifeed.db
"""

import argparse
import asyncio
import csv
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict


async def evaluate(csv_path: str, db_path: str) -> None:
    """Read labeled CSV and v2 DB results, then print evaluation metrics.

    Args:
        csv_path: Path to CSV file with columns article_id,expected_topic_name.
        db_path:  Path to the CuliFeed SQLite database.
    """
    # --- Load labels --------------------------------------------------------
    expected: Dict[str, str] = {}
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            expected[row["article_id"]] = row["expected_topic_name"]

    print(f"Articles labeled: {len(expected)}", end="")

    if not expected:
        print(", scored: 0")
        print()
        print("Nothing to evaluate — CSV is empty.")
        return

    # --- Fetch v2 results from DB -------------------------------------------
    db = Path(db_path)
    placeholders = ",".join("?" * len(expected))
    with sqlite3.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT article_id, topic_name FROM processing_results "
            f"WHERE pipeline_version='v2' AND article_id IN ({placeholders})",
            tuple(expected.keys()),
        ).fetchall()

    # Last assignment wins per article (schema has UNIQUE on article_id+version).
    actual: Dict[str, str] = {row["article_id"]: row["topic_name"] for row in rows}

    print(f", scored: {len(actual)}")
    print()

    # --- Compute metrics ----------------------------------------------------
    tp: Dict[str, int] = defaultdict(int)
    fp: Dict[str, int] = defaultdict(int)
    fn: Dict[str, int] = defaultdict(int)
    confusion: Counter = Counter()

    for aid, exp_topic in expected.items():
        act_topic = actual.get(aid, "__missing__")
        confusion[(exp_topic, act_topic)] += 1
        if act_topic == exp_topic:
            tp[exp_topic] += 1
        else:
            fp[act_topic] += 1
            fn[exp_topic] += 1

    # --- Per-topic table ----------------------------------------------------
    print(f"{'Topic':<60} {'Prec':>6} {'Rec':>6} {'F1':>6}")
    print("-" * 76)
    topics = set(expected.values()) | set(actual.values())
    for t in sorted(topics):
        prec = tp[t] / (tp[t] + fp[t]) if (tp[t] + fp[t]) else 0.0
        rec = tp[t] / (tp[t] + fn[t]) if (tp[t] + fn[t]) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        print(f"{t[:60]:<60} {prec:>6.2f} {rec:>6.2f} {f1:>6.2f}")

    print()

    # --- Top-1 accuracy -----------------------------------------------------
    accuracy = sum(tp.values()) / len(expected)
    print(f"Top-1 accuracy: {accuracy:.1%}")
    print()

    # --- Confusion matrix (top-10 mismatches) -------------------------------
    print("Confusion (expected → actual): top mismatches")
    mismatches = [(pair, n) for pair, n in confusion.most_common() if pair[0] != pair[1]]
    if not mismatches:
        print("  (no mismatches)")
    else:
        for (exp, act), n in mismatches[:10]:
            print(f"  {n:>3}  {exp[:40]:<40} → {act[:40]}")


def main() -> None:
    """CLI entry point."""
    p = argparse.ArgumentParser(
        description="Evaluate v2 topic matching against hand-labeled data."
    )
    p.add_argument("--csv", required=True, help="Path to labeled CSV file")
    p.add_argument("--db", required=True, help="Path to CuliFeed SQLite database")
    args = p.parse_args()
    asyncio.run(evaluate(args.csv, args.db))


if __name__ == "__main__":
    main()
