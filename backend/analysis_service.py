"""
분석 실행 단일 서비스 레이어.
캐시 조회 → 쿼터 체크 → OpenAI 호출 → usage_logs 기록 → 캐시 저장.
"""
import hashlib
import json
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from backend.auth import STATUS_APPROVED, get_user
from backend.config import (
    CACHE_TTL_HOURS,
    CHAT_MODEL,
    OPENAI_MODEL,
)
from backend.database import get_connection
from backend.quota_rate import check_quota
from backend.usage_logger import log_usage, _estimate_cost

logger = logging.getLogger(__name__)


def _cache_key(normalized_input: str, options: str, model: str, vs_id: str) -> str:
    raw = f"{normalized_input}|{options}|{model}|{vs_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize_cache_options(options: dict) -> str:
    """캐시 적중률을 높이기 위해 분석 결과에 영향을 주는 필드만 정규화."""
    canonical = {
        "phase": (options.get("phase") or "full").strip().lower(),
        "judge": bool(options.get("judge")),
        "top_k_platform": int(options.get("top_k_platform", 6)),
        "top_k_pledge": int(options.get("top_k_pledge", 6)),
        "top_k_regional": int(options.get("top_k_regional", 8)),
    }
    return json.dumps(canonical, sort_keys=True)


def _get_cached(user_id: int, cache_key: str) -> Optional[str]:
    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT result_payload, expires_at FROM analysis_cache WHERE user_id = ? AND cache_key = ?",
            (user_id, cache_key),
        )
        row = cur.fetchone()
        if not row:
            return None
        expires = row["expires_at"]
        if expires and datetime.fromisoformat(expires.replace("Z", "+00:00")) < datetime.now(timezone.utc):
            conn.execute("DELETE FROM analysis_cache WHERE cache_key = ?", (cache_key,))
            conn.commit()
            return None
        return row["result_payload"]
    finally:
        conn.close()


def _set_cached(user_id: int, cache_key: str, fingerprint: str, result: str) -> None:
    expires = (datetime.now(timezone.utc) + timedelta(hours=CACHE_TTL_HOURS)).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO analysis_cache (user_id, cache_key, request_fingerprint, result_payload, expires_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, cache_key, fingerprint[:500], result, expires),
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.warning("cache save failed: %s", e)
    finally:
        conn.close()


def _extract_fit_score(result: Any) -> float:
    """검증 결과(dict)에서 fit_score를 안전하게 추출한다."""
    if not isinstance(result, dict):
        return 0.0
    for key in ("total_score", "fit_score"):
        value = result.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    summary = result.get("summary")
    if isinstance(summary, dict):
        value = summary.get("fit_score")
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def _signal_from_score(score: float) -> str:
    if score >= 80:
        return "green"
    if score >= 60:
        return "yellow"
    return "red"


def _enrich_verify_result(result: Any) -> Any:
    """
    검증 결과에 총점/신호등/PDF 가능 여부를 공통 필드로 보강한다.
    - total_score: 0~100
    - signal_light: green|yellow|red
    - pdf_eligible: bool (80점 이상)
    - summary.scores: { alignment, conflict_risk, differentiation }
    - evidence_links: 프론트 친화 근거 매핑 배열
    """
    if not isinstance(result, dict):
        return result

    score = max(0.0, min(100.0, _extract_fit_score(result)))
    score = round(score, 1)
    signal = _signal_from_score(score)
    eligible = score >= 80.0

    result["total_score"] = score
    result["signal_light"] = signal
    result["pdf_eligible"] = eligible

    summary = result.get("summary")
    if not isinstance(summary, dict):
        summary = {}
        result["summary"] = summary
    summary["fit_score"] = score
    summary["total_score"] = score
    summary["signal_light"] = signal
    summary["pdf_eligible"] = eligible
    summary["label"] = (
        "강한 부합" if score >= 80 else
        "부합" if score >= 60 else
        "부분부합" if score >= 40 else
        "미부합"
    )

    # 3축 점수: 기존 rubric 항목에서 파생
    summary["scores"] = _build_axis_scores(result)

    # improvements 통일: 문자열/객체 혼합 → 객체 배열
    raw_imps = result.get("improvements", [])
    if isinstance(raw_imps, list):
        result["improvements"] = [
            imp if isinstance(imp, dict) else {"title": str(imp), "detail": ""}
            for imp in raw_imps
        ]

    return result


def _avg_score_0_5(items: list) -> float:
    if not isinstance(items, list) or not items:
        return 0.0
    scores = [
        float(it.get("score_0_5", 0))
        for it in items
        if isinstance(it, dict) and isinstance(it.get("score_0_5"), (int, float))
    ]
    return (sum(scores) / len(scores)) if scores else 0.0


