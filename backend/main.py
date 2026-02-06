"""
개혁신당 정책 멘토링 API. 공약 텍스트를 받아 GPT 기반 부합 점검 결과를 반환한다.
"""
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.config import ROOT_DIR
from backend.check_service import check_pledge_alignment
from backend.pdf_loader import load_platform_context, load_pledges_context

app = FastAPI(
    title="개혁신당 정책 멘토링",
    description="출마자 공약의 중앙당 정강정책·공약과의 적합도 점검 API",
    version="0.1.0",
)

STATIC_DIR = ROOT_DIR / "static"


class PledgeCheckRequest(BaseModel):
    pledge: str = Field(..., description="점검할 출마자 공약 텍스트")


class PledgeCheckResponse(BaseModel):
    result: str = Field(..., description="부합 점검 결과 (판정, 근거, 체크리스트 등)")


def _serve_html(filename: str):
    path = STATIC_DIR / filename
    if path.exists():
        return FileResponse(path, media_type="text/html; charset=utf-8")
    return None


@app.get("/")
def index():
    """메인 페이지: 서비스 소개 및 공약 점검 진입."""
    res = _serve_html("index.html")
    if res is not None:
        return res
    return {"service": "개혁신당 정책 멘토링", "endpoint": "POST /check"}


@app.get("/pledge")
def pledge_page():
    """공약 입력·점검 폼 페이지."""
    res = _serve_html("pledge.html")
    if res is not None:
        return res
    raise HTTPException(status_code=404, detail="pledge.html not found")


@app.get("/api")
def api_info():
    return {"service": "개혁신당 정책 멘토링", "endpoint": "POST /check"}


@app.get("/debug/pdf")
def debug_pdf():
    """PDF 로드 상태 확인용 디버깅 엔드포인트."""
    from backend.config import PDF_DIR
    
    # PDF 디렉토리 확인
    all_pdfs = list(PDF_DIR.rglob("*.pdf")) if PDF_DIR.exists() else []
    
    platform = load_platform_context()
    pledges = load_pledges_context()
    
    # 파일명 추출
    platform_files = [line.split("---")[1].strip() for line in platform.split("\n") if "---" in line and ".pdf" in line] if platform else []
    pledges_files = [line.split("---")[1].strip() for line in pledges.split("\n") if "---" in line and ".pdf" in line] if pledges else []
    
    return {
        "pdf_dir_exists": PDF_DIR.exists(),
        "pdf_dir_path": str(PDF_DIR),
        "total_pdf_files": len(all_pdfs),
        "all_pdf_paths": [str(p.relative_to(PDF_DIR)) for p in all_pdfs],
        "platform_files_count": len(platform_files),
        "platform_files": platform_files,
        "platform_length": len(platform),
        "pledges_files_count": len(pledges_files),
        "pledges_files": pledges_files,
        "pledges_length": len(pledges),
        "platform_preview": platform[:1000] + "..." if len(platform) > 1000 else platform,
        "pledges_preview": pledges[:1000] + "..." if len(pledges) > 1000 else pledges,
    }


@app.post("/check", response_model=PledgeCheckResponse)
def check_pledge(body: PledgeCheckRequest):
    """공약을 입력하면 중앙당의 정강정책·공약과의 적합도, 근거, 수정·보완 체크리스트를 반환한다."""
    pledge = (body.pledge or "").strip()
    if not pledge:
        raise HTTPException(status_code=400, detail="pledge 내용이 비어 있습니다.")
    result = check_pledge_alignment(pledge)
    if result.startswith("오류:"):
        raise HTTPException(status_code=503, detail=result)
    return PledgeCheckResponse(result=result)
