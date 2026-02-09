"""
사용량 로그 기록 (usage_logs).
"""
import logging
import time
import uuid
from typing import Optional

from backend.database import get_connection

logger = logging.getLogger(__name__)


def log_usage(
    user_id: Optional[int],
    ip: str,
    endpoint: str,
    action: str,
    input_chars: int,
    output_chars: int,
    model: str,
    token_in: Optional[int],
    token_out: Optional[int],
    cost_estimate: Optional[float],
    status_code: int,
    latency_ms: int,
    error_message: Optional[str] = None,
) -> str:
    request_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO usage_logs (
                user_id, ip, endpoint, action, request_id,
                input_chars, output_chars, model, token_in, token_out, cost_estimate,
                status_code, latency_ms, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                ip,
                endpoint,
                action,
                request_id,
                input_chars,
                output_chars,
                model,
                token_in,
                token_out,
                cost_estimate,
                status_code,
                latency_ms,
                error_message,
            ),
        )
        conn.commit()
        return request_id
    except Exception as e:
        conn.rollback()
        logger.exception("usage log failed: %s", e)
        return request_id
    finally:
        conn.close()


def _estimate_cost(token_in: int, token_out: int, model: str) -> float:
    if "gpt-4" in model or "gpt-5" in model:
        return (token_in * 0.00001 + token_out * 0.00003)
    return (token_in * 0.0000001 + token_out * 0.0000002)
