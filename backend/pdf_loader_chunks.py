"""
PDF를 청크 리스트로 로드하는 모듈 (벡터 검색용).
"""
import logging
from pathlib import Path
from typing import List

from backend.chunking import DocChunk, build_chunks
from backend.config import MAX_CHUNKS_PER_FILE, PDF_DIR
from backend.pdf_loader import extract_text_from_pdf

logger = logging.getLogger(__name__)


def load_pdf_chunks(folder_name: str, source_type: str) -> List[DocChunk]:
    """
    지정된 폴더의 모든 PDF를 청크로 분할하여 반환한다.
    
    Args:
        folder_name: 폴더명 ("정강정책", "공약", "지역별 공약")
        source_type: 소스 타입 ("platform", "pledge", "regional")
    
    Returns:
        DocChunk 리스트
    """
    pdf_dir_str = str(PDF_DIR.resolve())
    pdf_dir = Path(pdf_dir_str)
    
    if not pdf_dir.exists():
        logger.warning(f"PDF_DIR이 존재하지 않음: {pdf_dir}")
        return []
    
    target_dir = pdf_dir / folder_name
    
    if not target_dir.exists():
        logger.warning(f"{folder_name} 폴더가 존재하지 않음: {target_dir}")
        return []
    
    logger.info(f"{folder_name} 폴더에서 PDF 청크 로드 시작: {target_dir}")
    
    all_chunks = []
    
    # 폴더 안의 모든 PDF 파일 찾기 (재귀적)
    try:
        pdf_files = list(target_dir.rglob("*.pdf"))
        logger.info(f"{folder_name} 폴더에서 {len(pdf_files)}개 PDF 파일 발견")
    except Exception as e:
        logger.error(f"PDF 파일 검색 실패 ({target_dir}): {e}")
        return []
    
    for pdf_path in sorted(pdf_files):
        try:
            # 상대 경로 계산
            rel_path = str(pdf_path.relative_to(target_dir))
            doc_id = f"{source_type}:{folder_name}/{rel_path}"
            
            # PDF 텍스트 추출
            text = extract_text_from_pdf(pdf_path)
            text_len = len(text.strip()) if text else 0
            
            if not text or text_len < 10:
                logger.warning(f"PDF 텍스트가 비어있거나 너무 짧음: {rel_path} (길이: {text_len}자)")
                continue
            
            # 청크로 분할
            chunks = build_chunks(
                text=text,
                doc_id=doc_id,
                source=source_type,
                path=rel_path,
                max_chunks=MAX_CHUNKS_PER_FILE
            )
            
            if chunks:
                all_chunks.extend(chunks)
                logger.info(f"PDF 청크 생성 완료: {rel_path} ({len(chunks)}개 청크)")
            else:
                logger.warning(f"PDF에서 청크 생성 실패: {rel_path}")
        
        except Exception as e:
            logger.error(f"PDF 읽기 실패: {pdf_path} - {e}", exc_info=True)
            continue
    
    logger.info(f"{folder_name} 폴더 청크 로드 완료: {len(all_chunks)}개 청크")
    return all_chunks


def load_platform_chunks() -> List[DocChunk]:
    """정강정책 폴더의 모든 PDF를 청크로 로드한다."""
    return load_pdf_chunks("정강정책", "platform")


def load_pledge_chunks() -> List[DocChunk]:
    """공약 폴더의 모든 PDF를 청크로 로드한다."""
    return load_pdf_chunks("공약", "pledge")


def load_regional_chunks() -> List[DocChunk]:
    """지역별 공약 폴더의 모든 PDF를 청크로 로드한다."""
    return load_pdf_chunks("지역별 공약", "regional")
