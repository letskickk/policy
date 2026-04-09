"""
당 부합 점검: PDF 기준 문서 또는 Vector Store(file_search) 또는 FAISS 검색 + GPT API 호출.
- USE_OPENAI_VECTOR_STORE=1: Vector Store(file_search) 사용.
- FAISS 모드에서 indexes 전달 시: 출마자 공약으로 검색한 관련 청크만 넣어 GPT 호출(공약 300개 등 대량도 가능).
"""
import logging
import re
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Iterator

from openai import OpenAI

from backend.config import OPENAI_API_KEY, OPENAI_MODEL
from backend.pdf_loader import load_platform_context, load_pledges_context
from backend.prompts import build_user_message, build_pledge_meta_from_user, load_system_prompt

logger = logging.getLogger(__name__)

_RESULT_CACHE: "OrderedDict[str, str]" = OrderedDict()
_RESULT_CACHE_MAX = 128
_ACTION_HINT_KEYWORDS = (
    "설치", "도입", "확대", "신설", "운영", "지원", "조성", "건립",
    "개편", "추진", "배치", "확충", "정비", "개소", "확보", "개선",
)
_POLICY_HINT_KEYWORDS = (
    "교통", "주차", "안전", "보행", "버스", "주거", "돌봄", "복지", "청년",
    "일자리", "상권", "경제", "환경", "시설", "교육", "통학", "보육", "의료",
)


def _is_low_quality_pledge_input(text: str) -> bool:
    raw = re.sub(r"\s+", " ", (text or "").strip())
    if not raw:
        return True

    char_len = len(raw)
    jamo_count = len(re.findall(r"[ㄱ-ㅎㅏ-ㅣ]", raw))
    repeat_noise = bool(re.search(r"(.)\1{4,}", raw))
    policy_hits = sum(1 for kw in _POLICY_HINT_KEYWORDS if kw in raw)
    action_hits = sum(1 for kw in _ACTION_HINT_KEYWORDS if kw in raw)
    delimiter_hits = sum(raw.count(ch) for ch in ("+", "/", "|"))
    digit_hits = len(re.findall(r"\d+", raw))
    hangul_word_count = len(re.findall(r"[가-힣]{2,}", raw))
    alpha_word_count = len(re.findall(r"[A-Za-z]{2,}", raw))
    meaningful_word_count = hangul_word_count + alpha_word_count
    long_alpha_noise = bool(re.search(r"[A-Za-z]{10,}", raw))

    if jamo_count >= 4 and (jamo_count / max(char_len, 1)) >= 0.18:
        return True
    if repeat_noise and meaningful_word_count <= 2:
        return True
    if long_alpha_noise and action_hits == 0 and char_len < 120:
        return True
    if delimiter_hits >= 1 and action_hits == 0 and digit_hits == 0 and char_len < 120:
        return True
    if char_len < 25 and policy_hits == 0 and action_hits == 0 and meaningful_word_count <= 2:
        return True
    return False


