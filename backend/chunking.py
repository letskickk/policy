"""
PDF 텍스트를 청크로 분할하는 모듈.
"""
import re
from dataclasses import dataclass
from typing import List

from backend.config import CHUNK_OVERLAP, CHUNK_SIZE


@dataclass
class DocChunk:
    """문서 청크 데이터 클래스."""
    doc_id: str  # 예: "platform:정강정책/강령.pdf"
    source: str  # "platform" | "pledge" | "regional"
    path: str  # dir 기준 상대경로
    chunk_id: int  # 0부터
    text: str


def normalize_text(text: str) -> str:
    """
    텍스트 정규화: 공백/개행 과다를 최소화한다.
    - 연속 공백 축소
    - 연속 개행 3개 이상을 2개로
    """
    # 연속 공백을 하나로
    text = re.sub(r' +', ' ', text)
    # 연속 개행 3개 이상을 2개로
    text = re.sub(r'\n{3,}', '\n\n', text)
    # 앞뒤 공백 제거
    return text.strip()


def split_into_chunks(text: str, chunk_size: int = CHUNK_SIZE, chunk_overlap: int = CHUNK_OVERLAP) -> List[str]:
    """
    텍스트를 청크로 분할한다.
    
    Args:
        text: 분할할 텍스트
        chunk_size: 청크 크기 (문자 수)
        chunk_overlap: 청크 간 겹치는 문자 수
    
    Returns:
        청크 리스트
    """
    if len(text) <= chunk_size:
        return [text]
    
    chunks = []
    start = 0
    
    while start < len(text):
        end = start + chunk_size
        
        if end >= len(text):
            # 마지막 청크
            chunks.append(text[start:])
            break
        
        # 문장 경계에서 자르기 시도 (개행 또는 마침표)
        # overlap 범위 내에서 가장 가까운 문장 끝 찾기
        search_start = max(start, end - chunk_overlap)
        last_newline = text.rfind('\n', search_start, end)
        last_period = text.rfind('.', search_start, end)
        
        # 개행이 있으면 개행에서 자르기, 없으면 마침표에서
        if last_newline > search_start:
            end = last_newline + 1
        elif last_period > search_start:
            end = last_period + 1
        
        chunks.append(text[start:end])
        start = end - chunk_overlap  # overlap만큼 뒤로 이동
    
    return chunks


def build_chunks(
    text: str,
    doc_id: str,
    source: str,
    path: str,
    max_chunks: int = None
) -> List[DocChunk]:
    """
    텍스트를 정규화하고 청크로 분할하여 DocChunk 리스트를 만든다.
    
    Args:
        text: 원본 텍스트
        doc_id: 문서 ID (예: "platform:정강정책/강령.pdf")
        source: 소스 타입 ("platform" | "pledge" | "regional")
        path: 상대 경로
        max_chunks: 파일당 최대 청크 수 (None이면 제한 없음)
    
    Returns:
        DocChunk 리스트
    """
    # 정규화
    normalized = normalize_text(text)
    
    # 텍스트가 너무 짧으면 빈 리스트 반환
    if len(normalized.strip()) < 10:
        return []
    
    # 청크로 분할
    chunk_texts = split_into_chunks(normalized, CHUNK_SIZE, CHUNK_OVERLAP)
    
    # 최대 청크 수 제한
    if max_chunks and len(chunk_texts) > max_chunks:
        chunk_texts = chunk_texts[:max_chunks]
    
    # DocChunk 리스트 생성
    chunks = []
    for i, chunk_text in enumerate(chunk_texts):
        chunk = DocChunk(
            doc_id=doc_id,
            source=source,
            path=path,
            chunk_id=i,
            text=chunk_text
        )
        chunks.append(chunk)
    
    return chunks
