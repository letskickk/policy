"""
당 부합 점검: PDF 기준 문서 또는 Vector Store(file_search) 또는 FAISS 검색 + GPT API 호출.
- USE_OPENAI_VECTOR_STORE=1: Vector Store(file_search) 사용.
- FAISS 모드에서 indexes 전달 시: 출마자 공약으로 검색한 관련 청크만 넣어 GPT 호출(공약 300개 등 대량도 가능).
"""
import logging
import re
from collections import OrderedDict
from typing import Any

from openai import OpenAI

from backend.config import OPENAI_API_KEY, OPENAI_MODEL
from backend.pdf_loader import load_platform_context, load_pledges_context
from backend.prompts import build_user_message, build_pledge_meta_from_user, load_system_prompt

logger = logging.getLogger(__name__)

_RESULT_CACHE: "OrderedDict[str, str]" = OrderedDict()
_RESULT_CACHE_MAX = 128


def apply_check_postprocessing(result: str, pledge: str) -> str:
    """GPT 응답 후처리: 섹션 2 형식, 명칭만 제시 시 보정."""
    # 유사·중복 공약: 없음. (긴 설명...) → "없음"만 유지
    result = re.sub(
        r'(유사·중복 공약:\s*)없음\.?\s*\([^)]+\)',
        r'\1없음',
        result,
        count=1,
    )
    # 섹션 2 제목·결과 형식 보정
    result = result.replace("2. 개혁신당 공약과의 비교", "2. 개혁신당 중앙당 공약과의 유사성")
    _idx = result.find("2. 개혁신당")
    if _idx != -1:
        _rest = result[_idx:]
        _m = re.search(r"결과:\s*(?:적합|부분적 적합|부적합)\s*\((\d{1,3})점\)", _rest)
        if _m:
            _start = _idx + _m.start()
            _end = _idx + _m.end()
            result = result[:_start] + f"결과: 유사도({_m.group(1)}점)" + result[_end:]

    if len(pledge.strip()) < 80:
        result = re.sub(
            r"(2\. 개혁신당 중앙당 공약과의 유사성\s*\n결과:\s*유사도\s*\()(\d{2,3})(점\))",
            lambda m: m.group(1) + ("50" if int(m.group(2)) >= 95 else m.group(2)) + m.group(3),
            result,
            count=1,
        )
        _fix = "제시공약은 명칭만 있어 구체적으로 뭘 하겠다는 내용이 없음. 우리당 공약의 구체적 방안을 참고해 보완 필요."
        result = re.sub(r"일치율은\s*90% 이상[^.]*판단한다?", f"{_fix}", result)
        result = re.sub(r"90% 이상\s*\(거의 동일\)", _fix, result)
        result = result.replace("거의 동일", "구체적 방안 부족")
        result = result.replace("사실상 동일한", "명칭만 동일한")
        result = result.replace("동일하여", "명칭은 같으나")
        result = result.replace("동일로 판단", "구체성 부족으로 판단")
    return result


def _get_cached_result(key: str) -> str | None:
    if key in _RESULT_CACHE:
        _RESULT_CACHE.move_to_end(key)
        return _RESULT_CACHE[key]
    return None


def _set_cached_result(key: str, value: str) -> None:
    _RESULT_CACHE[key] = value
    _RESULT_CACHE.move_to_end(key)
    if len(_RESULT_CACHE) > _RESULT_CACHE_MAX:
        _RESULT_CACHE.popitem(last=False)


def _context_from_hits(hits: list) -> str:
    """검색된 (DocChunk, score) 리스트를 프롬프트용 컨텍스트 문자열로 만든다."""
    return "\n\n".join(
        f"--- {chunk.path} ---\n{chunk.text}"
        for chunk, _ in hits
    )


def _load_candidates_context() -> str:
    """DB에 등록된 출마자 공약을 컨텍스트 텍스트로 로드."""
    try:
        from backend.candidate_context import load_candidates_pledges_context
        return load_candidates_pledges_context()
    except Exception as e:
        logger.warning("출마자 공약 컨텍스트 로드 실패: %s", e)
        return ""


def _build_winners2022_context_for_non_vs(
    pledge: str,
    winners2022_vector_store_id: str | None,
    user_meta: dict | None,
) -> str:
    """FAISS/PDF 경로에서도 winners2022 컨텍스트를 생성한다.

    winners2022 벡터 스토어 ID가 있으면 OpenAI 벡터 검색 + 공공 API를 사용하고,
    벡터 스토어가 없으면 공공 API만 사용한다.
    """
    try:
        from backend.openai_vector_store import build_winners2022_context
        ctx = build_winners2022_context(
            pledge, winners2022_vector_store_id or "", user_meta or {},
        )
        if ctx:
            logger.info("winners2022 컨텍스트 생성 완료: %d자", len(ctx))
        return ctx
    except Exception as e:
        logger.warning("winners2022 컨텍스트 생성 실패: %s", e)
        return ""


