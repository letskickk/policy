"""
개혁신당 정책 멘토링 API. 공약 텍스트를 받아 GPT 기반 부합 점검 결과를 반환한다.
접근제어: 회원가입→관리자 승인→쿼터/레이트리밋 적용.
"""
import locale
import logging
import os
import sys
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from backend.config import (
    ADMIN_EMAILS,
    ROOT_DIR,
    PDF_DIR,
    INDEX_CACHE_DIR,
    OPENAI_MODEL,
    CHAT_MODEL,
    DEBUG_ENDPOINTS_ENABLED,
    PDF_S3_URI,
    USE_OPENAI_VECTOR_STORE,
    SKIP_PDF_SCAN_ON_STARTUP,
    OPENAI_VECTOR_STORE_ID,
    _nfc,
)
from backend.auth import (
    STATUS_APPROVED,
    STATUS_PENDING,
    ROLE_ADMIN,
    create_session_token,
    verify_session_token,
    signup as auth_signup,
    login as auth_login,
    verify_email_token,
    resend_verification_email,
    get_user,
    list_users_pending,
    list_users_all,
    set_user_status,
)
from backend.database import init_db
from backend.usage_logger import log_usage
from backend.quota_rate import check_rate_limit_ip, check_rate_limit_user
from backend.pdf_loader import (
    HAS_PDFPLUMBER,
    _iter_doc_files,
    load_platform_context,
    load_pledges_context,
    get_context_summary,
)
from backend.index_builder import build_all_indexes

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="개혁신당 정책 멘토링",
    description="출마자 공약의 중앙당 정강정책·공약과의 적합도 점검 API",
    version="0.1.0",
)

# 전역 인덱스 (서버 시작 시 초기화). USE_OPENAI_VECTOR_STORE=1이면 _vector_store_id 사용.
_indexes = None
_vector_store_id = None
_regional_vector_store_id = None


