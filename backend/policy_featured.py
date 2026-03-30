from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import HTTPException

from backend.database import get_connection


RECENT_ACTIVITY_WEIGHT = 20
RECENT_BILL_WEIGHT = 40
RECENT_STATEMENT_WEIGHT = 12
RECENT_POLICY_UPDATE_WEIGHT = 18
DOC_TYPE_DIVERSITY_WEIGHT = 15


@dataclass
class PositionActivity:
    position_id: int
    title: str
    slug: str
    category: str
    summary: str
    recent_document_count: int = 0
    recent_bill_count: int = 0
    recent_statement_count: int = 0
    recent_policy_updates: int = 0
    doc_types: set[str] = field(default_factory=set)
    latest_activity_at: Optional[str] = None

    @property
    def score(self) -> int:
        score = 0
        if self.recent_document_count > 0:
            score += RECENT_ACTIVITY_WEIGHT
        score += self.recent_bill_count * RECENT_BILL_WEIGHT
        score += self.recent_statement_count * RECENT_STATEMENT_WEIGHT
        score += self.recent_policy_updates * RECENT_POLICY_UPDATE_WEIGHT
        if len(self.doc_types) >= 2:
            score += DOC_TYPE_DIVERSITY_WEIGHT
        return score


def _today() -> date:
    return date.today()


def _parse_date(value: Optional[str]) -> Optional[date]:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _row_to_featured_issue(row) -> dict:
    return {
        "id": int(row["id"]),
        "position_id": int(row["position_id"]),
        "position_title": row["position_title"],
        "position_slug": row["position_slug"],
        "position_category": row["position_category"],
        "position_summary": row["position_summary"],
        "reason": row["reason"] or "",
        "priority_score": float(row["priority_score"] or 0),
        "manual_weight": int(row["manual_weight"] or 0),
        "start_at": row["start_at"],
        "end_at": row["end_at"],
        "status": row["status"],
        "created_at": row["created_at"],
    }


def _current_featured_row():
    today = _today().isoformat()
    conn = get_connection()
    try:
        return conn.execute(
            """
            SELECT fi.id, fi.position_id, fi.reason, fi.priority_score, fi.manual_weight,
                   fi.start_at, fi.end_at, fi.status, fi.created_at,
                   p.title AS position_title, p.slug AS position_slug, p.category AS position_category,
                   COALESCE(p.summary, '') AS position_summary
            FROM policy_featured_issues fi
            JOIN policy_positions p ON p.id = fi.position_id
            WHERE fi.status = 'active'
              AND (fi.start_at IS NULL OR fi.start_at <= ?)
              AND (fi.end_at IS NULL OR fi.end_at >= ?)
            ORDER BY COALESCE(fi.start_at, '0000-00-00') DESC, fi.id DESC
            LIMIT 1
            """,
            (today, today),
        ).fetchone()
    finally:
        conn.close()


def get_current_featured_issue() -> Optional[dict]:
    row = _current_featured_row()
    return _row_to_featured_issue(row) if row is not None else None


