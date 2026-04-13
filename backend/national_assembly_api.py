"""
열린국회정보 (open.assembly.go.kr) — 국회의원 + 발의법안 조회 모듈.

API 키 없으면 빈 결과 반환 (graceful degradation).
결과는 24시간 캐시 (assembly_api.py 패턴 동일).

엔드포인트:
  - ALLNAMEMBER  : 역대 + 현역 의원 (GTELT_ERACO 필터로 22대 현역 조회)
  - nwbpacrgavhjryiph : 발의법안 (AGE + BILL_KIND_CD 또는 PROPOSER 필터)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode

import urllib.request

from backend.database import get_connection

logger = logging.getLogger(__name__)

NATIONAL_ASSEMBLY_API_KEY = os.getenv("NATIONAL_ASSEMBLY_API_KEY", "")
NATIONAL_BASE = "https://open.assembly.go.kr/portal/openapi"
CACHE_TTL_HOURS = 24
CURRENT_AGE = "22"  # 22대 국회

_UA = "Mozilla/5.0 (compatible; PolicyBot/1.0)"


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------
def _cache_key(prefix: str, **kwargs) -> str:
    raw = prefix + "|" + json.dumps(kwargs, sort_keys=True, ensure_ascii=False)
    return "natasm_" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def _get_cached(key: str) -> Optional[dict]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT result_payload, expires_at FROM analysis_cache WHERE cache_key = ?",
            (key,),
        ).fetchone()
        if not row:
            return None
        if row["expires_at"] and row["expires_at"] < datetime.now(timezone.utc).isoformat():
            conn.execute("DELETE FROM analysis_cache WHERE cache_key = ?", (key,))
            conn.commit()
            return None
        return json.loads(row["result_payload"])
    except Exception:
        return None
    finally:
        conn.close()


def _set_cached(key: str, data: dict) -> None:
    from datetime import timedelta
    expires = (datetime.now(timezone.utc) + timedelta(hours=CACHE_TTL_HOURS)).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO analysis_cache (cache_key, result_payload, expires_at)
               VALUES (?, ?, ?)
               ON CONFLICT(cache_key) DO UPDATE SET result_payload=excluded.result_payload, expires_at=excluded.expires_at""",
            (key, json.dumps(data, ensure_ascii=False), expires),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# HTTP helper — User-Agent 필수 (없으면 400)
# ---------------------------------------------------------------------------
def _http_get(endpoint: str, params: dict) -> Optional[dict]:
    url = f"{NATIONAL_BASE}/{endpoint}?{urlencode(params, encoding='utf-8')}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        return json.loads(body)
    except Exception as e:
        logger.warning("[national_assembly] HTTP error %s: %s", url[:100], e)
        return None


# ---------------------------------------------------------------------------
# 지역구 국회의원 조회  (ALLNAMEMBER, 22대 필터)
# ---------------------------------------------------------------------------
def _fetch_all_22nd_members() -> list[dict]:
    """
    22대 국회의원 전체 목록 조회 (페이지 순회, 24h 캐시).
    ALLNAMEMBER API는 전체 역대 의원 3,000+건을 알파벳순으로 반환하므로
    pIndex를 늘려가며 22대 필터 결과를 모두 수집한다.
    """
    cache_k = _cache_key("all_22nd_members")
    cached = _get_cached(cache_k)
    if cached is not None:
        return cached.get("members", [])

    all_22 = []
    page_size = 300
    max_pages = 12  # 3286 / 300 ≈ 11 pages
    for page in range(1, max_pages + 1):
        params = {
            "KEY": NATIONAL_ASSEMBLY_API_KEY,
            "Type": "json",
            "pIndex": page,
            "pSize": page_size,
        }
        data = _http_get("ALLNAMEMBER", params)
        if not data:
            break
        try:
            rows = data.get("ALLNAMEMBER", [{}])[1].get("row", [])
        except (IndexError, KeyError, TypeError):
            break
        if not rows:
            break
        for r in rows:
            if CURRENT_AGE + "대" in (r.get("GTELT_ERACO") or ""):
                all_22.append(r)
        # 전체 건수 확인 — 다음 페이지가 없으면 중단
        try:
            total = data.get("ALLNAMEMBER", [{}])[0].get("head", [{}])[0].get("list_total_count", 0)
        except Exception:
            total = 0
        if total and page * page_size >= total:
            break

    _set_cached(cache_k, {"members": all_22})
    logger.info("[national_assembly] 22대 전체 의원 %d명 캐시 완료", len(all_22))
    return all_22