def check_pledge_alignment(
    pledge: str,
    vector_store_id: str | None = None,
    regional_vector_store_id: str | None = None,
    winners2022_vector_store_id: str | None = None,
    indexes: dict[str, Any] | None = None,
    user_id: int | None = None,
) -> str:
    """
    출마자 공약(pledge)에 대해:
    1) 정강·정책 문서와 대조해 이념·취지 부합 여부를 판단하고,
    2) 우리당 공약과 비교해 취지에 맞는지 판단한 결과를 GPT로 생성해 반환한다.

    - vector_store_id가 있으면 Vector Store(file_search) 사용.
    - indexes가 있으면 FAISS 검색으로 관련 청크만 넣어 호출(공약 수백 개도 가능).
    """
    if not OPENAI_API_KEY:
        return "오류: OPENAI_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요."

    pledge_key = (pledge or "").strip()
    cached = _get_cached_result(pledge_key)
    if cached is not None:
        logger.info("캐시된 결과 반환")
        return cached

    user_meta = None
    if user_id is not None:
        from backend.auth import get_user
        user = get_user(user_id)
        user_meta = build_pledge_meta_from_user(user)

    candidates_context = _load_candidates_context()

    use_vector_store = bool(vector_store_id)
    use_faiss_search = (
        not use_vector_store
        and indexes
        and all(indexes.get(k) for k in ("platform", "pledge"))
        and indexes["platform"].size() > 0
        and indexes["pledge"].size() > 0
    )

    if use_vector_store:
        logger.info("Vector Store 기반 점검...")
        from backend.openai_vector_store import run_check
        result = run_check(
            vector_store_id,
            pledge_key,
            "",
            winners2022_vector_store_id or "",
            max_results=10,
            user_meta=user_meta,
            candidates_context=candidates_context,
        )
    elif use_faiss_search:
        logger.info("FAISS 검색 기반 점검 (관련 청크만 사용)...")
        from backend.report import search_all_indexes
        hits = search_all_indexes(
            pledge_key,
            indexes["platform"],
            indexes["pledge"],
            indexes.get("regional"),
            top_k_platform=12,
            top_k_pledge=20,
            top_k_regional=0,
        )
        platform_context = _context_from_hits(hits["platform"])
        pledges_context = _context_from_hits(hits["pledge"])

        if not platform_context.strip() and not pledges_context.strip():
            return "오류: 검색 결과가 없습니다. 인덱스를 확인하세요."

        winners2022_ctx = _build_winners2022_context_for_non_vs(
            pledge_key, winners2022_vector_store_id, user_meta,
        )

        system = load_system_prompt()
        user = build_user_message(
            platform_context, pledges_context, pledge_key, winners2022_ctx,
            candidates_pledges_context=candidates_context,
            user_meta=user_meta,
        )
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        result = response.choices[0].message.content or ""
    else:
        logger.info("PDF 컨텍스트 로드 시작...")
        platform_context = load_platform_context()
        pledges_context = load_pledges_context()

        logger.info(f"정강정책 컨텍스트 길이: {len(platform_context)}자")
        logger.info(f"공약 컨텍스트 길이: {len(pledges_context)}자")

        if not platform_context.strip() and not pledges_context.strip():
            return "오류: 기준 문서가 없습니다. data/pdf/정강정책/ 와 data/pdf/공약/ 폴더에 PDF를 넣어 주세요."

        if not pledges_context.strip():
            logger.warning("공약 컨텍스트가 비어있습니다. GPT가 공약 비교를 제대로 할 수 없습니다.")

        winners2022_ctx = _build_winners2022_context_for_non_vs(
            pledge_key, winners2022_vector_store_id, user_meta,
        )

        system = load_system_prompt()
        user = build_user_message(
            platform_context, pledges_context, pledge_key, winners2022_ctx,
            candidates_pledges_context=candidates_context,
            user_meta=user_meta,
        )

        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        result = response.choices[0].message.content or ""

    # 모델이 verify 형식 JSON을 반환한 경우 저장/표시하지 않고 안내 문구로 대체
    # 접두사([결과], ```json 등)가 있어도 내용에 fit_score·rubric 등이 있으면 JSON으로 간주
    s = (result or "").strip()
    head = s[:4000]  # 앞부분만 검사
    if ("fit_score" in head and "rubric" in head) or ('"breakdown"' in head and "fit_score" in head):
        logger.warning("[check] GPT가 JSON 형식을 반환함. 텍스트 결과로 대체합니다.")
        result = (
            "점검 결과가 요청한 텍스트 형식으로 생성되지 않았습니다. "
            "잠시 후 다시 시도해 주세요."
        )

    logger.info(f"GPT 응답 길이: {len(result)}자")
    result = apply_check_postprocessing(result, pledge_key)
    _set_cached_result(pledge_key, result)
    return result