def _build_axis_scores(result: dict) -> dict:
    """platform/pledges/conflicts rubric → 3축 0~100 점수."""
    platform = result.get("platform", [])
    pledges = result.get("pledges", [])
    conflicts = result.get("conflicts", [])

    alignment = round(_avg_score_0_5(platform) * 20, 1)
    differentiation = round(_avg_score_0_5(pledges) * 20, 1)
    conflict_raw = _avg_score_0_5(conflicts)
    conflict_risk = round(conflict_raw * 20, 1)

    return {
        "alignment": min(alignment, 100.0),
        "conflict_risk": min(conflict_risk, 100.0),
        "differentiation": min(differentiation, 100.0),
    }




def run_check_analysis(
    user_id: int,
    pledge_text: str,
    ip: str,
    vector_store_id: Optional[str],
    regional_vector_store_id: Optional[str],
    winners2022_vector_store_id: Optional[str],
    indexes: Optional[dict],
) -> tuple[str, int, bool]:
    """
    당 부합 점검 실행.
    Returns: (result_or_error, status_code, from_cache)
    """
    user = get_user(user_id)
    if not user or user["status"] != STATUS_APPROVED:
        return "승인되지 않은 사용자입니다.", 403, False

    ok, msg = check_quota(user_id)
    if not ok:
        return msg, 429, False

    normalized = (pledge_text or "").strip()
    if not normalized:
        return "공약 내용이 비어 있습니다.", 400, False

    vs_id = vector_store_id or ""
    regional_id = regional_vector_store_id or ""
    winners2022_id = winners2022_vector_store_id or ""
    # v5: /check 프롬프트에 추가 데이터 소스 4종 연결 → 캐시 무효화
    opts = f"check|{vs_id}|{regional_id}|{winners2022_id}|v5"
    cache_key = _cache_key(normalized, opts, OPENAI_MODEL, vs_id)

    cached = _get_cached(user_id, cache_key)
    if cached:
        log_usage(
            user_id=user_id,
            ip=ip,
            endpoint="/check",
            action="cache_hit",
            input_chars=len(normalized),
            output_chars=len(cached),
            model=OPENAI_MODEL,
            token_in=0,
            token_out=0,
            cost_estimate=0.0,
            status_code=200,
            latency_ms=0,
        )
        return cached, 200, True

    start = time.perf_counter()
    try:
        from backend.check_service import check_pledge_alignment

        result = check_pledge_alignment(
            normalized,
            vector_store_id=vector_store_id,
            regional_vector_store_id=regional_vector_store_id,
            winners2022_vector_store_id=winners2022_vector_store_id,
            indexes=indexes,
            user_id=user_id,
        )
    except Exception as e:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        log_usage(
            user_id=user_id,
            ip=ip,
            endpoint="/check",
            action="analysis_run",
            input_chars=len(normalized),
            output_chars=0,
            model=OPENAI_MODEL,
            token_in=None,
            token_out=None,
            cost_estimate=None,
            status_code=500,
            latency_ms=elapsed_ms,
            error_message=str(e)[:500],
        )
        raise

    if result.startswith("오류:"):
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        log_usage(
            user_id=user_id,
            ip=ip,
            endpoint="/check",
            action="analysis_run",
            input_chars=len(normalized),
            output_chars=len(result),
            model=OPENAI_MODEL,
            token_in=None,
            token_out=None,
            cost_estimate=None,
            status_code=503,
            latency_ms=elapsed_ms,
            error_message=result[:500],
        )
        return result, 503, False

    elapsed_ms = int((time.perf_counter() - start) * 1000)
    logger.info("[check] user=%s elapsed=%dms chars=%d", user_id, elapsed_ms, len(result))
    token_in = len(normalized) // 2
    token_out = len(result) // 2
    cost = _estimate_cost(token_in, token_out, OPENAI_MODEL)

    log_usage(
        user_id=user_id,
        ip=ip,
        endpoint="/check",
        action="analysis_run",
        input_chars=len(normalized),
        output_chars=len(result),
        model=OPENAI_MODEL,
        token_in=token_in,
        token_out=token_out,
        cost_estimate=cost,
        status_code=200,
        latency_ms=elapsed_ms,
    )

    _set_cached(user_id, cache_key, normalized, result)
    return result, 200, False


