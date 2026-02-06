"""
정강·정책(이념·취지) PDF와 우리당 공약 PDF를 구분해 로드한다.
"""
import logging
from pathlib import Path

from pypdf import PdfReader

logger = logging.getLogger(__name__)

try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False

from backend.config import (
    MAX_CONTEXT_CHARS,
    PDF_DIR,
    PDF_DIR_PLATFORM,
    PDF_DIR_PLEDGES,
)


def extract_text_from_pdf(path: Path) -> str:
    """PDF 한 파일에서 텍스트 추출. pypdf로 시도하고 실패하면 pdfplumber 사용."""
    # 먼저 pypdf로 시도
    try:
        reader = PdfReader(path)
        parts = []
        for page in reader.pages:
            t = page.extract_text()
            if t:
                parts.append(t)
        text = "\n\n".join(parts)
        # 텍스트가 너무 짧으면(빈 페이지일 수 있음) pdfplumber로 재시도
        if len(text.strip()) < 50 and HAS_PDFPLUMBER:
            return _extract_with_pdfplumber(path)
        return text
    except Exception:
        # pypdf 실패 시 pdfplumber로 재시도
        if HAS_PDFPLUMBER:
            return _extract_with_pdfplumber(path)
        raise


def _extract_with_pdfplumber(path: Path) -> str:
    """pdfplumber로 PDF 텍스트 추출 (fallback)."""
    try:
        parts = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    parts.append(t)
        return "\n\n".join(parts)
    except Exception as e:
        raise Exception(f"PDF 추출 실패 ({path.name}): {e}")


def _load_pdfs_from_dir(dir_path: Path, limit_chars: int) -> str:
    """지정 폴더 내 PDF들을 읽어 하나의 문자열로 합친다. 하위 폴더까지 재귀적으로 찾는다. limit_chars 초과 시 앞부분만 사용."""
    if not dir_path.exists():
        return ""
    combined: list[str] = []
    total_len = 0
    for path in sorted(dir_path.rglob("*.pdf")):
        try:
            text = extract_text_from_pdf(path)
            text = f"--- {path.name} ---\n{text}".strip()
            if total_len + len(text) <= limit_chars:
                combined.append(text)
                total_len += len(text)
            else:
                remain = limit_chars - total_len
                if remain > 500:
                    combined.append(text[:remain] + "\n[... 일부 생략 ...]")
                break
        except Exception as e:
            combined.append(f"--- {path.name} --- (읽기 실패: {str(e)[:100]})\n")
            continue
    return "\n\n".join(combined) if combined else ""


def _platform_file_filter(path: Path) -> bool:
    """정강·정책 문서로 볼 파일인지. 파일명에 '정강' 또는 '정책' 포함 (공약 설명이 아닌 정강정책 문서)."""
    name = path.name
    return "정강" in name or "정책" in name


def load_platform_context() -> str:
    """
    정강·정책(이념·취지) 문서만 로드한다.
    - data/pdf/ 밑의 모든 하위 폴더를 재귀적으로 탐색하여 파일명에 '정강' 또는 '정책'이 들어간 PDF를 찾는다.
    """
    if not PDF_DIR.exists():
        logger.warning(f"PDF_DIR이 존재하지 않음: {PDF_DIR}")
        return ""
    combined: list[str] = []
    total_len = 0
    limit = MAX_CONTEXT_CHARS // 2
    found_files = []
    for path in sorted(PDF_DIR.rglob("*.pdf")):
        if not _platform_file_filter(path):
            continue
        found_files.append(str(path))
        try:
            text = extract_text_from_pdf(path)
            text = f"--- {path.name} ---\n{text}".strip()
            if total_len + len(text) <= limit:
                combined.append(text)
                total_len += len(text)
                logger.info(f"정강정책 PDF 로드 성공: {path.name} ({len(text)}자)")
            else:
                remain = limit - total_len
                if remain > 500:
                    combined.append(text[:remain] + "\n[... 일부 생략 ...]")
                break
        except Exception as e:
            logger.error(f"정강정책 PDF 읽기 실패: {path.name} - {e}")
            combined.append(f"--- {path.name} --- (읽기 실패: {str(e)[:100]})\n")
            continue
    logger.info(f"정강정책 PDF 총 {len(found_files)}개 발견, {len(combined)}개 로드됨")
    return "\n\n".join(combined) if combined else ""


def load_pledges_context() -> str:
    """
    우리당 공약 문서만 로드한다.
    - data/pdf/ 밑의 모든 하위 폴더를 재귀적으로 탐색하여 정강·정책으로 분류되지 않은 모든 PDF를 찾는다.
    """
    if not PDF_DIR.exists():
        logger.warning(f"PDF_DIR이 존재하지 않음: {PDF_DIR}")
        return ""
    combined: list[str] = []
    total_len = 0
    limit = MAX_CONTEXT_CHARS // 2
    found_files = []
    for path in sorted(PDF_DIR.rglob("*.pdf")):
        # 정강·정책 파일은 제외
        if _platform_file_filter(path):
            continue
        found_files.append(str(path))
        try:
            text = extract_text_from_pdf(path)
            text = f"--- {path.name} ---\n{text}".strip()
            if total_len + len(text) <= limit:
                combined.append(text)
                total_len += len(text)
                logger.info(f"공약 PDF 로드 성공: {path.name} ({len(text)}자)")
            else:
                remain = limit - total_len
                if remain > 500:
                    combined.append(text[:remain] + "\n[... 일부 생략 ...]")
                break
        except Exception as e:
            logger.error(f"공약 PDF 읽기 실패: {path.name} - {e}")
            combined.append(f"--- {path.name} --- (읽기 실패: {str(e)[:100]})\n")
            continue
    logger.info(f"공약 PDF 총 {len(found_files)}개 발견, {len(combined)}개 로드됨")
    return "\n\n".join(combined) if combined else ""


def load_all_pdf_context() -> str:
    """
    (하위 호환) 전체 PDF를 한 덩어리로 로드. 정강정책 + 공약 순으로 합친다.
    """
    platform = load_platform_context()
    pledges = load_pledges_context()
    return f"{platform}\n\n{pledges}".strip() if (platform or pledges) else ""
