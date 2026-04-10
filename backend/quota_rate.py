"""
쿼터(daily/monthly) 및 레이트리밋(IP, user) 검사.
관리자(ADMIN role 또는 ADMIN_EMAILS)는 일일/월간 쿼터 적용 안 함.
"""
import time
from collections import OrderedDict, defaultdict
from threading import Lock

from backend.auth import ROLE_ADMIN, get_user
from backend.config import (
    ADMIN_EMAILS,
    QUOTA_DAILY,
    QUOTA_MONTHLY,
    QUOTA_DAILY_TOKENS,
    QUOTA_MONTHLY_TOKENS,
    RATE_LIMIT_IP_PER_MIN,
    RATE_LIMIT_USER_PER_MIN,
)
from backend.database import get_connection

_WINDOW_SEC = 60
_rate_lock = Lock()
_rate_ip: dict[str, list[float]] = defaultdict(list)
_rate_user: dict[int, list[float]] = defaultdict(list)
UNLIMITED_QUOTA_TEST_EMAILS = {
    "gtest@test.kr",
    "ktest@test.kr",
    "atest@test.kr",
}


def _clean_old(now: float, timestamps: list[float]) -> None:
    cutoff = now - _WINDOW_SEC
    while timestamps and timestamps[0] < cutoff:
        timestamps.pop(0)


def is_unlimited_quota_user(user: dict | None) -> bool:
    if not user:
        return False
    email = (user.get("email") or "").strip().lower()
    return (
        user.get("role") == ROLE_ADMIN
        or email in ADMIN_EMAILS
        or email in UNLIMITED_QUOTA_TEST_EMAILS
    )


def check_quota(user_id: int) -> tuple[bool, str]:
    """
    user_id의 일일/월간 토큰 쿼터 초과 여부 확인.
    usage_logs에서 status_code 2xx인 token_in+token_out 합산.
    관리자(role=ADMIN 또는 ADMIN_EMAILS)는 쿼터 미적용.
    """
    user = get_user(user_id)
    if is_unlimited_quota_user(user):
        return True, ""

    conn = get_connection()
    try:
        today = time.strftime("%Y-%m-%d")
        month = time.strftime("%Y-%m")
        cur = conn.execute(
            """
            SELECT
                COALESCE((SELECT SUM(COALESCE(token_in,0)+COALESCE(token_out,0)) FROM usage_logs WHERE user_id = ? AND date(created_at) = ? AND status_code >= 200 AND status_code < 300 AND endpoint != '/api/pledge/verify'), 0) AS daily_tokens,
                COALESCE((SELECT SUM(COALESCE(token_in,0)+COALESCE(token_out,0)) FROM usage_logs WHERE user_id = ? AND strftime('%Y-%m', created_at) = ? AND status_code >= 200 AND status_code < 300 AND endpoint != '/api/pledge/verify'), 0) AS monthly_tokens
            """,
            (user_id, today, user_id, month),
        )
        row = cur.fetchone()
        daily_tokens = row["daily_tokens"] if row else 0
        monthly_tokens = row["monthly_tokens"] if row else 0
        if daily_tokens >= QUOTA_DAILY_TOKENS:
            return False, f"일일 토큰 한도 초과 ({QUOTA_DAILY_TOKENS // 1000}K토큰/일)"
        if monthly_tokens >= QUOTA_MONTHLY_TOKENS:
            return False, f"월간 토큰 한도 초과 ({QUOTA_MONTHLY_TOKENS // 1000}K토큰/월)"
        return True, ""
    finally:
        conn.close()


def check_rate_limit_ip(ip: str) -> tuple[bool, str]:
    if ip in ("127.0.0.1", "::1", "localhost"):
        return True, ""
    now = time.time()
    with _rate_lock:
        _clean_old(now, _rate_ip[ip])
        if len(_rate_ip[ip]) >= RATE_LIMIT_IP_PER_MIN:
            return False, f"IP당 분당 {RATE_LIMIT_IP_PER_MIN}회 제한 초과"
        _rate_ip[ip].append(now)
    return True, ""


def check_rate_limit_user(user_id: int) -> tuple[bool, str]:
    user = get_user(user_id)
    if is_unlimited_quota_user(user):
        return True, ""
    now = time.time()
    with _rate_lock:
        _clean_old(now, _rate_user[user_id])
        if len(_rate_user[user_id]) >= RATE_LIMIT_USER_PER_MIN:
            return False, f"사용자당 분당 {RATE_LIMIT_USER_PER_MIN}회 제한 초과"
        _rate_user[user_id].append(now)
    return True, ""