def _build_low_quality_check_text(pledge: str) -> str:
    raw = (pledge or "").strip()
    return (
        "1. 개혁신당 정강정책과의 부합성\n"
        "결과: 낮음\n"
        "강점: 현재 입력에서는 정강정책과 연결할 만한 정책 내용이 확인되지 않습니다.\n"
        "보완 핵심: 키보드 난타, 감정 표현, 단편 문구가 섞여 있어 공약의 대상·문제·수단을 읽을 수 없습니다.\n\n"
        "2. 개혁신당 중앙당 공약과의 유사성\n"
        "결과: 없음\n"
        "강점: 비교 가능한 공약 문장으로 보기 어려워 과장된 유사 판정을 하지 않았습니다.\n"
        "보완 핵심: 누구를 위해 무엇을 어떻게 하겠다는 문장으로 다시 써야 중앙당 공약과 비교가 가능합니다.\n\n"
        "3. 제8회 지방선거(2022) 당선인 공약과의 비교\n"
        "결과: 없음\n"
        "강점: 의미 없는 입력을 억지로 타지역 사례와 연결하지 않았습니다.\n"
        "보완 핵심: 생활 문제, 대상 지역, 실행 수단이 있어야 비교가 가능합니다.\n\n"
        "4. 우리 당 출마자 공약 비교\n"
        "결과: 없음\n"
        "강점: 실제 정책 문장으로 확인되지 않아 무리한 유사 판정을 피했습니다.\n"
        "보완 핵심: 감정 표현 대신 정책 문장으로 다시 입력해야 비교가 가능합니다.\n\n"
        "5. 총평\n"
        "결과: 종합 점수(8점)\n"
        "정강정책 정합성(1점) - 강점: 정책 방향을 판단할 정보가 거의 없습니다. 보완 핵심: 가치·문제의식·수단이 드러나지 않습니다.\n"
        "정책 설계 완성도(2점) - 강점: 없음. 보완 핵심: 대상, 문제, 수단, 기대효과가 빠져 있습니다.\n"
        "실현 가능성(1점) - 강점: 없음. 보완 핵심: 권한 주체와 실행 방식이 보이지 않습니다.\n"
        "구체성(1점) - 강점: 없음. 보완 핵심: 수치, 일정, 재원, 대상이 없습니다.\n"
        "전달력(3점) - 강점: 감정 표현 의도는 읽히지만 정책 메시지는 아닙니다. 보완 핵심: 한 문장 구호가 아니라 정책 설명 문장으로 바꿔야 합니다.\n"
        "종합 점수: 8점\n"
        "종합해석 등급: F\n\n"
        "6. 수정·보완 제안\n"
        "- [문제 정의 추가] 어느 지역에서 어떤 문제가 반복되는지 한 문장으로 먼저 적어 주세요.\n"
        "- [대상 명시] 누가 가장 크게 불편을 겪는지 적어 주세요.\n"
        "- [실행 수단 추가] 설치, 정비, 지원, 조례, 예산 같은 실행 수단을 한 가지 이상 넣어 주세요.\n"
        f"- [불필요 표현 제거] '{raw[:30]}' 같은 감정 표현이나 난타 텍스트는 빼고 정책 문장만 남겨 주세요."
    )


def _strip_usage_markers(text: str) -> str:
    return re.sub(r"\s*\[USAGE\][^\n]*(?:\n|$)", "\n", str(text or ""), flags=re.MULTILINE)


def _is_multi_policy_input(text: str) -> bool:
    raw = re.sub(r"\s+", " ", (text or "").strip())
    if not raw:
        return False
    separator_pattern = r"[+/]|(?:,|·| 및 | 또는 | 와 | 과 )"
    separator_hits = len(re.findall(separator_pattern, raw))
    action_hits = len(re.findall(r"(도입|설치|지원|확대|축소|폐지|금지|강화|개편|추진|지급|규제)", raw))
    segments = [seg.strip() for seg in re.split(separator_pattern, raw) if seg.strip()]
    long_segments = [seg for seg in segments if len(seg) >= 4]
    return separator_hits >= 2 or (len(long_segments) >= 2 and action_hits <= 1)


def _needs_conservative_rewrite(text: str) -> bool:
    raw = re.sub(r"\s+", " ", (text or "").strip())
    if not raw:
        return False
    if len(raw) < 120:
        return True
    return _is_multi_policy_input(raw)


def _looks_practical_local_pledge(text: str) -> bool:
    raw = re.sub(r"\s+", " ", (text or "").strip())
    if not raw or _is_low_quality_pledge_input(raw):
        return False
    if "+" in raw or "|" in raw or raw.count("/") > 1:
        return False

    digit_hits = len(re.findall(r"\d+", raw))
    action_hits = len(re.findall(r"(도입|확대|확충|정비|개선|설치|추진|운영|관리|복층화|개정)", raw))
    policy_hits = len(re.findall(r"(주차|교통|안전|주거|돌봄|청년|복지|공원|보행|주민|생활)", raw))
    stage_hits = len(re.findall(r"(1단계|2단계|3단계|우선|단계별|확대|개선|정비|도입|확충)", raw))

    if len(raw) < 35:
        return False
    if digit_hits >= 1 and action_hits >= 1:
        return True
    if action_hits >= 2 and (policy_hits >= 1 or stage_hits >= 1):
        return True
    return False


