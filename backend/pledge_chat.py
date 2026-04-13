"""
정책 방향 제안 챗봇 — 대화형 정책 보조.

출마자와 AI가 대화하면서 지역 이슈, 정책 방향, 우선순위를 함께 정리하고,
필요할 때 초안 보조에 참고할 수 있는 구조화된 결과를 만든다.
기존 policy_drafter의 RAG 파이프라인을 재사용.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Optional

from backend.config import PROMPTS_DIR, ROOT_DIR
from backend.database import get_connection
from backend.auth import get_user

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
CHAT_MODEL = os.getenv("CHAT_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-4o"
REASONING_MODEL = os.getenv("OPENAI_MODEL") or "gpt-5.4"  # 추론이 필요한 턴에 사용

MAX_HISTORY_MESSAGES = 40  # 대화 히스토리 최대 메시지 수 (시스템 제외)

def _needs_reasoning(user_message: str) -> bool:
    """mini 모델로 1차 판단: 이 메시지가 깊은 추론을 요구하는지."""
    text = (user_message or "").strip()
    if not text or len(text) < 5:
        return False

    # 빠른 키워드 사전 필터 — 명백한 일반 대화는 API 호출 안 함
    _QUICK_SKIP = {"네", "아니", "좋아", "ㅇㅇ", "ㄴㄴ", "그래", "응", "1", "2", "3", "고마워", "감사"}
    if text in _QUICK_SKIP or len(text) <= 3:
        return False

    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model=CHAT_MODEL,  # mini로 판단
            messages=[
                {"role": "system", "content": (
                    "너는 메시지 분류기다. 사용자 메시지가 다음 중 하나에 해당하면 YES, 아니면 NO만 답하라.\n"
                    "YES 기준: 타지역 사례 요청, 전략 비교/분석, 왜 유리한지 논리 설명, 경쟁 후보 차별화, "
                    "트렌드/흐름 해석, 복잡한 정책 설계, 프레임 제안, 장단점 비교\n"
                    "NO 기준: 단순 선택(1번/2번), 감사/인사, 특정 분야 더 보기, 정리 요청, 의회 안건 조회, "
                    "간단한 질문, 방향 확인"
                )},
                {"role": "user", "content": text},
            ],
            max_completion_tokens=3,
            timeout=5,
        )
        answer = (resp.choices[0].message.content or "").strip().upper()
        result = answer.startswith("YES")
        logger.info("[pledge_chat] reasoning_check: %s → %s", text[:40], result)
        return result
    except Exception as e:
        logger.warning("[pledge_chat] reasoning check failed: %s", e)
        return False

# 모듈 레벨 캐시 — 서버 재시작 전까지 유지
_DISTRICT_DONG_MAP_CACHE: Optional[dict] = None
_PLATFORM_CACHE: Optional[str] = None
_PLEDGES_CACHE: Optional[str] = None


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------
def _load_chat_system_prompt() -> str:
    path = PROMPTS_DIR / "공약_챗봇_시스템.txt"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return "개혁신당 공약 생성 코치 역할이다. 출마자와 대화하면서 지역 이슈, 정책 방향, 우선순위와 근거를 함께 정리한다. 바로 완성 공약을 단정적으로 써주기보다 선택지와 방향성을 제안한다."


def _load_district_dong_map() -> dict:
    """선거구-동 매핑 JSON 로드 (모듈 레벨 캐시)."""
    global _DISTRICT_DONG_MAP_CACHE
    if _DISTRICT_DONG_MAP_CACHE is not None:
        return _DISTRICT_DONG_MAP_CACHE
    import json as _json
    path = ROOT_DIR / "data" / "district_dong_map.json"
    if not path.exists():
        _DISTRICT_DONG_MAP_CACHE = {}
        return _DISTRICT_DONG_MAP_CACHE
    try:
        with open(path, encoding="utf-8") as f:
            _DISTRICT_DONG_MAP_CACHE = _json.load(f)
    except Exception:
        _DISTRICT_DONG_MAP_CACHE = {}
    return _DISTRICT_DONG_MAP_CACHE


def _fetch_platform_pledges() -> tuple[str, str]:
    """정강정책 + 우리당 공약을 캐시에서 반환. 최초 호출 시 RAG 검색."""
    global _PLATFORM_CACHE, _PLEDGES_CACHE
    if _PLATFORM_CACHE is not None:
        return _PLATFORM_CACHE, _PLEDGES_CACHE or ""
    try:
        from backend.policy_drafter import _get_rag_contexts
        rag = _get_rag_contexts("정강정책 복지 안전 교육 경제 환경 주거 교통 지방분권 행정혁신 공약")
        _PLATFORM_CACHE = rag.get("platform") or ""
        _PLEDGES_CACHE = rag.get("pledges") or ""
    except Exception as e:
        logger.warning("[pledge_chat] platform pre-load failed: %s", e)
        _PLATFORM_CACHE = ""
        _PLEDGES_CACHE = ""
    return _PLATFORM_CACHE, _PLEDGES_CACHE or ""


def _get_district_dongs(election_position: str, region_name: str, district_name: str) -> list[str]:
    """선거구에 포함된 행정동 목록 반환."""
    # metro_mayor: 광역시 전체 구 목록 반환 (동 매핑 없으므로 구 단위 하드코딩)
    _METRO_DISTRICTS = {
        "광주광역시": ["동구", "서구", "남구", "북구", "광산구"],
        "대구광역시": ["중구", "동구", "서구", "남구", "북구", "수성구", "달서구", "달성군"],
        "부산광역시": ["중구", "서구", "동구", "영도구", "부산진구", "동래구", "남구", "북구", "해운대구", "사하구", "금정구", "강서구", "연제구", "수영구", "사상구", "기장군"],
        "인천광역시": ["중구", "동구", "미추홀구", "연수구", "남동구", "부평구", "계양구", "서구", "강화군", "옹진군"],
        "대전광역시": ["동구", "중구", "서구", "유성구", "대덕구"],
        "울산광역시": ["중구", "남구", "동구", "북구", "울주군"],
        "세종특별자치시": ["조치원읍", "연서면", "연동면", "부강면", "금남면", "장군면", "연기면", "전의면", "소정면", "한솔동", "새롬동", "나성동", "가람동", "아름동", "종촌동", "고운동", "소담동", "보람동", "대평동", "반곡동"],
        "서울특별시": ["종로구", "중구", "용산구", "성동구", "광진구", "동대문구", "중랑구", "성북구", "강북구", "도봉구", "노원구", "은평구", "서대문구", "마포구", "양천구", "강서구", "구로구", "금천구", "영등포구", "동작구", "관악구", "서초구", "강남구", "송파구", "강동구"],
    }
    if "metro" in (election_position or ""):
        # region_name에서 광역시명 추출
        parts = (region_name or "").strip().split()
        sido = parts[0] if parts else ""
        # _PROVINCE_MAP 정규화 없이 직접 매핑
        for metro_key, dists in _METRO_DISTRICTS.items():
            if metro_key.startswith(sido) or sido.startswith(metro_key.replace("광역시", "").replace("특별시", "").replace("특별자치시", "")):
                return dists
        return []

    mapping = _load_district_dong_map()
    # election_position → 매핑 키
    if "local" in (election_position or ""):
        layer = mapping.get("local_council", {})
    elif "regional" in (election_position or ""):
        layer = mapping.get("regional_council", {})
    else:
        return []

    # region_name: "서울특별시" 또는 "서울특별시 강북구"
    parts = (region_name or "").strip().split()
    sido = parts[0] if parts else ""
    gu_from_region = parts[-1] if len(parts) >= 2 else ""

    # district_name: "강남구 가선거구" → 구 + 선거구명
    dn_parts = (district_name or "").strip().split()
    gu_from_district = dn_parts[0] if len(dn_parts) >= 2 else ""
    sgg = dn_parts[-1] if dn_parts else ""

    # 구: district_name에서 우선, 없으면 region_name에서
    gu = gu_from_district if gu_from_district and gu_from_district != sgg else gu_from_region

    if "local" in (election_position or ""):
        return layer.get(sido, {}).get(gu, {}).get(sgg, [])
    else:
        # 광역의원: 선거구명이 "강북구제1선거구" 형태
        full_sgg = gu + sgg if gu and sgg else sgg
        return layer.get(sido, {}).get(full_sgg, [])


def _build_user_context_text(user_id: int) -> str:
    user = get_user(user_id) or {}
    parts = []
    if user.get("name"):
        parts.append(f"이름: {user['name']}")
    if user.get("election_position"):
        parts.append(f"출마 직책: {user['election_position']}")
    if user.get("region_name"):
        parts.append(f"주요 지역: {user['region_name']}")
    if user.get("district_name"):
        parts.append(f"선거구: {user['district_name']}")

    # 선거구 포함 동 매핑
    dongs = _get_district_dongs(
        user.get("election_position", ""),
        user.get("region_name", ""),
        user.get("district_name", ""),
    )
    if dongs:
        parts.append(f"선거구 관할 행정동: {', '.join(dongs)}")

    if not parts:
        return ""
    guide = (
        "위 정보를 대화 시작부터 반영하라. "
        "선거구 관할 행정동이 있으면 그 동 이름을 구체적으로 언급하라. "
        "선거구 정보가 이미 있으면 다시 묻지 말고 그 기준으로 답하라. "
        "기초의원이면 해당 동 단위 생활 이슈를, 광역이면 시·도 단위 이슈를 먼저 다루라."
    )
    return "\n\n[후보자 기본 정보]\n" + "\n".join(parts) + "\n" + guide


# ---------------------------------------------------------------------------
# Session CRUD
# ---------------------------------------------------------------------------
def create_session(user_id: int, topic: str, output_format: str = "정책") -> dict:
    """새 챗봇 세션 생성. topic이 있으면 즉시 RAG 수행."""
    session_id = uuid.uuid4().hex[:16]

    # topic이 있으면 즉시 RAG (의회, 공공데이터 포함)
    rag = _fetch_rag_context(topic, user_id=user_id) if topic and len(topic.strip()) > 2 else {}
    system_msg = _build_system_message(rag, user_id=user_id)

    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO pledge_chat_sessions
               (id, user_id, topic, output_format, rag_context)
               VALUES (?, ?, ?, ?, ?)""",
            (session_id, user_id, topic, output_format, json.dumps({k: v[:100] if isinstance(v, str) else v for k, v in rag.items()}) if rag else "{}"),
        )
        conn.execute(
            "INSERT INTO pledge_chat_messages (session_id, role, content) VALUES (?, 'system', ?)",
            (session_id, system_msg),
        )
        conn.commit()
    finally:
        conn.close()

    return {"session_id": session_id, "topic": topic, "output_format": output_format}


