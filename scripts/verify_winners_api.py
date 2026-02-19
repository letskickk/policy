#!/usr/bin/env python3
"""
winners2022 API 파이프라인 검증 스크립트.

1) 당선인 API · 공약 API 호출 및 응답 구조 확인
2) 서울/시도지사 입력 시 타지역·타직책 이름이 결과에 섞이지 않는지 스모크 검증

실행: 프로젝트 루트에서
  python3 scripts/verify_winners_api.py

필요: .env에 DATA_GO_KR_API_KEY 또는 DATA_GO_KR_WINNER_API_KEY, DATA_GO_KR_PLEDGE_API_KEY
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# backend.config가 .env 로드 (dotenv 없으면 수동 로드)
from backend.config import DATA_GO_KR_WINNER_API_KEY, DATA_GO_KR_PLEDGE_API_KEY
from backend.openai_vector_store import (
    SG_ID_2022,
    _fetch_winners_api,
    _fetch_winner_pledges_api,
    _normalize_user_meta_for_winners,
    _winner_row_to_position_region,
)


def test_normalize_user_meta():
    """user_meta 정규화: 서울 + 시도지사 → sdName=서울특별시, sgTypecodes 포함 3"""
    norm = _normalize_user_meta_for_winners({
        "region_province": "서울",
        "election_type": "metro_mayor",
    })
    assert "3" in norm["sgTypecodes"], "시도지사면 sgTypecode 3 포함"
    assert "서울" in norm["sdName"] or norm["sdName"] == "서울특별시", "sdName 정규화"
    print("[OK] _normalize_user_meta_for_winners (서울, metro_mayor)")


def test_winner_api_seoul():
    """당선인 API: 2022 서울 시도지사 1명 조회, 이름/지역 canonical"""
    key = DATA_GO_KR_WINNER_API_KEY
    if not key:
        print("[SKIP] DATA_GO_KR_WINNER_API_KEY 없음")
        return None
    request_dedup = set()
    rows = _fetch_winners_api(SG_ID_2022, "3", "서울특별시", "", key, request_dedup)
    if not rows:
        print("[WARN] 당선인 API 0건 (키/승인 확인)")
        return None
    # 서울 시도지사는 1명
    for r in rows:
        pos, reg = _winner_row_to_position_region("3", r["sdName"], r["sggName"], r["wiwName"])
        assert "서울" in reg or "서울" in (r["sdName"] or ""), "서울 외 지역이면 실패"
        assert "시장" in pos or "지사" in pos, "직책에 시장/지사 포함"
    print(f"[OK] 당선인 API 서울 시도지사 {len(rows)}명: {[r['name'] for r in rows]}")
    return rows[0] if rows else None


def test_pledge_api(winner_row):
    """공약 API: 위 당선인 huboid로 공약 목록 조회"""
    if not winner_row:
        return
    key = DATA_GO_KR_PLEDGE_API_KEY
    if not key:
        print("[SKIP] DATA_GO_KR_PLEDGE_API_KEY 없음")
        return
    request_dedup = set()
    pledges = _fetch_winner_pledges_api(
        SG_ID_2022, "3", winner_row["huboid"], key, request_dedup
    )
    print(f"[OK] 공약 API {len(pledges)}건 (huboid={winner_row['huboid']})")
    if pledges:
        print(f"     예시: {pledges[0].get('prmsTitle', '')[:50]}...")


def _fetch_metro_mayor_rows(province_name: str):
    """시도지사(코드 3) 당선인 조회 헬퍼."""
    key = DATA_GO_KR_WINNER_API_KEY
    if not key:
        print("[SKIP] API 키 없음")
        return []
    norm = _normalize_user_meta_for_winners({
        "region_province": province_name,
        "election_type": "metro_mayor",
    })
    request_dedup = set()
    return _fetch_winners_api(SG_ID_2022, "3", norm["sdName"], norm["sggName"], key, request_dedup)


def test_smoke_seoul_only():
    """스모크1: 서울/시도지사일 때 타지역 이름/직책 미출력"""
    rows = _fetch_metro_mayor_rows("서울")
    if not rows:
        print("[WARN] 서울 시도지사 조회 0건")
        return
    other_regions = ["경기", "전남", "전북", "경남", "경북", "부산", "대구", "인천", "광주", "대전", "울산", "강원", "충청", "제주"]
    for r in rows:
        sd = (r.get("sdName") or "").strip()
        pos, reg = _winner_row_to_position_region("3", r["sdName"], r["sggName"], r["wiwName"])
        for other in other_regions:
            if other in sd or (other in reg and "서울" not in reg):
                print(f"[FAIL] 서울 조회 결과에 타지역 포함: sdName={sd}, region={reg}")
                sys.exit(1)
        if "경기도지사" in pos:
            print(f"[FAIL] 서울 조회 결과에 타직책 포함: position={pos}")
            sys.exit(1)
    print("[OK] 스모크1: 서울/시도지사 조회 시 타지역·타직책 없음")


def test_smoke_gyeonggi_only():
    """스모크2: 경기도/시도지사일 때 서울시장 이름 미출력"""
    rows = _fetch_metro_mayor_rows("경기")
    if not rows:
        print("[WARN] 경기도 시도지사 조회 0건")
        return
    for r in rows:
        name = (r.get("name") or "").strip()
        sd = (r.get("sdName") or "").strip()
        pos, reg = _winner_row_to_position_region("3", r["sdName"], r["sggName"], r["wiwName"])
        if "서울" in sd or "서울" in reg or "서울시장" in pos or "서울특별시장" in pos:
            print(f"[FAIL] 경기도 조회 결과에 서울시장 계열 포함: name={name}, sd={sd}, pos={pos}, region={reg}")
            sys.exit(1)
    print("[OK] 스모크2: 경기도/시도지사 조회 시 서울시장 계열 없음")


def main():
    print("=== winners2022 API 파이프라인 검증 ===\n")
    test_normalize_user_meta()
    winner = test_winner_api_seoul()
    test_pledge_api(winner)
    test_smoke_seoul_only()
    test_smoke_gyeonggi_only()
    print("\n=== 검증 완료 ===")


if __name__ == "__main__":
    main()
