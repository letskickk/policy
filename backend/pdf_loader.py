"""
정강·정책(이념·취지) PDF와 우리당 공약 PDF를 구분해 로드한다.

폴더 구조:
- data/pdf/정강정책/ : 정강정책 문서 (모든 파일)
- data/pdf/공약/ : 우리당 공약 문서 (모든 파일)
- data/pdf/지역별 공약/ : 타지역 공약 문서 (모든 파일)

Linux(AWS 등)에서는 pdfplumber를 우선 사용해 한글/폰트 차이로 인한 추출 품질 저하를 줄인다.
"""
import logging
import sys
from pathlib import Path
from typing import Iterable

from pypdf import PdfReader

logger = logging.getLogger(__name__)

# PDF_EXTRACTOR: "pdfplumber" | "pypdf" | "auto". "pdfplumber"면 항상 동일 라이브러리로 로컬/AWS 일치.
def _use_pdfplumber_first() -> bool:
    if PDF_EXTRACTOR == "pdfplumber":
        return True
    if PDF_EXTRACTOR == "pypdf":
        return False
    return sys.platform != "win32"  # auto: Linux만 pdfplumber 우선

# pdfplumber는 필수 의존성이다. 없으면 바로 에러를 발생시켜 디버깅을 쉽게 한다.
try:
    import pdfplumber
except ImportError as e:
    raise RuntimeError("pdfplumber not installed") from e

HAS_PDFPLUMBER = True

from backend.config import (
    MAX_CONTEXT_CHARS,
    PDF_DIR,
    PDF_EXTRACTOR,
)


def iter_pdf_texts(dir_path: Path) -> Iterable[tuple[str, str]]:
    """
    폴더의 실제 pdf_files 리스트(rglob)를 기반으로 순회하며, 각 PDF 전체 텍스트를 yield한다.
    실패/빈 텍스트는 로그만 남기고 yield하지 않아 상위에서 자동 스킵된다.
    """
    if not dir_path.exists():
        logger.warning(f"폴더가 존재하지 않음: {dir_path}")
        return

    try:
        pdf_files = sorted(dir_path.rglob("*.pdf"))
    except Exception as e:
        logger.error(f"PDF 파일 검색 실패 ({dir_path}): {e}")
        return

    for path in pdf_files:
        rel_path_str = str(path.relative_to(dir_path))
        try:
            text = extract_text_from_pdf(path)
            text_stripped = (text or "").strip()
            if len(text_stripped) < 10:
                logger.warning(
                    f"[ITER-SKIP] 빈/짧은 텍스트: {rel_path_str} (길이: {len(text_stripped)}자)"
                )
                continue
            yield (rel_path_str, text_stripped)
        except Exception as e:
            logger.warning(f"[ITER-SKIP] 읽기 실패: {rel_path_str} - {e}")
            continue


def get_context_summary() -> dict:
    """
    각 폴더별 PDF 파일 수·추출 성공 수·총 문자 수를 반환. 로컬 vs AWS 비교용.
    (호출 시 PDF를 읽으므로 다소 무거울 수 있음)
    """
    summary = {}
    for folder_name, dir_path in [
        ("platform", PDF_DIR / "정강정책"),
        ("pledges", PDF_DIR / "공약"),
        ("regional", PDF_DIR / "지역별 공약"),
    ]:
        if not dir_path.exists():
            summary[folder_name] = {"files_found": 0, "files_loaded": 0, "total_chars": 0}
            continue
        try:
            pdf_files = list(dir_path.rglob("*.pdf"))
        except Exception:
            pdf_files = []
        files_loaded = 0
        total_chars = 0
        for _rel, text in iter_pdf_texts(dir_path):
            files_loaded += 1
            total_chars += len(text or "")
        summary[folder_name] = {
            "files_found": len(pdf_files),
            "files_loaded": files_loaded,
            "total_chars": total_chars,
        }
    return summary