def get_session(session_id: str, user_id: int) -> Optional[dict]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM pledge_chat_sessions WHERE id = ? AND user_id = ?",
            (session_id, user_id),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_sessions(user_id: int, limit: int = 20) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT id, topic, output_format, status, created_at, updated_at
               FROM pledge_chat_sessions WHERE user_id = ?
               ORDER BY updated_at DESC LIMIT ?""",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_messages(session_id: str) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT role, content, created_at FROM pledge_chat_messages WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _save_message(session_id: str, role: str, content: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO pledge_chat_messages (session_id, role, content) VALUES (?, ?, ?)",
            (session_id, role, content),
        )
        conn.execute(
            "UPDATE pledge_chat_sessions SET updated_at = datetime('now') WHERE id = ?",
            (session_id,),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# RAG context (reuse policy_drafter)
# ---------------------------------------------------------------------------
def _extract_region_from_topic(topic: str) -> tuple[str | None, str | None]:
    """topic 텍스트에서 시도/시군구 지역명 추출. (region, district) 반환."""
    from backend.public_data_api import _PROVINCE_MAP, normalize_region
    import re

    # 시군구 패턴: "해남군", "강북구", "수원시" 등
    district_match = re.search(r"[가-힣]{2,5}[시군구]", topic)
    # 시도 패턴: "전라남도", "경기도", "서울" 등
    province_match = re.search(
        r"(서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주)"
        r"(?:특별시|광역시|특별자치시|특별자치도|도)?", topic
    )

    district = district_match.group(0) if district_match else None
    province_raw = province_match.group(0) if province_match else None
    province = _PROVINCE_MAP.get(province_raw, province_raw) if province_raw else None

    return province, district


def _fetch_rag_context(topic: str, user_id: int | None = None) -> dict:
    """policy_drafter의 _get_rag_contexts + research_topic 재사용."""
    try:
        from backend.policy_drafter import _get_rag_contexts
        rag = _get_rag_contexts(topic)
    except Exception as e:
        logger.warning("pledge_chat RAG failed: %s", e)
        rag = {"platform": "", "pledges": "", "winners2022": "", "candidates": "", "messages": ""}

    try:
        from backend.research_assistant import research_topic
        user = get_user(user_id) if user_id else {}

        # 지역: 프로필 우선, 없으면 topic 텍스트에서 추출
        region = (user or {}).get("region_name") or None
        district_name = (user or {}).get("district_name") or None
        if not region and not district_name:
            region, district_name = _extract_region_from_topic(topic)

        research = research_topic(
            topic=topic,
            region=region,
            district_name=district_name,
            election_type=(user or {}).get("election_position") or None,
            years=2,
        )
        assembly_info = research.get("assembly") or {}
        rag["assembly"] = assembly_info.get("context_text", "") if isinstance(assembly_info, dict) else ""
        rag["research"] = research.get("briefing_text", "") or ""
        public_data_info = research.get("public_data") or {}
        rag["public_data"] = public_data_info.get("context_text", "") if isinstance(public_data_info, dict) else ""
    except Exception as e:
        import traceback as _tb
        logger.warning("pledge_chat research failed: %s\n%s", e, _tb.format_exc())
        rag["assembly"] = ""
        rag["research"] = ""
        rag["public_data"] = ""

    return rag


# 2자 이하는 RAG 스킵 (단, 정책 관련 2자 키워드는 예외)
_POLICY_TRIGGER_SET = {"교통", "복지", "상권", "주차", "노인", "청년", "환경", "교육", "안전", "주거", "돌봄", "의료"}


def _maybe_inject_rag(session_id: str, user_message: str) -> None:
    """세션에 topic RAG가 없으면 user_message를 주제로 RAG 검색 후 시스템 메시지 업데이트."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT rag_context, user_id FROM pledge_chat_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not row:
            return
        existing = json.loads(row["rag_context"] or "{}")
        if existing.get("assembly") is not None or existing.get("public_data") is not None:
            return  # 이미 topic RAG 완료

        # 2자 이하 + 정책 키워드 아닌 경우만 스킵 (숫자, 단순 선택 등)
        short = user_message.strip()
        if len(short) <= 2 and short not in _POLICY_TRIGGER_SET:
            return
    finally:
        conn.close()

    # RAG 수행
    user_id = row["user_id"] if row else None
    logger.info("[pledge_chat] lazy RAG for session=%s topic=%s", session_id, user_message[:50])
    rag = _fetch_rag_context(user_message, user_id=user_id)

    # 시스템 메시지 업데이트 (정강정책+후보자정보 포함)
    new_system = _build_system_message(rag, user_id=user_id)
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE pledge_chat_messages SET content = ? WHERE session_id = ? AND role = 'system'",
            (new_system, session_id),
        )
        conn.execute(
            "UPDATE pledge_chat_sessions SET rag_context = ?, topic = ? WHERE id = ?",
            (json.dumps(rag, ensure_ascii=False), user_message[:200], session_id),
        )
        conn.commit()
    finally:
        conn.close()


