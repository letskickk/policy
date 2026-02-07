"""
OpenAI 임베딩 생성 모듈.
"""
import logging
from typing import List

from openai import OpenAI

from backend.config import EMBEDDING_BATCH_SIZE, EMBEDDING_DIMENSION, EMBEDDING_MODEL, OPENAI_API_KEY

logger = logging.getLogger(__name__)

# OpenAI 클라이언트 (싱글톤)
_client = None


def get_openai_client() -> OpenAI:
    """OpenAI 클라이언트를 반환한다 (싱글톤)."""
    global _client
    if _client is None:
        if not OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY가 설정되지 않았습니다.")
        _client = OpenAI(api_key=OPENAI_API_KEY)
    return _client


def embed_texts(texts: List[str], batch_size: int = EMBEDDING_BATCH_SIZE) -> List[List[float]]:
    """
    텍스트 리스트를 임베딩으로 변환한다.
    
    Args:
        texts: 임베딩할 텍스트 리스트
        batch_size: 배치 크기
    
    Returns:
        임베딩 벡터 리스트 (각 벡터는 float 리스트)
    """
    if not texts:
        return []
    
    client = get_openai_client()
    embeddings = []
    
    # 배치 단위로 처리
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        try:
            response = client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=batch
            )
            batch_embeddings = [item.embedding for item in response.data]
            embeddings.extend(batch_embeddings)
            logger.debug(f"임베딩 생성: {len(batch)}개 텍스트 처리 완료 ({i+1}/{len(texts)})")
        except Exception as e:
            logger.error(f"임베딩 생성 실패 (배치 {i//batch_size + 1}): {e}")
            # 실패한 배치는 빈 벡터로 채움 (나중에 필터링)
            embeddings.extend([[0.0] * EMBEDDING_DIMENSION for _ in batch])
    
    return embeddings


def normalize_embedding(embedding: List[float]) -> List[float]:
    """
    임베딩 벡터를 L2 정규화한다 (코사인 유사도용).
    
    Args:
        embedding: 임베딩 벡터
    
    Returns:
        정규화된 임베딩 벡터
    """
    import numpy as np
    vec = np.array(embedding, dtype=np.float32)
    norm = np.linalg.norm(vec)
    if norm > 0:
        return (vec / norm).tolist()
    return embedding
