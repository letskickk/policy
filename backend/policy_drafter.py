"""
정책 드래프터 — AI 기반 정책 초안 생성.

기존 run_check 패턴을 기반으로:
- 리서치 어시스턴트가 수집한 컨텍스트 + RAG 검색 결과를 합산
- GPT에 프롬프트를 보내 정책 초안 생성
- 결과를 policy_positions(draft)로 저장 가능
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

from backend.config import PROMPTS_DIR
from backend.database import get_connection
from backend.research_assistant import research_topic

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
CHAT_MODEL = os.getenv("CHAT_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-4o"

OUTPUT_FORMATS = {
    "정책포지션": "정책포지션",
    "지역공약": "지역공약",
    "입법취지서": "입법취지서",
}


# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------
def _load_drafter_system_prompt() -> str:
    path = PROMPTS_DIR / "정책_생성_시스템.txt"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return "당 정책 기획 전문가로서 정책 초안을 작성하세요."


def _load_drafter_user_template() -> str:
    path = PROMPTS_DIR / "정책_생성_유저.txt"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return "주제: {{TOPIC}}\n{{RESEARCH_CONTEXT}}"


def _build_drafter_user_message(
    *,
    topic: str,
    output_format: str,
    platform_context: str,
    pledges_context: str,
    winners2022_context: str,
    candidates_context: str,
    assembly_context: str,
    research_context: str,
    election_type: str = "",
    region_province: str = "",
    region_city: str = "",
    district_name: str = "",
) -> str:
    template = _load_drafter_user_template()
    return (
        template.replace("{{PLATFORM_CONTEXT}}", platform_context or "(정강정책 문서 없음)")
        .replace("{{PLEDGES_CONTEXT}}", pledges_context or "(우리당 공약 문서 없음)")
        .replace("{{WINNERS2022_PLEDGES_CONTEXT}}", winners2022_context or "(2022 당선인 공약 없음)")
        .replace("{{CANDIDATES_PLEDGES_CONTEXT}}", candidates_context or "(등록된 출마자 공약 없음)")
        .replace("{{ASSEMBLY_CONTEXT}}", assembly_context or "(지방의회 데이터 없음)")
        .replace("{{RESEARCH_CONTEXT}}", research_context or "(연구 자료 없음)")
        .replace("{{TOPIC}}", topic)
        .replace("{{OUTPUT_FORMAT}}", output_format)
        .replace("{{ELECTION_TYPE}}", election_type or "")
        .replace("{{REGION_PROVINCE}}", region_province or "")
        .replace("{{REGION_CITY}}", region_city or "")
        .replace("{{DISTRICT_NAME}}", district_name or "")
    )


# ---------------------------------------------------------------------------
# RAG context retrieval (reuse existing Vector Store search)
# ---------------------------------------------------------------------------
def _get_rag_contexts(topic: str, user_meta: Optional[dict] = None) -> dict:
    """기존 Vector Store에서 RAG 컨텍스트 검색."""
    try:
        from backend.openai_vector_store import (
            OPENAI_VECTOR_STORE_ID,
            OPENAI_REGIONAL_VECTOR_STORE_ID,
            OPENAI_WINNERS2022_VECTOR_STORE_ID,
            get_openai_client,
        )

        client = get_openai_client()
        if not client or not OPENAI_VECTOR_STORE_ID:
            return {"platform": "", "pledges": "", "winners2022": "", "candidates": ""}

        # 정강정책 + 공약 검색
        results = []
        try:
            page = client.vector_stores.search(
                vector_store_id=OPENAI_VECTOR_STORE_ID,
                query=topic,
                max_num_results=8,
                rewrite_query=True,
            )
            for r in page.data:
                content = ""
                if hasattr(r, "content") and r.content:
                    for c in (r.content if isinstance(r.content, list) else [r.content]):
                        if hasattr(c, "text"):
                            content += str(c.text) + "\n"
                fn = getattr(r, "filename", "") or ""
                results.append((fn, content.strip()))
        except Exception as e:
            logger.warning("drafter RAG search failed: %s", e)

        # 정강정책 vs 공약 분리
        platform_parts = []
        pledge_parts = []
        for fn, text in results:
            if "정강" in fn or "정책" in fn:
                platform_parts.append(text)
            else:
                pledge_parts.append(text)

        # Winners 2022
        winners_text = ""
        if OPENAI_WINNERS2022_VECTOR_STORE_ID:
            try:
                page2 = client.vector_stores.search(
                    vector_store_id=OPENAI_WINNERS2022_VECTOR_STORE_ID,
                    query=topic,
                    max_num_results=5,
                    rewrite_query=True,
                )
                parts = []
                for r in page2.data:
                    if hasattr(r, "content") and r.content:
                        for c in (r.content if isinstance(r.content, list) else [r.content]):
                            if hasattr(c, "text"):
                                parts.append(str(c.text))
                winners_text = "\n".join(parts)
            except Exception as e:
                logger.warning("drafter winners2022 search failed: %s", e)

        return {
            "platform": "\n\n".join(platform_parts)[:8000],
            "pledges": "\n\n".join(pledge_parts)[:8000],
            "winners2022": winners_text[:5000],
            "candidates": "",
        }
    except ImportError:
        return {"platform": "", "pledges": "", "winners2022": "", "candidates": ""}


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
def _draft_cache_key(topic: str, output_format: str, region: str = "") -> str:
    raw = f"draft|{topic}|{output_format}|{region}"
    return "draft_" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def _get_cached_draft(key: str) -> Optional[str]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT result_payload FROM analysis_cache WHERE cache_key = ?", (key,)
        ).fetchone()
        return row["result_payload"] if row else None
    except Exception:
        return None
    finally:
        conn.close()


def _set_cached_draft(key: str, result: str) -> None:
    from datetime import datetime, timedelta, timezone

    expires = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO analysis_cache
               (user_id, cache_key, request_fingerprint, result_payload, expires_at)
               VALUES (0, ?, ?, ?, ?)""",
            (key, "policy_drafter", result, expires),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def generate_policy_draft(
    *,
    topic: str,
    output_format: str = "정책포지션",
    region: Optional[str] = None,
    election_type: str = "",
    region_province: str = "",
    region_city: str = "",
    district_name: str = "",
    use_cache: bool = True,
    stream: bool = False,
) -> dict | str:
    """
    정책 초안 생성.

    Returns (stream=False):
        {
            "draft_text": str,
            "research": {...},  # research_topic 결과
            "output_format": str,
            "from_cache": bool,
            "model": str,
        }
    Returns (stream=True):
        Generator[str] — streaming text chunks
    """
    if not OPENAI_API_KEY:
        return {
            "draft_text": "(OPENAI_API_KEY가 설정되지 않아 초안을 생성할 수 없습니다)",
            "research": {},
            "output_format": output_format,
            "from_cache": False,
            "model": "",
            "error": "no_api_key",
        }

    region_str = " ".join(filter(None, [region_province, region_city, district_name])).strip()

    # Cache check
    cache_key = _draft_cache_key(topic, output_format, region_str)
    if use_cache:
        cached = _get_cached_draft(cache_key)
        if cached:
            return {
                "draft_text": cached,
                "research": {},
                "output_format": output_format,
                "from_cache": True,
                "model": CHAT_MODEL,
            }

    # 1. Research
    research = research_topic(
        topic=topic,
        region=region or region_province or region_city,
        years=2,
    )

    # 2. RAG contexts
    rag = _get_rag_contexts(topic)

    # 3. Build prompt
    system = _load_drafter_system_prompt()
    user_msg = _build_drafter_user_message(
        topic=topic,
        output_format=OUTPUT_FORMATS.get(output_format, output_format),
        platform_context=rag["platform"],
        pledges_context=rag["pledges"],
        winners2022_context=rag["winners2022"],
        candidates_context=rag["candidates"],
        assembly_context=research["assembly"]["context_text"],
        research_context=research["briefing_text"],
        election_type=election_type,
        region_province=region_province,
        region_city=region_city,
        district_name=district_name,
    )

    # 4. GPT call
    from openai import OpenAI

    client = OpenAI(api_key=OPENAI_API_KEY)
    t_start = time.perf_counter()

    if stream:
        def _gen():
            s = client.chat.completions.create(
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
            for chunk in s:
                if chunk.choices and chunk.choices[0].delta.content:
                    text = chunk.choices[0].delta.content
                    full.append(text)
                    yield text
            # Save to cache after stream completes
            _set_cached_draft(cache_key, "".join(full))

        return _gen()

    resp = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        max_completion_tokens=4096,
        timeout=180,
    )
    text = resp.choices[0].message.content or ""
    t_elapsed = time.perf_counter() - t_start
    logger.info("[drafter] llm_call ms=%.0f model=%s", t_elapsed * 1000, CHAT_MODEL)

    if not text.strip():
        return {
            "draft_text": "(모델이 텍스트를 반환하지 않았습니다)",
            "research": research,
            "output_format": output_format,
            "from_cache": False,
            "model": CHAT_MODEL,
            "error": "empty_response",
        }

    _set_cached_draft(cache_key, text.strip())

    return {
        "draft_text": text.strip(),
        "research": research,
        "output_format": output_format,
        "from_cache": False,
        "model": CHAT_MODEL,
    }


# ---------------------------------------------------------------------------
# Save draft to policy_positions
# ---------------------------------------------------------------------------
def save_draft_as_position(
    *,
    title: str,
    summary: str,
    key_points: str,
    body: str,
    category: str = "general",
    created_by: Optional[int] = None,
) -> int:
    """초안을 policy_positions에 draft 상태로 저장. Returns position ID."""
    from backend.policy_ssot import upsert_policy_position

    result = upsert_policy_position(
        position_id=None,
        title=title,
        category=category,
        summary=summary,
        key_points=key_points,
        body=body,
        status="draft",
        owner_scope="party",
        effective_from=None,
        effective_to=None,
        version_label=None,
        actor_id=created_by,
    )
    return result["id"]
