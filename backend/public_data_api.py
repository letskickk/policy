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
    district_raw = (district_name or "").strip()

    # "서울특별시 강북구" → province / district 분리
    parts = region.replace("  ", " ").split()
    province = ""
    district = ""

    # region_name에서 시도/구 추출
    if len(parts) >= 2:
        province = parts[0]
        district = parts[-1]  # "강북구"
    elif len(parts) == 1:
        token = parts[0]
        if token in _PROVINCE_MAP or token in _PROVINCE_MAP.values():
            province = token
        else:
            district = token

    # district_name이 "강북구 가선거구" 같은 형태면, 구 이름 추출
    if district_raw:
        # "강북구 가선거구" → "강북구" 추출
        dn_parts = district_raw.split()
        for p in dn_parts:
            if p.endswith("구") or p.endswith("시") or p.endswith("군"):
                if not district:
                    district = p
                break

    if not district and district_raw:
        district = district_raw.split()[0] if district_raw.split() else district_raw

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
        "district": district,             # "강북구"
        "district_raw": district_raw,     # "강북구 가선거구" (원본)
        "district_short": district_short, # "강북"
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
                   headers: Optional[dict] = None,
                   encode_plus: bool = False) -> Optional[dict | list]:
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

    if encode_plus:
        full_url = url + "?" + urllib.parse.urlencode(params)
    else:
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
        except (OSError, TimeoutError) as e:
            # 타임아웃/네트워크 에러는 재시도 안 함
            logger.warning("public_data API timeout/network: %s — %s", url, e)
            return None
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
# 1. 소상공인 상권정보 (apis.data.go.kr)
# ---------------------------------------------------------------------------
# https://apis.data.go.kr/B553077/api/open/sdsc/storeListInDong
# divId: ctprvnCd(시도), signguCd(시군구), adongCd(행정동)
# ---------------------------------------------------------------------------

SEMAS_BASE = "https://apis.data.go.kr/B553077/api/open/sdsc"


def _fetch_semas_commercial(region_info: dict) -> dict:
    """소상공인 상권정보 — 시군구 기준 상가업소 조회."""
    from backend.config import SEMAS_API_KEY

    source = "semas"
    if not SEMAS_API_KEY:
        return _empty_result(source, "API 키 미설정")

    district = region_info["district"]
    province_code = region_info["province_code"]
    if not district:
        return _empty_result(source, "지역 미지정")

    ck = _cache_key("semas_v2", district=district, province=region_info["province"])
    cached = _get_cached(ck)
    if cached:
        cached["from_cache"] = True
        return cached

    # 시도 코드로 상가 목록 조회
    url = f"{SEMAS_BASE}/storeListInDong"
    params = {
        "ServiceKey": SEMAS_API_KEY,
        "divId": "ctprvnCd",
        "key": province_code or "11",
        "numOfRows": "200",
        "pageNo": "1",
        "type": "json",
    }
    raw = _http_get_json(url, params, timeout=15)

    items = []
    if raw and isinstance(raw, dict):
        body = raw.get("body", raw)
        if isinstance(body, dict):
            item_list = body.get("items", [])
            if isinstance(item_list, dict):
                item_list = item_list.get("item", [])
                if isinstance(item_list, dict):
                    item_list = [item_list]
            items = item_list if isinstance(item_list, list) else []

    # 지역 필터
    filtered = []
    district_short = region_info["district_short"]
    for item in items:
        addr = (item.get("rdnmAdr") or item.get("lnoAdr") or
                item.get("rdnWhlAddr") or "")
        gu = item.get("signguNm") or ""
        if district in addr or district in gu or district_short in addr:
            filtered.append(item)

    # 업종 분포 요약
    summary_lines = []
    if filtered:
        biz_counts = {}
        for item in filtered:
            biz = item.get("indsLclsNm") or item.get("indsMclsNm") or "기타"
            biz_counts[biz] = biz_counts.get(biz, 0) + 1
        top_biz = sorted(biz_counts.items(), key=lambda x: x[1], reverse=True)[:8]
        summary_lines.append(f"{district} 상가 현황 ({len(filtered)}개 업소):")
        for biz, cnt in top_biz:
            summary_lines.append(f"  - {biz}: {cnt}개")
    elif items:
        summary_lines.append(f"상가 데이터 {len(items)}건 조회됨 ({district} 필터 결과 없음)")
    else:
        summary_lines.append("상가 데이터 없음")

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
# 2. TAAS 교통사고 다발지역 (opendata.koroad.or.kr)
# ---------------------------------------------------------------------------
# 도로교통공단 TAAS 오픈 API
# authKey, searchYearCd, siDo(2자리), guGun(3자리)
# ---------------------------------------------------------------------------

