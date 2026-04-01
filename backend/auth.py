"""
회원가입 / 로그인 / 세션 / 승인 상태.
"""
import hashlib
import logging
import os
import re
import secrets
import time
from datetime import datetime, timezone
from typing import Optional

from backend.config import ADMIN_EMAILS, SESSION_SECRET, EMAIL_VERIFICATION_ENABLED
from backend.database import get_connection

logger = logging.getLogger(__name__)

try:
    from passlib.hash import pbkdf2_sha256 as PASSWORD_HASHER
except Exception:
    from passlib.registry import get_crypt_handler
    PASSWORD_HASHER = get_crypt_handler("pbkdf2_sha256")

STATUS_PENDING = "PENDING"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"
STATUS_SUSPENDED = "SUSPENDED"
ROLE_USER = "USER"
ROLE_ADMIN = "ADMIN"


def _hash_password(password: str) -> str:
    # bcrypt 대신 pbkdf2 사용 (72바이트 제한 없음)
    return PASSWORD_HASHER.using(rounds=100000).hash(password)


def _verify_password(password: str, hash_: str) -> bool:
    try:
        return PASSWORD_HASHER.verify(password, hash_)
    except Exception:
        return False


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _valid_email(email: str) -> bool:
    if not email or len(email) > 254:
        return False
    return bool(re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email))


