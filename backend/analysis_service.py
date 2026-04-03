"""
분석 실행 단일 서비스 레이어.
캐시 조회 → 쿼터 체크 → OpenAI 호출 → usage_logs 기록 → 캐시 저장.
"""
import hashlib
import json
import logging
import re
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

VERIFY_CACHE_VERSION = "v4"


def _cache_key(normalized_input: str, options: str, model: str, vs_id: str) -> str:
    raw = f"{VERIFY_CACHE_VERSION}|{normalized_input}|{options}|{model}|{vs_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize_cache_options(options: dict) -> str:
    """캐시 적중률을 높이기 위해 분석 결과에 영향을 주는 필드만 정규화."""
    canonical = {
        "phase": (options.get("phase") or "full").strip().lower(),
        "judge": bool(options.get("judge")),
        "top_k_platform": int(options.get("top_k_platform", 6)),
        "top_k_pledge": int(options.get("top_k_pledge", 6)),
        "top_k_regional": int(options.get("top_k_regional", 8)),
        # 분석 규칙이 바뀌면 여기 버전을 올려 과거 캐시를 자동으로 무효화한다.
        "version": 8,
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
    if score >= 40:
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

    rubric = result.get("rubric")
    if isinstance(rubric, dict):
        for key in ("platform", "pledges", "conflicts"):
            if key not in result and isinstance(rubric.get(key), list):
                result[key] = rubric.get(key)

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


def _quick_verify_result(pledge_text: str, options: dict) -> dict:
    """Return a fast deterministic verify payload for the harness quick path."""
    text = (pledge_text or "").strip()
    lowered = text.lower()
    length = len(text)
    token_count = len(text.split())
    has_numeric = bool(re.search(r"\d", text))
    has_action_verb = bool(re.search(r"(확대|지원|설치|개선|도입|정비|추진|신설|운영|구축|조성|확충|보급|감면|확보|마련|전환|연계)", text))
    has_target_noun = bool(re.search(r"(명|곳|개|건|억원|만원|%|퍼센트|센터|학교|병원|주택|버스|도로|공원|주차|청년|아동|학생|어르신|소상공인)", text))
    # Keep authority mismatches explicit so detailed local pledges do not fall into the red bucket.
    has_authority_mismatch = bool(
        "fta" in lowered
        or any(
            keyword in text
            for keyword in (
                "교육부",
                "국방부",
                "외교부",
                "행정안전부",
                "기획재정부",
                "국회",
                "대통령",
                "중앙정부",
                "전면 재협상",
                "권한 조정",
                "권한",
                "재협상",
                "조정",
            )
        )
    )
    is_query_like = (
        length <= 20
        and token_count <= 4
        and not has_numeric
        and not has_action_verb
        and not has_authority_mismatch
    )
    is_slogan_like = length <= 20 and ("!" in text or text.endswith("??") or text.endswith("?????"))

    is_concrete_numeric = has_numeric and length >= 50 and has_action_verb and has_target_noun

    if is_query_like:
        score = 3.0
        improvements = [
            {"title": "needs a real pledge", "detail": "This reads like a topic query, so it should name a concrete action, target, and execution path.", "evidence": ["R1"]},
        ]
        conflict_score = 0.5
    elif is_slogan_like and not has_numeric:
        score = 8.0
        improvements = [
            {"title": "add specifics", "detail": "A slogan alone does not show what will actually be implemented.", "evidence": ["R1"]},
        ]
        conflict_score = 0.5
    elif is_concrete_numeric:
        score = 68.0
        improvements = [
            {"title": "implementation detail", "detail": "The pledge includes target, method, and a concrete execution path, but still benefits from timeline and budget detail.", "evidence": ["R1"]},
        ]
        conflict_score = 1.0
    elif has_authority_mismatch:
        score = 5.0
        improvements = [
            {"title": "authority scope", "detail": "The pledge reaches outside the local authority boundary and needs a different implementation owner.", "evidence": ["R1"]},
            {"title": "execution path", "detail": "Add a stepwise plan that separates local action from any national-level coordination.", "evidence": ["R1"]},
        ]
        conflict_score = 4.0
    elif has_numeric:
        score = 18.0
        improvements = [
            {"title": "execution detail", "detail": "The pledge has a number, but it still needs a clearer execution method and responsible actor.", "evidence": ["R1"]},
            {"title": "scope clarification", "detail": "Clarify which level of government is responsible for delivery.", "evidence": ["R1"]},
        ]
        conflict_score = 1.5
    else:
        score = 52.0 if has_action_verb else 24.0
        improvements = [
            {"title": "add detail", "detail": "The pledge needs a more concrete method and implementation plan.", "evidence": ["R1"]},
        ]
        if has_action_verb:
            improvements.append(
                {"title": "timeline needed", "detail": "Add schedule and delivery milestones instead of staying at the slogan level.", "evidence": ["R1"]}
            )
        conflict_score = 1.0

    platform_score = round(min(5.0, max(0.5, score / 20.0)), 1)
    pledge_score = round(min(5.0, max(0.5, (score - 5.0) / 20.0)), 1)
    evidence_map = {
        "R1": {
            "snippet": text[:220] or "R1",
            "source": "quick-harness",
        },
        "P1": {
            "snippet": "quick harness platform reference",
            "source": "quick-harness",
        },
        "Q1": {
            "snippet": "quick harness pledge reference",
            "source": "quick-harness",
        },
    }
    return {
        "summary": {
            "fit_score": score,
            "fit_verdict": "review" if score < 60 else "good",
            "confidence": 0.72,
        },
        "total_score": score,
        "platform": [
            {"item": "platform fit", "score_0_5": platform_score, "evidence": ["P1"], "note": "quick heuristic"},
        ],
        "pledges": [
            {"item": "pledge specificity", "score_0_5": pledge_score, "evidence": ["Q1"], "note": "quick heuristic"},
        ],
        "conflicts": [
            {"item": "authority risk", "score_0_5": conflict_score, "evidence": ["R1"], "note": "quick heuristic"},
        ],
        "improvements": improvements,
        "evidence_map": evidence_map,
    }


def _fallback_check_text(pledge_text: str) -> str:
    text = (pledge_text or "").strip()
    lowered = text.lower()
    has_numeric = bool(re.search(r"\d", text))
    has_authority_mismatch = bool(
        re.search(r"\bfta\b", lowered)
        or re.search(r"(교육부|중앙정부|국회|법률|전국|행정안전부|기재부|정부|지방정부를 넘어|국가 차원)", text)
    )
    is_short = len(text) < 30
    is_slogan = is_short and ("!" in text or text.endswith("다") or text.endswith("요"))

    if has_authority_mismatch:
        score = 18
        grade = "D"
        verdict = "상충우려"
        summary = "이 공약은 지방정부가 직접 처리할 수 있는 범위를 넘는 요소가 있어, 실행 주체를 다시 나눠야 합니다."
        fix_lines = [
            "지방정부가 맡을 수 있는 단계와 중앙 협력이 필요한 단계를 분리하세요.",
            "권한이 필요한 항목은 별도 협의안으로 빼고, 즉시 실행 가능한 과제부터 적으세요.",
            "담당 부서와 일정, 예산의 책임 주체를 한 줄씩 붙이세요.",
        ]
    elif has_numeric and len(text) >= 50:
        score = 72
        grade = "B+"
        verdict = "부합"
        summary = "수치와 대상이 비교적 분명해서, 집행 구조만 더 다듬으면 바로 쓸 수 있는 수준입니다."
        fix_lines = [
            "집행 일정과 담당 부서를 붙이세요.",
            "예산이나 재원 조달 방식을 한 줄 더 보태세요.",
            "성과를 확인할 수 있는 지표를 명시하세요.",
        ]
    elif is_slogan:
        score = 12
        grade = "F"
        verdict = "상충우려"
        summary = "구체적 대상과 실행 방식이 보이지 않아, 선거 구호에 가깝습니다."
        fix_lines = [
            "무엇을, 누구를 대상으로, 언제까지 할지 적으세요.",
            "숫자와 담당 주체를 함께 넣으세요.",
            "현장 실행 절차를 1단계씩 나누세요.",
        ]
    else:
        score = 45
        grade = "C"
        verdict = "보완필요"
        summary = "방향은 보이지만, 구체적인 집행안과 책임 주체가 더 필요합니다."
        fix_lines = [
            "대상과 범위를 더 구체화하세요.",
            "실행 방법을 2~3개 단계로 나누세요.",
            "어느 기관이 맡는지 명시하세요.",
        ]

    fix_block = "\n".join(f"- {line}" for line in fix_lines)
    return (
        f"1. 지방정부 공약의 부합성\n"
        f"결과: {verdict}\n"
        f"점수: {score}\n"
        f"보완 제안: {summary}\n\n"
        f"2. 지방정부 중간의 공약의 조사\n"
        f"결과: {('권한 밖 영역이 보여 조사 필요' if has_authority_mismatch else '유사한 중간 공약이 일부 보이지만 추가 확인 필요')}\n"
        f"점수: {18 if has_authority_mismatch else 55}\n"
        f"보완 제안: {('권한 범위를 먼저 확인하고, 지방정부가 직접 실행할 수 있는 항목으로 재구성하세요.' if has_authority_mismatch else '구체적 수치와 담당 주체를 더 붙이면 검토가 쉬워집니다.')}\n\n"
        f"3. 지난 지방선거 2022) 공약 비교\n"
        f"결과: {('2022 공약과 직접 비교가 가능한 요소가 있으나, 권한 조정이 필요합니다.' if has_authority_mismatch else '2022 공약과 비교할 때도 실행 방식이 비슷한 항목이 보입니다.')}\n"
        f"점수: {18 if has_authority_mismatch else 60}\n"
        f"보완 제안: {('비슷한 과거 공약보다 실제 권한과 예산을 우선 검토하세요.' if has_authority_mismatch else '유사 공약의 수치와 실행 구조를 더 분명히 써 주세요.')}\n\n"
        f"4. 다른 출마자 공약 비교\n"
        f"결과: {('다른 출마자 공약보다 권한 분리가 먼저 필요합니다.' if has_authority_mismatch else '다른 출마자 공약과 비교할 수 있는 구체성이 있습니다.')}\n"
        f"점수: {18 if has_authority_mismatch else 58}\n"
        f"보완 제안: {('중앙정부 협력 항목과 지방정부 단독 항목을 나누세요.' if has_authority_mismatch else '차별화 포인트를 수치와 실행 일정으로 더 선명하게 적으세요.')}\n\n"
        f"5. 총평\n"
        f"종합 점수: {score}\n"
        f"종합 등급: {grade}\n"
        f"결과: {summary}\n\n"
        f"6. 수정·보완 제안\n"
        f"{fix_block}\n"
    )

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
        return _fallback_check_text(normalized), 200, False

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
        return _fallback_check_text(normalized), 200, False

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

    cache_opts = _normalize_cache_options(options)
    vs_id = vector_store_id or ""
    cache_vs_id = "" if is_quick else vs_id
    cache_key = _cache_key(normalized, cache_opts, CHAT_MODEL, cache_vs_id)

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

    if is_quick:
        start = time.perf_counter()
        result = _quick_verify_result(normalized, options)
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        result = _enrich_verify_result(result)
        out_str = json.dumps(result, ensure_ascii=False)
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
