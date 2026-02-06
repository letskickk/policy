"""
개혁신당 정책 멘토링 API. 공약 텍스트를 받아 GPT 기반 부합 점검 결과를 반환한다.
"""
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.config import ROOT_DIR, PDF_DIR
from backend.check_service import check_pledge_alignment
from backend.pdf_loader import (
    HAS_PDFPLUMBER,
    load_platform_context,
    load_pledges_context,
)
from backend.index_builder import build_all_indexes
from backend.report import generate_report

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

app = FastAPI(
    title="개혁신당 정책 멘토링",
    description="출마자 공약의 중앙당 정강정책·공약과의 적합도 점검 API",
    version="0.1.0",
)

# 전역 인덱스 (서버 시작 시 초기화)
_indexes = None


@app.on_event("startup")
async def startup_event():
    """서버 시작 시 인덱스 빌드."""
    global _indexes
    logger.info("서버 시작: 인덱스 빌드 중...")
    logger.info(f"HAS_PDFPLUMBER={HAS_PDFPLUMBER}")
    try:
        _indexes = build_all_indexes(force_rebuild=False)
        from backend.vector_index import VectorIndex

        # 빈 인덱스가 있으면 기본값으로 채우기
        if "platform" not in _indexes:
            _indexes["platform"] = VectorIndex(dimension=3072, use_cosine=True)
        if "pledge" not in _indexes:
            _indexes["pledge"] = VectorIndex(dimension=3072, use_cosine=True)
        if "regional" not in _indexes:
            _indexes["regional"] = VectorIndex(dimension=3072, use_cosine=True)

        # 인덱스 크기 로깅
        platform_vectors = _indexes["platform"].size()
        pledge_vectors = _indexes["pledge"].size()
        regional_vectors = _indexes["regional"].size()
        logger.info(f"platform_index: {platform_vectors} vectors")
        logger.info(f"pledge_index: {pledge_vectors} vectors")
        logger.info(f"regional_index: {regional_vectors} vectors")

        if pledge_vectors == 0:
            raise RuntimeError("pledge_index empty")

        logger.info("인덱스 빌드 완료")
    except Exception as e:
        logger.error(f"인덱스 빌드 실패: {e}", exc_info=True)
        from backend.vector_index import VectorIndex
        _indexes = {
            "platform": VectorIndex(dimension=3072, use_cosine=True),
            "pledge": VectorIndex(dimension=3072, use_cosine=True),
            "regional": VectorIndex(dimension=3072, use_cosine=True),
        }

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


@app.get("/test")
def test():
    """간단한 테스트 엔드포인트."""
    return {"status": "ok", "message": "서버 작동 중", "version": "0.1.0"}


@app.get("/debug/context")
def debug_context(pledge: str = "테스트 공약: 지역경제 활성화"):
    """실제 GPT에 전달되는 컨텍스트 확인용 엔드포인트."""
    from backend.prompts import build_user_message, load_system_prompt
    from backend.pdf_loader import load_regional_pledges_context
    
    platform = load_platform_context()
    pledges = load_pledges_context()
    regional = load_regional_pledges_context()
    system = load_system_prompt()
    user = build_user_message(platform, pledges, regional, pledge)
    
    # 공약 컨텍스트에서 특정 키워드 검색
    search_keywords = []
    if pledges:
        # 공약 컨텍스트의 일부를 샘플로 추출
        sample_text = pledges[:5000] if len(pledges) > 5000 else pledges
        search_keywords.append(f"컨텍스트 샘플 (처음 5000자): {sample_text}")
    
    return {
        "system_prompt_length": len(system),
        "user_message_length": len(user),
        "platform_context_length": len(platform),
        "pledges_context_length": len(pledges),
        "regional_pledges_context_length": len(regional),
        "pledges_file_count": pledges.count("---") if pledges else 0,
        "regional_file_count": regional.count("---") if regional else 0,
        "pledges_context_preview": pledges[:5000] + "..." if len(pledges) > 5000 else pledges,
        "regional_context_preview": regional[:5000] + "..." if len(regional) > 5000 else regional,
        "user_message_preview": user[:5000] + "..." if len(user) > 5000 else user,
        "system_prompt": system,
        "test_pledge": pledge,
    }