def _startup_self_check() -> int:
    """
    서버 시작 시 강제 진단. 조건 불만족 시 RuntimeError.
    SKIP_PDF_SCAN_ON_STARTUP=1 + USE_OPENAI_VECTOR_STORE + OPENAI_VECTOR_STORE_ID 설정 시 PDF 스캔 생략.
    Returns: 공약 폴더 PDF 개수 (0이면 그대로 raise, skip 시 0 반환)
    """
    if USE_OPENAI_VECTOR_STORE and SKIP_PDF_SCAN_ON_STARTUP and OPENAI_VECTOR_STORE_ID:
        logger.info("[SELF-CHECK] SKIP_PDF_SCAN_ON_STARTUP=1 → PDF 스캔 생략")
        return 0

    # locale 확인: UTF-8 아님 → fail-fast
    enc = locale.getpreferredencoding()
    try:
        lc = locale.setlocale(locale.LC_ALL, None)
    except Exception:
        lc = "unknown"
    logger.info(f"[LOCALE] encoding={enc} LC_ALL={lc}")
    # Linux/컨테이너에서만 UTF-8 강제 (한글 rglob용). Windows(cp949)는 통과.
    if sys.platform != "win32" and enc.upper() not in ("UTF-8", "UTF8"):
        raise RuntimeError(
            f"UTF-8 locale이 필요합니다. 현재 encoding={enc}. "
            "Dockerfile에 ENV LANG=C.UTF-8 LC_ALL=C.UTF-8 또는 export LC_ALL=C.UTF-8 을 설정하세요."
        )

    # 경로
    cwd = os.getcwd()
    try:
        backend_file = Path(__file__).resolve()
        base_dir = backend_file.parent.parent
    except Exception:
        base_dir = Path(cwd)
    logger.info(f"[SELF-CHECK] cwd={cwd!r}, __file__ base={base_dir!s}")

    pdf_dir = Path(PDF_DIR).resolve()
    pdf_dir_exists = pdf_dir.exists()
    logger.info(f"[SELF-CHECK] PDF_DIR={pdf_dir!s}, exists={pdf_dir_exists}")

    folders = [
        ("정강정책", pdf_dir / _nfc("정강정책")),
        ("공약", pdf_dir / _nfc("공약")),
        ("지역별 공약", pdf_dir / _nfc("지역별 공약")),
    ]
    pledge_pdf_count = 0
    for name, dir_path in folders:
        exists = dir_path.exists()
        try:
            raw_entries = list(dir_path.iterdir())[:5] if exists else []
            logger.info(f"[SCAN RAW] {name} iterdir sample={[str(p) for p in raw_entries]}")
            pdf_list = list(_iter_doc_files(dir_path)) if exists else []
            logger.info(f"[SCAN DOC] {name} pdf+txt count={len(pdf_list)}")
        except Exception as e:
            logger.warning(f"[SELF-CHECK] {name} rglob failed: {e}")
            pdf_list = []
        count = len(pdf_list)
        samples = [p.name for p in sorted(pdf_list)[:5]]
        if name == "공약":
            pledge_pdf_count = count
        has_sin_gu = any("신구연금" in p.name for p in pdf_list)
        logger.info(f"[SELF-CHECK] {name} exists={exists} pdf_count={count} sample={samples!r} 신구연금포함={has_sin_gu}")

    if not HAS_PDFPLUMBER:
        raise RuntimeError("HAS_PDFPLUMBER is False. pdfplumber is required. Install: pip install pdfplumber pdfminer.six")
    logger.info("[SELF-CHECK] HAS_PDFPLUMBER=True")

    cache_dir = Path(INDEX_CACHE_DIR).resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_exists = cache_dir.exists()
    writable = False
    try:
        touch = cache_dir / ".write_test"
        touch.write_text("ok")
        touch.unlink(missing_ok=True)
        writable = True
    except Exception as e:
        logger.error(f"[SELF-CHECK] INDEX_CACHE_DIR not writable: {cache_dir} - {e}")
    logger.info(f"[SELF-CHECK] INDEX_CACHE_DIR={cache_dir!s} exists={cache_exists} writable={writable}")
    if not writable:
        raise RuntimeError(f"INDEX_CACHE_DIR is not writable: {cache_dir}")

    if pledge_pdf_count == 0 and not PDF_S3_URI:
        raise RuntimeError(
            "공약 폴더 PDF 개수가 0입니다. AWS에 PDF를 배포했는지 확인하세요. "
            "또는 PDF_S3_URI를 설정해 S3에서 내려받도록 하세요."
        )
    logger.info(f"[SELF-CHECK] 공약 pdf count={pledge_pdf_count} (>0 or PDF_S3_URI set)")

    if pledge_pdf_count == 0 and PDF_S3_URI:
        try:
            import subprocess
            pdf_dir.mkdir(parents=True, exist_ok=True)
            pdf_pledge = pdf_dir / _nfc("공약")
            pdf_pledge.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["aws", "s3", "sync", PDF_S3_URI.rstrip("/") + "/", str(pdf_pledge)],
                check=True,
                timeout=300,
                capture_output=True,
            )
            pledge_pdf_count = len(list(_iter_doc_files(pdf_pledge)))
            logger.info(f"[SELF-CHECK] S3 sync done, 공약 pdf count={pledge_pdf_count}")
        except Exception as e:
            raise RuntimeError(f"PDF_S3_URI sync failed: {e}") from e
        if pledge_pdf_count == 0:
            raise RuntimeError("S3 sync 후에도 공약 폴더 PDF가 0건입니다.")

    return pledge_pdf_count


