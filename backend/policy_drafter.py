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
    "정책": "정책",
    "정책포지션": "정책",  # 하위호환
    "지역공약": "지역공약",
    "입법취지서": "입법취지서",
    "논평": "논평",
    "메시지": "메시지",
}


# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------
def _load_drafter_system_prompt() -> str:
    path = PROMPTS_DIR / "정책_생성_시스템.txt"
    if path.exists():
        return path.read_text(encoding="utf-8-sig").strip()
    return "당 정책 기획 전문가로서 정책 초안을 작성하세요."


def _load_drafter_user_template() -> str:
    path = PROMPTS_DIR / "정책_생성_유저.txt"
    if path.exists():
        return path.read_text(encoding="utf-8-sig").strip()
    return "주제: {{TOPIC}}\n{{RESEARCH_CONTEXT}}"


def _build_drafter_user_message(
    *,
    topic: str,
    output_format: str,
    platform_context: str,
    pledges_context: str,
    winners2022_context: str,
    candidates_context: str,
    messages_context: str,
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
        .replace("{{MESSAGES_CONTEXT}}", messages_context or "(공식 논평·보도자료 없음)")
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
def _extract_vs_texts(page_data) -> list[tuple[str, str]]:
    """Vector Store 검색 결과에서 (filename, text) 리스트 추출."""
    results = []
    for r in page_data:
        content = ""
        if hasattr(r, "content") and r.content:
            for c in (r.content if isinstance(r.content, list) else [r.content]):
                if hasattr(c, "text"):
                    content += str(c.text) + "\n"
        fn = getattr(r, "filename", "") or ""
        results.append((fn, content.strip()))
    return results


def _search_messages_by_topic(topic: str, limit: int = 8) -> str:
    """DB에서 토픽과 관련된 공식 논평·보도자료·브리핑 검색."""
    conn = get_connection()
    try:
        # FTS5 검색 시도
        safe_q = topic.strip().replace('"', '""')
        try:
            rows = conn.execute(
                """SELECT d.title, d.speaker_name, d.published_at, d.summary
                   FROM hub_docs_fts f
                   JOIN policy_documents d ON d.id = f.rowid
                   WHERE hub_docs_fts MATCH ?
                     AND d.doc_type IN ('statement','press_release','briefing','commentary')
                     AND d.status = 'active'
                   ORDER BY rank LIMIT ?""",
                (f'"{safe_q}"', limit),
            ).fetchall()
        except Exception:
            # FTS 없으면 LIKE 검색
            rows = conn.execute(
                """SELECT title, speaker_name, published_at, summary
                   FROM policy_documents
                   WHERE doc_type IN ('statement','press_release','briefing','commentary')
                     AND status = 'active'
                     AND (title LIKE ? OR summary LIKE ?)
                   ORDER BY published_at DESC LIMIT ?""",
                (f"%{topic}%", f"%{topic}%", limit),
            ).fetchall()

        if not rows:
            return ""
        parts = []
        for r in rows:
            line = f"[{r['published_at'] or ''}] {r['speaker_name'] or ''}: {r['title']}"
            if r["summary"]:
                line += f"\n{r['summary'][:300]}"
            parts.append(line)
        return "\n\n".join(parts)
    finally:
        conn.close()


def _get_rag_contexts(topic: str, user_meta: Optional[dict] = None) -> dict:
    """기존 Vector Store에서 RAG 컨텍스트 검색 + DB에서 공식 메시지 검색."""
    try:
        from backend.config import (
            OPENAI_VECTOR_STORE_ID,
            OPENAI_WINNERS2022_VECTOR_STORE_ID,
        )
        OPENAI_REGIONAL_VECTOR_STORE_ID = os.getenv("OPENAI_REGIONAL_VECTOR_STORE_ID", "").strip()
        from backend.embeddings import get_openai_client

        client = get_openai_client()
        if not client or not OPENAI_VECTOR_STORE_ID:
            return {"platform": "", "pledges": "", "winners2022": "", "candidates": "", "messages": ""}

        from concurrent.futures import ThreadPoolExecutor

        def _vs_search(vs_id, query, max_results):
            try:
                page = client.vector_stores.search(
                    vector_store_id=vs_id, query=query,
                    max_num_results=max_results, rewrite_query=True,
                )
                return _extract_vs_texts(page.data)
            except Exception as e:
                logger.warning("drafter RAG search failed: %s", e)
                return []

        # 병렬 검색: ① 정책 VS 토픽 ② 지역 VS 토픽 ③ 정강정책 전용 ④ 2022 당선인
        with ThreadPoolExecutor(max_workers=4) as ex:
            f_topic = ex.submit(_vs_search, OPENAI_VECTOR_STORE_ID, topic, 12)
            f_regional = ex.submit(
                _vs_search, OPENAI_REGIONAL_VECTOR_STORE_ID, topic, 8,
            ) if OPENAI_REGIONAL_VECTOR_STORE_ID else None
            f_platform = ex.submit(
                _vs_search, OPENAI_VECTOR_STORE_ID,
                "개혁신당 강령 정강정책 이념 취지 가치", 5,
            )
            f_winners = ex.submit(
                _vs_search,
                OPENAI_WINNERS2022_VECTOR_STORE_ID or "",
                topic, 5,
            ) if OPENAI_WINNERS2022_VECTOR_STORE_ID else None

        topic_results = f_topic.result()
        regional_results = f_regional.result() if f_regional else []
        platform_results = f_platform.result()

        logger.info(
            "[drafter] VS search results: topic=%d, regional=%d, platform=%d",
            len(topic_results), len(regional_results), len(platform_results),
        )
        for fn, text in platform_results:
            logger.debug("[drafter] platform result file=%s chars=%d", fn, len(text))

        # 정강정책 전용 결과 + 토픽 결과 중 정강 파일 합산
        platform_parts = [text for _, text in platform_results if text]
        pledge_parts = []
        seen_texts = set()  # 중복 제거용

        for fn, text in topic_results + regional_results:
            if not text:
                continue
            text_key = text[:200]  # 앞 200자로 중복 판단
            if text_key in seen_texts:
                continue
            seen_texts.add(text_key)

            if "정강" in fn or "정책" in fn:
                if text not in platform_parts:
                    platform_parts.append(text)
            else:
                pledge_parts.append(text)

        # Winners 2022
        winners_text = ""
        if f_winners:
            winners_results = f_winners.result()
            winners_text = "\n".join(text for _, text in winners_results if text)

        # 공식 논평·보도자료 (DB 검색)
        messages_text = ""
        try:
            messages_text = _search_messages_by_topic(topic)
        except Exception as e:
            logger.warning("drafter messages search failed: %s", e)

        result = {
            "platform": "\n\n".join(platform_parts)[:8000],
            "pledges": "\n\n".join(pledge_parts)[:8000],
            "winners2022": winners_text[:5000],
            "candidates": "",
            "messages": messages_text[:6000],
        }
        logger.info(
            "[drafter] RAG context sizes: platform=%d pledges=%d winners=%d messages=%d",
            len(result["platform"]), len(result["pledges"]),
            len(result["winners2022"]), len(result["messages"]),
        )
        return result
    except ImportError:
        return {"platform": "", "pledges": "", "winners2022": "", "candidates": "", "messages": ""}


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
def _draft_cache_key(topic: str, output_format: str, region: str = "", election_type: str = "") -> str:
    raw = f"draft|{topic}|{output_format}|{region}|{election_type}"
    return "draft_" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def _get_cached_draft(key: str) -> Optional[str]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT result_payload FROM analysis_cache WHERE cache_key = ? AND expires_at > datetime('now')",
            (key,),
        ).fetchone()
        return row["result_payload"] if row else None
    except Exception:
        return None
    finally:
        conn.close()


