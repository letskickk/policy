"""
공천 확정자 명단 CSV 임포트 스크립트.
CSV 컬럼: 성명, 생년월일, 성별, 전화번호, 이메일, 시·도, 시·군·구+선거구역, 출마직책
status_note = '공천 확정' 으로 전원 설정.
"""
import csv
import sqlite3
import sys
import os

CSV_PATH = sys.argv[1] if len(sys.argv) > 1 else "data/nominators_temp.csv"
DB_PATH = os.environ.get("DATABASE_PATH", "data/policy.db")

print(f"CSV: {CSV_PATH}")
print(f"DB:  {DB_PATH}")

with open(CSV_PATH, encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)
    rows = list(reader)

print(f"읽은 행 수: {len(rows)}")
if rows:
    print(f"컬럼: {list(rows[0].keys())}")
    print(f"샘플: {rows[0]}")

conn = sqlite3.connect(DB_PATH)
conn.execute("DELETE FROM party_applicants")

inserted = 0
for r in rows:
    name = r.get("성명", "").strip()
    phone = r.get("전화번호", "").strip()
    email = r.get("이메일", "").strip()
    region_province = r.get("시·도", "").strip()
    district_info = r.get("시·군·구+선거구역", "").strip()
    election_position = r.get("출마직책", "").strip()

    if not name:
        continue

    conn.execute(
        """INSERT INTO party_applicants
           (name, phone, email, region_province, district_info, election_position,
            doc_submitted, interview_done, status_note)
           VALUES (?, ?, ?, ?, ?, ?, 0, 0, '공천 확정')""",
        (name, phone, email, region_province, district_info, election_position),
    )
    inserted += 1

conn.commit()
conn.close()
print(f"임포트 완료: {inserted}명")