def _rewrite_flagged_output(text: str, pledge: str) -> str:
    if not _needs_conservative_rewrite(pledge):
        return text

    rewritten = text
    rewritten = re.sub(
        r"(1\.\s*개혁신당 정강정책과의 부합성\s*[\r\n]+결과:\s*)(.+)",
        r"\1낮음",
        rewritten,
        count=1,
    )
    replacements = {
        "강점: 책임 있는 복지라는 큰 틀과 일부 접점은 있습니다.": "강점: 복지와 노동 문제를 제기한 방향성 자체는 읽힙니다.",
        "보완 핵심: 기본소득의 지속 가능성과 재정 건전성 논리가 전혀 없어 정강정책과의 정합성이 약합니다.": "보완 핵심: 넓은 가치 언어만으로 정강정책과 직접 연결할 수 없습니다. 대상, 재원, 기존 제도와의 관계, 지속 가능성이 있어야 정합성을 논할 수 있습니다.",
        "강점: 기본소득과 ai로봇이라는 두 축은 제시했습니다.": "강점: 서로 다른 요구를 나열해 문제의식은 드러났습니다.",
        "강점: 기본소득과 AI로봇이라는 두 축은 제시했습니다.": "강점: 서로 다른 요구를 나열해 문제의식은 드러났습니다.",
        "보완 핵심: 수치, 일정, 대상, 재원, 조례 근거가 없어 실제 공약으로는 매우 추상적입니다.": "보완 핵심: 두 정책을 한 줄에 함께 적어 정책 단위 자체가 흐려졌고, 대상, 수단, 재원, 일정, 법적 근거가 없어 공약으로 보기 어렵습니다.",
    }
    for old, new in replacements.items():
        rewritten = rewritten.replace(old, new)
    return rewritten


def _soften_over_detailed_improvements(text: str) -> str:
    replacements = {
        "권한 주체 명확화": "추진 가능 범위 정리",
        "구청, 구의회, 경찰, 민간 참여시설이 각각 무엇을 맡는지 한 문장씩 분리해 쓰세요.": "누가 협의가 필요한 정도인지까지만 간단히 쓰면 됩니다.",
        "구청, 구의회, 경찰, 민간 참여시설이 각각 무엇을 맡는지 분리해 쓰세요.": "누가 협의가 필요한 정도인지까지만 간단히 쓰면 됩니다.",
        "구청, 구의회, 경찰, 민간 참여시설의 역할을 나눠 적어야 합니다.": "협의가 필요한 주체가 있다는 정도만 적으면 됩니다.",
        "각 단계의 실행 주체, 우선순위, 조례와 예산 집행의 관계를 더 구체화해야 합니다.": "각 단계에서 무엇을 먼저 할지와 적용 기준 정도만 더 분명히 하면 됩니다.",
        "기초의원이 직접 할 수 있는 조례 제정, 예산 심의, 행정사무감사와 시 집행부·경찰·민간시설 협의가 필요한 사항을 문장별로 나눠 적어야 합니다.": "기초의회에서 추진 가능한 수단인지 정도만 분명히 하면 됩니다.",
        "실현 경로를 더 구체화해야 한다.": "협의가 필요한 사업인지 정도만 더 분명히 하면 됩니다.",
        "실현 경로를 더 구체화해야 합니다.": "협의가 필요한 사업인지 정도만 더 분명히 하면 됩니다.",
        "권한 범위 구분": "추진 가능 범위 정리",
        "실행 주체": "추진 방식",
        "부서별 역할": "추진 순서",
        "조례와 예산 집행의 관계": "추진 방식",
    }
    softened = text
    for old, new in replacements.items():
        softened = softened.replace(old, new)
    return softened




def _grade_for_total(total: int) -> str:
    if total >= 95: return "A+"
    if total >= 90: return "A"
    if total >= 85: return "B+"
    if total >= 80: return "B"
    if total >= 75: return "C+"
    if total >= 70: return "C"
    if total >= 65: return "D+"
    if total >= 60: return "D"
    return "F"


def _normalize_grade_by_total(text: str) -> str:
    match = re.search(r"종합 점수:\s*(\d+)점\s*/\s*100점", text)
    if not match:
        match = re.search(r"결과:\s*종합 점수\((\d+)점\s*/\s*100점\)", text)
    if not match:
        return text
    total = int(match.group(1))
    grade = _grade_for_total(total)
    normalized = re.sub(r"종합해석 등급:\s*[A-Z][+]?", f"종합해석 등급: {grade}", text)
    normalized = normalized.replace("점 / 100점 / 100점", "점 / 100점")
    return normalized