def _build_system_message(rag: dict, user_id: int | None = None) -> str:
    """챗봇 시스템 프롬프트 + RAG 컨텍스트를 합산.

    정강정책 + 우리당 공약은 topic 무관 항상 포함 (캐시 사용).
    user_id가 주어지면 후보자 기본 정보도 포함.
    의회·공공데이터는 포함하되 태그 내 지시로 사용 시점을 제어.
    """
    base_prompt = _load_chat_system_prompt()

    # 정강정책 + 우리당 공약 항상 포함 (캐시 우선, rag 내용으로 덮어씀)
    cached_platform, cached_pledges = _fetch_platform_pledges()
    platform_text = rag.get("platform") or cached_platform
    pledges_text = rag.get("pledges") or cached_pledges

    # 참고 자료 섹션
    context_parts = []
    if platform_text:
        context_parts.append(f"[참고: 정강정책]\n{platform_text[:8000]}")
    if pledges_text:
        context_parts.append(f"[참고: 우리당 공약]\n{pledges_text[:6000]}")
    if rag.get("winners2022"):
        context_parts.append(f"[참고: 2022 당선인 공약]\n{rag['winners2022'][:3000]}")
    if rag.get("messages"):
        context_parts.append(f"[참고: 공식 논평·보도자료]\n{rag['messages'][:3000]}")
    if rag.get("assembly"):
        context_parts.append(f"[참고: 지방의회 논의 — 사용자가 의회·조례·안건을 직접 물었을 때만 인용하라.]\n{rag['assembly'][:3000]}")
    if rag.get("research"):
        context_parts.append(f"[참고: 연구 자료]\n{rag['research'][:3000]}")
    if rag.get("public_data"):
        context_parts.append(f"[참고: 공공데이터 — 관련 수치를 답변에 구체적으로 인용하라. 예: '해남군 공원 58개소(873,893㎡)가 있지만 면 지역 접근성은...' 식으로 숫자를 직접 언급해야 신뢰도가 높아진다. 무관한 수치는 나열하지 말 것.]\n{rag['public_data'][:4000]}")

    if context_parts:
        context_section = "\n\n---\n아래는 대화 중 참고할 자료이다. 대화에서 자연스럽게 활용하되 내부 태그를 노출하지 마라.\n\n" + "\n\n".join(context_parts)
    else:
        context_section = ""

    user_context = _build_user_context_text(user_id) if user_id else ""
    return base_prompt + context_section + user_context


