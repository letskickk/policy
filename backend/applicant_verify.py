"""
당원 명단(party_applicants) 기반 본인 확인.
회원가입 시 또는 엑셀 업로드 후 기존 사용자 재검증에 사용.
"""
import logging
import re
from typing import Optional

from backend.database import get_connection

logger = logging.getLogger(__name__)


def _normalize_phone(phone: str) -> str:
    """하이픈·공백 제거, 숫자만 남긴다."""
    return re.sub(r"[^0-9]", "", (phone or "").strip())


def verify_user_against_applicants(
    user_id: int,
    user_phone: str = "",
    user_email: str = "",
    user_name: str = "",
    user_region: str = "",
) -> dict:
    """
    party_applicants 테이블과 대조하여 매칭 결과를 users 테이블에 기록한다.

    Returns: {"verified": 0|1|-1, "match_id": int|None, "note": str}
    """
    conn = get_connection()
    try:
        phone_norm = _normalize_phone(user_phone)
        email_lower = (user_email or "").strip().lower()
        name_clean = (user_name or "").strip()
        region_clean = (user_region or "").strip()

        match = None
        match_method = ""

        # 1차: 전화번호 매칭 (가장 신뢰)
        if phone_norm:
            row = conn.execute(
                "SELECT * FROM party_applicants WHERE replace(replace(phone, '-', ''), ' ', '') = ?",
                (phone_norm,),
            ).fetchone()
            if row:
                match = row
                match_method = "전화번호 일치"

        # 2차: 이메일 매칭
        if not match and email_lower:
            row = conn.execute(
                "SELECT * FROM party_applicants WHERE lower(trim(email)) = ?",
                (email_lower,),
            ).fetchone()
            if row:
                match = row
                match_method = "이메일 일치"

        # 3차: 이름 + 시·도 매칭
        if not match and name_clean and region_clean:
            row = conn.execute(
                "SELECT * FROM party_applicants WHERE trim(name) = ? AND trim(region_province) = ?",
                (name_clean, region_clean),
            ).fetchone()
            if row:
                match = row
                match_method = "이름+지역 일치"

        if match:
            status_note = (match["status_note"] or "").strip()
            note = f"{match_method}"
            if status_note:
                note += f" / {status_note}"
            verified = 1
            match_id = match["id"]
        else:
            verified = -1
            match_id = None
            note = "명단 미확인"

        conn.execute(
            "UPDATE users SET applicant_verified = ?, applicant_match_id = ?, applicant_match_note = ? WHERE id = ?",
            (verified, match_id, note, user_id),
        )
        conn.commit()
        return {"verified": verified, "match_id": match_id, "note": note}
    except Exception as e:
        logger.warning("본인확인 검증 실패 (user_id=%s): %s", user_id, e)
        return {"verified": 0, "match_id": None, "note": "검증 오류"}
    finally:
        conn.close()


def reverify_all_users() -> int:
    """
    모든 사용자를 party_applicants와 재대조한다.
    엑셀 업로드 후 호출. 반환: 재검증된 사용자 수.
    """
    conn = get_connection()
    try:
        users = conn.execute(
            "SELECT id, phone, email, name, region_name FROM users"
        ).fetchall()
    finally:
        conn.close()

    count = 0
    for u in users:
        verify_user_against_applicants(
            u["id"],
            user_phone=u["phone"] or "",
            user_email=u["email"] or "",
            user_name=u["name"] or "",
            user_region=u["region_name"] or "",
        )
        count += 1
    return count