@app.on_event("startup")
async def startup_event():
    """서버 시작: Self-Check → (선택 S3 sync) → 인덱스 또는 Vector Store 준비."""
    global _indexes, _vector_store_id, _regional_vector_store_id
    logger.info("서버 시작: Self-Check 및 인덱스/Vector Store 준비 중...")
    logger.info(f"OPENAI_MODEL (check)= {OPENAI_MODEL!r}, CHAT_MODEL (verify/cards)= {CHAT_MODEL!r}")
    logger.info(f"USE_OPENAI_VECTOR_STORE= {USE_OPENAI_VECTOR_STORE}")

    _startup_self_check()
    init_db()

    if USE_OPENAI_VECTOR_STORE:
        from backend.config import OPENAI_REGIONAL_VECTOR_STORE_ID
        from backend.rag_registry import get_vector_store_ids

        logger.info("[VECTOR_STORE] INGEST SKIPPED (runtime)")

        policy_id, regional_id = get_vector_store_ids()
        if not policy_id and OPENAI_VECTOR_STORE_ID:
            policy_id = OPENAI_VECTOR_STORE_ID
            regional_id = OPENAI_REGIONAL_VECTOR_STORE_ID
            logger.warning("[VECTOR_STORE] .rag ID 없음 → .env ID 사용 (ingest는 실행하지 않음)")

        if not policy_id:
            raise RuntimeError(
                "Vector Store ID가 없습니다. 먼저 ingest 스크립트를 실행하세요: "
                "python scripts/ingest_vector_store.py"
            )

        _vector_store_id = policy_id
        _regional_vector_store_id = regional_id
        logger.info(f"[VECTOR_STORE] ID 사용: policy={_vector_store_id}, regional={_regional_vector_store_id or '(없음)'}")
    else:
        _indexes = build_all_indexes(force_rebuild=False)
        from backend.vector_index import VectorIndex
        from backend.config import EMBEDDING_DIMENSION
        if "platform" not in _indexes:
            _indexes["platform"] = VectorIndex(dimension=EMBEDDING_DIMENSION, use_cosine=True)
        if "pledge" not in _indexes:
            _indexes["pledge"] = VectorIndex(dimension=EMBEDDING_DIMENSION, use_cosine=True)
        if "regional" not in _indexes:
            _indexes["regional"] = VectorIndex(dimension=EMBEDDING_DIMENSION, use_cosine=True)
        platform_vectors = _indexes["platform"].size()
        pledge_vectors = _indexes["pledge"].size()
        regional_vectors = _indexes["regional"].size()
        logger.info(f"platform_index: {platform_vectors} vectors")
        logger.info(f"pledge_index: {pledge_vectors} vectors")
        logger.info(f"regional_index: {regional_vectors} vectors")
        if pledge_vectors == 0:
            raise RuntimeError(
                "pledge_index 벡터가 0입니다. PDF 추출 또는 인덱스 빌드가 실패한 상태로 서비스를 시작할 수 없습니다. "
                "로그에서 [EXTRACT] final_chars, [SELF-CHECK] 공약 pdf count를 확인하세요."
            )
    logger.info("준비 완료")

STATIC_DIR = ROOT_DIR / "static"
AUTH_COOKIE = "policy_auth"
AUTH_COOKIE_MAX_AGE = 7 * 24 * 3600


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else (request.headers.get("x-forwarded-for", "").split(",")[0].strip() or "0.0.0.0")


def get_current_user(request: Request) -> Optional[dict]:
    token = request.cookies.get(AUTH_COOKIE)
    return verify_session_token(token) if token else None


def require_user(request: Request) -> dict:
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    return user


def require_approved(request: Request) -> dict:
    user = require_user(request)
    # ADMIN_EMAILS에 있으면 항상 승인된 것으로 처리
    if user["email"] in ADMIN_EMAILS:
        return user
    if user["status"] != STATUS_APPROVED:
        log_usage(
            user_id=user["id"],
            ip=_client_ip(request),
            endpoint=request.url.path,
            action="blocked_unapproved",
            input_chars=0,
            output_chars=0,
            model="",
            token_in=None,
            token_out=None,
            cost_estimate=None,
            status_code=403,
            latency_ms=0,
            error_message="승인되지 않은 사용자",
        )
        raise HTTPException(status_code=403, detail="승인되지 않은 사용자입니다. 관리자 승인 후 이용 가능합니다.")
    return user


class PledgeCheckRequest(BaseModel):
    pledge: str = Field(..., description="점검할 출마자 공약 텍스트")


class PledgeCheckResponse(BaseModel):
    result: str = Field(..., description="부합 점검 결과 (판정, 근거, 체크리스트 등)")


def _serve_html(filename: str):
    path = STATIC_DIR / filename
    if path.exists():
        return FileResponse(path, media_type="text/html; charset=utf-8")
    return None


@app.api_route("/", methods=["GET", "HEAD"])
def index():
    """메인 페이지: 서비스 소개 및 공약 점검 진입."""
    res = _serve_html("index.html")
    if res is not None:
        return res
    return {"service": "개혁신당 정책 멘토링", "endpoint": "POST /check"}


def _login_redirect(path: str):
    from urllib.parse import quote
    return RedirectResponse(url=f"/login?next={quote(path)}", status_code=302)


@app.api_route("/pledge", methods=["GET", "HEAD"])
def pledge_page(request: Request):
    """공약 입력·점검 폼 페이지. (승인 사용자 전용)"""
    user = get_current_user(request)
    if not user:
        return _login_redirect(request.url.path)
    if user["status"] != STATUS_APPROVED:
        return RedirectResponse(url="/pending", status_code=302)
    res = _serve_html("pledge.html")
    if res is not None:
        return res
    raise HTTPException(status_code=404, detail="pledge.html not found")


@app.api_route("/signup", methods=["GET", "HEAD"])
def signup_page():
    res = _serve_html("signup.html")
    if res:
        return res
    raise HTTPException(status_code=404, detail="signup.html not found")


