#!/usr/bin/env python3
"""
analysis_history 기존 레코드의 total_score 컬럼을 파싱하여 backfill.
실행: python scripts/backfill_scores.py
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.score_parser import parse_total_score_any  # noqa: E402


def main() -> int:
    import os
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    db_path = os.environ.get("DATABASE_PATH", "") or str(ROOT / "data" / "policy.db")
    db_path = Path(db_path)
    if not db_path.exists():
        print(f"DB not found: {db_path}")
        return 1

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, result_text, result_format FROM analysis_history WHERE total_score IS NULL"
        ).fetchall()
        print(f"Backfilling {len(rows)} records...")

        updated = 0
        for row in rows:
            score = parse_total_score_any(row["result_text"], row["result_format"])
            if score is not None:
                conn.execute(
                    "UPDATE analysis_history SET total_score = ? WHERE id = ?",
                    (score, row["id"]),
                )
                updated += 1

        conn.commit()
        print(f"Done. Updated {updated}/{len(rows)} records.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