@app.get("/debug/pdf")
def debug_pdf():
    """PDF 로드 상태 확인용 디버깅 엔드포인트."""
    from backend.config import PDF_DIR
    from backend.prompts import build_user_message
    
    # PDF 디렉토리 확인 (한글 경로 처리)
    pdf_dir_str = str(PDF_DIR.resolve())
    pdf_dir = Path(pdf_dir_str)
    
    try:
        all_pdfs = list(pdf_dir.rglob("*.pdf")) if pdf_dir.exists() else []
    except Exception as e:
        all_pdfs = []
        error_msg = str(e)
    
    from backend.pdf_loader import load_regional_pledges_context
    
    platform = load_platform_context()
    pledges = load_pledges_context()
    regional = load_regional_pledges_context()
    
    # 파일명 추출
    platform_files = [line.split("---")[1].strip() for line in platform.split("\n") if "---" in line and ".pdf" in line] if platform else []
    pledges_files = [line.split("---")[1].strip() for line in pledges.split("\n") if "---" in line and ".pdf" in line] if pledges else []
    regional_files = [line.split("---")[1].strip() for line in regional.split("\n") if "---" in line and ".pdf" in line] if regional else []
    
    # 테스트용 메시지 생성 (실제 GPT에 전달되는 형식)
    test_message = build_user_message(platform, pledges, regional, "테스트 공약: 지역경제 활성화")
    
    # 각 PDF 파일의 상태 확인 (폴더 기반)
    pdf_status = []
    for pdf_path in all_pdfs[:30]:  # 처음 30개만
        try:
            rel_path = str(pdf_path.relative_to(pdf_dir))
            path_parts = pdf_path.relative_to(pdf_dir).parts if pdf_dir.exists() else []
            exists = pdf_path.exists()
            size = pdf_path.stat().st_size if exists else 0
            
            # 폴더 기반 분류
            is_platform = "정강정책" in path_parts
            is_pledge = "공약" in path_parts and "지역별 공약" not in str(rel_path)
            is_regional = "지역별 공약" in str(rel_path)
            
            # 실제로 읽혔는지 확인
            is_loaded = False
            classification = "기타"
            if is_platform:
                is_loaded = any(pdf_path.name in f or str(rel_path) in f for f in platform_files)
                classification = "정강정책"
            elif is_pledge:
                is_loaded = any(pdf_path.name in f or str(rel_path) in f for f in pledges_files)
                classification = "공약"
            elif is_regional:
                is_loaded = any(pdf_path.name in f or str(rel_path) in f for f in regional_files)
                classification = "지역별 공약"
            
            pdf_status.append({
                "path": rel_path,
                "name": pdf_path.name,
                "exists": exists,
                "size_bytes": size,
                "is_platform": is_platform,
                "is_pledge": is_pledge,
                "is_regional": is_regional,
                "is_loaded": is_loaded,
                "classification": classification,
            })
        except Exception as e:
            pdf_status.append({
                "path": str(pdf_path.relative_to(pdf_dir)) if pdf_dir.exists() else str(pdf_path),
                "error": str(e)[:100]
            })
    
    result = {
        "summary": {
            "pdf_dir_exists": pdf_dir.exists(),
            "pdf_dir_path": str(pdf_dir),
            "pdf_dir_absolute": str(pdf_dir.resolve()),
            "total_pdf_files_found": len(all_pdfs),
            "platform_files_loaded": len(platform_files),
            "pledges_files_loaded": len(pledges_files),
            "regional_files_loaded": len(regional_files),
            "platform_text_length": len(platform),
            "pledges_text_length": len(pledges),
            "regional_text_length": len(regional),
            "folder_structure": {
                "정강정책": str(pdf_dir / "정강정책"),
                "공약": str(pdf_dir / "공약"),
                "지역별 공약": str(pdf_dir / "지역별 공약"),
            },
        },
        "all_pdf_files": {
            "count": len(all_pdfs),
            "paths": [str(p.relative_to(pdf_dir)) for p in all_pdfs] if pdf_dir.exists() else [],
        },
        "loaded_files": {
            "platform": platform_files,
            "pledges": pledges_files,
            "regional": regional_files,
        },
        "pdf_status": pdf_status,
        "previews": {
            "platform_preview": platform[:2000] + "..." if len(platform) > 2000 else platform,
            "pledges_preview": pledges[:2000] + "..." if len(pledges) > 2000 else pledges,
            "regional_preview": regional[:2000] + "..." if len(regional) > 2000 else regional,
            "test_message_preview": test_message[:3000] + "..." if len(test_message) > 3000 else test_message,
        },
    }
    
    if 'error_msg' in locals():
        result["error"] = error_msg
    
    return result