def _set_cached_draft(key: str, result: str) -> None:
    from datetime import datetime, timedelta, timezone

    expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
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
    cache_key = _draft_cache_key(topic, output_format, region_str, election_type)
    if use_cache:
        cached = _get_cached_draft(cache_key)
        if cached:
            if stream:
                def _cached_gen():
                    yield cached
                return _cached_gen()
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
        messages_context=rag.get("messages", ""),
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
                stream_options={"include_usage": True},
            )
            full = []
            usage_in = 0
            usage_out = 0
            for chunk in s:
                if chunk.choices and chunk.choices[0].delta.content:
                    text = chunk.choices[0].delta.content
                    full.append(text)
                    yield text
                if hasattr(chunk, "usage") and chunk.usage:
                    usage_in = chunk.usage.prompt_tokens or 0
                    usage_out = chunk.usage.completion_tokens or 0
            # Save to cache after stream completes
            _set_cached_draft(cache_key, "".join(full))
            yield f"[USAGE]{usage_in},{usage_out}"

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
        "rag_sources": {
            "platform": bool(rag["platform"]),
            "pledges": bool(rag["pledges"]),
            "winners2022": bool(rag["winners2022"]),
            "candidates": bool(rag["candidates"]),
            "messages": bool(rag.get("messages", "")),
        },
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
