"""
인덱스 빌드 및 캐시 관리 모듈.
"""
import hashlib
import logging
import pickle
from pathlib import Path
from typing import Dict, List, Optional

from backend.chunking import DocChunk
from backend.config import (
    EMBEDDING_BATCH_SIZE,
    INDEX_CACHE_DIR,
    MAX_CHUNKS_PER_FILE,
    PDF_DIR,
)
from backend.embeddings import embed_texts
try:
    from backend.pdf_loader_chunks import (
        load_pledge_chunks,
        load_platform_chunks,
        load_regional_chunks,
    )
except ImportError:
    # pdf_loader_chunks가 없으면 빈 함수로 대체
    def load_platform_chunks():
        return []
    def load_pledge_chunks():
        return []
    def load_regional_chunks():
        return []
from backend.vector_index import VectorIndex

logger = logging.getLogger(__name__)


def compute_file_hash(file_path: Path) -> str:
    """
    파일의 해시를 계산한다 (경로 + mtime + size 기반).
    
    Args:
        file_path: 파일 경로
    
    Returns:
        해시 문자열
    """
    try:
        stat = file_path.stat()
        # 경로 + 수정시간 + 크기로 해시 생성
        content = f"{file_path}:{stat.st_mtime}:{stat.st_size}"
        return hashlib.md5(content.encode()).hexdigest()
    except Exception:
        return ""


def compute_folder_hash(folder_path: Path) -> Dict[str, str]:
    """
    폴더 내 모든 PDF 파일의 해시를 계산한다.
    
    Args:
        folder_path: 폴더 경로
    
    Returns:
        {파일경로: 해시} 딕셔너리
    """
    hashes = {}
    try:
        for pdf_path in folder_path.rglob("*.pdf"):
            rel_path = str(pdf_path.relative_to(folder_path))
            hashes[rel_path] = compute_file_hash(pdf_path)
    except Exception as e:
        logger.error(f"폴더 해시 계산 실패 ({folder_path}): {e}")
    return hashes


def load_cache_hashes(cache_dir: Path, cache_name: str) -> Optional[Dict[str, str]]:
    """
    캐시된 파일 해시를 로드한다.
    
    Args:
        cache_dir: 캐시 디렉토리
        cache_name: 캐시 이름 ("platform", "pledge", "regional")
    
    Returns:
        {파일경로: 해시} 딕셔너리 또는 None
    """
    hash_file = cache_dir / f"{cache_name}_hashes.pkl"
    if hash_file.exists():
        try:
            with open(hash_file, 'rb') as f:
                return pickle.load(f)
        except Exception as e:
            logger.warning(f"해시 캐시 로드 실패 ({hash_file}): {e}")
    return None


