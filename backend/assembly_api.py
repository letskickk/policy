"""
지방의회 API 온디맨드 조회 모듈.

두 가지 소스:
1. 국회지방의회의정포털 (clik.nanet.go.kr) — 회의록·의안·의원정보·정책정보
2. 발언 빅데이터 (dataset.nanet.go.kr) — 발언 검색

API 키가 없거나 호출 실패 시 빈 결과 반환 (graceful degradation).
결과는 24시간 캐시.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode, quote

from backend.database import get_connection

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ASSEMBLY_API_KEY = os.getenv("ASSEMBLY_API_KEY", "")
SPEECH_API_KEY = os.getenv("SPEECH_API_KEY", "")

# 국회지방의회의정포털 API — endpoints use .do suffix
CLIK_BASE_URL = "https://clik.nanet.go.kr/openapi"
# 발언 빅데이터 API base
SPEECH_BASE_URL = "https://dataset.nanet.go.kr/api"

CACHE_TTL_HOURS = 24
MAX_RESULTS_PER_QUERY = 20


# ---------------------------------------------------------------------------
# Cache helpers (analysis_cache 테이블 재활용)
# ---------------------------------------------------------------------------
def _cache_key(prefix: str, **kwargs) -> str:
    """캐시 키 생성."""
    raw = prefix + "|" + json.dumps(kwargs, sort_keys=True, ensure_ascii=False)
    return "assembly_" + hashlib.sha256(raw.encode()).hexdigest()[:24]


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
            (key, "assembly_api", json.dumps(data, ensure_ascii=False), expires),
        )
        conn.commit()
    except Exception as e:
        logger.warning("assembly cache save failed: %s", e)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------
def _http_get(url: str, params: dict, timeout: int = 10) -> Optional[dict]:
    """HTTP GET → JSON. 실패 시 None. Rate limit(429) 시 캐시에 빈 결과 저장해 재시도 방지."""
    try:
        import urllib.request
        import urllib.error

        full_url = url + "?" + urlencode(params, quote_via=quote)
        req = urllib.request.Request(full_url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            logger.warning("assembly API rate limit exceeded (429): %s", url)
            # Rate limit 시 30분간 빈 캐시를 저장해서 반복 호출 방지
            _set_rate_limit_backoff(url)
        else:
            logger.warning("assembly API HTTP error %d: %s — %s", e.code, url, e)
        return None
    except Exception as e:
        logger.warning("assembly API call failed: %s — %s", url, e)
        return None


def _set_rate_limit_backoff(url: str) -> None:
    """Rate limit 발생 시 30분간 해당 엔드포인트 호출을 억제."""
    key = "ratelimit_" + hashlib.sha256(url.encode()).hexdigest()[:16]
    expires = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO analysis_cache
               (user_id, cache_key, request_fingerprint, result_payload, expires_at)
               VALUES (0, ?, ?, ?, ?)""",
            (key, "rate_limit_backoff", json.dumps({"url": url, "reason": "429"}), expires),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def _is_rate_limited(url: str) -> bool:
    """해당 엔드포인트가 rate limit 백오프 중인지 확인."""
    key = "ratelimit_" + hashlib.sha256(url.encode()).hexdigest()[:16]
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
# 국회지방의회의정포털 API (clik.nanet.go.kr)
# ---------------------------------------------------------------------------
# 엔드포인트: /openapi/{service}.do
# 파라미터: key, type=json, displayType=list, startCount, listCount,
#           searchType=ALL, searchKeyword, rasmblyId
# ---------------------------------------------------------------------------

