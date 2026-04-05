"""
사용량 로그 기록 (usage_logs).
"""
import json
import logging
import uuid
from typing import Optional

from backend.database import get_connection

logger = logging.getLogger(__name__)


MODEL_PRICING_PER_1M = {
    "gpt-5.4": {"input": 2.50, "output": 15.00},
    "gpt-5.4-mini": {"input": 0.750, "output": 4.500},
    "gpt-5.4-nano": {"input": 0.20, "output": 1.25},
    # Family aliases/fallbacks
    "gpt-5": {"input": 2.50, "output": 15.00},
    "gpt-5-mini": {"input": 0.750, "output": 4.500},
    "gpt-5-nano": {"input": 0.20, "output": 1.25},
    "gpt-4.1": {"input": 2.00, "output": 8.00},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "gpt-4.1-nano": {"input": 0.10, "output": 0.40},
}


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


def _normalize_model_name(model: str) -> str:
    value = (model or "").strip().lower()
    if not value:
        return ""
    aliases = {
        "gpt-5.4 mini": "gpt-5.4-mini",
        "gpt-5.4 nano": "gpt-5.4-nano",
        "gpt-5 mini": "gpt-5-mini",
        "gpt-5 nano": "gpt-5-nano",
    }
    return aliases.get(value, value)


def _estimate_cost(token_in: int, token_out: int, model: str) -> float:
    normalized = _normalize_model_name(model)
    pricing = MODEL_PRICING_PER_1M.get(normalized)
    if pricing:
        return (
            (max(token_in or 0, 0) / 1_000_000.0) * pricing["input"]
            + (max(token_out or 0, 0) / 1_000_000.0) * pricing["output"]
        )

    if "gpt-5" in normalized:
        pricing = MODEL_PRICING_PER_1M["gpt-5"]
    elif "gpt-4.1-mini" in normalized:
        pricing = MODEL_PRICING_PER_1M["gpt-4.1-mini"]
    elif "gpt-4.1-nano" in normalized:
        pricing = MODEL_PRICING_PER_1M["gpt-4.1-nano"]
    elif "gpt-4.1" in normalized:
        pricing = MODEL_PRICING_PER_1M["gpt-4.1"]
    else:
        pricing = {"input": 0.10, "output": 0.40}

    return (
        (max(token_in or 0, 0) / 1_000_000.0) * pricing["input"]
        + (max(token_out or 0, 0) / 1_000_000.0) * pricing["output"]
    )


def parse_usage_marker(marker: str) -> Optional[dict]:
    if not marker or not marker.startswith("[USAGE]"):
        return None

    payload = marker[7:]
    if not payload:
        return {"model": "", "token_in": 0, "token_out": 0}

    if payload.startswith("{"):
        try:
            data = json.loads(payload)
            return {
                "model": str(data.get("model", "")).strip(),
                "token_in": int(data.get("input_tokens") or 0),
                "token_out": int(data.get("output_tokens") or 0),
            }
        except Exception:
            logger.warning("failed to parse usage marker json: %s", payload[:200])
            return None

    parts = payload.split(",")
    return {
        "model": "",
        "token_in": int(parts[0]) if len(parts) > 0 and parts[0] else 0,
        "token_out": int(parts[1]) if len(parts) > 1 and parts[1] else 0,
    }
