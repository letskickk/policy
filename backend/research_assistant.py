"""
리서치 어시스턴트 — 주제·지역 입력 시 관련 자료를 자동 수집.

SSOT 문서 + 포지션 + 지방의회 API 결과를 합산하여
"이 주제에 대해 알아야 할 것" 브리핑을 생성한다.

Phase 3 드래프터의 입력으로 사용됨.
"""

from __future__ import annotations

import logging
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Optional

from backend.assembly_api import query_assembly_context
from backend.public_data_api import query_public_data_context
from backend.database import get_connection
from backend.policy_ssot import (
    TOPIC_RULES,
    _classify_commentary_topic,
    _infer_related_positions_for_document,
    list_policy_documents,
    list_policy_positions,
)

logger = logging.getLogger(__name__)

DOC_TYPE_KO = {
    "bill": "법안", "commentary": "논평", "poll": "여론조사",
    "meeting": "회의록", "pledge": "공약", "platform": "정강정책",
    "briefing": "브리핑", "position": "정책", "statement": "성명",
    "press_release": "보도자료",
}


def research_topic(
    *,
    topic: str,
    region: Optional[str] = None,
    district_name: Optional[str] = None,
    election_type: Optional[str] = None,
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
            "news": {
                "available": bool,
                "articles": [...],
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

    # 4. 지방의회 API 조회 — 기초의회 + 광역의회 + 국회 모두 검색
    assembly_results = []

    # 4-1. 기초의회 (구/군의회) — 기초의원일 때
    if election_type and "local" in election_type and district_name:
        gu_name = district_name.strip().split()[0] if district_name.strip() else ""
        if gu_name:
            try:
                local_assembly = query_assembly_context(
                    region=gu_name, district_name=district_name,
                    election_type=election_type, keywords=keywords[:5], years=years,
                )
                if local_assembly.get("context_text"):
                    assembly_results.append(f"[기초의회]\n{local_assembly['context_text']}")
            except Exception as e:
                logger.warning("기초의회 검색 실패: %s", e)

    # 4-2. 광역의회 (시/도의회)
    if region:
        try:
            # 광역시/특별시 접미사 제거해 의회 ID 검색 정확도 향상 (광주광역시 → 광주)
            _region_for_assembly = region
            for _sfx in ("광역시", "특별자치시", "특별자치도", "특별시", "광역도"):
                if region.endswith(_sfx):
                    _region_for_assembly = region[:-len(_sfx)]
                    break
            metro_assembly = query_assembly_context(
                region=_region_for_assembly, district_name=district_name,
                election_type=election_type, keywords=keywords[:5], years=years,
            )
            if metro_assembly.get("context_text"):
                assembly_results.append(f"[광역의회]\n{metro_assembly['context_text']}")
        except Exception as e:
            logger.warning("광역의회 검색 실패: %s", e)

    # 합산
    assembly = {
        "available": len(assembly_results) > 0,
        "context_text": "\n\n".join(assembly_results),
    }

    # 5. 공공데이터 조회 (인구/상권/교통/시설)
    public_data = query_public_data_context(
        region=region, district_name=district_name,
        topic=topic, keywords=keywords[:5],
    )

    # 6. 관련 기사 검색 (보조 근거 레이��)
    news_articles = _fetch_related_news(topic=topic, region=region, keywords=keywords)

    # 7. 브리핑 텍스트 생성
    briefing = _build_briefing(
        topic=topic,
        region=region,
        classified=classified,
        related_docs=related_docs,
        related_positions=related_positions,
        assembly=assembly,
        public_data=public_data,
        news_articles=news_articles,
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
        "public_data": {
            "available": public_data.get("available", False),
            "context_text": public_data.get("context_text", ""),
            "sources": {k: v.get("available", False) for k, v in public_data.get("sources", {}).items()},
        },
        "news": {
            "available": bool(news_articles),
            "articles": news_articles,
            "result_count": len(news_articles),
        },
        "briefing_text": briefing,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# 의미 유사 키워드 확장 맵 (검색 다양성 향상)
_SYNONYM_MAP = {
    "주거": ["주택", "임대", "거주", "전세", "월세", "아파트"],
    "주택": ["주거", "임대", "거주"],
    "청년": ["2030", "MZ", "사회초년생"],
    "고령": ["노인", "어르신", "시니어", "고령자"],
    "노인": ["고령", "어르신", "시니어"],
    "교통": ["대중교통", "버스", "지하철", "도로"],
    "환경": ["기후", "탄소", "에너지"],
    "일자리": ["고용", "취업", "채용", "근로"],
    "고용": ["일자리", "취업", "근로"],
    "교육": ["학교", "학습", "등록금"],
    "복지": ["돌봄", "사회서비스", "지원"],
    "안전": ["재난", "방범", "치안"],
    "경제": ["산업", "기업", "성장"],
}


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

    # 의미 유사 키워드 확장 (검색 다양성 향상)
    expanded = list(tokens)
    for t in tokens:
        for syn in _SYNONYM_MAP.get(t, []):
            if syn not in expanded:
                expanded.append(syn)

    return expanded


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


def _fetch_related_news(*, topic: str, region: Optional[str], keywords: list[str], limit: int = 6) -> list[dict]:
    """Google News RSS 기반 보조 기사 수집. 지역 자료보다 후순위 참고용."""
    region_full = (region or "").strip()
    region_short = region_full.split()[-1] if region_full else ""
    query_candidates = []
    for region_token in [region_full, region_short, ""]:
        query_parts = []
        if region_token:
            query_parts.append(region_token)
        query_parts.append(topic)
        query_parts.extend(keywords[:3])
        query = " ".join(part for part in query_parts if part).strip()
        if query:
            query_candidates.append(query)

    articles = []
    seen = set()
    for query in query_candidates:
        url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({
            "q": query,
            "hl": "ko",
            "gl": "KR",
            "ceid": "KR:ko",
        })
        try:
            data = urllib.request.urlopen(url, timeout=15).read()
            root = ET.fromstring(data)
        except Exception as e:
            logger.warning("related news fetch failed: %s", e)
            continue

        for item in root.findall('.//item'):
            title = (item.findtext('title') or '').strip()
            link = (item.findtext('link') or '').strip()
            source = (item.findtext('source') or '').strip()
            if not title or title in seen:
                continue
            seen.add(title)
            score = 0
            hay = f"{title} {source}"
            if region_full and region_full in hay:
                score += 4
            if region_short and region_short in hay:
                score += 6
            score += sum(1 for kw in keywords[:4] if kw and kw in hay)
            articles.append({
                "title": title,
                "link": link,
                "source": source,
                "relevance_score": score,
            })

    articles.sort(key=lambda x: x["relevance_score"], reverse=True)
    return articles[:limit]



def _build_briefing(
    *,
    topic: str,
    region: Optional[str],
    classified: str,
    related_docs: list[dict],
    related_positions: list[dict],
    assembly: dict,
    public_data: dict,
    news_articles: list[dict],
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
            type_label = DOC_TYPE_KO.get(doc['doc_type'], doc['doc_type'])
            lines.append(f"- ({type_label}) {doc['title']} ({doc.get('published_at', '')})")
            if doc.get("summary"):
                lines.append(f"  {doc['summary'][:150]}")
        lines.append("")
    else:
        lines.append("[관련 SSOT 문서] 없음")
        lines.append("")

    # 지방의회
    lines.append("[지방의회 논의]")
    lines.append(assembly["context_text"])
    lines.append("")

    # 공공데이터
    if public_data.get("available") and public_data.get("context_text"):
        lines.append(public_data["context_text"])
        lines.append("")
    else:
        lines.append("[공공데이터] 없음")
        lines.append("")

    # 관련 기사 (보조 근거)
    if news_articles:
        lines.append(f"[관련 기사] {len(news_articles)}건")
        for article in news_articles[:5]:
            source = article.get("source") or "기사"
            lines.append(f"- ({source}) {article['title']}")
    else:
        lines.append("[관련 기사] 없음")

    return "\n".join(lines)