def search_local_assembly(
    *,
    region: Optional[str] = None,
    keywords: Optional[list[str]] = None,
    years: int = 2,
    limit: int = MAX_RESULTS_PER_QUERY,
) -> dict:
    """
    지방의회 회의록 + 의안 통합 검색.

    Returns:
        {
            "source": "clik",
            "available": bool,
            "query": {...},
            "results": [{"title": ..., "speaker": ..., "date": ..., "summary": ..., "type": ...}],
            "total_count": int,
        }
    """
    if not ASSEMBLY_API_KEY:
        return _empty_result("clik", region=region, keywords=keywords, reason="API 키 미설정")

    cache_k = _cache_key("clik", region=region, keywords=keywords, years=years, limit=limit)
    cached = _get_cached(cache_k)
    if cached:
        cached["from_cache"] = True
        return cached

    # clik API는 searchKeyword에 공백이 있으면 ERROR11 → 가장 핵심 키워드 1개 사용
    keyword_str = ""
    if keywords:
        # 가장 긴 (구체적인) 키워드를 우선 사용
        keyword_str = max(keywords, key=len) if keywords else ""
    if not keyword_str and region:
        keyword_str = region

    # 양쪽에서 절반씩 가져와서 의안도 노출
    half = max(limit // 2, 5)
    minute_items = []
    bill_items = []

    # 1) 회의록 검색 (minutes.do)
    minutes_url = f"{CLIK_BASE_URL}/minutes.do"
    minutes_data = None
    if not _is_rate_limited(minutes_url):
        minutes_params = {
            "key": ASSEMBLY_API_KEY,
            "type": "json",
            "displayType": "list",
            "startCount": "0",
            "listCount": str(min(half, 100)),
            "searchType": "ALL",
            "searchKeyword": keyword_str,
        }
        minutes_data = _http_get(minutes_url, minutes_params)
        if minutes_data:
            minute_items = _parse_minutes_response(minutes_data)

    # 2) 의안 검색 (bill.do) — BI_SJ 검색이 더 정확
    bill_url = f"{CLIK_BASE_URL}/bill.do"
    bill_data = None
    if not _is_rate_limited(bill_url):
        bill_params = {
            "key": ASSEMBLY_API_KEY,
            "type": "json",
            "displayType": "list",
            "startCount": "0",
            "listCount": str(min(half, 100)),
            "searchType": "BI_SJ",
            "searchKeyword": keyword_str,
        }
        bill_data = _http_get(bill_url, bill_params)
        if bill_data:
            bill_items = _parse_bill_response(bill_data)

    if not minute_items and not bill_items and minutes_data is None and bill_data is None:
        return _empty_result("clik", region=region, keywords=keywords, reason="API 호출 실패")

    # 의안 우선 (더 구체적인 정보), 회의록 보충
    all_items = bill_items + minute_items

    result = {
        "source": "clik",
        "available": True,
        "query": {"region": region, "keywords": keywords, "years": years},
        "results": all_items[:limit],
        "total_count": len(all_items),
        "from_cache": False,
    }

    _set_cached(cache_k, result)
    return result


def _extract_clik_rows(data) -> tuple[list[dict], dict]:
    """
    clik API 공통 응답 파싱.
    응답 형식: [{SERVICE, RESULT_CODE, TOTAL_COUNT, LIST_COUNT, LIST: [{ROW: {...}}, ...]}]
    Returns: (rows, meta) where meta has TOTAL_COUNT etc.
    """
    meta = {}
    rows = []

    # 응답이 배열로 래핑됨
    if isinstance(data, list) and len(data) > 0:
        data = data[0]

    if not isinstance(data, dict):
        return rows, meta

    # 에러 체크
    if data.get("RESULT_CODE", "").startswith("ERROR"):
        logger.warning("clik API error: %s — %s", data.get("RESULT_CODE"), data.get("RESULT_MESSAGE"))
        return rows, meta

    meta["total_count"] = data.get("TOTAL_COUNT", 0)
    meta["list_count"] = data.get("LIST_COUNT", 0)

    raw_list = data.get("LIST", [])
    if not isinstance(raw_list, list):
        raw_list = [raw_list] if raw_list else []

    for entry in raw_list:
        if isinstance(entry, dict):
            row = entry.get("ROW", entry)
            if isinstance(row, dict):
                rows.append(row)

    return rows, meta


def _parse_minutes_response(data) -> list[dict]:
    """회의록 (minutes.do) JSON 응답 파싱."""
    items = []
    try:
        rows, meta = _extract_clik_rows(data)
        for row in rows:
            items.append({
                "title": row.get("MTGNM", "") or "",  # 회의명 (본회의, 상임위 등)
                "speaker": "",
                "date": row.get("MTG_DE", "") or "",
                "council": row.get("RASMBLY_NM", "") or "",
                "summary": f"제{row.get('RASMBLY_SESN', '')}회 {row.get('MTGNM', '')} 제{row.get('MINTS_ODR', '')}차",
                "type": "회의록",
                "doc_id": row.get("DOCID", ""),
            })
    except Exception as e:
        logger.warning("minutes response parse error: %s", e)
    return items


def _parse_bill_response(data) -> list[dict]:
    """의안 (bill.do) JSON 응답 파싱."""
    items = []
    try:
        rows, meta = _extract_clik_rows(data)
        for row in rows:
            items.append({
                "title": row.get("BI_SJ", "") or "",
                "speaker": row.get("PROPSR", "") or "",
                "date": row.get("ITNC_DE", "") or "",
                "council": "",  # bill 목록에는 RASMBLY_NM 없음, RASMBLY_ID만
                "summary": row.get("BI_KND_NM", "") or "",  # 의안종류 (조례안, 건의안 등)
                "type": "의안",
                "bill_no": row.get("BI_NO", ""),
                "doc_id": row.get("DOCID", ""),
            })
    except Exception as e:
        logger.warning("bill response parse error: %s", e)
    return items


def search_assembly_members(
    *,
    region: Optional[str] = None,
    name: Optional[str] = None,
    party: Optional[str] = None,
    limit: int = 20,
) -> dict:
    """지방의원 정보 검색 (assemblyinfo.do)."""
    if not ASSEMBLY_API_KEY:
        return _empty_result("assemblyinfo", reason="API 키 미설정")

    keyword = name or party or region or ""
    search_type = "ALL"
    if name:
        search_type = "ASEMBY_NM"
    elif party:
        search_type = "PPRTY_NM"

    params = {
        "key": ASSEMBLY_API_KEY,
        "type": "json",
        "displayType": "list",
        "startCount": "0",
        "listCount": str(min(limit, 100)),
        "searchType": search_type,
        "searchKeyword": keyword,
    }

    info_url = f"{CLIK_BASE_URL}/assemblyinfo.do"
    if _is_rate_limited(info_url):
        return _empty_result("assemblyinfo", reason="API rate limit 백오프 중")
    data = _http_get(info_url, params)
    if data is None:
        return _empty_result("assemblyinfo", reason="API 호출 실패")

    rows, meta = _extract_clik_rows(data)
    if not rows:
        return _empty_result("assemblyinfo", reason="결과 없음")

    return {"source": "assemblyinfo", "available": True, "results": rows, "total_count": meta.get("total_count", 0)}


def search_policy_info(
    *,
    keywords: Optional[list[str]] = None,
    limit: int = 20,
) -> dict:
    """정책정보 검색 (policyinfoList.do)."""
    if not ASSEMBLY_API_KEY:
        return _empty_result("policyinfo", reason="API 키 미설정")

    keyword_str = " ".join(keywords) if keywords else ""
    params = {
        "key": ASSEMBLY_API_KEY,
        "type": "json",
        "displayType": "list",
        "startCount": "0",
        "listCount": str(min(limit, 100)),
        "searchType": "ALL",
        "searchKeyword": keyword_str,
    }

    policy_url = f"{CLIK_BASE_URL}/policyinfoList.do"
    if _is_rate_limited(policy_url):
        return _empty_result("policyinfo", reason="API rate limit 백오프 중")
    data = _http_get(policy_url, params)
    if data is None:
        return _empty_result("policyinfo", reason="API 호출 실패")

    rows, meta = _extract_clik_rows(data)
    if not rows:
        return _empty_result("policyinfo", reason="결과 없음")

    return {"source": "policyinfo", "available": True, "results": rows, "total_count": meta.get("total_count", 0)}


# ---------------------------------------------------------------------------
# 발언 빅데이터 API
# ---------------------------------------------------------------------------
def search_speeches(
    *,
    region: Optional[str] = None,
    keywords: Optional[list[str]] = None,
    years: int = 2,
    limit: int = MAX_RESULTS_PER_QUERY,
) -> dict:
    """
    발언 빅데이터에서 발언 검색.

    Returns:
        {
            "source": "speech",
            "available": bool,
            "query": {...},
            "results": [{"speaker": ..., "date": ..., "content": ..., "committee": ...}],
            "total_count": int,
        }
    """
    if not SPEECH_API_KEY:
        return _empty_result("speech", region=region, keywords=keywords, reason="API 키 미설정")

    cache_k = _cache_key("speech", region=region, keywords=keywords, years=years)
    cached = _get_cached(cache_k)
    if cached:
        cached["from_cache"] = True
        return cached

    params = {
        "apiKey": SPEECH_API_KEY,
        "type": "json",
        "numOfRows": str(min(limit, 100)),
        "pageNo": "1",
    }
    if keywords:
        params["keyword"] = " ".join(keywords)
    if region:
        params["localName"] = region

    speech_url = f"{SPEECH_BASE_URL}/search"
    if _is_rate_limited(speech_url):
        return _empty_result("speech", region=region, keywords=keywords, reason="API rate limit 백오프 중")
    data = _http_get(speech_url, params)

    if data is None:
        return _empty_result("speech", region=region, keywords=keywords, reason="API 호출 실패")

    items = _parse_speech_response(data)

    result = {
        "source": "speech",
        "available": True,
        "query": {"region": region, "keywords": keywords, "years": years},
        "results": items[:limit],
        "total_count": len(items),
        "from_cache": False,
    }

    _set_cached(cache_k, result)
    return result


def _parse_speech_response(data: dict) -> list[dict]:
    """발언 빅데이터 JSON 응답 파싱."""
    items = []
    try:
        raw_items = data.get("data", data.get("items", []))
        if not isinstance(raw_items, list):
            raw_items = []

        for item in raw_items:
            items.append({
                "speaker": item.get("memberName") or item.get("speaker") or "",
                "date": item.get("meetDt") or item.get("date") or "",
                "content": (item.get("speechContent") or item.get("content") or "")[:500],
                "committee": item.get("committeeName") or item.get("committee") or "",
                "council": item.get("localName") or item.get("assemblyName") or "",
            })
    except Exception as e:
        logger.warning("speech response parse error: %s", e)

    return items


# ---------------------------------------------------------------------------
# 통합 검색 (두 소스 합산)
# ---------------------------------------------------------------------------
def query_assembly_context(
    *,
    region: Optional[str] = None,
    district_name: Optional[str] = None,
    election_type: Optional[str] = None,
    keywords: Optional[list[str]] = None,
    years: int = 2,
) -> dict:
    """
    지방의회 컨텍스트 통합 조회.
    두 API를 모두 시도하고 결과를 합산.
    API 키가 없으면 빈 결과 반환 (graceful degradation).

    Returns:
        {
            "available": bool,
            "sources_tried": int,
            "sources_available": int,
            "assembly_results": [...],
            "speech_results": [...],
            "context_text": str,  # 프롬프트에 넣을 요약 텍스트
        }
    """
    assembly = search_local_assembly(region=region, keywords=keywords, years=years)
    speeches = search_speeches(region=region, keywords=keywords, years=years)

    def _score_item(item: dict) -> int:
        score = 0
        region_text = (region or "").strip()
        district_text = (district_name or "").strip()
        election_text = (election_type or "").strip()
        hay = " ".join([
            str(item.get("council") or ""),
            str(item.get("title") or ""),
            str(item.get("summary") or ""),
            str(item.get("speaker") or ""),
            str(item.get("committee") or ""),
            str(item.get("content") or ""),
        ])
        if region_text and region_text in hay:
            score += 100
        if district_text and district_text in hay:
            score += 140
        if election_text:
            if ("기초" in election_text or "구의원" in election_text or "시의원" in election_text or "군의원" in election_text):
                if any(tok in hay for tok in ["구의회", "시의회", "군의회", "구의원", "시의원", "군의원"]):
                    score += 40
            elif ("광역" in election_text or "시장" in election_text or "도지사" in election_text):
                if any(tok in hay for tok in ["시의회", "도의회", "시장", "도지사"]):
                    score += 30
        return score

    if assembly.get("results"):
        assembly["results"] = sorted(assembly["results"], key=_score_item, reverse=True)
    if speeches.get("results"):
        speeches["results"] = sorted(speeches["results"], key=_score_item, reverse=True)

    sources_tried = 2
    sources_available = sum([assembly.get("available", False), speeches.get("available", False)])

    # 프롬프트에 넣을 텍스트 생성
    context_lines = []

    if assembly.get("results"):
        context_lines.append(f"[지방의회 의정정보] {len(assembly['results'])}건 검색됨")
        for item in assembly["results"][:10]:
            line = f"- [{item.get('type', '')}] [{item.get('council', '')}] {item.get('title', '')} ({item.get('date', '')})"
            if item.get("speaker"):
                line += f" — {item['speaker']}"
            if item.get("summary"):
                line += f"\n  {item['summary'][:200]}"
            context_lines.append(line)

    if speeches.get("results"):
        context_lines.append(f"\n[발언 빅데이터] {len(speeches['results'])}건 검색됨")
        for item in speeches["results"][:10]:
            line = f"- [{item.get('council', '')} {item.get('committee', '')}] {item.get('speaker', '')} ({item.get('date', '')})"
            if item.get("content"):
                line += f"\n  {item['content'][:200]}"
            context_lines.append(line)

    if not context_lines:
        if not ASSEMBLY_API_KEY and not SPEECH_API_KEY:
            context_text = "(지방의회 API 키가 설정되지 않아 데이터를 조회할 수 없습니다)"
        else:
            context_text = "(지방의회 관련 데이터가 검색되지 않았습니다)"
    else:
        context_text = "\n".join(context_lines)

    return {
        "available": sources_available > 0,
        "sources_tried": sources_tried,
        "sources_available": sources_available,
        "assembly_results": assembly.get("results", []),
        "speech_results": speeches.get("results", []),
        "context_text": context_text,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _empty_result(source: str, reason: str = "", **query_params) -> dict:
    return {
        "source": source,
        "available": False,
        "query": query_params,
        "results": [],
        "total_count": 0,
        "reason": reason,
        "from_cache": False,
    }
