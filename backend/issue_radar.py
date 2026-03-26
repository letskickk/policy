"""
Issue Radar — 정책 사각지대 자동 발견

기존 policy_ssot.py의 _infer_related_positions_for_document()를 역방향으로 사용:
매칭 실패(score < 4) = 당이 아직 대응하지 않은 이슈.

사용법:
    from backend.issue_radar import run_issue_scan
    report = run_issue_scan()
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.database import get_connection
from backend.policy_ssot import (
    TOPIC_RULES,
    _classify_commentary_topic,
    _infer_related_positions_for_document,
    list_policy_documents,
    list_policy_positions,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Gap classification thresholds
# ---------------------------------------------------------------------------
SCORE_NO_POSITION = 4      # score < 4 또는 매칭 0건 → 사각지대
SCORE_WEAK_COVERAGE = 6    # score 4~6 → 약한 커버리지

# Priority scoring weights
WEIGHT_RECENT_BILL = 40
WEIGHT_RECENT_COMMENTARY = 12
WEIGHT_DOC_TYPE_DIVERSITY = 15
RECENT_DAYS = 7


def run_issue_scan(
    *,
    days_back: Optional[int] = None,
    doc_type_filter: Optional[str] = None,
) -> dict:
    """
    전체 SSOT 문서를 스캔하여 정책 사각지대 리포트를 생성한다.

    Returns:
        {
            "scan_time": ISO timestamp,
            "total_documents": int,
            "total_positions": int,
            "gaps": [
                {
                    "topic": str,
                    "gap_type": "no_position" | "weak_coverage",
                    "priority_score": float,
                    "document_count": int,
                    "documents": [
                        {
                            "id": int,
                            "title": str,
                            "doc_type": str,
                            "published_at": str,
                            "best_match_score": int,
                            "best_match_title": str | None,
                        }
                    ],
                    "recent_bill_count": int,
                    "recent_commentary_count": int,
                }
            ],
            "coverage_summary": {
                "well_covered": int,
                "weak_coverage": int,
                "no_position": int,
            },
        }
    """
    scan_start = datetime.now(timezone.utc)

    # Step 1: Load data
    approved_positions = list_policy_positions(status="approved")
    all_documents = list_policy_documents(status="active")

    # Optional filters
    if doc_type_filter:
        all_documents = [d for d in all_documents if d["doc_type"] == doc_type_filter]

    if days_back:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")
        all_documents = [
            d for d in all_documents
            if (d.get("published_at") or d.get("created_at", "")) >= cutoff
        ]

    if not all_documents:
        return _empty_report(scan_start, 0, len(approved_positions))

    # Step 2: Classify and match each document
    topic_buckets: dict[str, list[dict]] = defaultdict(list)
    coverage = {"well_covered": 0, "weak_coverage": 0, "no_position": 0}

    for doc in all_documents:
        topic = _classify_commentary_topic(doc)
        matches = _infer_related_positions_for_document(doc, approved_positions)

        best_score = max((m["score"] for m in matches), default=0)
        best_title = matches[0]["position_title"] if matches else None

        doc_entry = {
            "id": doc["id"],
            "title": doc["title"],
            "doc_type": doc.get("doc_type", ""),
            "published_at": doc.get("published_at", ""),
            "best_match_score": best_score,
            "best_match_title": best_title,
        }

        if best_score < SCORE_NO_POSITION or not matches:
            topic_buckets[topic].append(("no_position", doc_entry, doc))
            coverage["no_position"] += 1
        elif best_score < SCORE_WEAK_COVERAGE:
            topic_buckets[topic].append(("weak_coverage", doc_entry, doc))
            coverage["weak_coverage"] += 1
        else:
            coverage["well_covered"] += 1

    # Step 3: Build gap report per topic
    gaps = []
    now = datetime.now(timezone.utc)
    recent_cutoff = (now - timedelta(days=RECENT_DAYS)).strftime("%Y-%m-%d")

    for topic, entries in topic_buckets.items():
        if not entries:
            continue

        gap_docs = [e[1] for e in entries]
        raw_docs = [e[2] for e in entries]
        gap_types = [e[0] for e in entries]

        # Determine worst gap type for this topic
        has_no_position = "no_position" in gap_types
        gap_type = "no_position" if has_no_position else "weak_coverage"

        # Priority scoring
        recent_bills = sum(
            1 for d in raw_docs
            if d.get("doc_type") == "bill"
            and (d.get("published_at") or "") >= recent_cutoff
        )
        recent_commentary = sum(
            1 for d in raw_docs
            if d.get("doc_type") in ("statement", "press_release", "message")
            and (d.get("published_at") or "") >= recent_cutoff
        )
        doc_types_seen = len({d.get("doc_type") for d in raw_docs})

        priority = (
            recent_bills * WEIGHT_RECENT_BILL
            + recent_commentary * WEIGHT_RECENT_COMMENTARY
            + min(doc_types_seen, 3) * WEIGHT_DOC_TYPE_DIVERSITY
            + len(gap_docs) * 2  # more documents = more urgent
        )

        # Boost no_position gaps
        if gap_type == "no_position":
            priority += 20

        gaps.append({
            "topic": topic,
            "gap_type": gap_type,
            "priority_score": min(priority, 100),
            "document_count": len(gap_docs),
            "documents": sorted(gap_docs, key=lambda d: d["published_at"] or "", reverse=True)[:10],
            "recent_bill_count": recent_bills,
            "recent_commentary_count": recent_commentary,
        })

    # Sort by priority descending
    gaps.sort(key=lambda g: g["priority_score"], reverse=True)

    return {
        "scan_time": scan_start.isoformat(),
        "total_documents": len(all_documents),
        "total_positions": len(approved_positions),
        "gaps": gaps,
        "coverage_summary": coverage,
    }


def get_cached_scan(max_age_hours: int = 6) -> Optional[dict]:
    """캐시된 스캔 결과 반환. max_age_hours 이내 결과만."""
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT result_json, scanned_at FROM issue_radar_cache
               ORDER BY scanned_at DESC LIMIT 1"""
        ).fetchone()
        if not row:
            return None
        scanned_at = datetime.fromisoformat(row["scanned_at"].replace("Z", "+00:00"))
        if datetime.now(timezone.utc) - scanned_at > timedelta(hours=max_age_hours):
            return None
        return json.loads(row["result_json"])
    except Exception:
        return None
    finally:
        conn.close()


def save_scan_result(report: dict) -> None:
    """스캔 결과를 캐시 테이블에 저장."""
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO issue_radar_cache (result_json, scanned_at)
               VALUES (?, ?)""",
            (json.dumps(report, ensure_ascii=False), report["scan_time"]),
        )
        # Keep only last 10 scans
        conn.execute(
            """DELETE FROM issue_radar_cache
               WHERE id NOT IN (
                   SELECT id FROM issue_radar_cache ORDER BY scanned_at DESC LIMIT 10
               )"""
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.warning("issue radar cache save failed: %s", e)
    finally:
        conn.close()


def _empty_report(scan_start: datetime, doc_count: int, pos_count: int) -> dict:
    return {
        "scan_time": scan_start.isoformat(),
        "total_documents": doc_count,
        "total_positions": pos_count,
        "gaps": [],
        "coverage_summary": {"well_covered": 0, "weak_coverage": 0, "no_position": 0},
    }
