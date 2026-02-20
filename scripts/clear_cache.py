"""analysis_cache 전체 삭제 (오래된 '없음' 결과 무효화)"""
import sqlite3, sys
from pathlib import Path

db = Path(__file__).resolve().parent.parent / "data" / "policy.db"
conn = sqlite3.connect(str(db))
cur = conn.execute("DELETE FROM analysis_cache")
print(f"삭제: {cur.rowcount}건")
conn.commit()
conn.close()
print("완료")
