"""
리서치 어시스턴트 — 주제·지역 입력 시 관련 자료를 자동 수집.

SSOT 문서 + 포지션 + 지방의회 API 결과를 합산하여
"이 주제에 대해 알아야 할 것" 브리핑을 생성한다.

Phase 3 드래프터의 입력으로 사용됨.
"""

from __future__ import annotations

import logging
from typing import Optional

from backend.assembly_api import query_assembly_context
from backend.database import get_connection
from backend.policy_ssot import (
    TOPIC_RULES,
    _classify_commentary_topic,
    _infer_related_positions_for_document,
    list_policy_documents,
    list_policy_positions,
)

logger = logging.getLogger(__name__)


def research_topic(
    *,
    topic: str,
    region: Optional[str] = None,
    years: int = 2,
    max_docs: int = 20,
) -> dict:
    """
    주제·지역에 대한 리서치 브리핑 생성.

    Args:
        topic: 정책 주제 키워드 (예: "청년 주거", "AI 규제")
        region: 지역명 (예: "마포구", "서울")
        years: 검색 기간 (년)
        max_docs: 최대 반환 문서 수

    Returns:
        {
            "topic": str,
            "region": str | None,
            "classified_topic": str,  # TOPIC_RULES 분류 결과
            "ssot": {
                "related_documents": [...],
                "related_positions": [...],
                "document_count": int,
                "position_count": int,
            },
            "assembly": {
                "available": bool,
                "context_text": str,
                "result_count": int,
            },
            "briefing_text": str,  # 요약 브리핑 (프롬프트용)
        }
    """
    # 1. 주제 분류
    topic_item = {"title": topic, "summary": topic, "body": ""}
    classified = _classify_commentary_topic(topic_item)

    # 2. 주제 키워드 추출
    keywords = _extract_topic_keywords(topic, classified)

    # 3. SSOT 관련 문서 검색
    all_docs = list_policy_documents(status="active")
    all_positions = list_policy_positions(status="approved")

    related_docs = _find_related_documents(all_docs, keywords, topic, max_docs)
    related_positions = _find_related_positions(all_positions, keywords, topic)

    # 4. 지방의회 API 조회
    assembly = query_assembly_context(
        region=region,
        keywords=keywords[:5],  # API 검색어는 5개까지
        years=years,
    )

    # 5. 브리핑 텍스트 생성
    briefing = _build_briefing(
        topic=topic,
        region=region,
        classified=classified,
        related_docs=related_docs,
        related_positions=related_positions,
        assembly=assembly,
    )

    return {
        "topic": topic,
        "region": region,
        "classified_topic": classified,
        "ssot": {
            "related_documents": related_docs[:max_docs],
            "related_positions": related_positions,
            "document_count": len(related_docs),
            "position_count": len(related_positions),
        },
        "assembly": {
            "available": assembly["available"],
            "context_text": assembly["context_text"],
            "result_count": len(assembly.get("assembly_results", []))
            + len(assembly.get("speech_results", [])),
        },
        "briefing_text": briefing,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_topic_keywords(topic: str, classified_label: str) -> list[str]:
    """주제 문자열과 분류 결과에서 검색 키워드 추출."""
    # 기본: 사용자 입력 토큰
    stopwords = {"에", "를", "을", "의", "와", "과", "대한", "관련", "위한", "및", "등"}
    tokens = [t for t in topic.split() if len(t) > 1 and t not in stopwords]

    # TOPIC_RULES에서 매칭된 주제의 키워드 추가
    for rule in TOPIC_RULES:
        if rule["label"] == classified_label:
            # 사용자 입력과 겹치는 키워드만 추가 (너무 넓어지지 않게)
            for kw in rule["keywords"]:
                if kw not in tokens and any(kw in t or t in kw for t in tokens):
                    tokens.append(kw)
            break

    return tokens


def _find_related_documents(
    docs: list[dict], keywords: list[str], topic: str, limit: int
) -> list[dict]:
    """키워드 매칭으로 관련 문서 검색. 간단한 점수 기반."""
    scored = []
    kw_set = set(k.lower() for k in keywords)

    for doc in docs:
        haystack = " ".join([
            doc.get("title") or "",
            doc.get("summary") or "",
            (doc.get("body") or "")[:2000],
        ]).lower()

        score = sum(2 for kw in kw_set if kw in haystack)
        # 제목에 키워드 있으면 보너스
        title_lower = (doc.get("title") or "").lower()
        score += sum(3 for kw in kw_set if kw in title_lower)

        if score > 0:
            scored.append((score, {
                "id": doc["id"],
                "title": doc["title"],
                "doc_type": doc.get("doc_type", ""),
                "published_at": doc.get("published_at", ""),
                "summary": (doc.get("summary") or "")[:300],
                "relevance_score": score,
            }))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:limit]]


def _find_related_positions(
    positions: list[dict], keywords: list[str], topic: str
) -> list[dict]:
    """키워드 매칭으로 관련 포지션 검색."""
    scored = []
    kw_set = set(k.lower() for k in keywords)

    for pos in positions:
        haystack = " ".join([
            pos.get("title") or "",
            pos.get("summary") or "",
            pos.get("key_points") or "",
        ]).lower()

        score = sum(2 for kw in kw_set if kw in haystack)
        title_lower = (pos.get("title") or "").lower()
        score += sum(3 for kw in kw_set if kw in title_lower)

        if score > 0:
            scored.append((score, {
                "id": pos["id"],
                "title": pos["title"],
                "category": pos.get("category", ""),
                "summary": (pos.get("summary") or "")[:300],
                "key_points": (pos.get("key_points") or "")[:300],
                "relevance_score": score,
            }))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:10]]


def _build_briefing(
    *,
    topic: str,
    region: Optional[str],
    classified: str,
    related_docs: list[dict],
    related_positions: list[dict],
    assembly: dict,
) -> str:
    """프롬프트에 넣을 리서치 브리핑 텍스트 생성."""
    lines = []
    lines.append(f"주제: {topic}")
    if region:
        lines.append(f"지역: {region}")
    lines.append(f"분류: {classified}")
    lines.append("")

    # 기존 포지션
    if related_positions:
        lines.append(f"[기존 당 포지션] {len(related_positions)}건")
        for pos in related_positions[:5]:
            lines.append(f"- {pos['title']}")
            if pos.get("key_points"):
                lines.append(f"  핵심: {pos['key_points'][:150]}")
        lines.append("")
    else:
        lines.append("[기존 당 포지션] 관련 포지션 없음 (정책 사각지대)")
        lines.append("")

    # SSOT 문서
    if related_docs:
        lines.append(f"[관련 SSOT 문서] {len(related_docs)}건")
        for doc in related_docs[:10]:
            lines.append(f"- [{doc['doc_type']}] {doc['title']} ({doc.get('published_at', '')})")
            if doc.get("summary"):
                lines.append(f"  {doc['summary'][:150]}")
        lines.append("")
    else:
        lines.append("[관련 SSOT 문서] 없음")
        lines.append("")

    # 지방의회
    lines.append("[지방의회 논의]")
    lines.append(assembly["context_text"])

    return "\n".join(lines)