def run_verify_analysis(
    user_id: int,
    pledge_text: str,
    ip: str,
    options: dict,
    vector_store_id: Optional[str],
    regional_vector_store_id: Optional[str],
    indexes: Optional[dict],
) -> tuple[Any, int, bool]:
    """
    벡터 검색 기반 검증 리포트 실행.
    Returns: (result_dict_or_error, status_code, from_cache)
    """
    user = get_user(user_id)
    if not user or user["status"] != STATUS_APPROVED:
        return {"detail": "승인되지 않은 사용자입니다."}, 403, False

    # verify는 /check/stream과 항상 병렬 호출되므로 별도 쿼터 차감 안 함
    # (쿼터는 /check/stream 쪽에서만 1회 차감)

    normalized = (pledge_text or "").strip()
    if not normalized:
        return {"detail": "공약 텍스트가 비어 있습니다."}, 400, False

    is_quick = (options.get("phase") or "").strip().lower() == "quick"
    if is_quick:
        options.setdefault("top_k_platform", 6)
        options.setdefault("top_k_pledge", 6)
        options.setdefault("top_k_regional", 8)
        if options["top_k_platform"] >= 6:
            options["top_k_platform"] = 4
        if options["top_k_pledge"] >= 6:
            options["top_k_pledge"] = 4
        if options["top_k_regional"] >= 8:
            options["top_k_regional"] = 5

    vs_id = vector_store_id or ""
    cache_opts = _normalize_cache_options(options)
    cache_key = _cache_key(normalized, cache_opts, CHAT_MODEL, vs_id)

    cached = _get_cached(user_id, cache_key)
    if cached:
        try:
            data = json.loads(cached)
            data = _enrich_verify_result(data)
            log_usage(
                user_id=user_id,
                ip=ip,
                endpoint="/api/pledge/verify",
                action="cache_hit",
                input_chars=len(normalized),
                output_chars=len(cached),
                model=CHAT_MODEL,
                token_in=0,
                token_out=0,
                cost_estimate=0.0,
                status_code=200,
                latency_ms=0,
            )
            return data, 200, True
        except Exception:
            pass

    start = time.perf_counter()
    use_vs = bool(vector_store_id)

    # DB 등록 출마자 공약 컨텍스트 로드
    try:
        from backend.candidate_context import load_candidates_pledges_context
        candidates_ctx = load_candidates_pledges_context()
    except Exception:
        candidates_ctx = ""

    try:
        if use_vs:
            from backend.config import FILE_SEARCH_MAX_RESULTS_QUICK
            from backend.openai_vector_store import run_verify, run_verify_judge
            max_results = FILE_SEARCH_MAX_RESULTS_QUICK if (options.get("phase") or "").strip().lower() == "quick" else None
            if options.get("judge"):
                result = run_verify_judge(
                    vector_store_id, normalized, regional_vector_store_id or "", max_results,
                    candidates_context=candidates_ctx,
                )
            else:
                result = run_verify(
                    vector_store_id, normalized, regional_vector_store_id or "", max_results,
                    candidates_context=candidates_ctx,
                )
        else:
            from backend.report import generate_report
            result = generate_report(
                normalized,
                indexes.get("platform") if indexes else None,
                indexes.get("pledge") if indexes else None,
                indexes.get("regional") if indexes else None,
                options.get("top_k_platform", 6),
                options.get("top_k_pledge", 6),
                options.get("top_k_regional", 8),
            )
    except Exception as e:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        log_usage(
            user_id=user_id,
            ip=ip,
            endpoint="/api/pledge/verify",
            action="analysis_run",
            input_chars=len(normalized),
            output_chars=0,
            model=CHAT_MODEL,
            token_in=None,
            token_out=None,
            cost_estimate=None,
            status_code=500,
            latency_ms=elapsed_ms,
            error_message=str(e)[:500],
        )
        raise

    elapsed_ms = int((time.perf_counter() - start) * 1000)
    phase_tag = "quick" if is_quick else "full"
    logger.info(
        "[verify][%s] user=%s elapsed=%dms top_k=(%s,%s,%s)",
        phase_tag, user_id, elapsed_ms,
        options.get("top_k_platform"), options.get("top_k_pledge"), options.get("top_k_regional"),
    )
    result = _enrich_verify_result(result)
    out_str = json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else str(result)
    token_in = len(normalized) // 2
    token_out = len(out_str) // 2
    cost = _estimate_cost(token_in, token_out, CHAT_MODEL)

    log_usage(
        user_id=user_id,
        ip=ip,
        endpoint="/api/pledge/verify",
        action="analysis_run",
        input_chars=len(normalized),
        output_chars=len(out_str),
        model=CHAT_MODEL,
        token_in=token_in,
        token_out=token_out,
        cost_estimate=cost,
        status_code=200,
        latency_ms=elapsed_ms,
    )

    _set_cached(user_id, cache_key, normalized, out_str)
    return result, 200, False
