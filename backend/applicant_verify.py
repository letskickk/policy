"""
Applicant list based verification helpers.

Used after signup and after admin Excel uploads to re-check existing users.
"""

import logging
import re
from typing import Optional

from backend.database import get_connection

logger = logging.getLogger(__name__)


def _normalize_phone(phone: str) -> str:
    return re.sub(r"[^0-9]", "", (phone or "").strip())


def _normalize_name(name: str) -> str:
    return re.sub(r"\s+", "", (name or "").strip()).lower()


def _normalize_region(region: str) -> str:
    return re.sub(r"\s+", "", (region or "").strip()).lower()


def _region_matches(user_region: str, applicant_region: str, applicant_district: str) -> bool:
    user_region_norm = _normalize_region(user_region)
    if not user_region_norm:
        return False
    for candidate in (applicant_region, applicant_district):
        candidate_norm = _normalize_region(candidate)
        if not candidate_norm:
            continue
        if user_region_norm in candidate_norm or candidate_norm in user_region_norm:
            return True
    return False


def _find_unique_match(conn, where_sql: str, params: tuple) -> Optional[dict]:
    rows = conn.execute(
        f"SELECT * FROM party_applicants WHERE {where_sql} ORDER BY id LIMIT 2",
        params,
    ).fetchall()
    if len(rows) == 1:
        return rows[0]
    return None


def verify_user_against_applicants(
    user_id: int,
    user_phone: str = "",
    user_email: str = "",
    user_name: str = "",
    user_region: str = "",
) -> dict:
    """
    Compare one user against party_applicants and persist the result on users.

    Returns: {"verified": 0|1|-1, "match_id": int|None, "note": str}
    """

    conn = get_connection()
    try:
        phone_norm = _normalize_phone(user_phone)
        email_lower = (user_email or "").strip().lower()
        name_norm = _normalize_name(user_name)
        region_clean = (user_region or "").strip()

        match = None
        match_method = ""

        if email_lower and name_norm:
            match = _find_unique_match(
                conn,
                "lower(trim(coalesce(email, ''))) = ? AND lower(replace(trim(coalesce(name, '')), ' ', '')) = ?",
                (email_lower, name_norm),
            )
            if match:
                match_method = "email+name"

        if match is None and phone_norm and name_norm:
            match = _find_unique_match(
                conn,
                "replace(replace(replace(replace(trim(coalesce(phone, '')), '-', ''), ' ', ''), '(', ''), ')', '') = ? "
                "AND lower(replace(trim(coalesce(name, '')), ' ', '')) = ?",
                (phone_norm, name_norm),
            )
            if match:
                match_method = "phone+name"

        if match is None and email_lower and region_clean:
            candidate = _find_unique_match(
                conn,
                "lower(trim(coalesce(email, ''))) = ?",
                (email_lower,),
            )
            if candidate and _region_matches(
                region_clean,
                candidate["region_province"] or "",
                candidate["district_info"] or "",
            ):
                match = candidate
                match_method = "email+region"

        if match is None and phone_norm and region_clean:
            candidate = _find_unique_match(
                conn,
                "replace(replace(replace(replace(trim(coalesce(phone, '')), '-', ''), ' ', ''), '(', ''), ')', '') = ?",
                (phone_norm,),
            )
            if candidate and _region_matches(
                region_clean,
                candidate["region_province"] or "",
                candidate["district_info"] or "",
            ):
                match = candidate
                match_method = "phone+region"

        if match is None and email_lower:
            match = _find_unique_match(
                conn,
                "lower(trim(coalesce(email, ''))) = ?",
                (email_lower,),
            )
            if match:
                match_method = "email"

        if match is None and phone_norm:
            match = _find_unique_match(
                conn,
                "replace(replace(replace(replace(trim(coalesce(phone, '')), '-', ''), ' ', ''), '(', ''), ')', '') = ?",
                (phone_norm,),
            )
            if match:
                match_method = "phone"

        if match:
            status_note = (match["status_note"] or "").strip()
            note = match_method
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
    except Exception as exc:
        logger.warning("Applicant verification failed (user_id=%s): %s", user_id, exc)
        return {"verified": 0, "match_id": None, "note": "검증 오류"}
    finally:
        conn.close()


def reverify_all_users() -> int:
    """Re-run applicant verification for every user."""

    conn = get_connection()
    try:
        users = conn.execute(
            "SELECT id, phone, email, name, region_name FROM users"
        ).fetchall()
    finally:
        conn.close()

    count = 0
    for user in users:
        verify_user_against_applicants(
            user["id"],
            user_phone=user["phone"] or "",
            user_email=user["email"] or "",
            user_name=user["name"] or "",
            user_region=user["region_name"] or "",
        )
        count += 1
    return count