def _normalize_score_display(text: str) -> str:
    axis_max = {
        "정강정책 정합성": 20,
        "정책 설계 완성도": 30,
        "실현 가능성": 20,
        "구체성": 15,
        "전달력": 15,
    }

    def _axis_repl(match: re.Match) -> str:
        label = match.group(1)
        raw_score = int(match.group(2))
        capped = min(raw_score, axis_max[label])
        return f"{label}({capped}점 / {axis_max[label]}점)"

    text = re.sub(
        r"(정강정책 정합성|정책 설계 완성도|실현 가능성|구체성|전달력)\((\d{1,3})점\)",
        _axis_repl,
        text,
    )
    text = re.sub(r"(결과:\s*종합 점수\()(\d{1,3})점\)", r"\g<1>\2점 / 100점)", text)
    text = re.sub(r"(종합 점수:\s*)(\d{1,3})점\b", r"\1\2점 / 100점", text)
    text = re.sub(
        r"(종합해석 등급:\s*)([a-z])\b",
        lambda m: m.group(1) + m.group(2).upper(),
        text,
    )
    return text


def _format_total_review_axes(text: str) -> str:
    axis_pattern = re.compile(
        r"^((?:정강정책 정합성|정책 설계 완성도|실현 가능성|구체성|전달력)\(\d+점\s*/\s*\d+점\))\s*-\s*강점:\s*(.*?)\s*보완 핵심:\s*(.*)$",
        re.MULTILINE,
    )

    def _repl(match: re.Match) -> str:
        title = match.group(1).strip()
        strength = match.group(2).strip()
        improvement = match.group(3).strip()
        return f"{title}\n강점: {strength}\n보완 핵심: {improvement}\n"

    return axis_pattern.sub(_repl, text)


def _rebalance_practical_scores_v2(text: str, pledge: str) -> str:
    raw_pledge = re.sub(r"\s+", " ", (pledge or "").strip())
    if not raw_pledge or len(raw_pledge) < 35:
        return text
    if "+" in raw_pledge or "|" in raw_pledge:
        return text

    has_target = bool(re.search(r"(주민|청년|어르신|노인|아동|여성|장애인|학생|시민|주민|세대|계층)", raw_pledge))
    has_means = bool(re.search(r"(설치|확충|지원|운영|도입|개선|정비|추진|조성|확대|신설|개편)", raw_pledge))
    has_numbers = bool(re.search(r"\d+", raw_pledge))
    has_substance = has_target and has_means and has_numbers
    has_exec_path = bool(re.search(r"(조례|예산|심의|국비|공모|시비|도비|매칭)", raw_pledge))

    axis_specs = [
        ("정강정책 정합성", 20),
        ("정책 설계 완성도", 30),
        ("실현 가능성", 20),
        ("구체성", 15),
        ("전달력", 15),
    ]

    scores: dict[str, int] = {}
    for label, _ in axis_specs:
        match = re.search(rf"{re.escape(label)}\((\d+)점\s*/\s*(\d+)점\)", text)
        if match:
            scores[label] = int(match.group(1))

    if not scores:
        return text

    def _replace_axis(label: str, new_score: int, max_score: int, current: str) -> str:
        return re.sub(
            rf"{re.escape(label)}\(\d+점\s*/\s*{max_score}점\)",
            f"{label}({new_score}점 / {max_score}점)",
            current,
        )

    adjusted = text

    # 실현 가능성 플로어: 실행 경로(조례/예산/국비 등)가 있으면 최소 11점
    if has_exec_path and "실현 가능성" in scores and scores["실현 가능성"] < 11:
        scores["실현 가능성"] = 11
        adjusted = _replace_axis("실현 가능성", 11, 20, adjusted)

    total = sum(scores[label] for label, _ in axis_specs if label in scores)

    if has_substance and total < 65:
        for label, max_score in [("구체성", 15), ("정책 설계 완성도", 30), ("실현 가능성", 20)]:
            if label not in scores:
                continue
            while total < 65 and scores[label] < max_score:
                scores[label] += 1
                total += 1
            adjusted = _replace_axis(label, scores[label], max_score, adjusted)
            if total >= 65:
                break

    adjusted = re.sub(r"결과:\s*종합 점수\(\d+점(?:\s*/\s*100점)?\)", f"결과: 종합 점수({total}점 / 100점)", adjusted)
    adjusted = re.sub(r"종합 점수:\s*\d+점(?:\s*/\s*100점)?", f"종합 점수: {total}점 / 100점", adjusted)
    return adjusted


