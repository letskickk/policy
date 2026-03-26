"""
Analysis history storage (analysis_history).
"""

import json
import logging
from typing import Any, Optional

from backend.database import get_connection
from backend.score_parser import parse_total_score_any

logger = logging.getLogger(__name__)


def _truncate(s: str, limit: int) -> str:
    if s is None:
        return ""
    s = str(s)
    return s if len(s) <= limit else (s[:limit] + "\n\n[...truncated...]")


def _coerce_history_score(row: dict) -> dict:
    """Backfill missing score values from stored result text for older rows."""
    if row.get("total_score") is None and int(row.get("status_code") or 0) == 200:
        score = parse_total_score_any(row.get("result_text") or "", row.get("result_format") or "text")
        if score is not None:
            row["total_score"] = score
    return row


def add_history(
    *,
    user_id: int,
    kind: str,
    input_text: str,
    result: Any,
    status_code: int,
    from_cache: bool,
    options: Optional[dict] = None,
    keep_per_user: int = 50,
) -> None:
    """
    Save analysis result to history, keeping only recent keep_per_user per user.
    """
    if kind not in ("check", "verify", "draft"):
        kind = "check"

    input_text_s = _truncate((input_text or "").strip(), 20000)

    if isinstance(result, (dict, list)):
        result_text = json.dumps(result, ensure_ascii=False)
        result_format = "json"
    else:
        result_text = str(result or "")
        result_format = "text"
    result_text = _truncate(result_text, 60000)

    options_json = json.dumps(options, ensure_ascii=False, sort_keys=True) if options else None

    # Extract total score for leaderboard
    score = parse_total_score_any(result_text, result_format) if int(status_code) == 200 else None

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO analysis_history (
              user_id, kind, input_text, options_json, result_text, result_format,
              status_code, from_cache, total_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                kind,
                input_text_s,
                options_json,
                result_text,
                result_format,
                int(status_code),
                1 if from_cache else 0,
                score,
            ),
        )
        # prune old rows for this user
        conn.execute(
            """
            DELETE FROM analysis_history
            WHERE user_id = ?
              AND id NOT IN (
                SELECT id FROM analysis_history
                WHERE user_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
              )
            """,
            (user_id, user_id, int(keep_per_user)),
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.warning("history save failed: %s", e)
    finally:
        conn.close()


def list_history(user_id: int, limit: int = 20) -> list[dict]:
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            SELECT id, kind, created_at, status_code, from_cache,
                   substr(input_text, 1, 160) AS input_preview,
                   substr(result_text, 1, 220) AS result_preview,
                   result_text, result_format, total_score
            FROM analysis_history
            WHERE user_id = ? AND kind != 'verify'
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (user_id, int(limit)),
        )
        rows = []
        for raw in cur.fetchall():
            row = dict(raw)
            rows.append(_coerce_history_score(row))
        return rows
    finally:
        conn.close()


def get_history_item(user_id: int, history_id: int) -> Optional[dict]:
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            SELECT id, kind, created_at, status_code, from_cache,
                   input_text, options_json, result_text, result_format, total_score
            FROM analysis_history
            WHERE user_id = ? AND id = ?
            """,
            (user_id, int(history_id)),
        )
        row = cur.fetchone()
        if not row:
            return None
        d = dict(row)
        d = _coerce_history_score(d)
        # parse options_json lazily
        if d.get("options_json"):
            try:
                d["options"] = json.loads(d["options_json"])
            except Exception:
                d["options"] = None
        else:
            d["options"] = None
        d.pop("options_json", None)
        return d
    finally:
        conn.close()


def delete_history_item(user_id: int, history_id: int) -> bool:
    conn = get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM analysis_history WHERE user_id = ? AND id = ?",
            (user_id, int(history_id)),
        )
        conn.commit()
        return (cur.rowcount or 0) > 0
    except Exception as e:
        conn.rollback()
        logger.warning("history delete failed: %s", e)
        return False
    finally:
        conn.close()


def clear_history(user_id: int) -> int:
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM analysis_history WHERE user_id = ?", (user_id,))
        conn.commit()
        return int(cur.rowcount or 0)
    except Exception as e:
        conn.rollback()
        logger.warning("history clear failed: %s", e)
        return 0
    finally:
        conn.close()