# 서버(AWS 시드니)에서 opendata.koroad.or.kr 차단됨 → api.odcloud.kr 사용
TAAS_ODCLOUD = "https://api.odcloud.kr/api/15045638/v1/uddi:69cb47bd-0373-4dee-9101-a1878f8c97c4"
# opendata.koroad.or.kr (로컬/한국 서버용 fallback)
TAAS_KOROAD_BASE = "https://opendata.koroad.or.kr/data/rest"
TAAS_KOROAD_ENDPOINTS = {
    "지자체별": "/frequentzone/lg",
    "보행자": "/frequentzone/pedstrians",
    "보행어린이": "/frequentzone/child",
    "보행노인": "/frequentzone/oldman",
    "어린이보호구역": "/frequentzone/schoolzone/child",
}

# 시군구 코드 매핑 (시도코드 2자리 + 시군구 3자리)
_GUGUN_MAP = {}  # 런타임에 필요하면 확장


def _fetch_taas_accidents(region_info: dict) -> dict:
    """교통사고 통계 조회. odcloud(연도별) 우선, koroad(다발지역) fallback."""
    from backend.config import TAAS_API_KEY, DATA_GO_KR_API_KEY

    source = "taas"
    district = region_info["district"]

    ck = _cache_key("taas_v3", province=region_info["province"], district=district)
    cached = _get_cached(ck)
    if cached:
        cached["from_cache"] = True
        return cached

    # 1) odcloud 교통사고 연도별 통계 (서버에서 항상 접근 가능)
    odcloud_key = DATA_GO_KR_API_KEY
    if odcloud_key:
        params = {"serviceKey": odcloud_key, "page": "1", "perPage": "30", "returnType": "JSON"}
        raw = _http_get_json(TAAS_ODCLOUD, params, timeout=8)
        if raw and isinstance(raw, dict) and raw.get("data"):
            items = raw["data"]
            recent = sorted(items, key=lambda x: int(x.get("연도") or 0), reverse=True)[:5]
            summary_lines = ["교통사고 통계 (최근 5년):"]
            for item in recent:
                summary_lines.append(
                    f"  - {item.get('연도')}년: 사고 {item.get('사고')}건, "
                    f"사망 {item.get('사망')}명, 부상 {item.get('부상')}명"
                )
            result = {
                "source": source, "available": True, "data": recent,
                "summary": "\n".join(summary_lines), "item_count": len(recent),
            }
            _set_cached(ck, result)
            return result

    # 2) koroad 다발지역 (한국 서버에서만 접근 가능)
    if TAAS_API_KEY:
        province_code = region_info["province_code"]
        current_year = datetime.now().year
        all_accidents = {}

        for label, endpoint in TAAS_KOROAD_ENDPOINTS.items():
            url = TAAS_KOROAD_BASE + endpoint
            params = {
                "authKey": TAAS_API_KEY,
                "searchYearCd": str(current_year - 2),
                "siDo": province_code or "11",
                "guGun": "",
                "type": "json",
                "numOfRows": "200",
                "pageNo": "1",
            }
            raw = _http_get_json(url, params, timeout=5)
            if not raw:
                continue
            try:
                items_wrapper = raw.get("items", {})
                items = items_wrapper.get("item", []) if isinstance(items_wrapper, dict) else []
                if isinstance(items, dict):
                    items = [items]
                district_short = region_info["district_short"]
                filtered = [
                    item for item in (items or [])
                    if (district and (district in (item.get("spot_nm") or "") or district in (item.get("sido_sgg_nm") or "")))
                    or (district_short and (district_short in (item.get("spot_nm") or "") or district_short in (item.get("sido_sgg_nm") or "")))
                    or not district
                ]
                if filtered:
                    all_accidents[label] = filtered[:5]
            except Exception as e:
                logger.warning("TAAS %s parse error: %s", label, e)

        if all_accidents:
            total = sum(len(v) for v in all_accidents.values())
            summary_lines = [f"{district or region_info['province']} 교통사고 다발지역 ({total}건):"]
            for label, items in all_accidents.items():
                summary_lines.append(f"  [{label}] {len(items)}건")
                for item in items[:3]:
                    spot = item.get("spot_nm") or "위치 미상"
                    cnt = item.get("occrrnc_cnt") or ""
                    death = item.get("dth_dnv_cnt") or ""
                    line = f"    - {spot}"
                    if cnt:
                        line += f" (사고 {cnt}건"
                        if death:
                            line += f", 사망 {death}명"
                        line += ")"
                    summary_lines.append(line)
            result = {
                "source": source, "available": True, "data": all_accidents,
                "summary": "\n".join(summary_lines),
                "category_count": len(all_accidents), "total_spots": total,
            }
            _set_cached(ck, result)
            return result

    return _empty_result(source, "API 호출 실패")



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

