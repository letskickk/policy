"""
공약 개발 챗봇 — 대화형 정책 초안 생성.

출마자와 AI가 대화하면서 공약을 점진적으로 구체화하고,
마지막에 정식 공약문을 자동 생성한다.
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
    return "개혁신당 정책 기획 코치 역할이다. 출마자와 대화하면서 공약을 함께 개발한다."


# ---------------------------------------------------------------------------
# Session CRUD
# ---------------------------------------------------------------------------
def create_session(user_id: int, topic: str, output_format: str = "정책") -> dict:
    """새 챗봇 세션 생성 + RAG 컨텍스트 1회 검색."""
    session_id = uuid.uuid4().hex[:16]

    # RAG 컨텍스트 (기존 drafter 재사용)
    rag_context = _fetch_rag_context(topic)

    # 시스템 메시지 구성
    system_msg = _build_system_message(rag_context)

    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO pledge_chat_sessions
               (id, user_id, topic, output_format, rag_context)
               VALUES (?, ?, ?, ?, ?)""",
            (session_id, user_id, topic, output_format, json.dumps(rag_context, ensure_ascii=False)),
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
def _fetch_rag_context(topic: str) -> dict:
    """policy_drafter의 _get_rag_contexts + research_topic 재사용."""
    try:
        from backend.policy_drafter import _get_rag_contexts
        rag = _get_rag_contexts(topic)
    except Exception as e:
        logger.warning("pledge_chat RAG failed: %s", e)
        rag = {"platform": "", "pledges": "", "winners2022": "", "candidates": "", "messages": ""}

    try:
        from backend.research_assistant import research_topic
        research = research_topic(topic=topic, years=2)
        rag["assembly"] = research.get("assembly", {}).get("context_text", "")
        rag["research"] = research.get("briefing_text", "")
    except Exception as e:
        logger.warning("pledge_chat research failed: %s", e)
        rag["assembly"] = ""
        rag["research"] = ""

    return rag


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
# Finalize — 대화 내용으로 정식 공약문 생성
# ---------------------------------------------------------------------------
def finalize_stream(session_id: str):
    """대화 내용을 기반으로 기존 생성기 형식의 공약문을 스트리밍 생성."""
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

    # 기존 drafter 프롬프트 재사용
    from backend.policy_drafter import (
        _load_drafter_system_prompt,
        _load_drafter_user_template,
        OUTPUT_FORMATS,
    )

    system = _load_drafter_system_prompt()
    fmt = OUTPUT_FORMATS.get(output_format, output_format)

    # 유저 프롬프트 구성 — 대화 내용을 주제로 삽입
    template = _load_drafter_user_template()
    user_msg = (
        template.replace("{{PLATFORM_CONTEXT}}", rag.get("platform", "") or "(정강정책 문서 없음)")
        .replace("{{PLEDGES_CONTEXT}}", rag.get("pledges", "") or "(우리당 공약 문서 없음)")
        .replace("{{WINNERS2022_PLEDGES_CONTEXT}}", rag.get("winners2022", "") or "(2022 당선인 공약 없음)")
        .replace("{{CANDIDATES_PLEDGES_CONTEXT}}", rag.get("candidates", "") or "(등록된 출마자 공약 없음)")
        .replace("{{MESSAGES_CONTEXT}}", rag.get("messages", "") or "(공식 논평·보도자료 없음)")
        .replace("{{ASSEMBLY_CONTEXT}}", rag.get("assembly", "") or "(지방의회 데이터 없음)")
        .replace("{{RESEARCH_CONTEXT}}", rag.get("research", "") or "(연구 자료 없음)")
        .replace("{{TOPIC}}", conversation_summary)
        .replace("{{OUTPUT_FORMAT}}", fmt)
        .replace("{{ELECTION_TYPE}}", "")
        .replace("{{REGION_PROVINCE}}", "")
        .replace("{{REGION_CITY}}", "")
        .replace("{{DISTRICT_NAME}}", "")
    )

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

    # 대화 내용을 주제로 변환
    summary = f"""아래는 출마자와 AI 코치의 공약 개발 대화 내용이다. 이 대화에서 논의된 내용을 바탕으로 정책 초안을 작성하라.

--- 대화 내용 ---
{conversation_text[:6000]}
--- 대화 끝 ---

위 대화에서 합의된 정책 방향, 대상, 수단, 기대효과를 반영하여 초안을 작성하라."""

    return summary