def apply_check_postprocessing(result: str, pledge: str) -> str:
    """GPT 응답 후처리: 섹션 2 형식, 명칭만 제시 시 보정."""
    result = _strip_usage_markers(result)
    result = _rewrite_flagged_output(result, pledge)
    result = re.sub(
        r"(유사·중복 공약:\s*)없음\.?\s*\([^)]+\)",
        r"\1없음",
        result,
        count=1,
    )
    result = result.replace("2. 개혁신당 공약과의 비교", "2. 개혁신당 중앙당 공약과의 유사성")
    # 섹션 2·3·4에서 숫자 점수 제거 (참고용 비교 섹션이므로 점수 불필요)
    result = re.sub(r"(결과:\s*)(?:적합|부분적 적합|부적합|유사도)\s*\(\d{1,3}점\)", r"\1일부 유사", result)
    result = re.sub(r"(결과:\s*)\d{1,3}%\s*일치", r"\1일부 유사", result)
    result = _normalize_score_display(result)
    result = _format_total_review_axes(result)
    result = _soften_over_detailed_improvements(result)
    result = _rebalance_practical_scores_v2(result, pledge)
    result = _normalize_grade_by_total(result)
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


def _build_enriched_context(pledge: str, user_meta: dict | None) -> dict[str, str]:
    region = " ".join(
        part for part in [
            (user_meta or {}).get("region_province", ""),
            (user_meta or {}).get("region_city", ""),
            (user_meta or {}).get("district_name", ""),
        ]
        if part
    ).strip()
    if not region:
        return {}

    try:
        from backend.policy_drafter import _search_messages_by_topic
        from backend.research_assistant import research_topic
    except Exception as e:
        logger.warning("추가 컨텍스트 로더 import 실패: %s", e)
        return {}

    def _safe_messages() -> str:
        try:
            return (_search_messages_by_topic(pledge) or "")[:15000]
        except Exception as e:
            logger.warning("공식 논평·보도자료 컨텍스트 생성 실패: %s", e)
            return ""

    def _safe_research() -> dict:
        try:
            return research_topic(
                topic=pledge,
                region=region,
                district_name=(user_meta or {}).get("district_name") or None,
                election_type=(user_meta or {}).get("election_type") or None,
                years=2,
            ) or {}
        except Exception as e:
            logger.warning("리서치 컨텍스트 생성 실패: %s", e)
            return {}

    with ThreadPoolExecutor(max_workers=2) as executor:
        messages_future = executor.submit(_safe_messages)
        research_future = executor.submit(_safe_research)
        messages = messages_future.result()
        research = research_future.result()

    assembly_info = research.get("assembly") if isinstance(research, dict) else {}
    public_data_info = research.get("public_data") if isinstance(research, dict) else {}

    return {
        "messages_context": messages,
        "assembly_context": ((assembly_info or {}).get("context_text") or "")[:20000],
        "public_data_context": ((public_data_info or {}).get("context_text") or "")[:20000],
        "research_context": ((research or {}).get("briefing_text") or "")[:15000],
    }


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
    if _is_low_quality_pledge_input(pledge_key):
        result = _build_low_quality_check_text(pledge_key)
        _set_cached_result(pledge_key, result)
        return result
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
    enriched_context = _build_enriched_context(pledge_key, user_meta)

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
            messages_context=enriched_context.get("messages_context", ""),
            assembly_context=enriched_context.get("assembly_context", ""),
            public_data_context=enriched_context.get("public_data_context", ""),
            research_context=enriched_context.get("research_context", ""),
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
            messages_context=enriched_context.get("messages_context", ""),
            assembly_context=enriched_context.get("assembly_context", ""),
            public_data_context=enriched_context.get("public_data_context", ""),
            research_context=enriched_context.get("research_context", ""),
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
            messages_context=enriched_context.get("messages_context", ""),
            assembly_context=enriched_context.get("assembly_context", ""),
            public_data_context=enriched_context.get("public_data_context", ""),
            research_context=enriched_context.get("research_context", ""),
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