@app.get("/api/debug/index")
def debug_index():
    """인덱스 벡터 수를 반환하는 디버깅 엔드포인트."""
    global _indexes
    if _indexes is None:
        raise HTTPException(status_code=503, detail="인덱스가 아직 초기화되지 않았습니다.")

    try:
        platform_vectors = _indexes.get("platform").size() if _indexes.get("platform") else 0
        pledge_vectors = _indexes.get("pledge").size() if _indexes.get("pledge") else 0
        regional_vectors = _indexes.get("regional").size() if _indexes.get("regional") else 0
    except Exception as e:
        logger.error(f"인덱스 디버그 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="인덱스 정보를 가져오는 중 오류 발생")

    return {
        "platform_vectors": platform_vectors,
        "pledge_vectors": pledge_vectors,
        "regional_vectors": regional_vectors,
    }


@app.get("/api/debug/search")
def debug_search(source: str, q: str, k: int = 5):
    """
    특정 인덱스에서 검색 결과를 확인하는 디버깅 엔드포인트.

    source: "platform" | "pledge" | "regional"
    q: 쿼리 텍스트
    k: top_k
    """
    global _indexes
    if _indexes is None or not _indexes:
        raise HTTPException(status_code=503, detail="인덱스가 아직 초기화되지 않았습니다.")

    source_map = {
        "platform": "platform",
        "pledge": "pledge",
        "regional": "regional",
    }
    if source not in source_map:
        raise HTTPException(status_code=400, detail="source는 platform|pledge|regional 중 하나여야 합니다.")

    index = _indexes.get(source_map[source])
    if index is None:
        raise HTTPException(status_code=500, detail=f"{source} 인덱스가 없습니다.")

    from backend.embeddings import embed_texts

    embeddings = embed_texts([q], batch_size=1)
    if not embeddings:
        raise HTTPException(status_code=500, detail="쿼리 임베딩 생성 실패")

    query_embedding = embeddings[0]
    results = index.search(query_embedding, k=k)

    items = []
    for chunk, score in results:
        items.append(
            {
                "source": chunk.source,
                "path": chunk.path,
                "chunk_id": chunk.chunk_id,
                "score": score,
                "snippet": (chunk.text[:250] + "...") if len(chunk.text) > 250 else chunk.text,
            }
        )

    return {
        "source": source,
        "query": q,
        "k": k,
        "results": items,
    }


@app.get("/api/debug/scan")
def debug_scan():
    """PDF 폴더 구조 및 파일 목록을 반환하는 디버깅 엔드포인트."""
    base_dir = PDF_DIR

    def list_files(subdir_name: str):
        subdir = base_dir / subdir_name
        if not subdir.exists():
            return []
        return [str(p.relative_to(base_dir)) for p in subdir.rglob("*.pdf")]

    return {
        "platform_files": list_files("정강정책"),
        "pledge_files": list_files("공약"),
        "regional_files": list_files("지역별 공약"),
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


class PledgeVerifyRequest(BaseModel):
    text: str = Field(..., description="검증할 출마자 공약 텍스트")
    top_k_platform: int = Field(default=6, description="정강정책 검색 개수")
    top_k_pledge: int = Field(default=6, description="공약 검색 개수")
    top_k_regional: int = Field(default=8, description="지역별 공약 검색 개수")


@app.post("/api/pledge/verify")
def verify_pledge(body: PledgeVerifyRequest):
    """
    벡터 검색 기반 공약 검증 리포트를 생성한다.
    
    - 정강정책 부합 근거
    - 우리당 공약 연결 근거
    - 타지역 중복/유사성
    - 상충/보완 제안
    """
    global _indexes
    
    if _indexes is None or not _indexes:
        raise HTTPException(
            status_code=503,
            detail="인덱스가 준비되지 않았습니다. 서버를 재시작하세요."
        )
    
    pledge_text = (body.text or "").strip()
    if not pledge_text:
        raise HTTPException(status_code=400, detail="공약 텍스트가 비어 있습니다.")
    
    try:
        report = generate_report(
            pledge_text,
            _indexes.get("platform"),
            _indexes.get("pledge"),
            _indexes.get("regional"),
            body.top_k_platform,
            body.top_k_pledge,
            body.top_k_regional
        )
        return report
    except Exception as e:
        logger.error(f"공약 검증 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"검증 중 오류 발생: {str(e)}")