def list_featured_issues(limit: int = 20) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT fi.id, fi.position_id, fi.reason, fi.priority_score, fi.manual_weight,
                   fi.start_at, fi.end_at, fi.status, fi.created_at,
                   p.title AS position_title, p.slug AS position_slug, p.category AS position_category,
                   COALESCE(p.summary, '') AS position_summary
            FROM policy_featured_issues fi
            JOIN policy_positions p ON p.id = fi.position_id
            ORDER BY COALESCE(fi.start_at, '0000-00-00') DESC, fi.id DESC
            LIMIT ?
            """,
            (max(1, min(limit, 100)),),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_featured_issue(row) for row in rows]


def _build_reason(item: PositionActivity) -> str:
    reasons: list[str] = []
    if item.recent_bill_count:
        reasons.append(f"최근 7일 법안 {item.recent_bill_count}건")
    if item.recent_statement_count:
        reasons.append(f"최근 7일 논평 {item.recent_statement_count}건")
    if item.recent_policy_updates:
        reasons.append("최근 정책 업데이트")
    if len(item.doc_types) >= 2:
        reasons.append(f"문서 유형 {len(item.doc_types)}종")
    if item.recent_document_count and not reasons:
        reasons.append(f"최근 3일 활동 {item.recent_document_count}건")
    return " / ".join(reasons) or "최근 활동 감지"


def recommend_featured_issues(limit: int = 5) -> list[dict]:
    today = _today()
    recent_3 = (today - timedelta(days=2)).isoformat()
    recent_7 = (today - timedelta(days=6)).isoformat()

    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT p.id AS position_id, p.title, p.slug, p.category, COALESCE(p.summary, '') AS summary,
                   p.updated_at, d.doc_type, d.published_at
            FROM policy_positions p
            LEFT JOIN policy_document_links l ON l.position_id = p.id
            LEFT JOIN policy_documents d ON d.id = l.document_id AND d.status = 'active'
            WHERE p.status = 'approved'
            ORDER BY p.title ASC
            """
        ).fetchall()
    finally:
        conn.close()

    items: dict[int, PositionActivity] = {}
    for row in rows:
        position_id = int(row["position_id"])
        activity = items.setdefault(
            position_id,
            PositionActivity(
                position_id=position_id,
                title=row["title"],
                slug=row["slug"],
                category=row["category"],
                summary=row["summary"] or "",
            ),
        )

        updated_at = (row["updated_at"] or "")[:10]
        if updated_at and updated_at >= recent_7:
            activity.recent_policy_updates = 1
            activity.latest_activity_at = max(filter(None, [activity.latest_activity_at, updated_at]))

        doc_type = row["doc_type"]
        published_at = row["published_at"]
        if not doc_type or not published_at:
            continue
        published = _parse_date(published_at)
        if published is None:
            continue
        iso_published = published.isoformat()
        if iso_published >= recent_7:
            activity.doc_types.add(str(doc_type))
            activity.latest_activity_at = max(filter(None, [activity.latest_activity_at, iso_published]))
        if iso_published >= recent_3:
            activity.recent_document_count += 1
        if iso_published >= recent_7 and doc_type == "bill":
            activity.recent_bill_count += 1
        if iso_published >= recent_7 and doc_type == "statement":
            activity.recent_statement_count += 1

    ranked = sorted(
        (
            {
                "position_id": item.position_id,
                "position_title": item.title,
                "position_slug": item.slug,
                "position_category": item.category,
                "position_summary": item.summary,
                "score": item.score,
                "reason": _build_reason(item),
                "latest_activity_at": item.latest_activity_at,
                "stats": {
                    "recent_document_count": item.recent_document_count,
                    "recent_bill_count": item.recent_bill_count,
                    "recent_statement_count": item.recent_statement_count,
                    "recent_policy_updates": item.recent_policy_updates,
                    "doc_type_diversity": len(item.doc_types),
                },
            }
            for item in items.values()
            if item.score > 0
        ),
        key=lambda entry: (
            entry["stats"]["recent_bill_count"],
            entry["score"],
            entry["latest_activity_at"] or "",
            entry["position_title"],
        ),
        reverse=True,
    )
    return ranked[: max(1, min(limit, 20))]


def set_featured_issue(
    *,
    position_id: int,
    reason: Optional[str],
    start_at: Optional[str],
    end_at: Optional[str],
    manual_weight: int,
    actor_id: Optional[int],
) -> dict:
    conn = get_connection()
    try:
        exists = conn.execute("SELECT id FROM policy_positions WHERE id = ?", (position_id,)).fetchone()
        if exists is None:
            raise HTTPException(status_code=404, detail="정책 항목을 찾을 수 없습니다.")

        start_clean = (start_at or _today().isoformat()).strip()
        end_clean = (end_at or "").strip() or None
        start_date = _parse_date(start_clean)
        end_date = _parse_date(end_clean)
        if start_date is None:
            raise HTTPException(status_code=400, detail="start_at 형식이 올바르지 않습니다.")
        if end_clean and end_date is None:
            raise HTTPException(status_code=400, detail="end_at 형식이 올바르지 않습니다.")
        if start_date and end_date and end_date < start_date:
            raise HTTPException(status_code=400, detail="end_at은 start_at보다 빠를 수 없습니다.")

        recommendation = next(
            (item for item in recommend_featured_issues(limit=20) if item["position_id"] == position_id),
            None,
        )
        priority_score = float((recommendation or {}).get("score") or 0) + float(manual_weight)

        conn.execute("UPDATE policy_featured_issues SET status = 'archived' WHERE status = 'active'")
        cur = conn.execute(
            """
            INSERT INTO policy_featured_issues (
                position_id, reason, priority_score, manual_weight,
                start_at, end_at, status, created_by, updated_by, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, datetime('now'))
            """,
            (
                position_id,
                (reason or "").strip() or None,
                priority_score,
                int(manual_weight),
                start_clean,
                end_clean,
                actor_id,
                actor_id,
            ),
        )
        conn.commit()
        featured_id = int(cur.lastrowid)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    current = get_current_featured_issue()
    if current and current["id"] == featured_id:
        return current
    raise HTTPException(status_code=500, detail="메인 이슈 저장 후 조회에 실패했습니다.")

