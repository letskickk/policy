"""
FAISS 기반 벡터 인덱스 모듈.
"""
import logging
import sys
from pathlib import Path
from typing import List, Tuple

import faiss
import numpy as np

from backend.chunking import DocChunk
from backend.config import EMBEDDING_DIMENSION
from backend.embeddings import normalize_embedding

logger = logging.getLogger(__name__)


def _faiss_path(path: str) -> str:
    """FAISS용 경로. Windows에서 한글 경로 시 8.3 단축 경로로 변환."""
    p = Path(path).resolve()
    if sys.platform != "win32":
        return str(p)
    try:
        import ctypes
        from ctypes import wintypes
        buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        n = ctypes.windll.kernel32.GetShortPathNameW(str(p.parent), buf, wintypes.MAX_PATH)
        if n > 0:
            short_dir = buf.value
            return str(Path(short_dir) / p.name)
    except Exception:
        pass
    return str(p)


class VectorIndex:
    """FAISS 기반 벡터 인덱스."""
    
    def __init__(self, dimension: int = None, use_cosine: bool = True):
        """
        벡터 인덱스를 초기화한다.
        
        Args:
            dimension: 임베딩 차원 수 (None이면 config.EMBEDDING_DIMENSION)
            use_cosine: True면 코사인 유사도(IndexFlatIP), False면 L2 거리(IndexFlatL2)
        """
        self.dimension = dimension if dimension is not None else EMBEDDING_DIMENSION
        self.use_cosine = use_cosine
        
        dim = self.dimension
        if use_cosine:
            # 내적 기반 (코사인 유사도용, 임베딩은 정규화되어야 함)
            self.index = faiss.IndexFlatIP(dim)
        else:
            # L2 거리 기반
            self.index = faiss.IndexFlatL2(dim)
        
        self.chunks: List[DocChunk] = []
        logger.info(f"벡터 인덱스 초기화 완료 (차원: {self.dimension}, 코사인: {use_cosine})")
    
    def add(self, embeddings: List[List[float]], chunks: List[DocChunk]):
        """
        임베딩과 청크를 인덱스에 추가한다.
        
        Args:
            embeddings: 임베딩 벡터 리스트
            chunks: DocChunk 리스트 (embeddings와 길이가 같아야 함)
        """
        if len(embeddings) != len(chunks):
            raise ValueError(f"임베딩 수({len(embeddings)})와 청크 수({len(chunks)})가 일치하지 않습니다.")
        
        if not embeddings:
            return
        
        # 정규화 (코사인 유사도 사용 시)
        if self.use_cosine:
            normalized_embeddings = [normalize_embedding(emb) for emb in embeddings]
        else:
            normalized_embeddings = embeddings
        
        # numpy 배열로 변환
        embeddings_array = np.array(normalized_embeddings, dtype=np.float32)
        
        # FAISS 인덱스에 추가
        self.index.add(embeddings_array)
        
        # 청크 저장
        self.chunks.extend(chunks)
        
        logger.info(f"인덱스에 {len(chunks)}개 청크 추가 완료 (총 {len(self.chunks)}개)")
    
    def search(self, query_embedding: List[float], k: int = 5) -> List[Tuple[DocChunk, float]]:
        """
        쿼리 임베딩으로 유사한 청크를 검색한다.
        
        Args:
            query_embedding: 쿼리 임베딩 벡터
            k: 반환할 최대 개수
        
        Returns:
            (DocChunk, score) 튜플 리스트 (score는 높을수록 유사)
        """
        if len(self.chunks) == 0:
            return []
        
        # 정규화 (코사인 유사도 사용 시)
        if self.use_cosine:
            query_embedding = normalize_embedding(query_embedding)
        
        # numpy 배열로 변환
        query_array = np.array([query_embedding], dtype=np.float32)
        
        # 검색
        k = min(k, len(self.chunks))
        scores, indices = self.index.search(query_array, k)
        
        # 결과 구성
        results = []
        for i, idx in enumerate(indices[0]):
            if idx < len(self.chunks):
                chunk = self.chunks[idx]
                score = float(scores[0][i])
                results.append((chunk, score))
        
        return results
    
    def size(self) -> int:
        """인덱스에 저장된 청크 수를 반환한다."""
        return len(self.chunks)
    
    def save(self, index_path: str, meta_path: str):
        """
        인덱스와 메타데이터를 파일에 저장한다.
        
        Args:
            index_path: FAISS 인덱스 파일 경로
            meta_path: 청크 메타데이터 파일 경로 (pickle)
        """
        import pickle

        # 디렉터리 확보 (한글 경로 대비)
        Path(index_path).parent.mkdir(parents=True, exist_ok=True)
        faiss_path = _faiss_path(index_path)

        # FAISS 인덱스 저장 (Windows 한글 경로 시 단축 경로 사용)
        faiss.write_index(self.index, faiss_path)

        # 메타데이터 저장 (pickle은 한글 경로 OK)
        with open(meta_path, 'wb') as f:
            pickle.dump(self.chunks, f)

        logger.info(f"인덱스 저장 완료: {index_path}, {meta_path}")
    
    @classmethod
    def load(cls, index_path: str, meta_path: str, dimension: int = None, use_cosine: bool = True):
        """
        파일에서 인덱스와 메타데이터를 로드한다.
        
        Args:
            index_path: FAISS 인덱스 파일 경로
            meta_path: 청크 메타데이터 파일 경로 (pickle)
            dimension: 임베딩 차원 수
            use_cosine: 코사인 유사도 사용 여부
        
        Returns:
            VectorIndex 인스턴스
        """
        import pickle

        faiss_path = _faiss_path(index_path)
        # FAISS 인덱스 로드 (Windows 한글 경로 시 단축 경로 사용)
        index = faiss.read_index(faiss_path)
        
        # 메타데이터 로드
        with open(meta_path, 'rb') as f:
            chunks = pickle.load(f)
        
        dim = dimension if dimension is not None else EMBEDDING_DIMENSION
        # 로드한 인덱스 차원과 불일치 시 에러
        if index.d != dim:
            raise ValueError(
                f"임베딩 차원 불일치: 저장된 인덱스는 {index.d}차원, 설정은 {dim}차원. "
                f"EMBEDDING_MODEL 또는 EMBEDDING_DIMENSION을 확인하세요."
            )
        
        # 인스턴스 생성
        instance = cls(dimension=dim, use_cosine=use_cosine)
        instance.index = index
        instance.chunks = chunks
        
        logger.info(f"인덱스 로드 완료: {index_path} ({len(chunks)}개 청크)")
        return instance
