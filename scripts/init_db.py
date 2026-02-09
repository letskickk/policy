#!/usr/bin/env python3
"""
DB 초기화 / 마이그레이션.
프로젝트 루트에서: python scripts/init_db.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.database import init_db

if __name__ == "__main__":
    init_db()
    print("DB 초기화 완료.")