# ---------------------------------------------------------------------------
# Chat (streaming)
# ---------------------------------------------------------------------------
def chat_stream(session_id: str, user_message: str):
    """사용자 메시지 저장 → AI 응답 스트리밍 제너레이터 반환."""
    _save_message(session_id, "user", user_message)

    # RAG가 아직 안 되어있으면 이 메시지를 주제로 RAG 수행
    _maybe_inject_rag(session_id, user_message)

    # 히스토리 로드
    turn_mode_prompt = _build_turn_mode_system_prompt(user_message)
    messages = _load_openai_messages(session_id, turn_mode_prompt=turn_mode_prompt)

    from openai import OpenAI, APIError, APITimeoutError
    client = OpenAI(api_key=OPENAI_API_KEY)

    # 추론 필요 여부에 따라 모델 선택
    use_reasoning = _needs_reasoning(user_message)
    model = REASONING_MODEL if use_reasoning else CHAT_MODEL
    logger.info("[pledge_chat] model=%s reasoning=%s msg=%s", model, use_reasoning, user_message[:50])

    try:
        stream = client.chat.completions.create(
            model=model,
            messages=messages,
            max_completion_tokens=4000,
            timeout=120 if use_reasoning else 90,
            stream=True,
            stream_options={"include_usage": True},
        )
    except APITimeoutError:
        yield "[ERROR]응답 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요."
        return
    except APIError as e:
        logger.error("[pledge_chat] OpenAI API error in chat_stream: %s", e)
        yield "[ERROR]AI 응답 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
        return

    full = []
    usage_in = 0
    usage_out = 0
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            text = chunk.choices[0].delta.content
            full.append(text)
            yield text
        if hasattr(chunk, "usage") and chunk.usage:
            usage_in = chunk.usage.prompt_tokens or 0
            usage_out = chunk.usage.completion_tokens or 0

    # AI 응답 저장
    assistant_text = "".join(full)
    if assistant_text:
        _save_message(session_id, "assistant", assistant_text)

    # 실제 토큰 사용량 전달 (tools_routes에서 파싱)
    yield f"[USAGE]{json.dumps({'model': model, 'input_tokens': usage_in, 'output_tokens': usage_out}, ensure_ascii=False)}"


