"""
검색 결과를 기반으로 LLM 리포트를 생성하는 모듈.
"""
import json
import logging
from typing import Dict, List, Tuple

from openai import OpenAI

from backend.chunking import DocChunk
from backend.config import CHAT_MODEL
from backend.embeddings import embed_texts, get_openai_client
from backend.vector_index import VectorIndex

logger = logging.getLogger(__name__)


def truncate_quote(text: str, max_length: int = 250) -> str:
    """
    인용문을 최대 길이로 자른다.
    
    Args:
        text: 원본 텍스트
        max_length: 최대 길이
    
    Returns:
        잘린 텍스트
    """
    if len(text) <= max_length:
        return text
    # 문장 경계에서 자르기 시도
    truncated = text[:max_length]
    last_period = truncated.rfind('.')
    last_newline = truncated.rfind('\n')
    cut_pos = max(last_period, last_newline)
    if cut_pos > max_length * 0.7:  # 너무 앞에서 자르지 않도록
        return truncated[:cut_pos + 1] + "..."
    return truncated + "..."


def search_all_indexes(
    query_text: str,
    platform_index: VectorIndex,
    pledge_index: VectorIndex,
    regional_index: VectorIndex,
    top_k_platform: int = 6,
    top_k_pledge: int = 6,
    top_k_regional: int = 8,
) -> Dict[str, List[Tuple[DocChunk, float]]]:
    """
    모든 인덱스에서 검색한다.
    
    Args:
        query_text: 검색 쿼리 텍스트
        platform_index: 정강정책 인덱스
        pledge_index: 공약 인덱스
        regional_index: 지역별 공약 인덱스
        top_k_platform: 정강정책 검색 개수
        top_k_pledge: 공약 검색 개수
        top_k_regional: 지역별 공약 검색 개수
    
    Returns:
        {"platform": [...], "pledge": [...], "regional": [...]} 딕셔너리
    """
    # 쿼리 임베딩 생성
    query_embeddings = embed_texts([query_text], batch_size=1)
    if not query_embeddings:
        logger.error("쿼리 임베딩 생성 실패")
        return {"platform": [], "pledge": [], "regional": []}
    
    query_embedding = query_embeddings[0]
    
    # 각 인덱스에서 검색
    platform_hits = platform_index.search(query_embedding, k=top_k_platform)
    pledge_hits = pledge_index.search(query_embedding, k=top_k_pledge)
    regional_hits = regional_index.search(query_embedding, k=top_k_regional)
    
    logger.info(f"검색 완료: 정강정책 {len(platform_hits)}개, 공약 {len(pledge_hits)}개, 지역별 {len(regional_hits)}개")
    
    return {
        "platform": platform_hits,
        "pledge": pledge_hits,
        "regional": regional_hits,
    }


def build_evidence_map(
    platform_hits: List[Tuple[DocChunk, float]],
    pledge_hits: List[Tuple[DocChunk, float]],
    regional_hits: List[Tuple[DocChunk, float]],
) -> Dict[str, Dict]:
    """
    검색 결과를 evidence_map 구조로 변환한다.

    Evidence ID 규칙:
    - P1, P2, ... : platform (정강정책)
    - G1, G2, ... : pledge (우리당 공약)
    - R1, R2, ... : regional (지역별 공약)
    """
    evidence_map: Dict[str, Dict] = {}

    # 플랫폼 evidence
    for i, (chunk, score) in enumerate(platform_hits, start=1):
        evid_id = f"P{i}"
        snippet = truncate_quote(chunk.text, max_length=250)
        evidence_map[evid_id] = {
            "source": "platform",
            "path": chunk.path,
            "chunk_id": chunk.chunk_id,
            "snippet": snippet,
            "score": score,
        }

    # 공약 evidence
    for i, (chunk, score) in enumerate(pledge_hits, start=1):
        evid_id = f"G{i}"
        snippet = truncate_quote(chunk.text, max_length=250)
        evidence_map[evid_id] = {
            "source": "pledge",
            "path": chunk.path,
            "chunk_id": chunk.chunk_id,
            "snippet": snippet,
            "score": score,
        }

    # 지역별 evidence
    for i, (chunk, score) in enumerate(regional_hits, start=1):
        evid_id = f"R{i}"
        snippet = truncate_quote(chunk.text, max_length=250)
        evidence_map[evid_id] = {
            "source": "regional",
            "path": chunk.path,
            "chunk_id": chunk.chunk_id,
            "snippet": snippet,
            "score": score,
        }

    return evidence_map