def set_featured_issue(
    *,
    position_id: int,
    reason: Optional[str],
    start_at: Optional[str],
    end_at: Optional[str],
    manual_weight: int,
    actor_id: Optional[int],
) -> dict:
    conn = get_connection()
    try:
        exists = conn.execute("SELECT id FROM policy_positions WHERE id = ?", (position_id,)).fetchone()
        if exists is None:
            raise HTTPException(status_code=404, detail="position not found.")

        start_clean = (start_at or _today().isoformat()).strip()
        end_clean = (end_at or "").strip() or None
        start_date = _parse_date(start_clean)
        end_date = _parse_date(end_clean)
        if start_date is None:
            raise HTTPException(status_code=400, detail="invalid start_at.")
        if end_clean and end_date is None:
            raise HTTPException(status_code=400, detail="invalid end_at.")
        if start_date and end_date and end_date < start_date:
            raise HTTPException(status_code=400, detail="end_at cannot be earlier than start_at.")

        recommendation = next(
            (item for item in recommend_featured_issues(limit=20) if item["position_id"] == position_id),
            None,
        )
        priority_score = float((recommendation or {}).get("score") or 0) + float(manual_weight)

        conn.execute("UPDATE policy_featured_issues SET status = 'archived' WHERE status = 'active'")
        cur = conn.execute(
            """
            INSERT INTO policy_featured_issues (
                position_id, reason, priority_score, manual_weight,
                start_at, end_at, status, created_by, updated_by, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, datetime('now'))
            """,
            (
                position_id,
                (reason or "").strip() or None,
                priority_score,
                int(manual_weight),
                start_clean,
                end_clean,
                actor_id,
                actor_id,
            ),
        )
        featured_id = int(cur.lastrowid)
        row = conn.execute(
            """
            SELECT fi.id, fi.position_id, fi.reason, fi.priority_score, fi.manual_weight,
                   fi.start_at, fi.end_at, fi.status, fi.created_at,
                   p.title AS position_title, p.slug AS position_slug, p.category AS position_category,
                   COALESCE(p.summary, '') AS position_summary
            FROM policy_featured_issues fi
            JOIN policy_positions p ON p.id = fi.position_id
            WHERE fi.id = ?
            LIMIT 1
            """,
            (featured_id,),
        ).fetchone()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    if row is not None:
        return _row_to_featured_issue(row)
    raise HTTPException(status_code=500, detail="failed to reload featured issue after save.")

def get_current_featured_issue() -> Optional[dict]:
    row = _current_featured_row()
    if row is None:
        conn = get_connection()
        try:
            row = conn.execute(
                """
                SELECT fi.id, fi.position_id, fi.reason, fi.priority_score, fi.manual_weight,
                       fi.start_at, fi.end_at, fi.status, fi.created_at,
                       p.title AS position_title, p.slug AS position_slug, p.category AS position_category,
                       COALESCE(p.summary, '') AS position_summary
                FROM policy_featured_issues fi
                JOIN policy_positions p ON p.id = fi.position_id
                WHERE fi.status = 'active'
                ORDER BY COALESCE(fi.start_at, '0000-00-00') DESC, fi.id DESC
                LIMIT 1
                """
            ).fetchone()
        finally:
            conn.close()
    return _row_to_featured_issue(row) if row is not None else None