# 주제 → API 매핑
_TOPIC_API_MAP = {
    "kosis": ["인구", "세대", "고령", "청년", "노인", "1인가구", "전입", "전출", "주민", "연령", "출산", "인구구조"],
    "taas": ["교통", "사고", "안전", "보행", "어린이", "통학", "보호구역", "횡단보도", "도로", "주차", "자전거"],
    "semas": ["상권", "상가", "자영업", "골목", "경제", "업종", "폐업", "창업", "소상공인", "시장"],
    "seoul": ["시설", "도서관", "체육", "공원", "복지", "문화", "주차장", "CCTV", "돌봄", "경로당", "어린이집"],
}


def _select_relevant_apis(all_fetchers: dict, topic: str, keywords: list[str] | None) -> dict:
    """주제/키워드 기반으로 필요한 API만 선택. 매칭 없으면 kosis만."""
    if not topic and not keywords:
        return {"kosis": all_fetchers["kosis"]}

    text = (topic + " " + " ".join(keywords or [])).lower()
    selected = {}
    for api_name, trigger_words in _TOPIC_API_MAP.items():
        if api_name in all_fetchers and any(w in text for w in trigger_words):
            selected[api_name] = all_fetchers[api_name]

    # 매칭 없으면 kosis(인구)만 기본 제공
    if not selected:
        selected["kosis"] = all_fetchers["kosis"]

    return selected


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

    # 주제 키워드 기반으로 필요한 API만 선택
    all_fetchers = {
        "semas": lambda: _fetch_semas_commercial(region_info),
        "taas": lambda: _fetch_taas_accidents(region_info),
        "kosis": lambda: _fetch_kosis_population(region_info),
        "seoul": lambda: _fetch_seoul_facilities(region_info),
    }

    fetchers = _select_relevant_apis(all_fetchers, topic, keywords)

    if not fetchers:
        return {"available": False, "context_text": "", "sources": {}}

    sources = {}
    with ThreadPoolExecutor(max_workers=len(fetchers)) as pool:
        futures = {pool.submit(fn): name for name, fn in fetchers.items()}
        for future in as_completed(futures, timeout=15):
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

    # TAAS (odcloud)
    from backend.config import DATA_GO_KR_API_KEY as _dgk
    if _dgk:
        params = {"serviceKey": _dgk, "page": "1", "perPage": "1", "returnType": "JSON"}
        raw = _http_get_json(TAAS_ODCLOUD, params, timeout=8)
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
