"""
공공데이터 API 통합 모듈.

소상공인 상권정보 · TAAS 교통사고 · KOSIS 인구통계 · 서울 열린데이터를
research_assistant 브리핑에 공급.

API 키 미설정이거나 호출 실패 시 빈 결과 반환 (graceful degradation).
결과는 24시간 캐시.
"""

from __future__ import annotations

import hashlib
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.database import get_connection

logger = logging.getLogger(__name__)

CACHE_TTL_HOURS = 24

# ---------------------------------------------------------------------------
# 지역명 정규화
# ---------------------------------------------------------------------------
_PROVINCE_MAP = {
    "서울": "서울특별시", "부산": "부산광역시", "대구": "대구광역시",
    "인천": "인천광역시", "광주": "광주광역시", "대전": "대전광역시",
    "울산": "울산광역시", "세종": "세종특별자치시", "경기": "경기도",
    "강원": "강원특별자치도", "충북": "충청북도", "충남": "충청남도",
    "전북": "전북특별자치도", "전남": "전라남도", "경북": "경상북도",
    "경남": "경상남도", "제주": "제주특별자치도",
}

_PROVINCE_CODE = {
    "서울특별시": "11", "부산광역시": "26", "대구광역시": "27",
    "인천광역시": "28", "광주광역시": "29", "대전광역시": "30",
    "울산광역시": "31", "세종특별자치시": "36", "경기도": "41",
    "강원특별자치도": "42", "충청북도": "43", "충청남도": "44",
    "전북특별자치도": "45", "전라남도": "46", "경상북도": "47",
    "경상남도": "48", "제주특별자치도": "50",
}

# 서울 자치구 목록 (서울 열린데이터 사용 여부 판단용)
_SEOUL_DISTRICTS = {
    "종로구", "중구", "용산구", "성동구", "광진구", "동대문구", "중랑구",
    "성북구", "강북구", "도봉구", "노원구", "은평구", "서대문구", "마포구",
    "양천구", "강서구", "구로구", "금천구", "영등포구", "동작구", "관악구",
    "서초구", "강남구", "송파구", "강동구",
}


def normalize_region(region: Optional[str], district_name: Optional[str] = None) -> dict:
    """지역명을 API 호출에 필요한 형태로 분해."""
    region = (region or "").strip()
    district = (district_name or "").strip()

    # "서울특별시 강북구" → province / district 분리
    parts = region.replace("  ", " ").split()
    province = ""
    if not district:
        if len(parts) >= 2:
            province = parts[0]
            district = parts[-1]
        elif len(parts) == 1:
            token = parts[0]
            # 시도 이름인지, 구/군 이름인지
            if token in _PROVINCE_MAP or token in _PROVINCE_MAP.values():
                province = token
            else:
                district = token
    else:
        province = region if len(parts) <= 1 else parts[0]

    # 시도 정규화
    province_full = _PROVINCE_MAP.get(province, province)
    if not province_full and district in _SEOUL_DISTRICTS:
        province_full = "서울특별시"

    province_code = _PROVINCE_CODE.get(province_full, "")
    district_short = district.replace("구", "").replace("시", "").replace("군", "") if district else ""
    is_seoul = province_full == "서울특별시"

    return {
        "province": province_full,
        "province_code": province_code,
        "district": district,
        "district_short": district_short,
        "is_seoul": is_seoul,
    }


