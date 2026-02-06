"""
검색 결과를 기반으로 LLM 리포트를 생성하는 모듈.
"""
import json
import logging
from typing import Dict, List, Tuple

from openai import OpenAI

from backend.chunking import DocChunk
from backend.config import CHAT_MODEL, OPENAI_API_KEY
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
    top_k_regional: int = 8
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
        "regional": regional_hits
    }


def build_report_prompt(
    user_pledge: str,
    platform_hits: List[Tuple[DocChunk, float]],
    pledge_hits: List[Tuple[DocChunk, float]],
    regional_hits: List[Tuple[DocChunk, float]]
) -> str:
    """
    LLM 리포트 생성을 위한 프롬프트를 만든다.
    
    Args:
        user_pledge: 사용자 공약 텍스트
        platform_hits: 정강정책 검색 결과
        pledge_hits: 공약 검색 결과
        regional_hits: 지역별 공약 검색 결과
    
    Returns:
        프롬프트 문자열
    """
    # 정강정책 근거
    platform_snippets = []
    for chunk, score in platform_hits:
        quote = truncate_quote(chunk.text)
        platform_snippets.append(f"- [{chunk.path}, 청크 {chunk.chunk_id}, 유사도: {score:.3f}]\n  {quote}")
    
    # 공약 근거
    pledge_snippets = []
    for chunk, score in pledge_hits:
        quote = truncate_quote(chunk.text)
        pledge_snippets.append(f"- [{chunk.path}, 청크 {chunk.chunk_id}, 유사도: {score:.3f}]\n  {quote}")
    
    # 지역별 공약 유사성
    regional_snippets = []
    for chunk, score in regional_hits:
        quote = truncate_quote(chunk.text)
        regional_snippets.append(f"- [{chunk.path}, 청크 {chunk.chunk_id}, 유사도: {score:.3f}]\n  {quote}")
    
    prompt = f"""당 정책 전문가로서 출마자 공약을 분석하고 리포트를 생성하세요.

[출마자 공약]
{user_pledge}

[정강정책 근거 스니펫]
{chr(10).join(platform_snippets) if platform_snippets else "없음"}

[우리당 공약 근거 스니펫]
{chr(10).join(pledge_snippets) if pledge_snippets else "없음"}

[타지역 공약 유사성 스니펫]
{chr(10).join(regional_snippets) if regional_snippets else "없음"}

위 근거 스니펫을 기반으로 다음 JSON 형식으로 리포트를 생성하세요. 반드시 스니펫을 인용하여 근거를 제시해야 합니다.

{{
  "summary": {{
    "fit_score": 0~100,
    "fit_verdict": "부합/부분부합/보완필요/상충우려"
  }},
  "platform": [
    {{"quote": "인용문 (최대 250자)", "source_path": "파일경로", "chunk_id": 번호, "reason": "근거 설명"}}
  ],
  "pledges": [
    {{"quote": "인용문 (최대 250자)", "source_path": "파일경로", "chunk_id": 번호, "reason": "근거 설명"}}
  ],
  "regional_similarity": [
    {{"source_path": "파일경로", "chunk_id": 번호, "similarity_note": "유사성 설명"}}
  ],
  "conflicts": [
    {{"issue": "상충 이슈", "why": "이유", "suggest": "제안"}}
  ],
  "improvements": [
    {{"title": "개선 제목", "detail": "상세 내용"}}
  ]
}}

중요:
- quote는 반드시 위 스니펫에서 인용해야 합니다.
- 모든 근거는 스니펫 기반으로 작성해야 합니다.
- 추측이나 일반적인 지식은 사용하지 마세요.
- JSON만 반환하고 다른 설명은 추가하지 마세요."""
    
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
        top_k_regional
    )
    
    # 프롬프트 생성
    prompt = build_report_prompt(
        user_pledge,
        hits["platform"],
        hits["pledge"],
        hits["regional"]
    )
    
    # LLM 호출
    client = get_openai_client()
    try:
        response = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "당 정책 전문가로서 근거 기반 리포트를 생성합니다. 반드시 제공된 스니펫을 인용하여 근거를 제시해야 합니다."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.3,
            response_format={"type": "json_object"}
        )
        
        result_text = response.choices[0].message.content
        report = json.loads(result_text)
        
        logger.info("리포트 생성 완료")
        return report
    
    except Exception as e:
        logger.error(f"LLM 리포트 생성 실패: {e}", exc_info=True)
        # 기본 리포트 반환
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
            "error": str(e)
        }
