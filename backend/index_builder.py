"""
인덱스 빌드 및 캐시 관리 모듈.
멀티워커 환경에서 build.lock으로 동시 빌드/저장 레이스를 막고, 원자적 저장으로 깨진 캐시 방지.
"""
import hashlib
import logging
import os
import pickle
from pathlib import Path
from typing import Dict, List, Optional

from filelock import FileLock

from backend.chunking import DocChunk
from backend.config import (
    BUILD_CARDS,
    EMBEDDING_BATCH_SIZE,
    EMBEDDING_DIMENSION,
    INDEX_CACHE_DIR,
    MAX_CHUNKS_PER_FILE,
    PDF_DIR,
    REBUILD_INDEX,
    _nfc,
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
    폴더 내 모든 .pdf, .txt 파일의 해시를 계산한다.
    
    Args:
        folder_path: 폴더 경로
    
    Returns:
        {파일경로: 해시} 딕셔너리
    """
    hashes = {}
    try:
        from backend.pdf_loader import _iter_doc_files
        for doc_path in _iter_doc_files(folder_path):
            rel_path = str(doc_path.relative_to(folder_path))
            hashes[rel_path] = compute_file_hash(doc_path)
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
    """파일 해시를 캐시에 원자적으로 저장한다."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    hash_file = cache_dir / f"{cache_name}_hashes.pkl"
    tmp_file = cache_dir / f"{cache_name}_hashes.pkl.tmp"
    try:
        with open(tmp_file, 'wb') as f:
            pickle.dump(hashes, f)
        os.replace(tmp_file, hash_file)
    except Exception as e:
        logger.error(f"해시 캐시 저장 실패 ({hash_file}): {e}")
        if tmp_file.exists():
            tmp_file.unlink(missing_ok=True)


def _build_index_inner(
    cache_dir: Path,
    cache_name: str,
    folder_name: str,
    source_type: str,
    load_chunks_func,
    force_rebuild: bool = False,
) -> VectorIndex:
    """락 밖에서 호출하지 말 것. 빌드/로드 로직만 수행."""
    index_path = cache_dir / f"{cache_name}.faiss"
    meta_path = cache_dir / f"{cache_name}_meta.pkl"
    index_tmp = cache_dir / f"{cache_name}.faiss.tmp"
    meta_tmp = cache_dir / f"{cache_name}_meta.pkl.tmp"

    if not force_rebuild and index_path.exists() and meta_path.exists():
        folder_path = PDF_DIR / _nfc(folder_name)
        if folder_path.exists():
            current_hashes = compute_folder_hash(folder_path)
            cached_hashes = load_cache_hashes(cache_dir, cache_name)
            if cached_hashes == current_hashes:
                logger.info(f"{cache_name} 인덱스 캐시 히트, 로드 중...")
                try:
                    return VectorIndex.load(str(index_path), str(meta_path))
                except Exception as e:
                    logger.warning(f"캐시 로드 실패, 재빌드: {e}")
            else:
                logger.info(f"{cache_name} 폴더 변경 감지, 재빌드 필요")

    logger.info(f"{cache_name} 인덱스 빌드 시작...")
    chunks = load_chunks_func()
    if not chunks:
        logger.warning(f"{cache_name} 폴더에 청크가 없습니다.")
        return VectorIndex(dimension=EMBEDDING_DIMENSION, use_cosine=True)

    logger.info(f"{cache_name} 청크 로드 완료: {len(chunks)}개")
    texts = [chunk.text for chunk in chunks]
    embeddings = embed_texts(texts, batch_size=EMBEDDING_BATCH_SIZE)
    if len(embeddings) != len(chunks):
        logger.error(f"임베딩 수({len(embeddings)})와 청크 수({len(chunks)})가 일치하지 않습니다.")
        return VectorIndex(dimension=EMBEDDING_DIMENSION, use_cosine=True)

    dimension = len(embeddings[0]) if embeddings else EMBEDDING_DIMENSION
    index = VectorIndex(dimension=dimension, use_cosine=True)
    index.add(embeddings, chunks)
    try:
        index.save(str(index_tmp), str(meta_tmp))
        os.replace(index_tmp, index_path)
        os.replace(meta_tmp, meta_path)
        folder_path = PDF_DIR / _nfc(folder_name)
        if folder_path.exists():
            save_cache_hashes(cache_dir, cache_name, compute_folder_hash(folder_path))
        logger.info(f"{cache_name} 인덱스 빌드 및 저장 완료: {len(chunks)}개 청크")
        if cache_name == "pledge":
            logger.info(f"pledge_index: {len(chunks)} vectors")
    except Exception as e:
        logger.error(f"인덱스 저장 실패: {e}")
        for p in (index_tmp, meta_tmp):
            if p.exists():
                p.unlink(missing_ok=True)
    return index


def build_index(
    cache_name: str,
    folder_name: str,
    source_type: str,
    load_chunks_func,
    force_rebuild: bool = False,
) -> VectorIndex:
    """인덱스 빌드/로드. build.lock으로 멀티워커 레이스 방지."""
    cache_dir = Path(INDEX_CACHE_DIR)
    cache_dir.mkdir(parents=True, exist_ok=True)
    lock_path = cache_dir / "build.lock"
    with FileLock(str(lock_path), timeout=600):
        return _build_index_inner(cache_dir, cache_name, folder_name, source_type, load_chunks_func, force_rebuild)


def build_all_indexes(force_rebuild: bool = False) -> Dict[str, VectorIndex]:
    """모든 인덱스 빌드. 전체를 build.lock으로 감싸 레이스 방지."""
    logger.info("모든 인덱스 빌드 시작...")
    effective_rebuild = force_rebuild or REBUILD_INDEX
    cache_dir = Path(INDEX_CACHE_DIR)
    cache_dir.mkdir(parents=True, exist_ok=True)
    lock_path = cache_dir / "build.lock"

    with FileLock(str(lock_path), timeout=600):
        if REBUILD_INDEX:
            try:
                logger.info("REBUILD_INDEX=1 감지, 기존 인덱스 캐시 삭제 중...")
                for pattern in ("*.faiss", "*_meta.pkl", "*_hashes.pkl", "*.tmp"):
                    for p in cache_dir.glob(pattern):
                        logger.info(f"캐시 삭제: {p}")
                        p.unlink(missing_ok=True)
            except Exception as e:
                logger.error(f"인덱스 캐시 삭제 실패: {e}")

        indexes = {}
        indexes["platform"] = _build_index_inner(
            cache_dir, "platform", "정강정책", "platform", load_platform_chunks, effective_rebuild
        )
        indexes["pledge"] = _build_index_inner(
            cache_dir, "pledge", "공약", "pledge", load_pledge_chunks, effective_rebuild
        )
        indexes["regional"] = _build_index_inner(
            cache_dir, "regional", "지역별 공약", "regional", load_regional_chunks, effective_rebuild
        )
    
    total_chunks = sum(idx.size() for idx in indexes.values())
    logger.info(f"모든 인덱스 빌드 완료: 총 {total_chunks}개 청크")

    if BUILD_CARDS and indexes.get("platform") and indexes.get("pledge"):
        try:
            from backend.cards import build_and_save_platform_cards, build_and_save_pledge_cards
            build_and_save_platform_cards(indexes["platform"].chunks)
            build_and_save_pledge_cards(indexes["pledge"].chunks)
        except Exception as e:
            logger.warning(f"카드 생성 스킵: {e}")

    return indexes
