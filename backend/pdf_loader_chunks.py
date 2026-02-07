"""
PDF를 청크 리스트로 로드하는 모듈 (벡터 검색용).
전체 컨텍스트(Global Context) 로드용 함수도 제공한다.
"""
import logging
from pathlib import Path
from typing import List

from backend.chunking import DocChunk, build_chunks
from backend.config import PDF_DIR, _nfc
from backend.pdf_loader import _iter_doc_files, iter_pdf_texts

logger = logging.getLogger(__name__)


# ---------- 전체 컨텍스트 로드 (LLM이 전체 문서를 한 번에 읽고 판단할 때 사용) ----------

def load_folder_full_text(folder_name: str) -> str:
    """
    지정 폴더의 모든 PDF를 청크로 나누지 않고, 전체 텍스트를 하나의 문자열로 합쳐 반환한다.
    정강정책·공약처럼 LLM이 전체 맥락(Global Context)을 보고 의미적 적합도를 판단할 때 사용한다.
    파일 간 구분은 "=== [파일명] ===" 헤더로 둔다.
    """
    target_dir = PDF_DIR / _nfc(folder_name)
    if not target_dir.exists():
        logger.warning(f"{folder_name} 폴더가 존재하지 않음: {target_dir}")
        return ""

    parts: List[str] = []
    total_chars = 0
    for rel_path_str, text in iter_pdf_texts(target_dir):
        block = f"=== [{rel_path_str}] ===\n{text}"
        parts.append(block)
        total_chars += len(block)

    result = "\n\n".join(parts) if parts else ""
    logger.info(f"[FULL-TEXT] {folder_name}: {len(parts)}개 파일, 총 {total_chars}자")
    return result


def get_platform_full_text() -> str:
    """정강정책 폴더의 모든 PDF 텍스트를 하나의 문자열로 반환한다 (전체 컨텍스트용)."""
    return load_folder_full_text("정강정책")


def get_pledge_full_text() -> str:
    """공약 폴더의 모든 PDF 텍스트를 하나의 문자열로 반환한다 (전체 컨텍스트용)."""
    return load_folder_full_text("공약")


# ---------- 벡터 인덱싱용 청크 로드 (RAG 검색용, 기존 유지) ----------


def load_pdf_chunks(folder_name: str, source_type: str) -> List[DocChunk]:
    """
    지정된 폴더의 모든 PDF를 limit 없이 iter_pdf_texts로 읽어 청크로 분할한다.
    인덱싱 전용: 누적 한도/파일당 청크 상한 없이 전부 반환한다.
    """
    pdf_dir_str = str(PDF_DIR.resolve())
    pdf_dir = Path(pdf_dir_str)

    if not pdf_dir.exists():
        logger.warning(f"PDF_DIR이 존재하지 않음: {pdf_dir}")
        return []

    target_dir = pdf_dir / _nfc(folder_name)
    if not target_dir.exists():
        logger.warning(f"{folder_name} 폴더가 존재하지 않음: {target_dir}")
        return []

    expected_doc_files = list(_iter_doc_files(target_dir))
    expected_count = len(expected_doc_files)
    logger.info(f"{folder_name} 폴더에서 문서 청크 로드 시작 (iter_pdf_texts): {target_dir}, expected={expected_count}")

    yielded_count = 0
    all_chunks: List[DocChunk] = []

    for rel_path_str, text in iter_pdf_texts(target_dir):
        yielded_count += 1
        doc_id = f"{source_type}:{folder_name}/{rel_path_str}"
        # 인덱싱용: max_chunks=None 으로 전체 청크 반환
        chunks = build_chunks(
            text=text,
            doc_id=doc_id,
            source=source_type,
            path=rel_path_str,
            max_chunks=None,
        )
        chars = len(text)
        if chunks:
            all_chunks.extend(chunks)
            logger.info(f"PDF 청크 생성 완료: {rel_path_str} ({len(chunks)}개 청크)")
        else:
            logger.warning(f"PDF에서 청크 생성 실패: {rel_path_str}")
        # pledge(공약) 폴더: PDF별 인덱싱 로그 (rel_path, chars, chunks) — "신구연금 분리" 등 파일 확인용
        if folder_name == "공약":
            logger.info(f"[INDEX-PDF] pledge: rel_path={rel_path_str!r}, chars={chars}, chunks={len(chunks)}")

    if expected_count != yielded_count:
        logger.warning(
            f"[INDEX-COMPARE] {folder_name}: expected={expected_count}, yielded={yielded_count}, skipped={expected_count - yielded_count}"
        )
    else:
        logger.info(f"[INDEX-COMPARE] {folder_name}: expected={expected_count}, yielded={yielded_count} (일치)")

    if folder_name == "공약":
        logger.info(f"[INDEX-SCAN] pledge files={yielded_count}")
        logger.info(f"[INDEX-BUILD] pledge chunks={len(all_chunks)}")

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