@app.api_route("/login", methods=["GET", "HEAD"])
def login_page():
    res = _serve_html("login.html")
    if res:
        return res
    raise HTTPException(status_code=404, detail="login.html not found")


@app.api_route("/pending", methods=["GET", "HEAD"])
def pending_page():
    res = _serve_html("pending.html")
    if res:
        return res
    raise HTTPException(status_code=404, detail="pending.html not found")


@app.api_route("/dashboard", methods=["GET", "HEAD"])
def dashboard_page(request: Request):
    user = get_current_user(request)
    if not user:
        return _login_redirect(request.url.path)
    res = _serve_html("dashboard.html")
    if res:
        return res
    raise HTTPException(status_code=404, detail="dashboard.html not found")


@app.api_route("/admin", methods=["GET", "HEAD"])
def admin_page(request: Request):
    user = get_current_user(request)
    if not user:
        return _login_redirect(request.url.path)
    if user["role"] != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="관리자만 접근 가능합니다.")
    res = _serve_html("admin/index.html")
    if res:
        return res
    raise HTTPException(status_code=404, detail="admin/index.html not found")


@app.api_route("/admin/users", methods=["GET", "HEAD"])
def admin_users_page(request: Request):
    user = get_current_user(request)
    if not user:
        return _login_redirect(request.url.path)
    if user["role"] != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="관리자만 접근 가능합니다.")
    res = _serve_html("admin/users.html")
    if res:
        return res
    raise HTTPException(status_code=404, detail="admin/users.html not found")


@app.api_route("/admin/usage", methods=["GET", "HEAD"])
def admin_usage_page(request: Request):
    user = get_current_user(request)
    if not user:
        return _login_redirect(request.url.path)
    if user["role"] != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="관리자만 접근 가능합니다.")
    res = _serve_html("admin/usage.html")
    if res:
        return res
    raise HTTPException(status_code=404, detail="admin/usage.html not found")


class SignupBody(BaseModel):
    name: str = Field(..., description="이름")
    phone: str = Field(..., description="전화번호")
    email: str = Field(..., description="이메일")
    password: str = Field(..., description="비밀번호")


@app.post("/api/auth/signup")
def api_signup(body: SignupBody):
    ok, msg = auth_signup(body.email, body.password, name=body.name, phone=body.phone)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg}


class LoginBody(BaseModel):
    email: str = Field(..., description="이메일")
    password: str = Field(..., description="비밀번호")
    next: str = Field(default="", description="로그인 후 이동할 경로")


@app.post("/api/auth/login")
def api_login(body: LoginBody, request: Request):
    user = auth_login(body.email, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="이메일 또는 비밀번호가 올바르지 않습니다.")
    if isinstance(user, dict) and user.get("error") == "email_not_verified":
        raise HTTPException(status_code=401, detail="이메일 인증이 필요합니다. 아래 '인증 메일 다시 받기'를 이용하세요.")
    token = create_session_token(user)
    if user["email"] in ADMIN_EMAILS or user["role"] == ROLE_ADMIN:
        redirect_url = "/admin"
    elif user["status"] != STATUS_APPROVED:
        redirect_url = "/pending"
    else:
        next_path = (body.next or "").strip()
        if next_path and next_path.startswith("/") and next_path not in ("/login", "/signup"):
            redirect_url = next_path
        else:
            redirect_url = "/"
    from fastapi.responses import JSONResponse
    resp = JSONResponse({"redirect": redirect_url})
    resp.set_cookie(AUTH_COOKIE, token, max_age=AUTH_COOKIE_MAX_AGE, httponly=True, samesite="lax")
    return resp


class ResendVerificationBody(BaseModel):
    email: str = Field(..., description="이메일")


@app.post("/api/auth/resend-verification")
def api_resend_verification(body: ResendVerificationBody):
    ok, msg = resend_verification_email(body.email)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg}


@app.api_route("/verify-email", methods=["GET", "HEAD"])
def verify_email_page(token: str = Query(default="", alias="token")):
    """이메일 인증 링크 처리. token 검증 후 로그인 페이지로 리다이렉트."""
    from urllib.parse import quote
    ok, msg = verify_email_token(token)
    if ok:
        return RedirectResponse(url="/login?verified=1", status_code=302)
    return RedirectResponse(url=f"/login?verified=0&msg={quote(msg)}", status_code=302)


@app.post("/api/auth/logout")
def api_logout():
    resp = RedirectResponse(url="/", status_code=302)
    resp.delete_cookie(AUTH_COOKIE)
    return resp