def build_rubric_prompt(user_pledge: str, evidence_map: Dict[str, Dict]) -> str:
    """
    LLM이 rubric + score_0_5 + evidence ID 배열만 생성하도록 하는 프롬프트.
    """
    # evidence를 텍스트로 풀어쓰기
    evid_lines = []
    for evid_id, info in evidence_map.items():
        evid_lines.append(
            f"[{evid_id}] ({info['source']} | {info['path']} | chunk {info['chunk_id']})\n{info['snippet']}"
        )

    evidence_block = "\n\n".join(evid_lines) if evid_lines else "없음"

    prompt = f"""너는 정책 정합성 채점관이다. 제공된 Evidence 범위 밖 사실/인용은 절대 금지다.

[출마자 공약]
{user_pledge}

[Evidence 목록]
각 Evidence는 ID와 스니펫으로 주어진다. ID만 사용해 인용해야 한다.
{evidence_block}

각 rubric 항목은 0~5점으로 채점한다.
- 0: 상충 또는 근거 전무
- 1~2: 대체로 부적합 / 일부만 맞음
- 3: 부분부합 (긍정/부정 요소가 섞여 있음)
- 4: 대체로 부합
- 5: 강한 부합, 매우 잘 맞음

Evidence 규칙:
- evidence 배열에는 반드시 1개 이상 ID를 넣는 것을 원칙으로 한다.
- 정말 근거가 없으면 evidence=[] 로 두고, note에 "근거 부족"을 명시하고 score_0_5를 낮게 준다.
- 스니펫에 없는 내용을 인용하거나 단정하지 않는다.

출력 JSON 스키마 (이 구조만 반환):
{{
  "confidence": 0-100,
  "rubric": {{
    "platform": [
      {{"item":"가치 정합성","score_0_5":0-5,"evidence":["P1","P3"],"note":"..."}},
      {{"item":"정책 방향 일치","score_0_5":0-5,"evidence":["P2"],"note":"..."}},
      {{"item":"수단 적합성","score_0_5":0-5,"evidence":["P4"],"note":"..."}},
      {{"item":"일관성","score_0_5":0-5,"evidence":["P1"],"note":"..."}}
    ],
    "pledges": [
      {{"item":"중복/연계 가능","score_0_5":0-5,"evidence":["G2"],"note":"..."}},
      {{"item":"차별성","score_0_5":0-5,"evidence":["G1"],"note":"..."}},
      {{"item":"정책 언어 호환","score_0_5":0-5,"evidence":["G3"],"note":"..."}}
    ],
    "conflicts": [
      {{"item":"명시적 상충","score_0_5":0-5,"evidence":["P5"],"note":"..."}},
      {{"item":"잠재 리스크","score_0_5":0-5,"evidence":["P2","G4"],"note":"..."}}
    ]
  }},
  "improvements":[
    {{"title":"...","detail":"...","evidence":["P2"]}}
  ]
}}

중요:
- fit_score, breakdown은 너가 계산하지 않는다. 오직 rubric.score_0_5와 evidence, note, confidence, improvements만 작성한다.
- evidence에는 반드시 위에서 정의된 ID만 사용한다.
- JSON만 반환하고 다른 설명은 절대 붙이지 마라.
"""
    return prompt


