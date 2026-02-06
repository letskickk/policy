"""
당 부합 점검 API. 공약 텍스트를 받아 GPT 기반 부합 점검 결과를 반환한다.
"""
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.config import ROOT_DIR
from backend.check_service import check_pledge_alignment

app = FastAPI(
    title="AI 공약 멘토링",
    description="출마자 공약의 당 방향 부합 점검 API",
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
    return {"service": "AI 공약 멘토링", "endpoint": "POST /check"}


@app.get("/pledge")
def pledge_page():
    """공약 입력·점검 폼 페이지."""
    res = _serve_html("pledge.html")
    if res is not None:
        return res
    raise HTTPException(status_code=404, detail="pledge.html not found")


@app.get("/api")
def api_info():
    return {"service": "AI 공약 멘토링", "endpoint": "POST /check"}


@app.post("/check", response_model=PledgeCheckResponse)
def check_pledge(body: PledgeCheckRequest):
    """공약을 입력하면 당 방향 부합 여부, 근거, 수정·보완 체크리스트를 반환한다."""
    pledge = (body.pledge or "").strip()
    if not pledge:
        raise HTTPException(status_code=400, detail="pledge 내용이 비어 있습니다.")
    result = check_pledge_alignment(pledge)
    if result.startswith("오류:"):
        raise HTTPException(status_code=503, detail=result)
    return PledgeCheckResponse(result=result)
