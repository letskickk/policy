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
    # 한글 경로 처리를 위해 문자열로 변환 후 다시 Path로
    path_str = str(path.resolve())
    path = Path(path_str)
    
    if not path.exists():
        raise FileNotFoundError(f"PDF 파일이 존재하지 않음: {path}")
    
    logger.debug(f"PDF 읽기 시도: {path} (존재: {path.exists()})")
    
    # 먼저 pypdf로 시도
    try:
        reader = PdfReader(str(path))  # 문자열로 전달
        parts = []
        for page in reader.pages:
            t = page.extract_text()
            if t:
                parts.append(t)
        text = "\n\n".join(parts)
        # 텍스트가 너무 짧으면(빈 페이지일 수 있음) pdfplumber로 재시도
        if len(text.strip()) < 50 and HAS_PDFPLUMBER:
            logger.info(f"pypdf로 추출한 텍스트가 짧음 ({len(text)}자), pdfplumber로 재시도: {path.name}")
            return _extract_with_pdfplumber(path)
        logger.debug(f"pypdf로 추출 성공: {path.name} ({len(text)}자)")
        return text
    except Exception as e:
        logger.warning(f"pypdf 실패 ({path.name}): {e}, pdfplumber로 재시도")
        # pypdf 실패 시 pdfplumber로 재시도
        if HAS_PDFPLUMBER:
            return _extract_with_pdfplumber(path)
        raise


def _extract_with_pdfplumber(path: Path) -> str:
    """pdfplumber로 PDF 텍스트 추출 (fallback)."""
    try:
        # 한글 경로 처리를 위해 문자열로 변환
        path_str = str(path.resolve())
        parts = []
        with pdfplumber.open(path_str) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    parts.append(t)
        text = "\n\n".join(parts)
        logger.debug(f"pdfplumber로 추출 성공: {path.name} ({len(text)}자)")
        return text
    except Exception as e:
        logger.error(f"pdfplumber도 실패 ({path.name}): {e}")
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
    pdf_dir_str = str(PDF_DIR.resolve())
    pdf_dir = Path(pdf_dir_str)
    
    if not pdf_dir.exists():
        logger.warning(f"PDF_DIR이 존재하지 않음: {pdf_dir}")
        return ""
    
    logger.info(f"정강정책 PDF 검색 시작: {pdf_dir}")
    combined: list[str] = []
    total_len = 0
    limit = MAX_CONTEXT_CHARS // 2
    found_files = []
    
    # 모든 PDF 파일 찾기 (한글 경로 처리)
    try:
        all_pdfs = list(pdf_dir.rglob("*.pdf"))
        logger.info(f"전체 PDF 파일 {len(all_pdfs)}개 발견")
    except Exception as e:
        logger.error(f"PDF 파일 검색 실패: {e}")
        return ""
    
    for path in sorted(all_pdfs):
        if not _platform_file_filter(path):
            continue
        found_files.append(str(path))
        try:
            text = extract_text_from_pdf(path)
            if not text or len(text.strip()) < 10:
                logger.warning(f"정강정책 PDF 텍스트가 비어있거나 너무 짧음: {path.name}")
                combined.append(f"--- {path.name} --- (텍스트 없음)\n")
                continue
            text = f"--- {path.name} ---\n{text}".strip()
            if total_len + len(text) <= limit:
                combined.append(text)
                total_len += len(text)
                logger.info(f"정강정책 PDF 로드 성공: {path.name} ({len(text)}자)")
            else:
                remain = limit - total_len
                if remain > 500:
                    combined.append(text[:remain] + "\n[... 일부 생략 ...]")
                logger.warning(f"정강정책 PDF 컨텍스트 한도 초과로 일부만 로드: {path.name}")
                break
        except Exception as e:
            logger.error(f"정강정책 PDF 읽기 실패: {path.name} - {e}", exc_info=True)
            combined.append(f"--- {path.name} --- (읽기 실패: {str(e)[:100]})\n")
            continue
    
    logger.info(f"정강정책 PDF 총 {len(found_files)}개 발견, {len(combined)}개 로드됨")
    if found_files:
        logger.info(f"발견된 정강정책 PDF 파일 목록: {found_files[:5]}...")  # 처음 5개만
    return "\n\n".join(combined) if combined else ""


def load_pledges_context() -> str:
    """
    우리당 공약 문서만 로드한다.
    - data/pdf/ 밑의 모든 하위 폴더를 재귀적으로 탐색하여 정강·정책으로 분류되지 않은 모든 PDF를 찾는다.
    """
    pdf_dir_str = str(PDF_DIR.resolve())
    pdf_dir = Path(pdf_dir_str)
    
    if not pdf_dir.exists():
        logger.warning(f"PDF_DIR이 존재하지 않음: {pdf_dir}")
        return ""
    
    logger.info(f"공약 PDF 검색 시작: {pdf_dir}")
    combined: list[str] = []
    total_len = 0
    limit = MAX_CONTEXT_CHARS // 2
    found_files = []
    skipped_files = []
    
    # 모든 PDF 파일 찾기 (한글 경로 처리)
    try:
        all_pdfs = list(pdf_dir.rglob("*.pdf"))
        logger.info(f"전체 PDF 파일 {len(all_pdfs)}개 발견")
        for pdf_path in all_pdfs:
            logger.debug(f"검사 중: {pdf_path}")
    except Exception as e:
        logger.error(f"PDF 파일 검색 실패: {e}")
        return ""
    
    for path in sorted(all_pdfs):
        # 정강·정책 파일은 제외
        if _platform_file_filter(path):
            skipped_files.append(str(path))
            continue
        found_files.append(str(path))
        try:
            text = extract_text_from_pdf(path)
            if not text or len(text.strip()) < 10:
                logger.warning(f"공약 PDF 텍스트가 비어있거나 너무 짧음: {path.name}")
                combined.append(f"--- {path.name} --- (텍스트 없음)\n")
                continue
            text = f"--- {path.name} ---\n{text}".strip()
            if total_len + len(text) <= limit:
                combined.append(text)
                total_len += len(text)
                logger.info(f"공약 PDF 로드 성공: {path.name} ({len(text)}자)")
            else:
                remain = limit - total_len
                if remain > 500:
                    combined.append(text[:remain] + "\n[... 일부 생략 ...]")
                logger.warning(f"공약 PDF 컨텍스트 한도 초과로 일부만 로드: {path.name}")
                break
        except Exception as e:
            logger.error(f"공약 PDF 읽기 실패: {path.name} - {e}", exc_info=True)
            combined.append(f"--- {path.name} --- (읽기 실패: {str(e)[:100]})\n")
            continue
    
    logger.info(f"공약 PDF 총 {len(found_files)}개 발견 (정강정책 제외: {len(skipped_files)}개), {len(combined)}개 로드됨")
    if found_files:
        logger.info(f"발견된 공약 PDF 파일 목록: {found_files[:5]}...")  # 처음 5개만
    return "\n\n".join(combined) if combined else ""


def load_all_pdf_context() -> str:
    """
    (하위 호환) 전체 PDF를 한 덩어리로 로드. 정강정책 + 공약 순으로 합친다.
    """
    platform = load_platform_context()
    pledges = load_pledges_context()
    return f"{platform}\n\n{pledges}".strip() if (platform or pledges) else ""