def load_full_text_from_dir(dir_path: Path) -> str:
    """
    폴더 안 모든 PDF를 한도 없이 전부 읽어 하나의 문자열로 합친다.
    정강정책·공약 전체 학습용(리포트 생성 시 GPT에 넣을 때 사용).
    """
    if not dir_path.exists():
        logger.warning(f"폴더가 존재하지 않음: {dir_path}")
        return ""
    parts = []
    total_chars = 0
    for rel_path_str, text in iter_pdf_texts(dir_path):
        block = f"--- {rel_path_str} ---\n{text}"
        parts.append(block)
        total_chars += len(block)
    result = "\n\n".join(parts) if parts else ""
    logger.info(f"[FULL-LOAD] {dir_path.name}: {len(parts)}개 파일, 총 {total_chars}자")
    return result


def extract_text_from_pdf(path: Path) -> str:
    """PDF 한 파일에서 텍스트 추출. PDF_EXTRACTOR에 따라 pdfplumber만 / pypdf만 / 자동(OS별) 사용."""
    path_str = str(path.resolve())
    path = Path(path_str)

    if not path.exists():
        raise FileNotFoundError(f"PDF 파일이 존재하지 않음: {path}")

    use_plumber_first = _use_pdfplumber_first()

    # 1) pdfplumber 우선 경로 (로컬/AWS 동일 출력을 위해 기본)
    if use_plumber_first and HAS_PDFPLUMBER:
        try:
            text = _extract_with_pdfplumber(path)
            if text is not None:
                logger.info(f"[PDF] {path.name} pdfplumber chars={len((text or '').strip())}")
                if PDF_EXTRACTOR == "pdfplumber" or len((text or "").strip()) >= 10:
                    return text or ""
                # auto 모드에서 너무 짧으면 pypdf 시도
        except Exception as e:
            logger.warning(f"[PDF] pdfplumber 실패 ({path.name}): {e}")
            if PDF_EXTRACTOR == "pdfplumber":
                raise
            # fall through to pypdf

    # 2) pypdf 시도
    try:
        reader = PdfReader(str(path))
        parts = []
        for page in reader.pages:
            t = page.extract_text()
            if t:
                parts.append(t)
        text = "\n\n".join(parts)
        stripped = text.strip()
        logger.info(f"[PDF] {path.name} pypdf chars={len(stripped)}")
        if len(stripped) < 50 and HAS_PDFPLUMBER and PDF_EXTRACTOR != "pypdf":
            try:
                text = _extract_with_pdfplumber(path)
                if text and len(text.strip()) > len(stripped):
                    return text
            except Exception:
                pass
        return text
    except Exception as e:
        logger.warning(f"pypdf 실패 ({path.name}): {e}")
        if HAS_PDFPLUMBER:
            return _extract_with_pdfplumber(path)
        raise


def _extract_with_pdfplumber(path: Path) -> str:
    """pdfplumber로 PDF 텍스트 추출 (Linux 우선 사용 또는 fallback)."""
    try:
        path_str = str(path.resolve())
        parts = []
        with pdfplumber.open(path_str) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    parts.append(t)
        text = "\n\n".join(parts)
        stripped = text.strip()
        logger.info(f"[PDF] {path.name} pdfplumber chars={len(stripped)}")
        return text
    except Exception as e:
        logger.error(f"pdfplumber 실패 ({path.name}): {e}")
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
        logger.info(f"[SCAN] dir={dir_path}")
        pdf_files = list(dir_path.rglob("*.pdf"))
        logger.info(f"[SCAN] found={len(pdf_files)}")
        # 공약 폴더 특별 로깅
        if dir_path.name == "공약":
            logger.info(f"[SCAN] 공약 폴더 found={len(pdf_files)}")
        # 샘플 몇 개 출력
        for p in pdf_files[:10]:
            logger.info(f"[SCAN] sample={p}")
    except Exception as e:
        logger.error(f"PDF 파일 검색 실패 ({dir_path}): {e}")
        return ""

    if not pdf_files:
        logger.warning(f"[SCAN] no pdf files found in {dir_path}. 폴더 경로/이름을 확인하세요.")
    
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
                # 남은 컨텍스트 한도를 넘었지만, 이후 파일 스캔은 계속한다.
                # (다른 파일이 완전히 누락되지 않도록 break 대신 continue 사용)
                continue
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