def first_message_stream(session_id: str, topic: str):
    """세션 시작 후 첫 AI 메시지 스트리밍. 유저 메시지를 그대로 전달."""
    return chat_stream(session_id, topic)


# ---------------------------------------------------------------------------
# Finalize — 대화 내용을 바탕으로 방향 정리 / 초안 보조 결과 생성
# ---------------------------------------------------------------------------
def finalize_stream(session_id: str):
    """대화 내용을 기반으로 방향 정리와 초안 보조에 쓸 결과를 스트리밍 생성."""
    conn = get_connection()
    try:
        session = conn.execute(
            "SELECT * FROM pledge_chat_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not session:
            yield "[ERROR]세션을 찾을 수 없습니다."
            return
    finally:
        conn.close()

    session = dict(session)
    output_format = session.get("output_format", "정책")
    rag = json.loads(session.get("rag_context", "{}"))

    # 대화 요약 구성
    conversation_summary = _build_conversation_summary(session_id)

    system = _load_chat_system_prompt()

    # 정강정책 + 우리당 공약: rag에 없으면 캐시 사용 (항상 포함)
    cached_platform, cached_pledges = _fetch_platform_pledges()
    context_blocks = []
    platform_text = rag.get("platform") or cached_platform
    pledges_text = rag.get("pledges") or cached_pledges
    if platform_text:
        context_blocks.append(f"[정강정책]\n{platform_text[:6000]}")
    if pledges_text:
        context_blocks.append(f"[우리당 공약]\n{pledges_text[:5000]}")
    if rag.get("messages"):
        context_blocks.append(f"[논평·보도자료]\n{rag['messages'][:2000]}")
    if rag.get("assembly"):
        context_blocks.append(f"[지방의회 논의]\n{rag['assembly'][:2500]}")
    if rag.get("research"):
        context_blocks.append(f"[연구 자료]\n{rag['research'][:2000]}")
    if rag.get("winners2022"):
        context_blocks.append(f"[2022 당선인 공약]\n{rag['winners2022'][:1800]}")

    user_msg = conversation_summary + "\n\n다음 형식으로 결과를 정리하라.\n1. 핵심 문제 정의\n2. 자료 기반 관찰\n3. 정책 방향 제안\n4. 가능한 옵션 2~3개\n5. 추가 검토 쟁점\n6. 인근 지역 2022년 당선인 공약과의 연결점 (2022 당선인 공약 자료가 있을 때만)\n7. 우리당 공약 방향과의 연결점 (우리당 공약 자료가 있을 때만)\n\n완성 공약문처럼 쓰지 말고, 사람이 다음 단계에서 판단하고 다듬을 수 있는 정책 방향 제안서처럼 작성하라. 각 항목은 짧은 문단 또는 불릿으로 명확히 정리하라. 6·7번 항목은 해당 자료가 없으면 생략하라."
    if context_blocks:
        user_msg += "\n\n참고 자료:\n\n" + "\n\n".join(context_blocks)

    from openai import OpenAI, APIError, APITimeoutError
    client = OpenAI(api_key=OPENAI_API_KEY)

    try:
        stream = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
            max_completion_tokens=4096,
            timeout=180,
            stream=True,
            stream_options={"include_usage": True},
        )
    except APITimeoutError:
        yield "[ERROR]응답 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요."
        return
    except APIError as e:
        logger.error("[pledge_chat] OpenAI API error in finalize_stream: %s", e)
        yield "[ERROR]AI 응답 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
        return

    full = []
    usage_in = 0
    usage_out = 0
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            text = chunk.choices[0].delta.content
            full.append(text)
            yield text
        if hasattr(chunk, "usage") and chunk.usage:
            usage_in = chunk.usage.prompt_tokens or 0
            usage_out = chunk.usage.completion_tokens or 0

    # 최종 공약문 저장
    final_text = "".join(full)
    if final_text:
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE pledge_chat_sessions SET status = 'finalized', final_draft = ?, updated_at = datetime('now') WHERE id = ?",
                (final_text, session_id),
            )
            conn.commit()
        finally:
            conn.close()

    yield f"[USAGE]{json.dumps({'model': CHAT_MODEL, 'input_tokens': usage_in, 'output_tokens': usage_out}, ensure_ascii=False)}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_CHAT_BRIEFING_KEYWORDS = (
    "조례", "의회", "회의록", "본회의", "상임위", "위원회", "안건", "의안",
    "가결", "부결", "발의", "심사", "의결", "구정질문", "시정질문",
    "행정사무감사", "속기록", "조문", "개정안", "폐지", "규칙",
)


