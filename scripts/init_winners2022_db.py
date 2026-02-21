#!/usr/bin/env python3
"""
2022(제8회) 지방선거 당선인 + 공약을 공공 API에서 수집해 SQLite DB에 저장.

1회만 실행하면 이후 서버는 DB에서 조회 (API 호출 없음).

실행:
  python scripts/init_winners2022_db.py           # 신규 데이터만 추가
  python scripts/init_winners2022_db.py --force   # 전체 재수집
"""

import argparse
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.config import (
    DATA_GO_KR_WINNER_API_KEY,
    DATA_GO_KR_PLEDGE_API_KEY,
    ROOT_DIR,
)
from backend.database import DB_PATH, init_db
from backend.openai_vector_store import (
    SG_ID_2022,
    _fetch_winners_api,
    _fetch_winner_pledges_api,
    _winner_row_to_position_region,
)

SG_TYPECODES = [
    ("3",  "광역단체장"),
    ("4",  "기초단체장"),
    ("11", "교육감"),
]


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_all_winners(winner_key: str, force: bool) -> list[dict]:
    """전국 당선인 전체 수집 (sg_typecode 3/4/11)."""
    all_rows: list[dict] = []
    dedup: set = set()
    for typecode, label in SG_TYPECODES:
        print(f"  [{label}(코드={typecode})] 당선인 조회 중...", flush=True)
        rows = _fetch_winners_api(SG_ID_2022, typecode, "", "", winner_key, dedup)
        print(f"    → {len(rows)}명", flush=True)
        for r in rows:
            r["_sg_typecode"] = typecode
        all_rows.extend(rows)
    return all_rows


def run(force: bool = False) -> int:
    winner_key = DATA_GO_KR_WINNER_API_KEY
    pledge_key = DATA_GO_KR_PLEDGE_API_KEY
    if not winner_key:
        print("오류: DATA_GO_KR_WINNER_API_KEY가 .env에 없습니다.")
        return 1
    if not pledge_key:
        print("오류: DATA_GO_KR_PLEDGE_API_KEY가 .env에 없습니다.")
        return 1

    # DB 테이블 초기화 (없으면 생성)
    init_db()

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        # --force 시 기존 데이터 삭제
        if force:
            print("[!] --force: 기존 winners2022 데이터 삭제", flush=True)
            conn.execute("DELETE FROM winner_pledges2022")
            conn.execute("DELETE FROM winners2022")
            conn.commit()

        # 이미 데이터가 있으면 스킵 여부 확인
        existing_count = conn.execute(
            "SELECT COUNT(*) FROM winners2022"
        ).fetchone()[0]
        if existing_count > 0 and not force:
            print(
                f"[INFO] 이미 당선인 {existing_count}명 저장되어 있습니다. "
                "--force 옵션으로 재수집할 수 있습니다.",
                flush=True,
            )

        print("\n[1/3] 전국 당선인 목록 수집 중...", flush=True)
        all_winners = fetch_all_winners(winner_key, force)
        print(f"      총 {len(all_winners)}명 수집 완료\n", flush=True)

        if not all_winners:
            print("오류: 당선인 데이터가 없습니다. API 키/승인 여부를 확인하세요.")
            return 1

        print("[2/3] 당선인 DB 저장 중...", flush=True)
        new_winners = 0
        for r in all_winners:
            huboid = str(r.get("huboid") or "").strip()
            name = str(r.get("name") or "").strip()
            if not huboid or not name:
                continue
            typecode = r.get("_sg_typecode", "")
            sd_name = str(r.get("sdName") or "").strip()
            sgg_name = str(r.get("sggName") or "").strip()
            wiw_name = str(r.get("wiwName") or "").strip()
            position, region = _winner_row_to_position_region(
                typecode, sd_name, sgg_name, wiw_name
            )
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO winners2022
                       (huboid, name, sg_typecode, sd_name, sgg_name, wiw_name,
                        position, region, fetched_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (huboid, name, typecode, sd_name, sgg_name, wiw_name,
                     position, region, _now()),
                )
                new_winners += 1
            except sqlite3.Error as e:
                print(f"    [WARN] 저장 실패 huboid={huboid} name={name}: {e}")
        conn.commit()
        print(f"      {new_winners}명 저장 완료\n", flush=True)

        print("[3/3] 당선인별 공약 수집 중...", flush=True)
        rows = conn.execute(
            "SELECT huboid, name, position, region, sg_typecode FROM winners2022"
        ).fetchall()
        total = len(rows)
        pledge_total = 0
        skipped = 0
        dedup2: set = set()

        for idx, row in enumerate(rows, 1):
            huboid, name, position, region, typecode = (
                row[0], row[1], row[2], row[3], row[4]
            )
            # 이미 공약이 있으면 스킵 (--force 시엔 이미 삭제됨)
            existing_pledges = conn.execute(
                "SELECT COUNT(*) FROM winner_pledges2022 WHERE huboid=?", (huboid,)
            ).fetchone()[0]
            if existing_pledges > 0 and not force:
                skipped += 1
                if idx % 50 == 0 or idx == total:
                    print(
                        f"  [{idx}/{total}] 진행중... (스킵 {skipped}명, 저장 {pledge_total}개)",
                        flush=True,
                    )
                continue

            pledges = _fetch_winner_pledges_api(
                SG_ID_2022, typecode, huboid, pledge_key, dedup2
            )
            now_str = _now()
            for pl in pledges[:10]:
                title = str(pl.get("prmsTitle") or "").strip()
                content = str(pl.get("prmsCont") or "").strip()
                realm = str(pl.get("prmsRealmName") or "").strip()
                if not title and not content:
                    continue
                try:
                    conn.execute(
                        """INSERT INTO winner_pledges2022
                           (huboid, title, content, realm, fetched_at)
                           VALUES (?, ?, ?, ?, ?)""",
                        (huboid, title, content, realm, now_str),
                    )
                    pledge_total += 1
                except sqlite3.Error as e:
                    print(f"    [WARN] 공약 저장 실패 huboid={huboid}: {e}")

            # 중간 커밋 (50명마다)
            if idx % 50 == 0:
                conn.commit()
                print(
                    f"  [{idx}/{total}] {name} ({position}/{region}) - "
                    f"공약 {len(pledges)}개 (누적 {pledge_total}개)",
                    flush=True,
                )
            elif idx == total:
                print(
                    f"  [{idx}/{total}] {name} ({position}/{region}) - "
                    f"공약 {len(pledges)}개 (누적 {pledge_total}개)",
                    flush=True,
                )

            # API rate limit 방지
            time.sleep(0.05)

        conn.commit()

        # 최종 통계
        final_winners = conn.execute("SELECT COUNT(*) FROM winners2022").fetchone()[0]
        final_pledges = conn.execute(
            "SELECT COUNT(*) FROM winner_pledges2022"
        ).fetchone()[0]

        print(f"\n{'='*50}")
        print(f"[완료] 당선인 {final_winners}명, 공약 {final_pledges}개 저장")
        print(f"       DB: {DB_PATH}")
        print(f"{'='*50}")
        return 0

    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="2022 당선인 공약 SQLite DB 초기화"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="기존 데이터 삭제 후 전체 재수집",
    )
    args = parser.parse_args()
    return run(force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
