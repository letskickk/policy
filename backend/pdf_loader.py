"""
정강·정책(이념·취지) PDF와 우리당 공약 PDF를 구분해 로드한다.

폴더 구조:
- data/pdf/정강정책/ : 정강정책 문서 (모든 파일)
- data/pdf/공약/ : 우리당 공약 문서 (모든 파일)
- data/pdf/지역별 공약/ : 타지역 공약 문서 (모든 파일)
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
        logger.warning(f"폴더가 존재하지 않음: {dir_path}")
        return ""
    
    combined: list[str] = []
    total_len = 0
    
    # 폴더 안의 모든 PDF 파일 찾기 (재귀적)
    try:
        pdf_files = list(dir_path.rglob("*.pdf"))
        logger.info(f"{dir_path.name} 폴더에서 {len(pdf_files)}개 PDF 파일 발견")
    except Exception as e:
        logger.error(f"PDF 파일 검색 실패 ({dir_path}): {e}")
        return ""
    
    for path in sorted(pdf_files):
        try:
            text = extract_text_from_pdf(path)
            text_len = len(text.strip()) if text else 0
            
            if not text or text_len < 10:
                logger.warning(f"PDF 텍스트가 비어있거나 너무 짧음: {path.name} (길이: {text_len}자)")
                combined.append(f"--- {path.name} --- (텍스트 없음)\n")
                continue
            
            # 상대 경로로 파일명 표시
            rel_path = path.relative_to(dir_path)
            text = f"--- {rel_path} ---\n{text}".strip()
            
            if total_len + len(text) <= limit_chars:
                combined.append(text)
                total_len += len(text)
                logger.info(f"PDF 로드 성공: {rel_path} ({text_len}자, 누적: {total_len}/{limit_chars}자)")
            else:
                remain = limit_chars - total_len
                if remain > 500:
                    combined.append(text[:remain] + "\n[... 일부 생략 ...]")
                    logger.warning(f"컨텍스트 한도 초과로 일부만 로드: {rel_path} (전체: {len(text)}자, 로드: {remain}자)")
                else:
                    logger.warning(f"컨텍스트 한도 초과로 스킵: {rel_path} (필요: {len(text)}자, 남은 공간: {remain}자)")
                break
        except Exception as e:
            logger.error(f"PDF 읽기 실패: {path.name} - {e}", exc_info=True)
            combined.append(f"--- {path.name} --- (읽기 실패: {str(e)[:100]})\n")
            continue
    
    result = "\n\n".join(combined) if combined else ""
    logger.info(f"{dir_path.name} 폴더 로드 완료: {len(pdf_files)}개 파일 중 {len(combined)}개 로드, 총 {total_len}자")
    return result


def load_platform_context() -> str:
    """
    정강·정책(이념·취지) 문서만 로드한다.
    - data/pdf/정강정책/ 폴더 안의 모든 PDF 파일을 로드한다.
    """
    pdf_dir_str = str(PDF_DIR.resolve())
    pdf_dir = Path(pdf_dir_str)
    
    if not pdf_dir.exists():
        logger.warning(f"PDF_DIR이 존재하지 않음: {pdf_dir}")
        return ""
    
    platform_dir = pdf_dir / "정강정책"
    
    if not platform_dir.exists():
        logger.warning(f"정강정책 폴더가 존재하지 않음: {platform_dir}")
        return ""
    
    logger.info(f"정강정책 PDF 로드 시작: {platform_dir}")
    limit = MAX_CONTEXT_CHARS // 2
    result = _load_pdfs_from_dir(platform_dir, limit)
    
    if result:
        logger.info(f"정강정책 컨텍스트 로드 완료: {len(result)}자")
    else:
        logger.warning("정강정책 컨텍스트가 비어있습니다.")
    
    return result


def load_pledges_context() -> str:
    """
    우리당 공약 문서만 로드한다.
    - data/pdf/공약/ 폴더 안의 모든 PDF 파일을 로드한다.
    """
    pdf_dir_str = str(PDF_DIR.resolve())
    pdf_dir = Path(pdf_dir_str)
    
    if not pdf_dir.exists():
        logger.warning(f"PDF_DIR이 존재하지 않음: {pdf_dir}")
        return ""
    
    pledges_dir = pdf_dir / "공약"
    
    if not pledges_dir.exists():
        logger.warning(f"공약 폴더가 존재하지 않음: {pledges_dir}")
        return ""
    
    logger.info(f"공약 PDF 로드 시작: {pledges_dir}")
    limit = MAX_CONTEXT_CHARS // 2
    result = _load_pdfs_from_dir(pledges_dir, limit)
    
    if result:
        logger.info(f"공약 컨텍스트 로드 완료: {len(result)}자")
    else:
        logger.warning("공약 컨텍스트가 비어있습니다.")
    
    return result


def load_regional_pledges_context() -> str:
    """
    타지역 공약 문서를 로드한다.
    - data/pdf/지역별 공약/ 폴더 안의 모든 PDF 파일을 로드한다.
    - 이 컨텍스트는 타지역 공약과의 유사성 분석에 사용된다.
    """
    pdf_dir_str = str(PDF_DIR.resolve())
    pdf_dir = Path(pdf_dir_str)
    
    if not pdf_dir.exists():
        logger.warning(f"PDF_DIR이 존재하지 않음: {pdf_dir}")
        return ""
    
    regional_dir = pdf_dir / "지역별 공약"
    
    if not regional_dir.exists():
        logger.warning(f"지역별 공약 폴더가 존재하지 않음: {regional_dir}")
        return ""
    
    logger.info(f"지역별 공약 PDF 로드 시작: {regional_dir}")
    limit = MAX_CONTEXT_CHARS // 3  # 지역별 공약은 별도로 관리하므로 더 작은 한도 사용
    result = _load_pdfs_from_dir(regional_dir, limit)
    
    if result:
        logger.info(f"지역별 공약 컨텍스트 로드 완료: {len(result)}자")
    else:
        logger.warning("지역별 공약 컨텍스트가 비어있습니다.")
    
    return result


def load_all_pdf_context() -> str:
    """
    (하위 호환) 전체 PDF를 한 덩어리로 로드. 정강정책 + 공약 순으로 합친다.
    """
    platform = load_platform_context()
    pledges = load_pledges_context()
    return f"{platform}\n\n{pledges}".strip() if (platform or pledges) else ""