def _build_chat_style_system_prompt() -> str:
    return (
        "이번 턴은 티키타카가 되는 코치 모드로 답하라.\n"
        "한 번에 다 설명하지 말고, 먼저 핵심 쟁점이나 방향 2~3개만 짧게 제시하라.\n"
        "기본 답변은 8~10문장 안팎 또는 짧은 항목 3개 이내로 제한하라.\n"
        "각 항목은 2~3문장 안쪽으로 쓰고, 왜 이 지역에서 중요한지 짧게 설명하라.\n"
        "자료가 있으면 첫 문장이나 첫 항목 안에 지역명, 의회 논의, 조례, 안건명, 구체 지점 중 최소 1개를 넣어라.\n"
        "구/동 이름, 정책·사업명은 GPT 일반 지식으로 자유롭게 말할 수 있다. 타지역 사례를 물으면 실제 구/동과 정책명을 구체적으로 들어라.\n"
        "단, 특정 학교명·아파트명·버스정류장명·교차로명은 자료에 있을 때만 말하라.\n"
        "아직 사용자가 고르지 않은 갈래는 깊게 풀지 마라.\n"
        "답변 마지막에는 사용자가 다음 턴에서 고를 수 있는 질문 1개만 남겨라.\n"
        "사용자가 특정 지점을 더 물으면 그 부분만 깊게 답하고, 다른 갈래를 다시 길게 벌리지 마라."
    )