def signup(
    email: str,
    password: str,
    name: str = "",
    phone: str = "",
    election_position: str = "",
    region_code: str = "",
    region_name: str = "",
    district_code: str = "",
    district_name: str = "",
) -> tuple[bool, str]:
    """
    회원가입. 성공 시 (True, 메시지), 실패 시 (False, 오류메시지).
    EMAIL_VERIFICATION_ENABLED 시 인증 메일 발송.
    """
    email = (email or "").strip().lower()
    name = (name or "").strip()
    phone = (phone or "").strip()
    if not _valid_email(email):
        return False, "올바른 이메일을 입력하세요."
    if not password or len(password) < 6:
        return False, "비밀번호는 6자 이상이어야 합니다."
    if not name:
        return False, "이름을 입력하세요."
    if not phone:
        return False, "전화번호를 입력하세요."

    ep_lower = (election_position or "").strip().lower()
    if ep_lower != "party_official" and not (region_code or "").strip():
        return False, "시·도를 선택하세요."
    if ep_lower in ("regional_council", "local_mayor", "local_council") and not (district_code or "").strip():
        return False, "선거구를 선택하세요."

    role = ROLE_ADMIN if email in ADMIN_EMAILS else ROLE_USER
    status = STATUS_APPROVED if email in ADMIN_EMAILS else STATUS_PENDING
    email_verified = 1
    verification_token = None
    verification_expires_at = None

    # 관리자는 이메일 인증 건너뜀
    if EMAIL_VERIFICATION_ENABLED and email not in ADMIN_EMAILS:
        email_verified = 0
        verification_token = secrets.token_urlsafe(32)
        from datetime import timedelta
        verification_expires_at = (datetime.now(timezone.utc) + timedelta(hours=72)).isoformat()

    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT id FROM users WHERE email = ?", (email,)
        )
        if cur.fetchone():
            return False, "이미 등록된 이메일입니다."
        ep = (election_position or "").strip()
        rc = (region_code or "").strip()
        rn = (region_name or "").strip()
        dc = (district_code or "").strip()
        dn = (district_name or "").strip()
        conn.execute(
            """INSERT INTO users (email, password_hash, status, role, email_verified, verification_token, verification_expires_at, name, phone, election_position, region_code, region_name, district_code, district_name)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (email, _hash_password(password), status, role, email_verified, verification_token, verification_expires_at, name, phone, ep or None, rc or None, rn or None, dc or None, dn or None),
        )
        conn.commit()
        # 가입 직후 지원서 자동 검증 (실패해도 가입에 영향 없음)
        try:
            new_user = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
            if new_user:
                from backend.applicant_verify import verify_user_against_applicants
                verify_user_against_applicants(
                    new_user["id"],
                    user_phone=phone,
                    user_email=email,
                    user_name=name,
                    user_region=rn,
                )
        except Exception:
            logger.warning("지원서 자동 검증 실패 (가입 자체는 정상 처리)")
        # 관리자에게 가입 알림 메일 (비동기적으로, 실패해도 가입에 영향 없음)
        try:
            from backend.email_sender import send_signup_notification
            send_signup_notification(
                user_email=email, name=name, phone=phone,
                election_position=ep, region_name=rn, district_name=dn,
            )
        except Exception:
            logger.warning("가입 알림 메일 발송 실패 (가입 자체는 정상 처리)")

        if EMAIL_VERIFICATION_ENABLED and verification_token:
            from backend.email_sender import send_verification_email
            if send_verification_email(email, verification_token):
                return True, "가입 완료. 입력한 이메일로 인증 링크를 보냈습니다. 링크를 클릭한 뒤 로그인하세요."
            return True, "가입 완료. (이메일 발송 실패. 관리자에게 문의하세요.)"
        return True, "가입 완료. 관리자 승인 후 이용 가능합니다."
    except Exception as e:
        conn.rollback()
        logger.exception("signup failed")
        msg = str(e)
        if "no such table" in msg.lower():
            return False, "DB가 초기화되지 않았습니다. python scripts/init_db.py 를 실행하세요."
        if "passlib" in msg or "bcrypt" in msg.lower():
            return False, "passlib가 없습니다. pip install \"passlib[bcrypt]\" 실행 후 서버 재시작."
        return False, f"등록 중 오류: {msg[:120]}"
    finally:
        conn.close()


def login(email: str, password: str) -> Optional[dict]:
    """
    로그인. 성공 시 {"id", "email", "status", "role"} 반환, 실패 시 None.
    EMAIL_VERIFICATION_ENABLED 시 이메일 미인증 사용자는 None 반환 (호출부에서 메시지 처리).
    """
    email = (email or "").strip().lower()
    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT id, email, password_hash, status, role, COALESCE(email_verified, 1) as email_verified FROM users WHERE email = ?",
            (email,),
        )
        row = cur.fetchone()
        if not row:
            return None
        if not _verify_password(password, row["password_hash"]):
            return None
        # 관리자는 이메일 인증 건너뜀
        if EMAIL_VERIFICATION_ENABLED and email not in ADMIN_EMAILS and not row["email_verified"]:
            return {"error": "email_not_verified"}
        status, role = row["status"], row["role"]
        # ADMIN_EMAILS에 있으면 DB에서도 승인·관리자로 맞춰둠 (나중에 .env 추가한 경우 대비)
        if email in ADMIN_EMAILS and (status != STATUS_APPROVED or role != ROLE_ADMIN):
            status, role = STATUS_APPROVED, ROLE_ADMIN
            conn.execute(
                "UPDATE users SET status = ?, role = ?, last_login_at = ?, updated_at = ? WHERE id = ?",
                (status, role, _now_utc(), _now_utc(), row["id"]),
            )
        else:
            conn.execute(
                "UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?",
                (_now_utc(), _now_utc(), row["id"]),
            )
        conn.commit()
        return {
            "id": row["id"],
            "email": row["email"],
            "status": status,
            "role": role,
        }
    finally:
        conn.close()


def verify_email_token(token: str) -> tuple[bool, str]:
    """
    인증 토큰으로 이메일 인증 완료.
    토큰은 인증 후에도 유지하여 재방문 시 "이미 완료" 응답 가능.
    Returns: (성공여부, 메시지)
    """
    if not token or not token.strip():
        return False, "유효하지 않은 링크입니다."
    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT id, email, COALESCE(email_verified, 0) as email_verified, verification_expires_at FROM users WHERE verification_token = ?",
            (token.strip(),),
        )
        row = cur.fetchone()
        if not row:
            return False, "유효하지 않거나 만료된 링크입니다."

        if row["email_verified"]:
            return True, "이메일 인증이 이미 완료되었습니다. 로그인해 주세요."

        user_email = (row["email"] or "").strip().lower()
        is_admin = user_email in ADMIN_EMAILS
        expires = row["verification_expires_at"]
        if expires and not is_admin:
            try:
                exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                if datetime.now(timezone.utc) > exp_dt:
                    return False, "인증 링크가 만료되었습니다. 로그인 페이지에서 '인증 메일 다시 받기'를 이용해 주세요."
            except Exception:
                pass
        if is_admin:
            conn.execute(
                "UPDATE users SET email_verified = 1, verification_expires_at = NULL, status = ?, role = ?, updated_at = ? WHERE id = ?",
                (STATUS_APPROVED, ROLE_ADMIN, _now_utc(), row["id"]),
            )
        else:
            conn.execute(
                "UPDATE users SET email_verified = 1, verification_expires_at = NULL, updated_at = ? WHERE id = ?",
                (_now_utc(), row["id"]),
            )
        conn.commit()
        return True, "이메일 인증이 완료되었습니다. 로그인해 주세요."
    except Exception as e:
        conn.rollback()
        logger.exception("verify_email_token failed: %s", e)
        return False, "처리 중 오류가 발생했습니다."
    finally:
        conn.close()


def resend_verification_email(email: str) -> tuple[bool, str]:
    """
    인증 메일 재발송. (메일 못 받았거나 만료 시)
    Returns: (성공여부, 메시지)
    """
    if not EMAIL_VERIFICATION_ENABLED:
        return False, "이메일 인증이 비활성화되어 있습니다."
    email = (email or "").strip().lower()
    if not _valid_email(email):
        return False, "올바른 이메일을 입력하세요."
    if email in ADMIN_EMAILS:
        return False, "관리자 계정은 인증이 필요하지 않습니다."
    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT id, COALESCE(email_verified, 1) as email_verified FROM users WHERE email = ?",
            (email,),
        )
        row = cur.fetchone()
        if not row:
            return False, "등록된 이메일이 없습니다. 먼저 회원가입해 주세요."
        if row["email_verified"]:
            return False, "이미 인증된 계정입니다. 로그인해 주세요."
        token = secrets.token_urlsafe(32)
        from datetime import timedelta
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=72)).isoformat()
        conn.execute(
            "UPDATE users SET verification_token = ?, verification_expires_at = ?, updated_at = ? WHERE id = ?",
            (token, expires_at, _now_utc(), row["id"]),
        )
        conn.commit()
        from backend.email_sender import send_verification_email
        if send_verification_email(email, token):
            return True, "인증 메일을 다시 발송했습니다. 이메일을 확인해 주세요."
        return False, "메일 발송에 실패했습니다. 잠시 후 다시 시도하거나 관리자에게 문의하세요."
    except Exception as e:
        conn.rollback()
        logger.exception("resend_verification_email failed: %s", e)
        return False, "처리 중 오류가 발생했습니다."
    finally:
        conn.close()


def get_user(user_id: int) -> Optional[dict]:
    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT id, email, name, status, role, election_position, region_code, region_name, district_code, district_name FROM users WHERE id = ?",
            (user_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def set_user_status(user_id: int, status: str, admin_id: int, note: str = "") -> bool:
    if status not in (STATUS_APPROVED, STATUS_REJECTED, STATUS_SUSPENDED):
        return False
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE users SET status = ?, updated_at = ? WHERE id = ?",
            (status, _now_utc(), user_id),
        )
        conn.execute(
            "INSERT INTO approval_requests (user_id, decided_by, decided_at, decision_note) VALUES (?, ?, ?, ?)",
            (user_id, admin_id, _now_utc(), note or ""),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def list_users_pending() -> list[dict]:
    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT id, email, status, role, created_at, name, phone, election_position, region_code, region_name, district_code, district_name, applicant_verified, applicant_match_id, applicant_match_note FROM users WHERE status = ? ORDER BY created_at",
            (STATUS_PENDING,),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def list_users_all() -> list[dict]:
    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT id, email, status, role, created_at, last_login_at, name, phone, election_position, region_code, region_name, district_code, district_name, applicant_verified, applicant_match_id, applicant_match_note FROM users ORDER BY created_at DESC"
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


SESSION_MAX_AGE_SECONDS = 7 * 24 * 3600  # 7 days


def create_session_token(user: dict) -> str:
    ts = int(time.time())
    data = f"{user['id']}:{user['email']}:{user['status']}:{user['role']}"
    h = hashlib.sha256((SESSION_SECRET + data + str(ts)).encode()).hexdigest()
    return f"{user['id']}.{ts}.{h[:32]}"


def verify_session_token(token: str) -> Optional[dict]:
    if not token:
        return None
    parts = token.split(".")
    # Support both old format (id.hash) and new format (id.timestamp.hash)
    if len(parts) == 2:
        # Legacy token — no expiry, accept for backward compatibility
        try:
            uid = int(parts[0])
        except ValueError:
            return None
        user = get_user(uid)
        if not user:
            return None
        old_data = f"{user['id']}:{user['email']}:{user['status']}:{user['role']}"
        old_h = hashlib.sha256((SESSION_SECRET + old_data).encode()).hexdigest()
        old_expected = f"{user['id']}.{old_h[:32]}"
        if not secrets.compare_digest(token, old_expected):
            return None
        return user
    elif len(parts) == 3:
        try:
            uid = int(parts[0])
            ts = int(parts[1])
        except ValueError:
            return None
        if time.time() - ts > SESSION_MAX_AGE_SECONDS:
            return None
        user = get_user(uid)
        if not user:
            return None
        # Recompute hash with the ORIGINAL timestamp from the token
        data = f"{user['id']}:{user['email']}:{user['status']}:{user['role']}"
        h = hashlib.sha256((SESSION_SECRET + data + str(ts)).encode()).hexdigest()
        expected = f"{user['id']}.{ts}.{h[:32]}"
        if not secrets.compare_digest(token, expected):
            return None
        return user
    return None
