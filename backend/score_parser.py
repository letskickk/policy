"""
Score parser – extract total score from analysis result text/JSON.

Mirrors the frontend parseScoresFromText() logic in
static/js/check-result-render.js (lines 38-72).
"""

import json
import re
from typing import Optional


def _pick_num(line: str) -> Optional[float]:
    """Extract first number from a line."""
    if not line:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)", line)
    return float(m.group(1)) if m else None


def _find_by_keywords(lines: list[str], *keywords: str) -> Optional[float]:
    """Find a line containing ALL keywords and extract the number."""
    for line in lines:
        if all(k in line for k in keywords):
            return _pick_num(line)
    return None


def parse_total_score(text: str) -> Optional[float]:
    """
    Extract total score from plain-text analysis result.

    Priority:
    1. Line containing '결과' + '종합' + '점수'
    2. Line containing '종합' + '점수'
    3. Weighted average of 5-axis scores
    """
    if not text:
        return None

    lines = [line.strip() for line in text.split("\n")]

    # Primary: 결과 + 종합 + 점수
    total = _find_by_keywords(lines, "결과", "종합", "점수")
    if total is not None:
        return total

    # Secondary: 종합 + 점수
    total = _find_by_keywords(lines, "종합", "점수")
    if total is not None:
        return total

    # Tertiary: weighted average of 5 axes
    axes = [
        (_find_by_keywords(lines, "정강정책", "부합도"), 0.30),
        (_find_by_keywords(lines, "정책", "설계", "완성도"), 0.25),
        (_find_by_keywords(lines, "실행", "가능성"), 0.20),
        (_find_by_keywords(lines, "구체성"), 0.15),
        (_find_by_keywords(lines, "메시지", "경쟁력"), 0.10),
    ]
    valid = [(v, w) for v, w in axes if v is not None]
    if valid:
        w_sum = sum(w for _, w in valid)
        v_sum = sum(v * w for v, w in valid)
        return round((v_sum / w_sum) * 10) / 10

    return None


def parse_total_score_any(result_text: str, result_format: str = "text") -> Optional[float]:
    """
    Extract total score from either text or JSON result format.
    """
    if not result_text:
        return None

    # Try JSON format first
    if result_format == "json":
        try:
            data = json.loads(result_text)
            for key in ("total_score", "fit_score"):
                if isinstance(data.get(key), (int, float)):
                    return float(data[key])
            summary = data.get("summary", {})
            if isinstance(summary, dict):
                for key in ("total_score", "fit_score"):
                    if isinstance(summary.get(key), (int, float)):
                        return float(summary[key])
        except (json.JSONDecodeError, TypeError):
            pass

    # Fallback to text parsing
    return parse_total_score(result_text)
