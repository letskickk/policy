"""
쿼터(daily/monthly) 및 레이트리밋(IP, user) 검사.
"""
import time
from collections import OrderedDict, defaultdict
from threading import Lock

from backend.config import (
    QUOTA_DAILY,
    QUOTA_MONTHLY,
    RATE_LIMIT_IP_PER_MIN,
    RATE_LIMIT_USER_PER_MIN,
)
from backend.database import get_connection

_WINDOW_SEC = 60
_rate_lock = Lock()
_rate_ip: dict[str, list[float]] = defaultdict(list)
_rate_user: dict[int, list[float]] = defaultdict(list)


def _clean_old(now: float, timestamps: list[float]) -> None:
    cutoff = now - _WINDOW_SEC
    while timestamps and timestamps[0] < cutoff:
        timestamps.pop(0)


def check_quota(user_id: int) -> tuple[bool, str]:
    """
    user_id의 일일/월간 쿼터 초과 여부 확인.
    usage_logs에서 status_code 2xx인 것만 카운트.
    """
    conn = get_connection()
    try:
        today = time.strftime("%Y-%m-%d")
        month = time.strftime("%Y-%m")
        cur = conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM usage_logs WHERE user_id = ? AND date(created_at) = ? AND status_code >= 200 AND status_code < 300) AS daily,
                (SELECT COUNT(*) FROM usage_logs WHERE user_id = ? AND strftime('%Y-%m', created_at) = ? AND status_code >= 200 AND status_code < 300) AS monthly
            """,
            (user_id, today, user_id, month),
        )
        row = cur.fetchone()
        daily_count = row["daily"] if row else 0
        monthly_count = row["monthly"] if row else 0
        if daily_count >= QUOTA_DAILY:
            return False, f"일일 쿼터 초과 ({QUOTA_DAILY}회/일)"
        if monthly_count >= QUOTA_MONTHLY:
            return False, f"월간 쿼터 초과 ({QUOTA_MONTHLY}회/월)"
        return True, ""
    finally:
        conn.close()


def check_rate_limit_ip(ip: str) -> tuple[bool, str]:
    now = time.time()
    with _rate_lock:
        _clean_old(now, _rate_ip[ip])
        if len(_rate_ip[ip]) >= RATE_LIMIT_IP_PER_MIN:
            return False, f"IP당 분당 {RATE_LIMIT_IP_PER_MIN}회 제한 초과"
        _rate_ip[ip].append(now)
    return True, ""


def check_rate_limit_user(user_id: int) -> tuple[bool, str]:
    now = time.time()
    with _rate_lock:
        _clean_old(now, _rate_user[user_id])
        if len(_rate_user[user_id]) >= RATE_LIMIT_USER_PER_MIN:
            return False, f"사용자당 분당 {RATE_LIMIT_USER_PER_MIN}회 제한 초과"
        _rate_user[user_id].append(now)
    return True, ""
