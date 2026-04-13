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
            # district는 비워둠 — 광역시 전체 단위로 province 필터 사용
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

    if not params:
        full_url = url  # URL에 이미 파라미터가 포함된 경우
    elif encode_plus:
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
# https://apis.data.go.kr/B553077/api/open/sdsc2/storeListInDong  (sdsc2 = 최신 버전)
# divId: ctprvnCd(시도), signguCd(시군구), adongCd(행정동)
# ---------------------------------------------------------------------------

SEMAS_BASE = "https://apis.data.go.kr/B553077/api/open/sdsc2"


def _fetch_semas_commercial(region_info: dict) -> dict:
    """소상공인 상권정보 — 시군구 기준 상가업소 조회."""
    from backend.config import SEMAS_API_KEY

    source = "semas"
    if not SEMAS_API_KEY:
        return _empty_result(source, "API 키 미설정")

    district = region_info["district"]
    province = region_info["province"]
    province_code = region_info["province_code"]

    # metro_mayor: district="" but province is 광역시/특별시 → 시 전체 조회
    is_metro_wide = not district and bool(province_code)
    if not district and not province_code:
        return _empty_result(source, "지역 미지정")

    display_name = district or province
    ck = _cache_key("semas_v2", district=district, province=province)
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
        "numOfRows": "500",  # metro_mayor는 더 많이 받아 전체 업종 파악
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
    # metro_wide(시 전체): 필터 없이 전체 반환, 구별 분포도 집계
    filtered = []
    district_short = region_info["district_short"]
    if is_metro_wide:
        filtered = items  # 시 전체 데이터 그대로 사용
    else:
        for item in items:
            addr = (item.get("rdnmAdr") or item.get("lnoAdr") or
                    item.get("rdnWhlAddr") or "")
            gu = item.get("signguNm") or ""
            if district in addr or district in gu or district_short in addr:
                filtered.append(item)

    # 업종 분포 요약
    summary_lines = []
    if filtered:
        total = len(filtered)
        biz_counts = {}
        gu_counts = {}
        for item in filtered:
            biz = item.get("indsLclsNm") or item.get("indsMclsNm") or "기타"
            biz_counts[biz] = biz_counts.get(biz, 0) + 1
            if is_metro_wide:
                gu = item.get("signguNm") or ""
                if gu:
                    gu_counts[gu] = gu_counts.get(gu, 0) + 1
        top_biz = sorted(biz_counts.items(), key=lambda x: x[1], reverse=True)[:8]
        summary_lines.append(f"{display_name} 상권 업종 분포 (SEMAS 기준):")
        for biz, cnt in top_biz:
            pct = round(cnt / total * 100)
            summary_lines.append(f"  - {biz}: {cnt}개 ({pct}%)")
        # 생활밀착형 vs 전문형 분류
        life_keys = {"소매", "음식", "수리·개인"}
        life_cnt = sum(c for b, c in biz_counts.items() if b in life_keys)
        life_pct = round(life_cnt / total * 100)
        summary_lines.append(
            f"  [업종 특성] 생활밀착형(음식·소매·수리) {life_cnt}개({life_pct}%) "
            f"— 자영업 의존도가 높은 생활상권 구조"
        )
        if is_metro_wide and gu_counts:
            top_gu = sorted(gu_counts.items(), key=lambda x: x[1], reverse=True)
            gu_str = ", ".join(f"{g} {c}개({round(c/total*100)}%)" for g, c in top_gu[:5])
            summary_lines.append(f"  [구별 분포] {gu_str}")
            # 가장 많은 구 vs 가장 적은 구 간 격차 강조
            if len(top_gu) >= 2:
                top_nm, top_c = top_gu[0]
                bot_nm, bot_c = top_gu[-1]
                summary_lines.append(
                    f"  [집중도] 최다 {top_nm}({round(top_c/total*100)}%) vs "
                    f"최소 {bot_nm}({round(bot_c/total*100)}%) — "
                    f"상권 분포 불균형 {round((top_c-bot_c)/total*100)}%p"
                )
    elif items:
        summary_lines.append(f"상가 데이터 {len(items)}건 조회됨 ({display_name} 필터 결과 없음)")
    else:
        summary_lines.append("상가 데이터 없음")

    result = {
        "source": source,
        "available": bool(filtered),
        "data": filtered[:30],
        "summary": "\n".join(summary_lines),
        "context_text": "\n".join(summary_lines),
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

# data.go.kr 지자체별 교통사고 통계 API (활용신청 완료)
TAAS_LGSTAT_URL = "http://apis.data.go.kr/B552061/lgStat/getRestLgStat"
# 구 odcloud API (uddi:69cb47bd)는 광주광역시 전용이라 비활성화
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

# 시도 코드 매핑 (data.go.kr lgStat API용 — 공식 코드표 기준)
_SIDO_CODE_MAP = {
    "서울": "1100", "서울특별시": "1100",
    "부산": "1200", "부산광역시": "1200",
    "대구": "2200", "대구광역시": "2200",
    "인천": "2300", "인천광역시": "2300",
    "광주": "2400", "광주광역시": "2400",
    "대전": "2500", "대전광역시": "2500",
    "울산": "2600", "울산광역시": "2600",
    "세종": "2700", "세종특별자치시": "2700",
    "경기": "1300", "경기도": "1300",
    "강원": "1400", "강원특별자치도": "1400", "강원도": "1400",
    "충북": "1500", "충청북도": "1500",
    "충남": "1600", "충청남도": "1600",
    "전북": "1700", "전북특별자치도": "1700", "전라북도": "1700",
    "전남": "1800", "전라남도": "1800",
    "경북": "1900", "경상북도": "1900",
    "경남": "2000", "경상남도": "2000",
    "제주": "2100", "제주특별자치도": "2100",
}
# 시군구 코드 매핑 (공식 코드표 기준)
_GUGUN_CODE_MAP = {
    # 서울 (1100) — 기존 코드 유지 (서울 전용 API와 별도)
    "종로구": "1101", "중구": "1102", "용산구": "1103", "성동구": "1104",
    "동대문구": "1105", "성북구": "1106", "도봉구": "1107", "은평구": "1108",
    "서대문구": "1109", "마포구": "1110", "강서구": "1111", "구로구": "1112",
    "영등포구": "1113", "동작구": "1114", "관악구": "1115", "강남구": "1116",
    "강동구": "1117", "송파구": "1118", "서초구": "1119", "양천구": "1120",
    "중랑구": "1121", "노원구": "1122", "광진구": "1123", "강북구": "1124",
    "금천구": "1125",
    # 부산 (1200) — 기존 코드 유지
    "중구_부산": "1201", "서구_부산": "1202", "동구_부산": "1203", "영도구": "1204",
    "진구": "1205", "동래구": "1206", "남구_부산": "1207", "북구_부산": "1208",
    "해운대구": "1209", "사하구": "1210", "금정구": "1211", "강서구_부산": "1212",
    "연제구": "1213", "수영구": "1214", "사상구": "1215", "기장군": "1216",
    # 대구 (2200) — TAAS 스캔 확인
    "중구_대구": "2201", "동구_대구": "2202", "서구_대구": "2203", "남구_대구": "2204",
    "북구_대구": "2205", "수성구": "2206", "달서구": "2207", "달성군": "2208",
    # 인천 (2300) — TAAS 스캔 확인
    "중구_인천": "2301", "동구_인천": "2302", "미추홀구": "2303", "부평구": "2304",
    "남동구": "2305", "서구_인천": "2306", "연수구": "2307", "계양구": "2308",
    "강화군": "2309", "옹진군": "2310",
    # 광주 (2400) — 기존 코드 유지
    "동구_광주": "2401", "서구_광주": "2402", "북구_광주": "2403",
    "광산구": "2404", "남구_광주": "2405",
    # 대전 (2500) — 기존 코드 유지
    "동구_대전": "2501", "중구_대전": "2502", "서구_대전": "2503",
    "유성구": "2504", "대덕구": "2505",
    # 울산 (2600) — TAAS 스캔 확인
    "중구_울산": "2601", "남구_울산": "2602", "동구_울산": "2603",
    "북구_울산": "2604", "울주군": "2605",
    # 경기 (1300) — TAAS 스캔 확인
    "수원시": "1302", "성남시": "1303", "의정부시": "1304", "안양시": "1305",
    "부천시": "1306", "안산시": "1307", "평택시": "1308", "광명시": "1309",
    "구리시": "1310", "양주시": "1311", "여주시": "1313", "화성시": "1315",
    "시흥시": "1316", "파주시": "1317", "고양시": "1318", "광주시_경기": "1319",
    "연천군": "1320", "포천시": "1321", "가평군": "1322", "양평군": "1323",
    "이천시": "1324", "용인시": "1325", "안성시": "1326", "김포시": "1327",
    "동두천시": "1330", "과천시": "1332", "군포시": "1333", "남양주시": "1334",
    "오산시": "1335", "의왕시": "1336", "하남시": "1337",
    # 강원 (1400) — TAAS 스캔 확인
    "춘천시": "1401", "원주시": "1402", "동해시": "1403", "강릉시": "1404",
    "속초시": "1405", "태백시": "1406", "삼척시": "1407", "홍천군": "1412",
    "횡성군": "1413", "영월군": "1415", "평창군": "1416", "정선군": "1417",
    "철원군": "1418", "화천군": "1419", "양구군": "1420", "인제군": "1421",
    "고성군_강원": "1422", "양양군": "1423",
    # 충북 (1500) — TAAS 스캔 확인
    "청주시": "1501", "충주시": "1502", "제천시": "1503", "보은군": "1512",
    "옥천군": "1513", "영동군": "1514", "진천군": "1515", "괴산군": "1516",
    "음성군": "1517", "단양군": "1520", "증평군": "1521",
    # 충남 (1600) — TAAS 스캔 확인
    "천안시": "1602", "아산시": "1603", "보령시": "1604", "공주시": "1605",
    "서산시": "1606", "금산군": "1611", "태안군": "1612", "논산시": "1615",
    "부여군": "1616", "서천군": "1617", "청양군": "1619", "홍성군": "1620",
    "예산군": "1621", "당진시": "1623", "계룡시": "1624",
    # 전북 (1700) — TAAS 스캔 확인
    "전주시": "1701", "군산시": "1702", "정읍시": "1704", "남원시": "1705",
    "김제시": "1706", "완주군": "1711", "진안군": "1712", "무주군": "1713",
    "장수군": "1714", "임실군": "1715", "순창군": "1717", "고창군": "1719",
    "부안군": "1720", "익산시": "1723",
    # 전남 (1800) — TAAS 스캔 확인
    "목포시": "1802", "여수시": "1803", "순천시": "1804", "나주시": "1806",
    "광양시": "1808", "담양군": "1812", "곡성군": "1813", "구례군": "1814",
    "고흥군": "1818", "보성군": "1819", "화순군": "1820", "장흥군": "1821",
    "강진군": "1822", "해남군": "1823", "영암군": "1824", "무안군": "1825",
    "함평군": "1827", "영광군": "1828", "장성군": "1829", "완도군": "1830",
    "진도군": "1831", "신안군": "1832",
    # 경북 (1900) — TAAS 스캔 확인
    "포항시": "1902", "경주시": "1903", "김천시": "1904", "안동시": "1905",
    "구미시": "1906", "영주시": "1907", "영천시": "1908", "문경시": "1909",
    "상주시": "1910", "군위군": "1912", "의성군": "1913", "청송군": "1915",
    "영양군": "1916", "영덕군": "1917", "청도군": "1922", "고령군": "1923",
    "성주군": "1924", "칠곡군": "1925", "예천군": "1930", "봉화군": "1932",
    "울진군": "1933", "울릉군": "1934", "경산시": "1935",
    # 경남 (2000) — TAAS 스캔 확인
    "진주시": "2003", "통영시": "2006", "김해시": "2008", "밀양시": "2009",
    "거제시": "2010", "의령군": "2012", "함안군": "2013", "창녕군": "2014",
    "양산시": "2016", "고성군_경남": "2022", "사천시": "2023", "남해군": "2024",
    "하동군": "2025", "산청군": "2026", "함양군": "2027", "거창군": "2028",
    "합천군": "2029", "창원시": "2030",
    # 제주 (2100) — TAAS 스캔 확인
    "제주시": "2101", "서귀포시": "2102",
}


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

    # 1) data.go.kr lgStat API — 시군구별 교통사고 통계 (정확한 지역별 데이터)
    if DATA_GO_KR_API_KEY:
        province = region_info.get("province", "")
        sido_code = _SIDO_CODE_MAP.get(province, "")
        gugun_code = ""
        if district:
            gugun_code = _GUGUN_CODE_MAP.get(district, "")
            if not gugun_code:
                # 동명이구 처리: "남구" → province 기반으로 찾기
                for suffix in [f"_{province[:2]}", ""]:
                    candidate = f"{district}{suffix}" if suffix else district
                    if candidate in _GUGUN_CODE_MAP:
                        gugun_code = _GUGUN_CODE_MAP[candidate]
                        break

        if sido_code:
            from datetime import datetime as _dt
            import urllib.parse as _up
            current_year = _dt.now().year
            encoded_key = _up.quote(DATA_GO_KR_API_KEY)
            sido_prefix = sido_code[:2]

            # 광역시장/광역단체장: district 없음 → 모든 구코드 병렬 조회 후 합산
            is_metro_wide = not district and not gugun_code

            def _fetch_taas_year_gu(year: int, gu_code: str) -> list:
                """단일 구·연도 TAAS 조회. 전체사고 항목 반환."""
                try:
                    url = (
                        f"{TAAS_LGSTAT_URL}?ServiceKey={encoded_key}"
                        f"&searchYearCd={year}&siDo={sido_code}&guGun={gu_code}"
                        f"&type=json&numOfRows=13&pageNo=1"
                    )
                    raw = _http_get_json(url, {}, timeout=10)
                    if not raw:
                        return []
                    items_wrap = raw.get("items", {}) if isinstance(raw, dict) else {}
                    if isinstance(items_wrap, dict):
                        items = items_wrap.get("item", [])
                    elif isinstance(items_wrap, list):
                        items = items_wrap
                    else:
                        items = []
                    return [i for i in (items if isinstance(items, list) else [items])
                            if isinstance(i, dict) and i.get("acc_cl_nm") == "전체사고"]
                except Exception as e:
                    logger.warning("TAAS gu=%s year=%s: %s", gu_code, year, e)
                    return []

            summary_lines = []
            recent_data = []

            if is_metro_wide:
                # 이 시도에 속하는 모든 구 코드 수집 (앞 2자리 일치)
                metro_gu_codes = {
                    k: v for k, v in _GUGUN_CODE_MAP.items()
                    if v.startswith(sido_prefix) and v != sido_code
                }
                # 최근 2년만 병렬 조회 (속도 우선)
                years = list(range(current_year - 1, current_year - 3, -1))
                tasks = [(y, gc) for y in years for gc in metro_gu_codes.values()]
                gu_year_results: dict = {}  # (year, gu_code) → item
                with ThreadPoolExecutor(max_workers=min(12, len(tasks) or 1)) as pool:
                    futures = {pool.submit(_fetch_taas_year_gu, y, gc): (y, gc)
                               for y, gc in tasks}
                    for fut in as_completed(futures, timeout=20):
                        y, gc = futures[fut]
                        try:
                            rows = fut.result()
                            if rows:
                                gu_year_results[(y, gc)] = rows[0]
                        except Exception:
                            pass

                for year in years:
                    year_items = {gc: gu_year_results[(year, gc)]
                                  for gc in metro_gu_codes.values()
                                  if (year, gc) in gu_year_results}
                    if not year_items:
                        continue
                    total_acc = sum(int(v.get("acc_cnt", 0) or 0) for v in year_items.values())
                    total_dth = sum(int(v.get("dth_dnv_cnt", 0) or 0) for v in year_items.values())
                    total_inj = sum(int(v.get("injpsn_cnt", 0) or 0) for v in year_items.values())
                    # 구별 사고 건수 내림차순 정렬
                    gu_ranking = sorted(
                        [(v.get("sido_sgg_nm", gc), int(v.get("acc_cnt", 0) or 0))
                         for gc, v in year_items.items()],
                        key=lambda x: x[1], reverse=True
                    )
                    top_gu_str = ", ".join(
                        f"{nm.split()[-1]} {cnt}건" for nm, cnt in gu_ranking[:5]
                    )
                    summary_lines.append(
                        f"  - {year}년: 전체 {total_acc}건, 사망 {total_dth}명, 부상 {total_inj}명\n"
                        f"    [구별] {top_gu_str}"
                    )
                    recent_data.extend(year_items.values())
            else:
                # 기초의원/기초단체장: 구코드 직접 조회
                for year in range(current_year - 1, current_year - 4, -1):
                    try:
                        num_rows = "13" if gugun_code else "100"
                        api_url = (
                            f"{TAAS_LGSTAT_URL}?ServiceKey={encoded_key}"
                            f"&searchYearCd={year}&siDo={sido_code}"
                            f"&type=json&numOfRows={num_rows}&pageNo=1"
                        )
                        if gugun_code:
                            api_url += f"&guGun={gugun_code}"
                        raw = _http_get_json(api_url, {}, timeout=10)
                        if not raw:
                            continue
                        items = []
                        if isinstance(raw, dict):
                            items_wrap = raw.get("items", {})
                            if isinstance(items_wrap, dict):
                                items = items_wrap.get("item", [])
                            elif isinstance(items_wrap, list):
                                items = items_wrap
                        for item in (items if isinstance(items, list) else [items]):
                            if not isinstance(item, dict):
                                continue
                            if item.get("acc_cl_nm") != "전체사고":
                                continue
                            if not gugun_code and district:
                                sgg_nm = item.get("sido_sgg_nm", "")
                                if district not in sgg_nm:
                                    continue
                            acc = item.get("acc_cnt", "")
                            dth = item.get("dth_dnv_cnt", "")
                            inj = item.get("injpsn_cnt", "")
                            summary_lines.append(
                                f"  - {year}년: 사고 {acc}건, 사망 {dth}명, 부상 {inj}명"
                            )
                            recent_data.append(item)
                            break
                    except Exception as e:
                        logger.warning("lgStat API year=%s error: %s", year, e)

            if summary_lines:
                region_label = district or province
                header = f"{region_label} 교통사고 통계 (최근 {len(summary_lines)}년, TAAS 기준):"
                result = {
                    "source": source, "available": True, "data": recent_data,
                    "summary": header + "\n" + "\n".join(summary_lines),
                    "item_count": len(recent_data),
                }
                _set_cached(ck, result)
                return result

    # koroad 다발지역 API는 AWS 서버에서 접근 불가 (타임아웃) → 제거
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
# 5. 생활시설 통계 (KOSIS 도시계획현황 + 한국도시통계 + e-지방지표)
# ---------------------------------------------------------------------------
# 공원:          orgId=460, tblId=TX_315_2009_H1126  (도시계획현황, 시군구별)
# 체육시설:      orgId=460, tblId=TX_315_2009_H1055  (도시계획현황, 시군구별)
# 도서관:        orgId=110, tblId=DT_110001_A033     (한국도시통계, 시군구별)
# 노인여가복지:  orgId=101, tblId=DT_1YL20961        (e-지방지표, 시군구별)
# ---------------------------------------------------------------------------


def _fetch_kosis_facilities(region_info: dict) -> dict:
    """공원·체육시설·도서관 현황 — KOSIS 도시계획현황·한국도시통계."""
    from backend.config import KOSIS_API_KEY

    source = "facilities"
    if not KOSIS_API_KEY:
        return _empty_result(source, "KOSIS API 키 미설정")

    district = region_info["district"]
    province = region_info["province"]
    if not province and not district:
        return _empty_result(source, "지역 미지정")

    ck = _cache_key("kosis_facilities", province=province, district=district)
    cached = _get_cached(ck)
    if cached:
        cached["from_cache"] = True
        return cached

    target = district or province
    current_year = datetime.now().year
    start_year = str(current_year - 2)
    end_year = str(current_year - 1)

    # (key, orgId, tblId, objL2 필요 여부)
    _tables = [
        ("park",    "460", "TX_315_2009_H1126", True),   # 공원 (시군구×공원종류)
        ("sports",  "460", "TX_315_2009_H1055", True),   # 체육시설 (시군구×종류)
        ("library", "110", "DT_110001_A033",    False),  # 공공도서관 (시군구만)
        ("welfare", "101", "DT_1YL20961",       False),  # 노인여가복지시설수 (e-지방지표)
        ("childcare", "101", "DT_1YL20951",     False),  # 유아 천명당 보육시설수 (e-지방지표)
        ("social_welfare", "101", "DT_1YL20941", False), # 인구 십만명당 사회복지시설수 (e-지방지표)
    ]

    # 도서관은 최근 연도 데이터가 최대 2023년까지 있음
    lib_end_year = str(min(int(end_year), 2023))

    all_data: dict[str, list] = {}
    def _fetch_one_table(args):
        key, org_id, tbl_id, need_obj2 = args
        params = {
            "method": "getList",
            "apiKey": KOSIS_API_KEY,
            "format": "json",
            "jsonVD": "Y",
            "orgId": org_id,
            "tblId": tbl_id,
            "prdSe": "Y",
            "startPrdDe": start_year if key != "library" else "2022",
            "endPrdDe": end_year if key != "library" else lib_end_year,
            "objL1": "ALL",
            "itmId": "ALL",
        }
        if need_obj2:
            params["objL2"] = "ALL"
        raw = _http_get_json(KOSIS_BASE, params, timeout=12,
                             headers={"Referer": "https://kosis.kr/"})
        if not isinstance(raw, list):
            return key, []
        # 시군구 필터 — province 전체명으로 정밀 매칭 (동명이지 오필터 방지)
        filtered = []
        for item in raw:
            c1 = item.get("C1_NM") or ""
            if province and district:
                matched = district in c1 and province in c1
            elif province:
                matched = c1 == province or c1.startswith(province + " ")
            elif district:
                matched = district in c1
            else:
                matched = False
            if matched:
                filtered.append(item)
        return key, filtered

    with ThreadPoolExecutor(max_workers=len(_tables)) as pool:
        for key, filtered in pool.map(_fetch_one_table, _tables, timeout=25):
            if filtered:
                all_data[key] = filtered

    if not all_data:
        result = _empty_result(source, "데이터 없음")
        _set_cached(ck, result)
        return result

    summary_parts = []

    # 공원
    if "park" in all_data:
        park_items = all_data["park"]
        # 최신 연도 "계" 행 우선
        total_cnt = total_area = None
        by_kind: dict[str, dict] = {}
        for x in park_items:
            if x.get("C2_NM") == "계" and x.get("ITM_NM") == "시설수":
                total_cnt = x.get("DT")
            if x.get("C2_NM") == "계" and x.get("ITM_NM") == "면적":
                total_area = x.get("DT")
            kind = x.get("C2_NM", "")
            if kind and kind != "계":
                by_kind.setdefault(kind, {})
                if x.get("ITM_NM") == "시설수":
                    by_kind[kind]["cnt"] = x.get("DT", "")
        lines = [f"{target} 공원 현황:"]
        if total_cnt:
            lines.append(f"  - 총 공원: {total_cnt}개소" + (f", {int(total_area):,}㎡" if total_area else ""))
        for kind, vals in list(by_kind.items())[:5]:
            cnt = vals.get("cnt", "")
            if cnt:
                lines.append(f"  - {kind}: {cnt}개소")
        summary_parts.append("\n".join(lines))

    # 체육시설
    if "sports" in all_data:
        sport_items = all_data["sports"]
        total_cnt = total_area = None
        by_kind: dict[str, dict] = {}
        for x in sport_items:
            if x.get("C2_NM") == "계" and x.get("ITM_NM") == "시설수":
                total_cnt = x.get("DT")
            if x.get("C2_NM") == "계" and x.get("ITM_NM") == "면적":
                total_area = x.get("DT")
            kind = x.get("C2_NM", "")
            if kind and kind != "계":
                by_kind.setdefault(kind, {})
                if x.get("ITM_NM") == "시설수":
                    by_kind[kind]["cnt"] = x.get("DT", "")
        lines = [f"{target} 공공체육시설 현황:"]
        if total_cnt:
            lines.append(f"  - 총 체육시설: {total_cnt}개" + (f", {int(total_area):,}㎡" if total_area else ""))
        for kind, vals in list(by_kind.items())[:5]:
            cnt = vals.get("cnt", "")
            if cnt:
                lines.append(f"  - {kind}: {cnt}개")
        summary_parts.append("\n".join(lines))

    # 도서관
    if "library" in all_data:
        lib_items = all_data["library"]
        lib_cnt = lib_seats = lib_per_capita = None
        for x in lib_items:
            nm = x.get("ITM_NM") or ""
            if "도서관 수" in nm:
                lib_cnt = x.get("DT")
            elif "열람석 수" in nm and "1인당" not in nm:
                lib_seats = x.get("DT")
            elif "1인당" in nm:
                lib_per_capita = x.get("DT")
        lines = [f"{target} 공공도서관 현황:"]
        if lib_cnt:
            lines.append(f"  - 도서관 수: {lib_cnt}개소")
        if lib_seats:
            lines.append(f"  - 총 열람석: {lib_seats}석")
        if lib_per_capita:
            lines.append(f"  - 1인당 열람석: {lib_per_capita}석")
        summary_parts.append("\n".join(lines))

    # 노인여가복지시설
    if "welfare" in all_data:
        wf_items = all_data["welfare"]
        wf_cnt = wf_per_thousand = None
        for x in wf_items:
            nm = x.get("ITM_NM") or ""
            if "노인여가복지시설수" in nm and "천명당" not in nm:
                wf_cnt = x.get("DT")
            elif "천명당" in nm:
                wf_per_thousand = x.get("DT")
        lines = [f"{target} 노인·복지시설 현황:"]
        if wf_cnt:
            lines.append(f"  - 노인여가복지시설: {wf_cnt}개소")
        if wf_per_thousand:
            lines.append(f"  - 노인 천 명당: {wf_per_thousand}개소")
        if lines[1:]:
            summary_parts.append("\n".join(lines))

    # 보육시설 (유아 천명당 보육시설수)
    if "childcare" in all_data:
        cc_items = all_data["childcare"]
        cc_cnt = cc_per_thousand = cc_pop = None
        for x in cc_items:
            nm = (x.get("ITM_NM") or "").replace("<br>", "").replace("＜br＞", "")
            dt = x.get("DT")
            if "보육시설" in nm and "천명당" not in nm and "인구" not in nm:
                cc_cnt = dt
            elif "유아 천명당" in nm:
                cc_per_thousand = dt
            elif "0~5세" in nm or "주민등록인구" in nm:
                cc_pop = dt
        lines = [f"{target} 보육시설 현황:"]
        if cc_cnt:
            lines.append(f"  - 어린이집·보육시설: {cc_cnt}개소")
        if cc_pop:
            lines.append(f"  - 0~5세 영유아: {int(cc_pop):,}명")
        if cc_per_thousand:
            lines.append(f"  - 유아 천 명당 보육시설: {cc_per_thousand}개소")
        if lines[1:]:
            summary_parts.append("\n".join(lines))

    # 사회복지시설 (인구 십만명당)
    if "social_welfare" in all_data:
        sw_items = all_data["social_welfare"]
        sw_cnt = sw_per_100k = sw_pop = None
        for x in sw_items:
            nm = (x.get("ITM_NM") or "").replace("<br>", "").replace("＜br＞", "")
            dt = x.get("DT")
            if "사회복지시설수" in nm and "십만명당" not in nm and "인구" not in nm:
                sw_cnt = dt
            elif "십만명당" in nm:
                sw_per_100k = dt
            elif "주민등록인구" in nm:
                sw_pop = dt
        lines = [f"{target} 사회복지시설 현황:"]
        if sw_cnt:
            lines.append(f"  - 사회복지시설: {sw_cnt}개소")
        if sw_pop:
            lines.append(f"  - 인구: {int(sw_pop):,}명")
        if sw_per_100k:
            lines.append(f"  - 인구 10만 명당: {sw_per_100k}개소")
        if lines[1:]:
            summary_parts.append("\n".join(lines))

    result = {
        "source": source,
        "available": True,
        "data": all_data,
        "summary": "\n\n".join(summary_parts),
    }
    _set_cached(ck, result)
    return result


# ---------------------------------------------------------------------------
# 통합 조회 함수
# ---------------------------------------------------------------------------

# 주제 → API 매핑
_TOPIC_API_MAP = {
    "kosis": ["인구", "세대", "고령", "청년", "노인", "1인가구", "전입", "전출", "주민", "연령", "출산", "인구구조",
              "복지", "돌봄", "어르신", "보육", "요양", "장애", "독거"],
    "taas": ["교통", "사고", "안전", "보행", "어린이", "통학", "보호구역", "횡단보도", "도로", "주차", "자전거"],
    "semas": ["상권", "상가", "자영업", "골목", "경제", "업종", "폐업", "창업", "소상공인", "시장"],
    "seoul": ["도서관", "체육", "공원", "문화", "주차장", "CCTV", "경로당", "어린이집"],  # 서울 전용
    "facilities": ["체육시설", "스포츠", "경기장", "체육관", "수영장",
                   "도서관", "문화시설", "문화센터",
                   "공원", "녹지", "산책로",
                   "공공시설", "시설 접근", "접근성",
                   "노인복지", "복지시설", "경로당", "여가시설",
                   "돌봄", "어르신", "요양", "복지 현안"],
}


# 포괄 주제어 — 전체 API 호출. "공약"/"정책"처럼 모든 챗봇 메시지에 포함되는 단어는 제외.
_GENERAL_TOPIC_WORDS = {"생활이슈", "생활현안", "주민생활", "지역현안", "종합현황"}


def _select_relevant_apis(all_fetchers: dict, topic: str, keywords: list[str] | None) -> dict:
    """주제/키워드 기반으로 필요한 API만 선택.
    일반 포괄 주제(생활이슈 등)는 전체 API 호출, 매칭 없으면 kosis+taas.
    """
    if not topic and not keywords:
        return {k: all_fetchers[k] for k in ("kosis", "taas") if k in all_fetchers}

    text = (topic + " " + " ".join(keywords or [])).lower()
    selected = {}
    for api_name, trigger_words in _TOPIC_API_MAP.items():
        if api_name in all_fetchers and any(w in text for w in trigger_words):
            selected[api_name] = all_fetchers[api_name]

    # 포괄 주제어("생활", "이슈", "현황" 등) → 전체 API 호출
    if any(w in text for w in _GENERAL_TOPIC_WORDS):
        for k in all_fetchers:
            selected.setdefault(k, all_fetchers[k])

    # 매칭 없으면 kosis + taas 기본
    if not selected:
        for k in ("kosis", "taas"):
            if k in all_fetchers:
                selected[k] = all_fetchers[k]

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
        "facilities": lambda: _fetch_kosis_facilities(region_info),
    }
    # 서울 시설 API는 서울 지역에서만 의미있음
    if region_info.get("is_seoul"):
        all_fetchers["seoul"] = lambda: _fetch_seoul_facilities(region_info)

    fetchers = _select_relevant_apis(all_fetchers, topic, keywords)

    if not fetchers:
        return {"available": False, "context_text": "", "sources": {}}

    sources = {}
    with ThreadPoolExecutor(max_workers=len(fetchers)) as pool:
        futures = {pool.submit(fn): name for name, fn in fetchers.items()}
        for future in as_completed(futures, timeout=35):
            name = futures[future]
            try:
                sources[name] = future.result()
            except Exception as e:
                logger.warning("public_data %s error: %s", name, e)
                sources[name] = _empty_result(name, str(e))

    # 브리핑 텍스트 생성
    target = region_info["district"] or region_info["province"]
    sections = []

    for name in ["kosis", "semas", "taas", "facilities", "seoul"]:
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