def _build_turn_mode_system_prompt(user_message: str) -> str:
    text = (user_message or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if not any(keyword in text or keyword in lowered for keyword in _CHAT_BRIEFING_KEYWORDS):
        return ""
    return (
        "이번 답변은 의회 자료 브리핑 모드이다.\n"
        "반드시 시스템 프롬프트 rule 10의 2단계 순서를 따르라.\n"
        "[1단계] 참고 자료에 있는 안건 전체를 분야별로 짧게 분류하여 한눈에 보여줘라.\n"
        "예: '행정·의회운영 2건(의원 공무국외출장 조례 전부개정조례안 등), 생활환경·웰니스 1건(웰니스 특화도시 조성 특별위원회 보고서)...'\n"
        "각 분야는 1줄, 안건명만 나열하라. 분석하지 마라.\n"
        "분류 후 '어느 분야를 먼저 볼까요?'로 끝내라.\n"
        "[2단계는 사용자가 분야를 고른 뒤에만 진행한다.]\n"
        "자료가 적어도(3건 이하) 분류 형식을 유지하라. 건수가 적으면 적다고 말하면 된다."
    )


def _load_openai_messages(session_id: str, turn_mode_prompt: Optional[str] = None) -> list[dict]:
    """DB에서 메시지 히스토리를 OpenAI 형식으로 로드."""
    all_msgs = get_messages(session_id)

    # 시스템 메시지는 항상 포함
    openai_msgs = []
    history = []
    for m in all_msgs:
        if m["role"] == "system":
            openai_msgs.append({"role": "system", "content": m["content"]})
        else:
            history.append({"role": m["role"], "content": m["content"]})

    # 최근 N개만
    if len(history) > MAX_HISTORY_MESSAGES:
        history = history[-MAX_HISTORY_MESSAGES:]

    openai_msgs.append({"role": "system", "content": _build_chat_style_system_prompt()})
    if turn_mode_prompt:
        openai_msgs.append({"role": "system", "content": turn_mode_prompt})
    return openai_msgs + history


def _build_conversation_summary(session_id: str) -> str:
    """대화 내용을 정책 주제 텍스트로 요약."""
    msgs = get_messages(session_id)

    parts = []
    for m in msgs:
        if m["role"] == "system":
            continue
        role_label = "출마자" if m["role"] == "user" else "AI코치"
        parts.append(f"{role_label}: {m['content']}")

    conversation_text = "\n".join(parts)

    # 대화 내용을 방향 정리용 텍스트로 변환
    summary = f"""아래는 출마자와 공약 생성 코치의 정책 방향 정리 대화 내용이다. 이 대화에서 논의된 내용을 바탕으로 지역 이슈, 정책 방향, 우선순위, 참고 근거를 구조화하라.

--- 대화 내용 ---
{conversation_text[:6000]}
--- 대화 끝 ---

위 대화에서 확인된 지역 맥락, 대상, 수단, 기대효과, 추가 검토가 필요한 쟁점을 먼저 정리하고, 필요하면 사람이 다듬을 수 있는 초안 보조 형태로 제시하라."""

    return summary
