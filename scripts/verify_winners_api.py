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
from backend.config import DATA_GO_KR_WINNER_API_KEY, DATA_GO_KR_PLEDGE_API_KEY, ROOT_DIR

# 디버그: .env 경로 및 키 로드 여부 (값은 노출 안 함)
_env_path = ROOT_DIR / ".env"
print(f"[DEBUG] .env 경로: {_env_path} (exists={_env_path.exists()})")
if _env_path.exists():
    with open(_env_path, encoding="utf-8-sig") as f:
        env_keys = [line.partition("=")[0].strip() for line in f if line.strip() and not line.strip().startswith("#") and "=" in line]
    print(f"[DEBUG] .env 내 키 목록: {[k for k in env_keys if 'KEY' in k or 'key' in k]}")
key_winner = (DATA_GO_KR_WINNER_API_KEY or "").strip()
key_pledge = (DATA_GO_KR_PLEDGE_API_KEY or "").strip()
print(f"[DEBUG] DATA_GO_KR_WINNER_API_KEY: {'있음 (len=%d)' % len(key_winner) if key_winner else '없음'}")
print(f"[DEBUG] DATA_GO_KR_PLEDGE_API_KEY: {'있음 (len=%d)' % len(key_pledge) if key_pledge else '없음'}\n")
from backend.openai_vector_store import (
    SG_ID_2022,
    _fetch_winners_api,
    _fetch_winner_pledges_api,
    _normalize_user_meta_for_winners,
    _parse_winner_api_xml,
    _winner_row_to_position_region,
)


def test_parse_winner_xml():
    """당선인 API XML 파서: 공공 API가 XML만 줄 때 빈 리스트/예외 없이 파싱."""
    # 실제 API 응답과 동일한 구조 (네임스페이스 없음)
    raw_no_ns = """<?xml version="1.0" encoding="UTF-8"?>
<response>
  <header><resultCode>INFO-00</resultCode><resultMsg>NORMAL SERVICE</resultMsg></header>
  <body>
    <items>
      <item>
        <num>1</num>
        <sgId>20220601</sgId>
        <sgTypecode>3</sgTypecode>
        <huboid>1001</huboid>
        <name>홍길동</name>
        <sdName>서울특별시</sdName>
        <sggName></sggName>
        <wiwName></wiwName>
      </item>
    </items>
  </body>
</response>"""
    out = _parse_winner_api_xml(raw_no_ns)
    assert isinstance(out, list), "리스트 반환"
    assert len(out) >= 1, "item 1건 이상 파싱"
    row = out[0]
    assert row.get("huboid") == "1001" or row.get("huboid") == "", "huboid"
    assert "홍길동" in (row.get("name") or ""), "name"
    assert "서울" in (row.get("sdName") or ""), "sdName"
    # 네임스페이스 있는 XML (공공 API 실제 응답 형태)
    raw_with_ns = """<?xml version="1.0" encoding="UTF-8"?>
<response xmlns="http://www.data.go.kr">
  <header><resultCode>INFO-00</resultCode></header>
  <body>
    <items>
      <item><num>1</num><sgId>20220601</sgId><sgTypecode>3</sgTypecode><name>김당선</name><sdName>경기도</sdName></item>
    </items>
  </body>
</response>"""
    out2 = _parse_winner_api_xml(raw_with_ns)
    assert isinstance(out2, list), "ns 리스트 반환"
    assert len(out2) >= 1, "ns item 1건 이상 파싱"
    assert "김당선" in (out2[0].get("name") or "") or "경기" in (out2[0].get("sdName") or ""), "ns 필드"
    print("[OK] _parse_winner_api_xml (무/유 네임스페이스)")


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
        # 안심소득 공약이 있으면 예시로 우선 표시
        example = None
        for p in pledges:
            t = (p.get("prmsTitle") or "") + (p.get("prmsCont") or "")
            if "안심소득" in t:
                example = (p.get("prmsTitle") or "")[:60]
                break
        if not example:
            example = (pledges[0].get("prmsTitle", "") or "")[:50]
        print(f"     예시: {example}...")


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
    test_parse_winner_xml()
    test_normalize_user_meta()
    winner = test_winner_api_seoul()
    test_pledge_api(winner)
    test_smoke_seoul_only()
    test_smoke_gyeonggi_only()
    print("\n=== 검증 완료 ===")


if __name__ == "__main__":
    main()
