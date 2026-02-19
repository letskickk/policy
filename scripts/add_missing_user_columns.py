#!/usr/bin/env python3
"""
users 테이블에 없는 컬럼만 추가. 한 번만 실행하면 됨.
실행: python scripts/add_missing_user_columns.py
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "policy.db"

COLUMNS = [
    "email_verified INTEGER NOT NULL DEFAULT 0",
    "verification_token TEXT",
    "verification_expires_at TEXT",
    "name TEXT",
    "phone TEXT",
    "election_position TEXT",
    "region_code TEXT",
    "region_name TEXT",
    "district_code TEXT",
    "district_name TEXT",
]


def main() -> int:
    if not DB_PATH.exists():
        print(f"DB 없음: {DB_PATH}")
        return 1
    conn = sqlite3.connect(str(DB_PATH))
    try:
        for col_def in COLUMNS:
            col_name = col_def.split()[0]
            try:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col_def}")
                print(f"  추가됨: {col_name}")
            except sqlite3.OperationalError as e:
                if "duplicate column" in str(e).lower():
                    print(f"  (이미 있음) {col_name}")
                else:
                    print(f"  오류 {col_name}: {e}")
        conn.commit()
        print("완료.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