def save_cache_hashes(cache_dir: Path, cache_name: str, hashes: Dict[str, str]):
    """
    파일 해시를 캐시에 저장한다.
    
    Args:
        cache_dir: 캐시 디렉토리
        cache_name: 캐시 이름
        hashes: {파일경로: 해시} 딕셔너리
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    hash_file = cache_dir / f"{cache_name}_hashes.pkl"
    try:
        with open(hash_file, 'wb') as f:
            pickle.dump(hashes, f)
    except Exception as e:
        logger.error(f"해시 캐시 저장 실패 ({hash_file}): {e}")


def build_index(
    cache_name: str,
    folder_name: str,
    source_type: str,
    load_chunks_func,
    force_rebuild: bool = False
) -> VectorIndex:
    """
    인덱스를 빌드하거나 캐시에서 로드한다.
    
    Args:
        cache_name: 캐시 이름 ("platform", "pledge", "regional")
        folder_name: 폴더명 ("정강정책", "공약", "지역별 공약")
        source_type: 소스 타입 ("platform", "pledge", "regional")
        load_chunks_func: 청크를 로드하는 함수
        force_rebuild: True면 강제로 재빌드
    
    Returns:
        VectorIndex 인스턴스
    """
    cache_dir = INDEX_CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    
    index_path = cache_dir / f"{cache_name}.faiss"
    meta_path = cache_dir / f"{cache_name}_meta.pkl"
    
    # 캐시 확인
    if not force_rebuild and index_path.exists() and meta_path.exists():
        # 현재 폴더 해시 계산
        folder_path = PDF_DIR / folder_name
        if folder_path.exists():
            current_hashes = compute_folder_hash(folder_path)
            cached_hashes = load_cache_hashes(cache_dir, cache_name)
            
            # 해시 비교
            if cached_hashes == current_hashes:
                logger.info(f"{cache_name} 인덱스 캐시 히트, 로드 중...")
                try:
                    return VectorIndex.load(str(index_path), str(meta_path))
                except Exception as e:
                    logger.warning(f"캐시 로드 실패, 재빌드: {e}")
            else:
                logger.info(f"{cache_name} 폴더 변경 감지, 재빌드 필요")
    
    # 인덱스 빌드
    logger.info(f"{cache_name} 인덱스 빌드 시작...")
    
    # 청크 로드
    chunks = load_chunks_func()
    
    if not chunks:
        logger.warning(f"{cache_name} 폴더에 청크가 없습니다.")
        # 빈 인덱스 반환 (차원은 임베딩 모델에 맞춰 설정)
        return VectorIndex(dimension=3072, use_cosine=True)
    
    logger.info(f"{cache_name} 청크 로드 완료: {len(chunks)}개")
    
    # 임베딩 생성
    texts = [chunk.text for chunk in chunks]
    logger.info(f"{cache_name} 임베딩 생성 시작 ({len(texts)}개 텍스트)...")
    
    embeddings = embed_texts(texts, batch_size=EMBEDDING_BATCH_SIZE)
    
    if len(embeddings) != len(chunks):
        logger.error(f"임베딩 수({len(embeddings)})와 청크 수({len(chunks)})가 일치하지 않습니다.")
        return VectorIndex()
    
    # 인덱스 생성 및 추가
    dimension = len(embeddings[0]) if embeddings else 3072
    index = VectorIndex(dimension=dimension, use_cosine=True)
    index.add(embeddings, chunks)
    
    # 인덱스 저장
    try:
        index.save(str(index_path), str(meta_path))
        
        # 해시 저장
        folder_path = PDF_DIR / folder_name
        if folder_path.exists():
            hashes = compute_folder_hash(folder_path)
            save_cache_hashes(cache_dir, cache_name, hashes)
        
        logger.info(f"{cache_name} 인덱스 빌드 및 저장 완료: {len(chunks)}개 청크")
    except Exception as e:
        logger.error(f"인덱스 저장 실패: {e}")
    
    return index


def build_all_indexes(force_rebuild: bool = False) -> Dict[str, VectorIndex]:
    """
    모든 인덱스를 빌드한다.
    
    Args:
        force_rebuild: True면 강제로 재빌드
    
    Returns:
        {인덱스명: VectorIndex} 딕셔너리
    """
    logger.info("모든 인덱스 빌드 시작...")
    
    indexes = {}
    
    # 정강정책 인덱스
    indexes["platform"] = build_index(
        cache_name="platform",
        folder_name="정강정책",
        source_type="platform",
        load_chunks_func=load_platform_chunks,
        force_rebuild=force_rebuild
    )
    
    # 공약 인덱스
    indexes["pledge"] = build_index(
        cache_name="pledge",
        folder_name="공약",
        source_type="pledge",
        load_chunks_func=load_pledge_chunks,
        force_rebuild=force_rebuild
    )
    
    # 지역별 공약 인덱스
    indexes["regional"] = build_index(
        cache_name="regional",
        folder_name="지역별 공약",
        source_type="regional",
        load_chunks_func=load_regional_chunks,
        force_rebuild=force_rebuild
    )
    
    total_chunks = sum(idx.size() for idx in indexes.values())
    logger.info(f"모든 인덱스 빌드 완료: 총 {total_chunks}개 청크")
    
    return indexes