def generate_report(
    user_pledge: str,
    platform_index: VectorIndex,
    pledge_index: VectorIndex,
    regional_index: VectorIndex,
    top_k_platform: int = 6,
    top_k_pledge: int = 6,
    top_k_regional: int = 8
) -> Dict:
    """
    검색 결과를 기반으로 LLM 리포트를 생성한다.
    
    Args:
        user_pledge: 사용자 공약 텍스트
        platform_index: 정강정책 인덱스
        pledge_index: 공약 인덱스
        regional_index: 지역별 공약 인덱스
        top_k_platform: 정강정책 검색 개수
        top_k_pledge: 공약 검색 개수
        top_k_regional: 지역별 공약 검색 개수
    
    Returns:
        리포트 JSON 딕셔너리
    """
    # 인덱스 유효성 검사
    if platform_index is None or pledge_index is None or regional_index is None:
        logger.error("인덱스가 None입니다.")
        return {
            "summary": {
                "fit_score": 0,
                "fit_verdict": "오류"
            },
            "platform": [],
            "pledges": [],
            "regional_similarity": [],
            "conflicts": [],
            "improvements": [],
            "error": "인덱스가 초기화되지 않았습니다."
        }
    
    # 검색
    hits = search_all_indexes(
        user_pledge,
        platform_index,
        pledge_index,
        regional_index,
        top_k_platform,
        top_k_pledge,
        top_k_regional,
    )

    platform_hits = hits["platform"]
    pledge_hits = hits["pledge"]
    regional_hits = hits["regional"]

    # evidence_map 생성
    evidence_map = build_evidence_map(platform_hits, pledge_hits, regional_hits)

    # 프롬프트 생성 (rubric 전용)
    prompt = build_rubric_prompt(user_pledge, evidence_map)

    # LLM 호출 (채점/근거정리만 수행, temperature=0)
    client = get_openai_client()
    try:
        response = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "너는 정책 정합성 채점관이다. "
                        "제공된 Evidence(ID로 식별되는 스니펫) 범위 밖 사실/인용은 절대 금지다. "
                        "각 rubric 항목에 대해 0~5점 score_0_5와 evidence ID 배열, note만 작성하라."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
        )

        result_text = response.choices[0].message.content
        raw = json.loads(result_text)

        logger.info("rubric 생성 완료")

        # rubric 구조 추출
        rubric = raw.get("rubric", {})
        confidence = int(raw.get("confidence", 0)) if isinstance(raw.get("confidence", 0), (int, float)) else 0
        improvements = raw.get("improvements", [])

        # 점수 산식 적용
        def avg_score(items: List[Dict]) -> float:
            if not items:
                return 0.0
            vals = []
            for it in items:
                try:
                    vals.append(float(it.get("score_0_5", 0)))
                except Exception:
                    vals.append(0.0)
            if not vals:
                return 0.0
            return sum(vals) / len(vals)

        platform_items = rubric.get("platform", [])
        pledge_items = rubric.get("pledges", [])
        conflict_items = rubric.get("conflicts", [])

        platform_avg_0_5 = avg_score(platform_items)
        pledge_avg_0_5 = avg_score(pledge_items)
        conflict_avg_0_5 = avg_score(conflict_items)

        platform_score = max(0.0, min(100.0, platform_avg_0_5 * 20.0))
        pledge_score = max(0.0, min(100.0, pledge_avg_0_5 * 20.0))
        conflict_penalty = max(0.0, min(100.0, conflict_avg_0_5 * 20.0))

        fit_score = 0.50 * platform_score + 0.35 * pledge_score - 0.15 * conflict_penalty
        fit_score = max(0.0, min(100.0, fit_score))

        # fit_verdict 간단 규칙 (원하면 나중에 조정 가능)
        if fit_score >= 80:
            fit_verdict = "부합"
        elif fit_score >= 60:
            fit_verdict = "부분부합"
        elif fit_score >= 40:
            fit_verdict = "보완필요"
        else:
            fit_verdict = "상충우려"

        report = {
            "fit_score": round(fit_score, 1),
            "confidence": max(0, min(100, confidence)),
            "breakdown": {
                "platform_score": round(platform_score, 1),
                "pledge_score": round(pledge_score, 1),
                "conflict_penalty": round(conflict_penalty, 1),
            },
            "rubric": rubric,
            "evidence_map": evidence_map,
            "improvements": improvements,
        }

        return report

    except Exception as e:
        logger.error(f"LLM 리포트 생성 실패: {e}", exc_info=True)
        # 기본 리포트 반환
        return {
            "fit_score": 0,
            "confidence": 0,
            "breakdown": {
                "platform_score": 0,
                "pledge_score": 0,
                "conflict_penalty": 0,
            },
            "rubric": {
                "platform": [],
                "pledges": [],
                "conflicts": [],
            },
            "evidence_map": {},
            "improvements": [],
            "error": str(e),
        }
