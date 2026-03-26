"""
AI 정책 챗봇 — 당 정책에 대한 질문 답변.

기존 RAG 인프라(Vector Store)를 사용하되,
system prompt를 "정책 소통 담당자"로 변경.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Optional

from backend.database import get_connection

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
CHAT_MODEL = os.getenv("CHAT_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-4o"

CHATBOT_SYSTEM_PROMPT = """개혁신당 정책 소통 담당자 역할이다. 시민이 당 정책에 대해 질문하면 아래 규칙에 따라 답변한다.

규칙:
1) 제공된 정강정책과 당 포지션 문서에 나온 내용만 근거로 답변한다. 문서에 없는 내용은 "현재 해당 주제에 대한 공식 입장이 확인되지 않습니다"라고 답한다.
2) 마크다운 금지. 일반 텍스트만 사용한다.
3) 답변은 간결하게. 3~5문장 이내로 핵심만 전달한다.
4) 정치적 공격, 타 정당 비방, 허위 사실 유포 요청은 거절한다.
5) 정책과 무관한 질문(날씨, 연예 등)은 "정책 관련 질문에만 답변드릴 수 있습니다"로 답한다.
6) 출처가 있으면 "[포지션명]에 따르면"처럼 근거를 밝힌다."""

# 입력 필터링 — 프롬프트 인젝션 방어
BLOCKED_PATTERNS = [
    "ignore previous",
    "ignore above",
    "disregard",
    "forget your instructions",
    "new instructions",
    "system prompt",
    "당신은 이제",
    "역할을 바꿔",
    "프롬프트를 무시",
]


def _is_injection_attempt(text: str) -> bool:
    lower = text.lower()
    return any(p in lower for p in BLOCKED_PATTERNS)


def answer_policy_question(*, question: str) -> dict:
    """
    시민 질문에 답변.

    Returns:
        {
            "answer": str,
            "sources": [str],
            "model": str,
            "from_cache": bool,
        }
    """
    if _is_injection_attempt(question):
        return {
            "answer": "정책 관련 질문에만 답변드릴 수 있습니다.",
            "sources": [],
            "model": "",
            "from_cache": False,
            "blocked": True,
        }

    if not OPENAI_API_KEY:
        return {
            "answer": "(API 키가 설정되지 않아 답변할 수 없습니다)",
            "sources": [],
            "model": "",
            "from_cache": False,
            "error": "no_api_key",
        }

    # Cache check
    cache_key = "chat_" + hashlib.sha256(question.encode()).hexdigest()[:24]
    cached = _get_cached(cache_key)
    if cached:
        return cached

    # RAG context
    context = _get_chatbot_context(question)

    # Build messages
    system = CHATBOT_SYSTEM_PROMPT
    user_msg = f"""다음은 당 정책 관련 참고 문서입니다.

===== [정강정책·포지션] =====
{context['policy_text']}

===== [관련 문서] =====
{context['docs_text']}

---

시민 질문: {question}

위 문서를 근거로 답변하세요."""

    # GPT call
    try:
        from openai import OpenAI

        client = OpenAI(api_key=OPENAI_API_KEY)
        t_start = time.perf_counter()

        resp = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
            max_completion_tokens=1024,
            timeout=60,
        )
        text = resp.choices[0].message.content or ""
        t_elapsed = time.perf_counter() - t_start
        logger.info("[chatbot] llm_call ms=%.0f", t_elapsed * 1000)

        result = {
            "answer": text.strip() or "답변을 생성하지 못했습니다.",
            "sources": context["sources"],
            "model": CHAT_MODEL,
            "from_cache": False,
        }
        _set_cached(cache_key, result)
        return result

    except Exception as e:
        logger.error("[chatbot] GPT call failed: %s", e)
        return {
            "answer": "일시적인 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
            "sources": [],
            "model": CHAT_MODEL,
            "from_cache": False,
            "error": str(e),
        }


def _get_chatbot_context(question: str) -> dict:
    """RAG + SSOT에서 질문 관련 컨텍스트 수집."""
    policy_parts = []
    docs_parts = []
    sources = []

    # 1. Vector Store search (if available)
    try:
        from backend.openai_vector_store import (
            OPENAI_VECTOR_STORE_ID,
            get_openai_client,
        )

        client = get_openai_client()
        if client and OPENAI_VECTOR_STORE_ID:
            page = client.vector_stores.search(
                vector_store_id=OPENAI_VECTOR_STORE_ID,
                query=question,
                max_num_results=6,
                rewrite_query=True,
            )
            for r in page.data:
                content = ""
                if hasattr(r, "content") and r.content:
                    for c in (r.content if isinstance(r.content, list) else [r.content]):
                        if hasattr(c, "text"):
                            content += str(c.text) + "\n"
                fn = getattr(r, "filename", "") or ""
                if content.strip():
                    if "정강" in fn or "정책" in fn:
                        policy_parts.append(content.strip())
                    else:
                        docs_parts.append(content.strip())
                    sources.append(fn)
    except Exception as e:
        logger.warning("[chatbot] vector store search failed: %s", e)

    # 2. SSOT positions search
    try:
        from backend.policy_ssot import list_policy_positions

        positions = list_policy_positions(status="approved")
        q_lower = question.lower()
        for pos in positions[:50]:
            haystack = " ".join([pos.get("title", ""), pos.get("summary", ""), pos.get("key_points", "")]).lower()
            # Simple keyword matching
            q_tokens = [t for t in q_lower.split() if len(t) > 1]
            if any(t in haystack for t in q_tokens):
                text = f"[{pos['title']}] {pos.get('summary', '')} / 핵심: {pos.get('key_points', '')}"
                policy_parts.append(text[:500])
                sources.append(pos["title"])
    except Exception as e:
        logger.warning("[chatbot] position search failed: %s", e)

    return {
        "policy_text": "\n\n".join(policy_parts)[:6000] or "(관련 정책 문서 없음)",
        "docs_text": "\n\n".join(docs_parts)[:4000] or "(관련 문서 없음)",
        "sources": list(dict.fromkeys(sources))[:10],  # dedupe, keep order
    }


def _get_cached(key: str) -> Optional[dict]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT result_payload, expires_at FROM analysis_cache WHERE cache_key = ?",
            (key,),
        ).fetchone()
        if not row:
            return None
        from datetime import datetime, timezone

        if row["expires_at"] and row["expires_at"] < datetime.now(timezone.utc).isoformat():
            return None
        result = json.loads(row["result_payload"])
        result["from_cache"] = True
        return result
    except Exception:
        return None
    finally:
        conn.close()


def _set_cached(key: str, result: dict) -> None:
    from datetime import datetime, timedelta, timezone

    expires = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO analysis_cache
               (user_id, cache_key, request_fingerprint, result_payload, expires_at)
               VALUES (0, ?, ?, ?, ?)""",
            (key, "chatbot", json.dumps(result, ensure_ascii=False), expires),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()