def check_pledge_alignment_stream(
    pledge: str,
    vector_store_id: str | None = None,
    regional_vector_store_id: str | None = None,
    winners2022_vector_store_id: str | None = None,
    indexes: dict[str, Any] | None = None,
    user_id: int | None = None,
) -> Iterator[str]:
    """
    GPT 응답을 청크 단위로 yield하는 스트리밍 버전.
    캐시 히트 시 저장된 텍스트를 즉시 반환.
    최종적으로 "[FINAL]<후처리된 전체 텍스트>" yield 후 종료.
    """
    if not OPENAI_API_KEY:
        yield "[ERROR]OPENAI_API_KEY가 설정되지 않았습니다."
        return

    pledge_key = (pledge or "").strip()
    if _is_low_quality_pledge_input(pledge_key):
        result = _build_low_quality_check_text(pledge_key)
        _set_cached_result(pledge_key, result)
        yield "[FINAL]" + result
        return

    cached = _get_cached_result(pledge_key)
    if cached is not None:
        logger.info("[stream] 캐시 히트")
        yield "[CACHED]"
        yield "[FINAL]" + cached
        return

    user_meta = None
    if user_id is not None:
        from backend.auth import get_user
        user = get_user(user_id)
        user_meta = build_pledge_meta_from_user(user)

    candidates_context = _load_candidates_context()
    enriched_context = _build_enriched_context(pledge_key, user_meta)

    use_vector_store = bool(vector_store_id)
    use_faiss_search = (
        not use_vector_store
        and indexes
        and all(indexes.get(k) for k in ("platform", "pledge"))
        and indexes["platform"].size() > 0
        and indexes["pledge"].size() > 0
    )

    accumulated: list[str] = []

    if use_vector_store:
        logger.info("[stream] Vector Store 기반 스트리밍...")
        from backend.openai_vector_store import run_check
        gen = run_check(
            vector_store_id,
            pledge_key,
            "",
            winners2022_vector_store_id or "",
            max_results=10,
            user_meta=user_meta,
            candidates_context=candidates_context,
            messages_context=enriched_context.get("messages_context", ""),
            assembly_context=enriched_context.get("assembly_context", ""),
            public_data_context=enriched_context.get("public_data_context", ""),
            research_context=enriched_context.get("research_context", ""),
            _stream=True,
        )
        for chunk in gen:
            accumulated.append(chunk)
            yield chunk

    elif use_faiss_search:
        logger.info("[stream] FAISS 기반 스트리밍...")
        from backend.report import search_all_indexes
        from openai import OpenAI
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
            yield "[ERROR]검색 결과가 없습니다. 인덱스를 확인하세요."
            return

        winners2022_ctx = _build_winners2022_context_for_non_vs(
            pledge_key, winners2022_vector_store_id, user_meta,
        )
        system = load_system_prompt()
        user_msg = build_user_message(
            platform_context, pledges_context, pledge_key, winners2022_ctx,
            candidates_pledges_context=candidates_context,
            messages_context=enriched_context.get("messages_context", ""),
            assembly_context=enriched_context.get("assembly_context", ""),
            public_data_context=enriched_context.get("public_data_context", ""),
            research_context=enriched_context.get("research_context", ""),
            user_meta=user_meta,
        )
        client = OpenAI(api_key=OPENAI_API_KEY)
        stream = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
            stream=True,
        )
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                text = chunk.choices[0].delta.content
                accumulated.append(text)
                yield text

    else:
        logger.info("[stream] PDF 컨텍스트 기반 스트리밍...")
        from backend.pdf_loader import load_platform_context, load_pledges_context
        from openai import OpenAI
        platform_context = load_platform_context()
        pledges_context = load_pledges_context()

        if not platform_context.strip() and not pledges_context.strip():
            yield "[ERROR]기준 문서가 없습니다. data/pdf/ 폴더에 PDF를 넣어 주세요."
            return

        winners2022_ctx = _build_winners2022_context_for_non_vs(
            pledge_key, winners2022_vector_store_id, user_meta,
        )
        system = load_system_prompt()
        user_msg = build_user_message(
            platform_context, pledges_context, pledge_key, winners2022_ctx,
            candidates_pledges_context=candidates_context,
            messages_context=enriched_context.get("messages_context", ""),
            assembly_context=enriched_context.get("assembly_context", ""),
            public_data_context=enriched_context.get("public_data_context", ""),
            research_context=enriched_context.get("research_context", ""),
            user_meta=user_meta,
        )
        client = OpenAI(api_key=OPENAI_API_KEY)
        stream = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
            stream=True,
        )
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                text = chunk.choices[0].delta.content
                accumulated.append(text)
                yield text

    full_text = "".join(accumulated).strip()

    # JSON 형식 방어
    head = full_text[:4000]
    if ("fit_score" in head and "rubric" in head) or ('"breakdown"' in head and "fit_score" in head):
        logger.warning("[stream] GPT가 JSON 형식 반환 → 안내 문구로 대체")
        full_text = "점검 결과가 요청한 텍스트 형식으로 생성되지 않았습니다. 잠시 후 다시 시도해 주세요."

    processed = apply_check_postprocessing(full_text, pledge_key)
    _set_cached_result(pledge_key, processed)
    logger.info("[stream] 완료, %d자", len(processed))
    yield "[FINAL]" + processed
