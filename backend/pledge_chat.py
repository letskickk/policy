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

from backend.config import PROMPTS_DIR
from backend.database import get_connection
from backend.auth import get_user

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
CHAT_MODEL = os.getenv("CHAT_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-4o"

MAX_HISTORY_MESSAGES = 40  # 대화 히스토리 최대 메시지 수 (시스템 제외)


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------
def _load_chat_system_prompt() -> str:
    path = PROMPTS_DIR / "공약_챗봇_시스템.txt"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return "개혁신당 정책 기획 코치 역할이다. 출마자와 대화하면서 지역 이슈, 정책 방향, 우선순위와 근거를 함께 정리한다. 바로 완성 공약을 단정적으로 써주기보다 선택지와 방향성을 제안한다."


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
    if not parts:
        return ""
    return "\n\n[후보자 기본 정보]\n" + "\n".join(parts) + "\n위 정보가 있으면 대화 첫 단계에서 지역 맥락과 출마 단위를 먼저 반영하라. 정보가 없는 부분은 추측하지 말고 필요한 경우 질문하라."


# ---------------------------------------------------------------------------
# Session CRUD
# ---------------------------------------------------------------------------
def create_session(user_id: int, topic: str, output_format: str = "정책") -> dict:
    """새 챗봇 세션 생성. RAG는 주제가 구체화된 후 lazy로 수행."""
    session_id = uuid.uuid4().hex[:16]

    # 시작 시에는 RAG 없이 기본 시스템 프롬프트 + 후보자 기본 정보만
    system_msg = _load_chat_system_prompt() + _build_user_context_text(user_id)

    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO pledge_chat_sessions
               (id, user_id, topic, output_format, rag_context)
               VALUES (?, ?, ?, ?, ?)""",
            (session_id, user_id, topic, output_format, "{}"),
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
        research = research_topic(
            topic=topic,
            region=(user or {}).get("region_name") or None,
            district_name=(user or {}).get("district_name") or None,
            election_type=(user or {}).get("election_position") or None,
            years=2,
        )
        rag["assembly"] = research.get("assembly", {}).get("context_text", "")
        rag["research"] = research.get("briefing_text", "")
    except Exception as e:
        logger.warning("pledge_chat research failed: %s", e)
        rag["assembly"] = ""
        rag["research"] = ""

    return rag


def _maybe_inject_rag(session_id: str, user_message: str) -> None:
    """세션에 RAG 컨텍스트가 없으면 user_message를 주제로 RAG 검색 후 시스템 메시지 업데이트."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT rag_context, user_id FROM pledge_chat_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not row:
            return
        existing = json.loads(row["rag_context"] or "{}")
        if existing.get("platform") or existing.get("pledges"):
            return  # 이미 RAG 완료

        # 유형 선택 메시지("1", "정책" 등)면 RAG 스킵
        short = user_message.strip()
        if len(short) <= 5 and (short.isdigit() or short in ("정책", "지역공약", "논평", "메시지")):
            return

        # 이 메시지가 주제가 될 만큼 구체적인지 확인 (최소 4자)
        if len(short) < 4:
            return
    finally:
        conn.close()

    # RAG 수행
    logger.info("[pledge_chat] lazy RAG for session=%s topic=%s", session_id, user_message[:50])
    rag = _fetch_rag_context(user_message, user_id=row["user_id"] if row else None)

    # 시스템 메시지 업데이트
    new_system = _build_system_message(rag)
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


def _build_system_message(rag: dict) -> str:
    """챗봇 시스템 프롬프트 + RAG 컨텍스트를 합산."""
    base_prompt = _load_chat_system_prompt()

    # 참고 자료 섹션
    context_parts = []
    if rag.get("platform"):
        context_parts.append(f"[참고: 정강정책]\n{rag['platform'][:4000]}")
    if rag.get("pledges"):
        context_parts.append(f"[참고: 우리당 공약]\n{rag['pledges'][:4000]}")
    if rag.get("winners2022"):
        context_parts.append(f"[참고: 2022 당선인 공약]\n{rag['winners2022'][:3000]}")
    if rag.get("messages"):
        context_parts.append(f"[참고: 공식 논평·보도자료]\n{rag['messages'][:3000]}")
    if rag.get("assembly"):
        context_parts.append(f"[참고: 지방의회 논의]\n{rag['assembly'][:3000]}")
    if rag.get("research"):
        context_parts.append(f"[참고: 연구 자료]\n{rag['research'][:3000]}")

    if context_parts:
        context_section = "\n\n---\n아래는 대화 중 참고할 자료이다. 대화에서 자연스럽게 활용하되 내부 태그를 노출하지 마라.\n\n" + "\n\n".join(context_parts)
    else:
        context_section = ""

    return base_prompt + context_section


# ---------------------------------------------------------------------------
# Chat (streaming)
# ---------------------------------------------------------------------------
def chat_stream(session_id: str, user_message: str):
    """사용자 메시지 저장 → AI 응답 스트리밍 제너레이터 반환."""
    _save_message(session_id, "user", user_message)

    # RAG가 아직 안 되어있으면 이 메시지를 주제로 RAG 수행
    _maybe_inject_rag(session_id, user_message)

    # 히스토리 로드
    messages = _load_openai_messages(session_id)

    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)

    stream = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=messages,
        max_completion_tokens=1024,
        timeout=60,
        stream=True,
    )

    full = []
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            text = chunk.choices[0].delta.content
            full.append(text)
            yield text

    # AI 응답 저장
    assistant_text = "".join(full)
    if assistant_text:
        _save_message(session_id, "assistant", assistant_text)


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

    context_blocks = []
    if rag.get("platform"):
        context_blocks.append(f"[정강정책]\n{rag['platform'][:2500]}")
    if rag.get("pledges"):
        context_blocks.append(f"[우리당 공약]\n{rag['pledges'][:2500]}")
    if rag.get("messages"):
        context_blocks.append(f"[논평·보도자료]\n{rag['messages'][:2000]}")
    if rag.get("assembly"):
        context_blocks.append(f"[지방의회 논의]\n{rag['assembly'][:2500]}")
    if rag.get("research"):
        context_blocks.append(f"[연구 자료]\n{rag['research'][:2000]}")
    if rag.get("winners2022"):
        context_blocks.append(f"[2022 당선인 공약]\n{rag['winners2022'][:1800]}")

    user_msg = conversation_summary + "\n\n다음 형식으로 결과를 정리하라.\n1. 핵심 문제 정의\n2. 자료 기반 관찰\n3. 정책 방향 제안\n4. 가능한 옵션 2~3개\n5. 추가 검토 쟁점\n\n완성 공약문처럼 쓰지 말고, 사람이 다음 단계에서 판단하고 다듬을 수 있는 정책 방향 제안서처럼 작성하라. 각 항목은 짧은 문단 또는 불릿으로 명확히 정리하라."
    if context_blocks:
        user_msg += "\n\n참고 자료:\n\n" + "\n\n".join(context_blocks)

    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)

    stream = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        max_completion_tokens=4096,
        timeout=180,
        stream=True,
    )

    full = []
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            text = chunk.choices[0].delta.content
            full.append(text)
            yield text

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _load_openai_messages(session_id: str) -> list[dict]:
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
    summary = f"""아래는 출마자와 AI 코치의 정책 방향 정리 대화 내용이다. 이 대화에서 논의된 내용을 바탕으로 지역 이슈, 정책 방향, 우선순위, 참고 근거를 구조화하라.

--- 대화 내용 ---
{conversation_text[:6000]}
--- 대화 끝 ---

위 대화에서 확인된 지역 맥락, 대상, 수단, 기대효과, 추가 검토가 필요한 쟁점을 먼저 정리하고, 필요하면 사람이 다듬을 수 있는 초안 보조 형태로 제시하라."""

    return summary