@app.get("/api/auth/me")
def api_me(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인 필요")
    return {"id": user["id"], "email": user["email"], "status": user["status"], "role": user["role"]}


@app.get("/api/admin/users/pending")
def api_admin_users_pending(request: Request):
    user = require_user(request)
    if user["role"] != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="관리자 전용")
    return {"users": list_users_pending()}


@app.get("/api/admin/users")
def api_admin_users_all(request: Request):
    user = require_user(request)
    if user["role"] != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="관리자 전용")
    return {"users": list_users_all()}


class ApproveBody(BaseModel):
    user_id: int = Field(..., description="사용자 ID")
    status: str = Field(..., description="APPROVED | REJECTED | SUSPENDED")
    note: str = Field(default="", description="결정 사유")


@app.post("/api/admin/users/approve")
def api_admin_approve(body: ApproveBody, request: Request):
    user = require_user(request)
    if user["role"] != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="관리자 전용")
    if body.status not in ("APPROVED", "REJECTED", "SUSPENDED"):
        raise HTTPException(status_code=400, detail="status must be APPROVED, REJECTED, or SUSPENDED")
    ok = set_user_status(body.user_id, body.status, user["id"], body.note)
    if not ok:
        raise HTTPException(status_code=400, detail="처리 실패")
    return {"message": "처리 완료"}


class DeleteUserBody(BaseModel):
    user_id: int = Field(..., description="사용자 ID")


