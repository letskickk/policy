import json
import re
from pathlib import Path
from typing import Optional

from fastapi import HTTPException

from backend.config import ROOT_DIR
from backend.database import get_connection
from backend.policy_ssot import link_policy_document

SUGGESTION_STATUS = {"pending", "accepted", "rejected"}
TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]{2,}")
STOPWORDS = {
    "개혁신당",
    "논평",
    "대변인",
    "부대변인",
    "수석대변인",
    "관련",
    "대한",
    "위한",
    "정책",
    "입장",
    "발표",
    "statement",
    "press",
    "release",
}


def _tokenize(*values: Optional[str]) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        if not value:
            continue
        for token in TOKEN_RE.findall(value.lower()):
            if token in STOPWORDS:
                continue
            tokens.add(token)
    return tokens


def _load_keyword_overrides() -> dict[str, list[str]]:
    path = ROOT_DIR / "data" / "policy_keyword_overrides.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, list[str]] = {}
    for key, value in data.items():
        if isinstance(value, list):
            out[str(key)] = [str(item).strip() for item in value if str(item).strip()]
    return out


def rebuild_link_suggestions(*, document_id: Optional[int] = None) -> dict:
    overrides = _load_keyword_overrides()
    conn = get_connection()
    try:
        positions = conn.execute(
            "SELECT id, title, summary, body, category FROM policy_positions WHERE status <> 'archived'"
        ).fetchall()
        if document_id is None:
            documents = conn.execute(
                "SELECT id, title, summary, body, metadata_json FROM policy_documents WHERE status = 'active'"
            ).fetchall()
        else:
            documents = conn.execute(
                "SELECT id, title, summary, body, metadata_json FROM policy_documents WHERE id = ?",
                (document_id,),
            ).fetchall()

        if document_id is None:
            conn.execute("DELETE FROM policy_link_suggestions")
        else:
            conn.execute("DELETE FROM policy_link_suggestions WHERE document_id = ?", (document_id,))

        created = 0
        for document in documents:
            metadata = {}
            try:
                metadata = json.loads(document["metadata_json"] or "{}")
            except json.JSONDecodeError:
                metadata = {}
            doc_tokens = _tokenize(
                document["title"],
                document["summary"],
                document["body"],
                " ".join(str(v) for v in metadata.values() if isinstance(v, str)),
            )
            if not doc_tokens:
                continue

            candidates = []
            for position in positions:
                extra = overrides.get(str(position["id"]), [])
                pos_tokens = _tokenize(position["title"], position["summary"], position["body"], position["category"], " ".join(extra))
                if not pos_tokens:
                    continue
                overlap = sorted(doc_tokens & pos_tokens)
                if not overlap:
                    continue
                score = round(len(overlap) / max(1, len(pos_tokens)), 4)
                reason = ", ".join(overlap[:8])
                candidates.append((score, position["id"], reason))

            candidates.sort(key=lambda item: (-item[0], item[1]))
            for score, position_id, reason in candidates[:5]:
                if score < 0.08:
                    continue
                conn.execute(
                    """
                    INSERT INTO policy_link_suggestions (
                        document_id, position_id, relation_type, score, reason, status, updated_at
                    )
                    VALUES (?, ?, 'explains', ?, ?, 'pending', datetime('now'))
                    """,
                    (int(document["id"]), int(position_id), score, reason),
                )
                created += 1

        conn.commit()
        return {"created": created, "documents": len(documents)}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_link_suggestions(status: Optional[str] = "pending", limit: int = 100) -> list[dict]:
    conn = get_connection()
    try:
        sql = """
            SELECT s.id, s.document_id, s.position_id, s.relation_type, s.score, s.reason, s.status, s.created_at, s.updated_at,
                   d.title AS document_title, d.doc_type AS document_type, d.speaker AS speaker_role, d.speaker_name AS speaker_name,
                   p.title AS position_title, p.category AS position_category
            FROM policy_link_suggestions s
            JOIN policy_documents d ON d.id = s.document_id
            JOIN policy_positions p ON p.id = s.position_id
            WHERE 1=1
        """
        params: list[object] = []
        if status:
            if status not in SUGGESTION_STATUS:
                raise HTTPException(status_code=400, detail="invalid suggestion status")
            sql += " AND s.status = ?"
            params.append(status)
        sql += " ORDER BY s.score DESC, s.id DESC LIMIT ?"
        params.append(max(1, min(limit, 300)))
        rows = conn.execute(sql, tuple(params)).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": int(row["id"]),
            "document_id": int(row["document_id"]),
            "document_title": row["document_title"],
            "document_type": row["document_type"],
            "speaker_role": row["speaker_role"] or "",
            "speaker_name": row["speaker_name"] or "",
            "position_id": int(row["position_id"]),
            "position_title": row["position_title"],
            "position_category": row["position_category"] or "",
            "relation_type": row["relation_type"],
            "score": float(row["score"]),
            "reason": row["reason"] or "",
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]


def update_link_suggestion_status(suggestion_id: int, status: str) -> dict:
    if status not in SUGGESTION_STATUS:
        raise HTTPException(status_code=400, detail="invalid suggestion status")
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT document_id, position_id, relation_type, reason FROM policy_link_suggestions WHERE id = ?",
            (suggestion_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="suggestion not found")
        conn.execute(
            "UPDATE policy_link_suggestions SET status = ?, updated_at = datetime('now') WHERE id = ?",
            (status, suggestion_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    if status == "accepted":
        link_policy_document(
            position_id=int(row["position_id"]),
            document_id=int(row["document_id"]),
            relation_type=row["relation_type"] or "explains",
            notes=row["reason"] or None,
            actor_id=None,
        )
    return {"ok": True, "id": suggestion_id, "status": status}