# ---------------------------------------------------------------------------
# Cache helpers (assembly_api.py 동일 패턴)
# ---------------------------------------------------------------------------
def _cache_key(prefix: str, **kwargs) -> str:
    raw = prefix + "|" + json.dumps(kwargs, sort_keys=True, ensure_ascii=False)
    return "pubdata_" + hashlib.sha256(raw.encode()).hexdigest()[:24]


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
    expires = (datetime.now(timezone.utc) + timedelta(hours=CACHE_TTL_HOURS)).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO analysis_cache
               (user_id, cache_key, request_fingerprint, result_payload, expires_at)
               VALUES (0, ?, ?, ?, ?)""",
            (key, "public_data_api", json.dumps(data, ensure_ascii=False), expires),
        )
        conn.commit()
    except Exception as e:
        logger.warning("public_data cache save failed: %s", e)
    finally:
        conn.close()


def _set_rate_limit_backoff(endpoint: str) -> None:
    key = "ratelimit_pubdata_" + hashlib.sha256(endpoint.encode()).hexdigest()[:16]
    expires = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO analysis_cache
               (user_id, cache_key, request_fingerprint, result_payload, expires_at)
               VALUES (0, ?, ?, ?, ?)""",
            (key, "rate_limit_backoff", json.dumps({"endpoint": endpoint}), expires),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def _is_rate_limited(endpoint: str) -> bool:
    key = "ratelimit_pubdata_" + hashlib.sha256(endpoint.encode()).hexdigest()[:16]
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT expires_at FROM analysis_cache WHERE cache_key = ?", (key,)
        ).fetchone()
        if not row:
            return False
        return row["expires_at"] > datetime.now(timezone.utc).isoformat()
    except Exception:
        return False
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------
def _http_get_json(url: str, params: dict, *, timeout: int = 10,
                   headers: Optional[dict] = None) -> Optional[dict | list]:
    """HTTP GET → JSON. 실패 시 None. 3회 재시도."""
    if _is_rate_limited(url):
        return None

    _headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (PolicyMentor)",
        "Referer": "https://www.data.go.kr/",
    }
    if headers:
        _headers.update(headers)

    full_url = url + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    last_err = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(full_url, headers=_headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return json.loads(body)
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 429:
                logger.warning("public_data API rate limit (429): %s", url)
                _set_rate_limit_backoff(url)
                return None
            if e.code in (401, 403):
                logger.warning("public_data API auth error %d: %s", e.code, url)
                return None
            if attempt < 2:
                import time
                time.sleep(0.5 * (1 + attempt))
        except Exception as e:
            last_err = e
            if attempt < 2:
                import time
                time.sleep(0.5 * (1 + attempt))

    logger.warning("public_data API failed after 3 attempts: %s — %s", url, last_err)
    return None


def _empty_result(source: str, reason: str = "") -> dict:
    return {"source": source, "available": False, "data": [], "summary": "", "reason": reason}


# ---------------------------------------------------------------------------
# 1. 소상공인 상권정보 (api.odcloud.kr)
# ---------------------------------------------------------------------------
# 소상공인시장진흥공단_상가(상권)정보, namespace=15083033
# Base URL: https://api.odcloud.kr/api/15083033/v1/{endpoint_id}
# 인증: serviceKey 쿼리 파라미터
# ---------------------------------------------------------------------------

ODCLOUD_BASE = "https://api.odcloud.kr/api"
# 소상공인 상가정보 엔드포인트 (시도/읍면동별 업소수)
SEMAS_ENDPOINT = f"{ODCLOUD_BASE}/15083033/v1/uddi:c7049f5a-d95e-4143-be96-b4d3c16130ee"


def _fetch_semas_commercial(region_info: dict) -> dict:
    """소상공인 상권정보 조회 — 읍면동별 업소수 현황."""
    from backend.config import SEMAS_API_KEY

    source = "semas"
    if not SEMAS_API_KEY:
        return _empty_result(source, "API 키 미설정")

    district = region_info["district"]
    if not district:
        return _empty_result(source, "지역 미지정")

    ck = _cache_key("semas", district=district)
    cached = _get_cached(ck)
    if cached:
        cached["from_cache"] = True
        return cached

    params = {
        "serviceKey": SEMAS_API_KEY,
        "page": "1",
        "perPage": "500",
        "returnType": "JSON",
    }
    raw = _http_get_json(SEMAS_ENDPOINT, params, timeout=15)
    if not raw or not isinstance(raw, dict):
        return _empty_result(source, "API 호출 실패")

    items = raw.get("data", [])
    # 지역 필터링: district(구/시)가 포함된 항목
    district_short = region_info["district_short"]
    filtered = []
    for item in items:
        sido = item.get("시도") or ""
        dong = item.get("읍면동") or ""
        if district in sido or district_short in sido or district in dong:
            filtered.append(item)

    # 요약
    summary_lines = []
    if filtered:
        total_stores = sum(int(item.get("업소수") or 0) for item in filtered)
        summary_lines.append(f"{district} 상권현황 ({len(filtered)}개 동):")
        summary_lines.append(f"  - 총 업소수: {total_stores:,}개")
        top = sorted(filtered, key=lambda x: int(x.get("업소수") or 0), reverse=True)[:5]
        for item in top:
            summary_lines.append(f"  - {item.get('읍면동', '')}: {item.get('업소수', '')}개")
    elif items:
        summary_lines.append("상권 데이터 조회됨 (해당 지역 필터링 결과 없음)")
    else:
        summary_lines.append("상권 데이터 없음")

    result = {
        "source": source,
        "available": bool(filtered),
        "data": filtered[:30],
        "summary": "\n".join(summary_lines),
        "item_count": len(filtered),
    }
    _set_cached(ck, result)
    return result


# ---------------------------------------------------------------------------
# 2. 한국도로공사 교통사고통계 (api.odcloud.kr)
# ---------------------------------------------------------------------------
# namespace=15045638, 연도별 사고/사망/부상
# ---------------------------------------------------------------------------

TAAS_ENDPOINT = f"{ODCLOUD_BASE}/15045638/v1/uddi:69cb47bd-0373-4dee-9101-a1878f8c97c4"


def _fetch_taas_accidents(region_info: dict) -> dict:
    """한국도로공사 교통사고통계 조회."""
    from backend.config import TAAS_API_KEY

    source = "taas"
    if not TAAS_API_KEY:
        return _empty_result(source, "API 키 미설정")

    ck = _cache_key("taas", province=region_info["province"])
    cached = _get_cached(ck)
    if cached:
        cached["from_cache"] = True
        return cached

    params = {
        "serviceKey": TAAS_API_KEY,
        "page": "1",
        "perPage": "30",
        "returnType": "JSON",
    }
    raw = _http_get_json(TAAS_ENDPOINT, params, timeout=15)
    if not raw or not isinstance(raw, dict):
        return _empty_result(source, "API 호출 실패")

    items = raw.get("data", [])
    if not items:
        return _empty_result(source, "데이터 없음")

    recent = sorted(items, key=lambda x: int(x.get("연도") or 0), reverse=True)[:5]

    summary_lines = []
    summary_lines.append("교통사고 통계 (고속도로, 최근 5년):")
    for item in recent:
        yr = item.get("연도", "")
        acc = item.get("사고", "")
        death = item.get("사망", "")
        inj = item.get("부상", "")
        summary_lines.append(f"  - {yr}년: 사고 {acc}건, 사망 {death}명, 부상 {inj}명")

    result = {
        "source": source,
        "available": bool(recent),
        "data": recent,
        "summary": "\n".join(summary_lines),
        "item_count": len(recent),
    }
    _set_cached(ck, result)
    return result



# ---------------------------------------------------------------------------
# 3. KOSIS 인구통계 (kosis.kr)
# ---------------------------------------------------------------------------
# https://kosis.kr/openapi/Param/statisticsParameterData.do
# apiKey, itmId, objL1~8, orgId, tblId, prdSe, startPrdDe, endPrdDe, format
# 주민등록인구현황: orgId=101, tblId=DT_1B040A3 (시군구/성/연령별)
# ---------------------------------------------------------------------------

KOSIS_BASE = "https://kosis.kr/openapi/Param/statisticsParameterData.do"


def _fetch_kosis_population(region_info: dict) -> dict:
    """KOSIS 시군구별 인구·세대 통계 조회."""
    from backend.config import KOSIS_API_KEY

    source = "kosis"
    if not KOSIS_API_KEY:
        return _empty_result(source, "API 키 미설정")

    district = region_info["district"]
    province = region_info["province"]
    if not province:
        return _empty_result(source, "시도 미지정")

    ck = _cache_key("kosis", province=province, district=district)
    cached = _get_cached(ck)
    if cached:
        cached["from_cache"] = True
        return cached

    current_year = datetime.now().year
    all_items = []

    # 1) 시군구별 주민등록세대수 (DT_1B040B3)
    params_household = {
        "method": "getList",
        "apiKey": KOSIS_API_KEY,
        "format": "json",
        "jsonVD": "Y",
        "orgId": "101",
        "tblId": "DT_1B040B3",
        "prdSe": "M",
        "startPrdDe": f"{current_year}01",
        "endPrdDe": f"{current_year}01",
        "objL1": "ALL",
        "itmId": "ALL",
    }
    raw1 = _http_get_json(KOSIS_BASE, params_household, timeout=15,
                          headers={"Referer": "https://kosis.kr/"})
    if isinstance(raw1, list):
        all_items.extend(raw1)

    # 2) 시군구별 성별 인구수 (DT_1B040A3)
    params_pop = {
        "method": "getList",
        "apiKey": KOSIS_API_KEY,
        "format": "json",
        "jsonVD": "Y",
        "orgId": "101",
        "tblId": "DT_1B040A3",
        "prdSe": "M",
        "startPrdDe": f"{current_year}01",
        "endPrdDe": f"{current_year}01",
        "objL1": "ALL",
        "objL2": "ALL",
        "itmId": "ALL",
    }
    raw2 = _http_get_json(KOSIS_BASE, params_pop, timeout=15,
                          headers={"Referer": "https://kosis.kr/"})
    if isinstance(raw2, list):
        all_items.extend(raw2)

    if not all_items:
        return _empty_result(source, "API 호출 실패")

    # 지역 필터링
    district_items = []
    province_items = []
    for item in all_items:
        c1_nm = item.get("C1_NM") or ""
        c1_eng = item.get("C1_NM_ENG") or ""
        if district and district in c1_nm:
            district_items.append(item)
        elif province and province in c1_nm:
            province_items.append(item)

    use_items = district_items or province_items

    # 요약
    summary_lines = []
    if use_items:
        target = district or province
        summary_lines.append(f"{target} 인구통계:")
        seen = set()
        for item in use_items:
            itm_nm = item.get("ITM_NM") or ""
            c2_nm = item.get("C2_NM") or ""
            val = item.get("DT") or ""
            unit = item.get("UNIT_NM") or ""
            prd = item.get("PRD_DE") or ""
            label = f"{itm_nm} {c2_nm}".strip()
            if val and label not in seen:
                seen.add(label)
                summary_lines.append(f"  - {label}: {val}{unit} ({prd})")
                if len(seen) >= 10:
                    break
    else:
        summary_lines.append("인구통계 데이터 없음")

    result = {
        "source": source,
        "available": bool(use_items),
        "data": use_items[:30],
        "summary": "\n".join(summary_lines),
        "item_count": len(use_items),
    }
    _set_cached(ck, result)
    return result


# ---------------------------------------------------------------------------
# 4. 서울 열린데이터 (data.seoul.go.kr)
# ---------------------------------------------------------------------------
# URL 형식: http://openapi.seoul.go.kr:8088/{KEY}/json/{서비스명}/{시작}/{끝}
# 주요 서비스:
#   - ListPublicReservationSport: 체육시설
#   - GnrlMltlParkInf: 공영주차장
#   - tvCultureEvent: 문화행사
#   - ListPubLibrarySeoIl: 공공도서관
# ---------------------------------------------------------------------------

SEOUL_BASE = "http://openapi.seoul.go.kr:8088"


def _fetch_seoul_facilities(region_info: dict) -> dict:
    """서울 열린데이터 — 생활시설(주차장/도서관/체육/복지) 현황."""
    from backend.config import SEOUL_OPEN_API_KEY

    source = "seoul"
    if not SEOUL_OPEN_API_KEY:
        return _empty_result(source, "API 키 미설정")
    if not region_info["is_seoul"]:
        return _empty_result(source, "서울 외 지역")

    district = region_info["district"]
    ck = _cache_key("seoul", district=district)
    cached = _get_cached(ck)
    if cached:
        cached["from_cache"] = True
        return cached

    key = urllib.parse.quote(SEOUL_OPEN_API_KEY, safe="")
    facility_results = {}

    services = {
        "공영주차장": "GnrlMltlParkInf",
        "공공도서관": "SeoulPublicLibraryInfo",
        "체육시설": "ListPublicReservationSport",
        "문화행사": "culturalEventInfo",
    }

    for label, svc in services.items():
        url = f"{SEOUL_BASE}/{key}/json/{svc}/1/100"
        try:
            req = urllib.request.Request(url, headers={
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (PolicyMentor)",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                data = json.loads(body)

            # 서울 API는 서비스명이 최상위 키
            svc_data = data.get(svc, {})
            rows = svc_data.get("row", [])
            if isinstance(rows, dict):
                rows = [rows]

            # 구 필터링
            filtered = []
            for row in rows:
                addr = row.get("ADDR") or row.get("ADDRESS") or row.get("PLACENM") or ""
                gu = row.get("GUNAME") or row.get("GU_NAME") or ""
                if district and (district in addr or district in gu):
                    filtered.append(row)

            if filtered:
                facility_results[label] = {
                    "count": len(filtered),
                    "items": filtered[:5],
                }
        except Exception as e:
            logger.warning("Seoul API %s error: %s", label, e)

    # 요약
    summary_lines = []
    if facility_results:
        summary_lines.append(f"{district} 생활시설 현황:")
        for label, info in facility_results.items():
            summary_lines.append(f"  - {label}: {info['count']}건")
    else:
        summary_lines.append("서울 생활시설 데이터 없음")

    result = {
        "source": source,
        "available": bool(facility_results),
        "data": facility_results,
        "summary": "\n".join(summary_lines),
    }
    _set_cached(ck, result)
    return result


# ---------------------------------------------------------------------------
# 통합 조회 함수
# ---------------------------------------------------------------------------

def query_public_data_context(
    *,
    region: Optional[str] = None,
    district_name: Optional[str] = None,
    topic: str = "",
    keywords: Optional[list[str]] = None,
) -> dict:
    """
    공공데이터 통합 조회 — 4개 API 병렬 호출 후 합산.

    Returns:
        {
            "available": bool,
            "context_text": str,  # 브리핑 텍스트
            "sources": {source_name: result_dict, ...},
        }
    """
    region_info = normalize_region(region, district_name)

    if not region_info["province"] and not region_info["district"]:
        return {"available": False, "context_text": "", "sources": {}}

    fetchers = {
        "semas": lambda: _fetch_semas_commercial(region_info),
        "taas": lambda: _fetch_taas_accidents(region_info),
        "kosis": lambda: _fetch_kosis_population(region_info),
        "seoul": lambda: _fetch_seoul_facilities(region_info),
    }

    sources = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fn): name for name, fn in fetchers.items()}
        for future in as_completed(futures, timeout=30):
            name = futures[future]
            try:
                sources[name] = future.result()
            except Exception as e:
                logger.warning("public_data %s error: %s", name, e)
                sources[name] = _empty_result(name, str(e))

    # 브리핑 텍스트 생성
    target = region_info["district"] or region_info["province"]
    sections = []

    for name in ["kosis", "semas", "taas", "seoul"]:
        res = sources.get(name, {})
        if res.get("available") and res.get("summary"):
            sections.append(res["summary"])

    context_text = ""
    if sections:
        context_text = f"[공공데이터 — {target} 현황]\n" + "\n\n".join(sections)

    return {
        "available": bool(sections),
        "context_text": context_text,
        "sources": sources,
    }


# ---------------------------------------------------------------------------
# 디버그: API 키 유효성 테스트
# ---------------------------------------------------------------------------

def test_all_apis() -> dict:
    """모든 공공 API에 최소 호출을 보내서 키 유효성 확인."""
    from backend.config import SEMAS_API_KEY, TAAS_API_KEY, KOSIS_API_KEY, SEOUL_OPEN_API_KEY

    results = {}

    # SEMAS
    if SEMAS_API_KEY:
        params = {"serviceKey": SEMAS_API_KEY, "page": "1", "perPage": "1", "returnType": "JSON"}
        raw = _http_get_json(SEMAS_ENDPOINT, params, timeout=10)
        results["semas"] = {"key_set": True, "response": bool(raw),
                            "sample": str(raw)[:200] if raw else None}
    else:
        results["semas"] = {"key_set": False}

    # TAAS
    if TAAS_API_KEY:
        params = {"serviceKey": TAAS_API_KEY, "page": "1", "perPage": "1", "returnType": "JSON"}
        raw = _http_get_json(TAAS_ENDPOINT, params, timeout=10)
        results["taas"] = {"key_set": True, "response": bool(raw),
                           "sample": str(raw)[:200] if raw else None}
    else:
        results["taas"] = {"key_set": False}

    # KOSIS
    if KOSIS_API_KEY:
        params = {"method": "getList", "apiKey": KOSIS_API_KEY, "itmId": "ALL", "objL1": "ALL",
                  "format": "json", "jsonVD": "Y", "prdSe": "M",
                  "startPrdDe": "202501", "endPrdDe": "202501",
                  "orgId": "101", "tblId": "DT_1B040B3"}
        raw = _http_get_json(KOSIS_BASE, params, timeout=15,
                             headers={"Referer": "https://kosis.kr/"})
        results["kosis"] = {"key_set": True, "response": bool(raw),
                            "sample": str(raw)[:200] if raw else None}
    else:
        results["kosis"] = {"key_set": False}

    # Seoul
    if SEOUL_OPEN_API_KEY:
        key = urllib.parse.quote(SEOUL_OPEN_API_KEY, safe="")
        url = f"{SEOUL_BASE}/{key}/json/GnrlMltlParkInf/1/1"
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                data = json.loads(body)
            results["seoul"] = {"key_set": True, "response": True,
                                "sample": str(data)[:200]}
        except Exception as e:
            results["seoul"] = {"key_set": True, "response": False, "error": str(e)}
    else:
        results["seoul"] = {"key_set": False}

    return results