@app.post("/api/admin/users/delete")
def api_admin_delete_user(body: DeleteUserBody, request: Request):
    user = require_user(request)
    if user["role"] != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="관리자 전용")
    if body.user_id == user["id"]:
        raise HTTPException(status_code=400, detail="자기 자신은 삭제할 수 없습니다.")

    from backend.database import get_connection
    conn = get_connection()
    try:
        # 사용자 존재 확인
        cur = conn.execute("SELECT id, role FROM users WHERE id = ?", (body.user_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
        if row["role"] == ROLE_ADMIN:
            raise HTTPException(status_code=400, detail="관리자 계정은 삭제할 수 없습니다.")

        # 연관 데이터 먼저 삭제 (FK cascade 없음)
        conn.execute("DELETE FROM approval_requests WHERE user_id = ? OR decided_by = ?", (body.user_id, body.user_id))
        conn.execute("DELETE FROM usage_logs WHERE user_id = ?", (body.user_id,))
        conn.execute("DELETE FROM analysis_cache WHERE user_id = ?", (body.user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (body.user_id,))
        conn.commit()
        return {"message": "삭제 완료"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise HTTPException(status_code=500, detail="삭제 중 오류가 발생했습니다.")
    finally:
        conn.close()


@app.get("/api/usage/summary")
def api_usage_summary(request: Request):
    u = require_user(request)
    from backend.database import get_connection
    import time
    today = time.strftime("%Y-%m-%d")
    month = time.strftime("%Y-%m")
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM usage_logs WHERE user_id = ? AND date(created_at) = ? AND status_code >= 200 AND status_code < 300) AS daily_used,
                (SELECT COUNT(*) FROM usage_logs WHERE user_id = ? AND strftime('%Y-%m', created_at) = ? AND status_code >= 200 AND status_code < 300) AS monthly_used
            """,
            (u["id"], today, u["id"], month),
        )
        row = cur.fetchone()
        from backend.config import QUOTA_DAILY, QUOTA_MONTHLY
        return {
            "daily_used": row["daily_used"] if row else 0,
            "monthly_used": row["monthly_used"] if row else 0,
            "daily_limit": QUOTA_DAILY,
            "monthly_limit": QUOTA_MONTHLY,
            "daily_remaining": max(0, QUOTA_DAILY - (row["daily_used"] if row else 0)),
            "monthly_remaining": max(0, QUOTA_MONTHLY - (row["monthly_used"] if row else 0)),
        }
    finally:
        conn.close()


@app.get("/api/admin/usage/stats")
def api_admin_usage_stats(request: Request):
    user = require_user(request)
    if user["role"] != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="관리자 전용")
    period = request.query_params.get("period", "7")
    days = min(90, max(1, int(period) if period.isdigit() else 7))
    from backend.database import get_connection
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            SELECT user_id, COUNT(*) as cnt, SUM(COALESCE(cost_estimate, 0)) as cost
            FROM usage_logs
            WHERE datetime(created_at) >= datetime('now', ?)
            AND status_code >= 200 AND status_code < 300 AND action = 'analysis_run'
            GROUP BY user_id
            ORDER BY cnt DESC
            """,
            (f"-{days} days",),
        )
        rows = cur.fetchall()
        users = {r["user_id"]: r for r in rows}
        user_info = {}
        for uid in users:
            u = get_user(uid)
            user_info[uid] = u["email"] if u else str(uid)
        return {
            "period_days": days,
            "by_user": [{"user_id": r["user_id"], "email": user_info.get(r["user_id"], str(r["user_id"])), "count": r["cnt"], "cost_estimate": r["cost"] or 0} for r in rows],
        }
    finally:
        conn.close()


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
        all_pdfs = list(_iter_doc_files(pdf_dir)) if pdf_dir.exists() else []
    except Exception as e:
        all_pdfs = []
        error_msg = str(e)
    
    from backend.pdf_loader import load_regional_pledges_context
    
    platform = load_platform_context()
    pledges = load_pledges_context()
    regional = load_regional_pledges_context()
    
    # 파일명 추출
    platform_files = [line.split("---")[1].strip() for line in platform.split("\n") if "---" in line and (".pdf" in line or ".txt" in line)] if platform else []
    pledges_files = [line.split("---")[1].strip() for line in pledges.split("\n") if "---" in line and (".pdf" in line or ".txt" in line)] if pledges else []
    regional_files = [line.split("---")[1].strip() for line in regional.split("\n") if "---" in line and (".pdf" in line or ".txt" in line)] if regional else []
    
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


def _get_fs_debug() -> dict:
    """PDF 디렉터리·폴더별 파일 수·샘플 파일명 (GET /api/debug/fs용)."""
    pdf_dir = Path(PDF_DIR).resolve()
    folders = [
        ("정강정책", pdf_dir / _nfc("정강정책")),
        ("공약", pdf_dir / _nfc("공약")),
        ("지역별 공약", pdf_dir / _nfc("지역별 공약")),
    ]
    by_folder = {}
    for name, dir_path in folders:
        exists = dir_path.exists()
        try:
            pdf_list = list(_iter_doc_files(dir_path)) if exists else []
        except Exception:
            pdf_list = []
        by_folder[name] = {
            "exists": exists,
            "doc_count": len(pdf_list),
            "sample_names": [p.name for p in sorted(pdf_list, key=lambda x: x.name)[:10]],
        }
    return {
        "pdf_dir": str(pdf_dir),
        "pdf_dir_exists": pdf_dir.exists(),
        "folders": by_folder,
    }


def _debug_endpoint(allowed: bool = True):
    """DEBUG_ENDPOINTS_ENABLED=0 시 404 반환."""
    if not allowed or not DEBUG_ENDPOINTS_ENABLED:
        raise HTTPException(status_code=404, detail="Debug endpoint disabled (DEBUG_ENDPOINTS_ENABLED=0)")


@app.get("/api/debug/fs")
def debug_fs():
    """PDF 디렉터리 존재·폴더별 PDF 개수·샘플 파일명. AWS 배포 확인용."""
    _debug_endpoint()
    return _get_fs_debug()


@app.get("/api/debug/vectorstore")
def debug_vectorstore():
    """
    persist_path, collection_name, total_count, embedding_model, embedding_dim, sample_doc 반환.
    AWS 배포 시 벡터스토어 상태 확인용.
    """
    _debug_endpoint()
    global _indexes, _vector_store_id, _regional_vector_store_id
    if USE_OPENAI_VECTOR_STORE:
        return {
            "mode": "openai_vector_store",
            "vector_store_id": _vector_store_id,
            "regional_vector_store_id": _regional_vector_store_id,
            "persist_path": "N/A (OpenAI 호스팅)",
            "collection_names": ["policy-rag-store"],
            "total_count": "N/A",
            "embedding_model_name": "OpenAI file_search",
            "embedding_dim": "N/A",
            "sample_doc": None,
        }
    if _indexes is None:
        raise HTTPException(status_code=503, detail="인덱스가 아직 초기화되지 않았습니다.")
    from backend.config import EMBEDDING_MODEL, EMBEDDING_DIMENSION
    cache_dir = Path(INDEX_CACHE_DIR).resolve()
    collections = ["platform", "pledge", "regional"]
    total_count = sum((_indexes.get(k).size() if _indexes.get(k) else 0) for k in collections)
    sample = None
    for name in collections:
        idx = _indexes.get(name)
        if idx and idx.chunks:
            c = idx.chunks[0]
            sample = {
                "collection": name,
                "doc_id": c.doc_id,
                "path": c.path,
                "text_length": len(c.text),
                "snippet": (c.text[:150] + "...") if len(c.text) > 150 else c.text,
            }
            break
    return {
        "persist_path": str(cache_dir),
        "collection_names": collections,
        "total_count": total_count,
        "embedding_model_name": EMBEDDING_MODEL,
        "embedding_dim": EMBEDDING_DIMENSION,
        "sample_doc": sample,
    }


@app.get("/api/debug/models")
def debug_models():
    """현재 서버에서 사용 중인 OpenAI 모델명을 반환 (AWS 등 배포 환경 확인용)."""
    _debug_endpoint()
    return {
        "openai_model": OPENAI_MODEL,
        "chat_model": CHAT_MODEL,
        "hint": "/check 는 OPENAI_MODEL, /api/pledge/verify·카드는 CHAT_MODEL 사용. 동일하게 쓰려면 .env에 둘 다 설정.",
    }


@app.get("/api/debug/context-summary")
def debug_context_summary():
    """
    폴더별 PDF 파일 수·추출 성공 수·총 문자 수. 로컬 vs AWS 비교용.
    수치가 AWS에서 현저히 작으면 PDF 추출이 다르게 되고 있는 것이므로 출력 차이 원인일 수 있음.
    """
    _debug_endpoint()
    from backend.config import PDF_EXTRACTOR
    try:
        summary = get_context_summary()
        return {
            "pdf_extractor": PDF_EXTRACTOR,
            "context": summary,
            "hint": "로컬과 AWS에서 이 수치를 비교하세요. total_chars 차이가 크면 추출이 다릅니다.",
        }
    except Exception as e:
        logger.exception("context-summary 실패")
        return {"error": str(e), "context": {}}


@app.get("/api/debug/index")
def debug_index():
    """인덱스 벡터 수를 반환하는 디버깅 엔드포인트."""
    _debug_endpoint()
    global _indexes, _vector_store_id, _regional_vector_store_id
    if USE_OPENAI_VECTOR_STORE:
        return {
            "mode": "openai_vector_store",
            "vector_store_id": _vector_store_id,
            "regional_vector_store_id": _regional_vector_store_id,
            "platform_vectors": 0,
            "pledge_vectors": 0,
            "regional_vectors": 0,
        }
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


def _run_debug_search(source: Literal["platform", "pledge", "regional"], q: str, top_k: int):
    """source/q/top_k로 인덱스 검색 후 [{ path, chunk_id, score, snippet }] 반환."""
    _debug_endpoint()
    global _indexes
    if _indexes is None or not _indexes:
        raise HTTPException(status_code=503, detail="인덱스가 아직 초기화되지 않았습니다.")

    index = _indexes.get(source)
    if index is None:
        raise HTTPException(status_code=500, detail=f"{source} 인덱스가 없습니다.")

    from backend.embeddings import embed_texts
    from backend.report import exact_match_search, _merge_exact_and_embedding

    embeddings = embed_texts([q], batch_size=1)
    if not embeddings:
        raise HTTPException(status_code=500, detail="쿼리 임베딩 생성 실패")

    query_embedding = embeddings[0]
    exact_hits = exact_match_search(q, index, top_k_exact=min(5, top_k))
    emb_hits = index.search(query_embedding, k=top_k)
    merged = _merge_exact_and_embedding(exact_hits, emb_hits, top_k)

    return [
        {
            "path": chunk.path,
            "chunk_id": chunk.chunk_id,
            "score": round(score, 6),
            "snippet": (chunk.text[:200] + "...") if len(chunk.text) > 200 else chunk.text,
        }
        for chunk, score in merged
    ]


@app.get("/api/debug/search")
def debug_search_get(
    source: Literal["platform", "pledge", "regional"] = Query(..., description="platform | pledge | regional"),
    q: str = Query(..., min_length=1, description="검색 쿼리"),
    top_k: int = Query(10, ge=1, le=50, description="상위 결과 개수"),
):
    """
    특정 인덱스에서 검색 결과를 확인하는 디버깅 엔드포인트 (GET).
    응답: [{ "path": "...", "chunk_id": 0, "score": 0.123, "snippet": "..." }]
    """
    return _run_debug_search(source, q, top_k)


class DebugSearchBody(BaseModel):
    source: Literal["platform", "pledge", "regional"] = Field(..., description="platform | pledge | regional")
    q: str = Field(..., min_length=1, description="검색 쿼리")
    top_k: int = Field(10, ge=1, le=50, description="상위 결과 개수")


@app.post("/api/debug/search")
def debug_search_post(body: DebugSearchBody):
    """
    특정 인덱스에서 검색 (POST, JSON 바디).
    응답: [{ "path": "...", "chunk_id": 0, "score": 0.123, "snippet": "..." }]
    """
    return _run_debug_search(body.source, body.q, body.top_k)


@app.get("/api/debug/scan")
def debug_scan():
    """PDF 폴더 구조 및 파일 목록을 반환하는 디버깅 엔드포인트."""
    _debug_endpoint()
    base_dir = PDF_DIR

    def list_files(subdir_name: str):
        subdir = base_dir / subdir_name
        if not subdir.exists():
            return []
        return [str(p.relative_to(base_dir)) for p in _iter_doc_files(subdir)]

    return {
        "platform_files": list_files("정강정책"),
        "pledge_files": list_files("공약"),
        "regional_files": list_files("지역별 공약"),
    }


@app.post("/check", response_model=PledgeCheckResponse)
def check_pledge(body: PledgeCheckRequest, request: Request):
    """공약을 입력하면 중앙당의 정강정책·공약과의 적합도, 근거, 수정·보완 체크리스트를 반환한다. (승인 사용자 전용)"""
    user = require_approved(request)
    ip = _client_ip(request)
    ok, msg = check_rate_limit_ip(ip)
    if not ok:
        raise HTTPException(status_code=429, detail=msg)
    ok, msg = check_rate_limit_user(user["id"])
    if not ok:
        raise HTTPException(status_code=429, detail=msg)

    from backend.analysis_service import run_check_analysis
    global _indexes, _vector_store_id, _regional_vector_store_id
    vs_id = _vector_store_id if USE_OPENAI_VECTOR_STORE else None
    regional_vs_id = _regional_vector_store_id if USE_OPENAI_VECTOR_STORE else None
    result, status_code, _ = run_check_analysis(
        user["id"],
        body.pledge or "",
        ip,
        vs_id,
        regional_vs_id,
        _indexes if not USE_OPENAI_VECTOR_STORE else None,
    )
    if status_code >= 400:
        raise HTTPException(status_code=status_code, detail=result)
    return PledgeCheckResponse(result=result)


class PledgeVerifyRequest(BaseModel):
    text: str = Field(..., description="검증할 출마자 공약 텍스트")
    top_k_platform: int = Field(default=6, description="정강정책 검색 개수")
    top_k_pledge: int = Field(default=6, description="공약 검색 개수")
    top_k_regional: int = Field(default=8, description="지역별 공약 검색 개수")
    phase: str = Field(default="full", description="quick=1차 빠른 판정(결과 3개, 속도 우선), full=2차 상세 근거·상충 분석(6개)")
    judge: bool = Field(default=False, description="true=strict judge 모드 (evidence, specificity cap, QUERY/VERIFY)")


@app.post("/api/pledge/verify")
def verify_pledge(body: PledgeVerifyRequest, request: Request):
    """
    벡터 검색 기반 공약 검증 리포트를 생성한다. (승인 사용자 전용)
    """
    user = require_approved(request)
    ip = _client_ip(request)
    ok, msg = check_rate_limit_ip(ip)
    if not ok:
        raise HTTPException(status_code=429, detail=msg)
    ok, msg = check_rate_limit_user(user["id"])
    if not ok:
        raise HTTPException(status_code=429, detail=msg)

    global _indexes, _vector_store_id, _regional_vector_store_id
    if USE_OPENAI_VECTOR_STORE and not _vector_store_id:
        raise HTTPException(status_code=503, detail="Vector Store가 준비되지 않았습니다.")
    if not USE_OPENAI_VECTOR_STORE and (not _indexes or not _indexes.get("pledge")):
        raise HTTPException(status_code=503, detail="인덱스가 준비되지 않았습니다.")

    from backend.analysis_service import run_verify_analysis
    options = {
        "top_k_platform": body.top_k_platform,
        "top_k_pledge": body.top_k_pledge,
        "top_k_regional": body.top_k_regional,
        "phase": body.phase or "full",
        "judge": body.judge,
    }
    result, status_code, _ = run_verify_analysis(
        user["id"],
        body.text or "",
        ip,
        options,
        _vector_store_id if USE_OPENAI_VECTOR_STORE else None,
        _regional_vector_store_id if USE_OPENAI_VECTOR_STORE else None,
        _indexes if not USE_OPENAI_VECTOR_STORE else None,
    )
    if status_code >= 400:
        raise HTTPException(status_code=status_code, detail=result.get("detail", result) if isinstance(result, dict) else result)
    return result