def search_member_by_district(region: str, district: str = "") -> Optional[dict]:
    """
    지역명/선거구로 22대 국회의원 검색.
    반환: {name, party, committee, district, age} or None
    """
    if not NATIONAL_ASSEMBLY_API_KEY:
        return None

    cache_k = _cache_key("member22", region=region, district=district)
    cached = _get_cached(cache_k)
    if cached is not None:
        return cached.get("member")

    members = _fetch_all_22nd_members()
    if not members:
        _set_cached(cache_k, {"member": None})
        return None

    # 검색 키워드 준비: district 앞 2글자(시군구명) → 없으면 region 앞 2글자(시도)
    district_base = (district or "").split()[0] if district else ""
    kw_list = []
    if district_base:
        kw_list.append(district_base[:2])  # e.g. "해남"
    if region:
        # "전라남도" → "전남", "서울특별시" → "서울"
        short = region.replace("특별시", "").replace("광역시", "").replace("특별자치시", "").replace("특별자치도", "")
        if len(short) >= 2:
            kw_list.append(short[:2])   # e.g. "전남" or "전라"

    # ELECD_NM에 키워드가 포함된 의원 검색 (district_base 우선, 그 다음 region)
    # ELECD_NM은 "22대선거구/21대선거구/..." 형식으로 역대 선거구가 모두 포함됨
    member_row = None
    matched_kw = ""
    for kw in kw_list:
        matched = [r for r in members if kw in (r.get("ELECD_NM") or "")]
        if matched:
            member_row = next((r for r in matched if "개혁" in (r.get("PLPT_NM") or "")), matched[0])
            matched_kw = kw
            break

    if not member_row:
        _set_cached(cache_k, {"member": None})
        return None

    # 22대 실제 선거구: ELECD_NM의 첫 번째 슬래시 앞 부분
    elecd_all = member_row.get("ELECD_NM") or ""
    elecd_22nd = elecd_all.split("/")[0].strip()

    # matched_kw가 실제 22대 선거구에 없으면 (이전 선거구에서 매칭) 노트 추가
    district_note = ""
    if matched_kw and matched_kw not in elecd_22nd:
        # 매칭된 구체적 선거구명 찾기 (이전 선거구)
        matched_old = next((d for d in elecd_all.split("/") if matched_kw in d), "")
        district_note = f"(전 {matched_old.strip()})" if matched_old else ""

    display_district = elecd_22nd + (" " + district_note if district_note else "")

    result = {
        "name": member_row.get("NAAS_NM", ""),
        "party": (member_row.get("PLPT_NM") or "").split("/")[0].strip(),
        "committee": member_row.get("CMIT_NM") or member_row.get("BLNG_CMIT_NM", ""),
        "district": display_district,
        "age": member_row.get("GTELT_ERACO", ""),
        "photo": member_row.get("NAAS_PIC", ""),
    }
    _set_cached(cache_k, {"member": result})
    return result


# ---------------------------------------------------------------------------
# 국회의원 발의법안 조회  (nwbpacrgavhjryiph)
# ---------------------------------------------------------------------------
def search_bills_by_member(member_name: str, limit: int = 10) -> list[dict]:
    """
    의원명으로 22대 발의법안 검색.
    반환: [{title, propose_date, status, bill_no}]
    """
    if not NATIONAL_ASSEMBLY_API_KEY or not member_name:
        return []

    cache_k = _cache_key("bills22", member_name=member_name, limit=limit)
    cached = _get_cached(cache_k)
    if cached is not None:
        return cached.get("bills", [])

    params = {
        "KEY": NATIONAL_ASSEMBLY_API_KEY,
        "Type": "json",
        "pIndex": 1,
        "pSize": min(limit, 100),
        "AGE": CURRENT_AGE,
        "BILL_KIND_CD": "bill",
        "PROPOSER": member_name,
    }
    data = _http_get("nwbpacrgavhjryiph", params)
    if not data:
        return []

    try:
        rows = data.get("nwbpacrgavhjryiph", [{}])[1].get("row", [])
    except (IndexError, KeyError, TypeError):
        rows = []

    bills = []
    for r in rows[:limit]:
        bills.append({
            "title": r.get("BILL_NM", ""),
            "propose_date": r.get("PROPOSE_DT", ""),
            "status": r.get("PROC_RESULT_CD", "") or r.get("PROC_RESULT", "") or "처리중",
            "bill_no": r.get("BILL_NO", ""),
        })

    _set_cached(cache_k, {"bills": bills})
    return bills


# ---------------------------------------------------------------------------
# 통합 조회 (엔드포인트용)
# ---------------------------------------------------------------------------
def query_national_assembly_overview(region: str, district: str = "") -> dict:
    """
    지역구 국회의원 + 발의법안 통합 조회.
    API 키 없으면 {"available": False} 반환.
    """
    if not NATIONAL_ASSEMBLY_API_KEY:
        return {"available": False, "reason": "API 키 미설정"}

    member = search_member_by_district(region, district)
    bills: list[dict] = []
    if member and member.get("name"):
        bills = search_bills_by_member(member["name"], limit=10)

    return {
        "available": True,
        "member": member,
        "bills": bills,
    }
