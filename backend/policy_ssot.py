import json
import re
import unicodedata
from typing import Optional

from fastapi import HTTPException

from backend.database import get_connection

POLICY_STATUS = {"draft", "review", "approved", "archived"}
POLICY_OWNER_SCOPE = {"party", "parliamentary_group", "spokesperson", "member", "campaign", "other"}
DOC_STATUS = {"active", "archived", "superseded"}
DOC_TYPES = {
    "policy",
    "bill",
    "statement",
    "press_release",
    "briefing",
    "pledge",
    "meeting_note",
    "research",
    "other",
}
RELATION_TYPES = {
    "references",
    "implements",
    "explains",
    "supports",
    "updates",
    "conflicts",
}


def _normalize_text(value: Optional[str]) -> str:
    return unicodedata.normalize("NFC", (value or "").strip())


def _normalize_optional_text(value: Optional[str]) -> Optional[str]:
    text = _normalize_text(value)
    return text or None


def _normalize_enum(value: Optional[str], allowed: set[str], field_name: str, default: str) -> str:
    text = (_normalize_text(value) or default).lower()
    if text not in allowed:
        raise HTTPException(status_code=400, detail=f"{field_name} 값이 올바르지 않습니다.")
    return text


def _validate_date(value: Optional[str], field_name: str) -> Optional[str]:
    text = _normalize_optional_text(value)
    if text is None:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise HTTPException(status_code=400, detail=f"{field_name} 형식은 YYYY-MM-DD 이어야 합니다.")
    return text


def slugify(value: str) -> str:
    text = _normalize_text(value).lower()
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"[^0-9a-z가-힣_-]", "", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    if not text:
        raise HTTPException(status_code=400, detail="slug를 생성할 수 없습니다.")
    return text[:120]


def _ensure_slug_unique(table: str, slug: str, current_id: Optional[int] = None) -> str:
    conn = get_connection()
    try:
        candidate = slug
        suffix = 2
        while True:
            if current_id is None:
                row = conn.execute(f"SELECT id FROM {table} WHERE slug = ?", (candidate,)).fetchone()
            else:
                row = conn.execute(
                    f"SELECT id FROM {table} WHERE slug = ? AND id <> ?",
                    (candidate, current_id),
                ).fetchone()
            if row is None:
                return candidate
            candidate = f"{slug}-{suffix}"
            suffix += 1
    finally:
        conn.close()


def upsert_policy_position(
    *,
    position_id: Optional[int],
    title: str,
    category: str,
    summary: Optional[str],
    body: Optional[str],
    status: str,
    owner_scope: str,
    effective_from: Optional[str],
    effective_to: Optional[str],
    version_label: Optional[str],
    actor_id: Optional[int],
) -> dict:
    title_clean = _normalize_text(title)
    if not title_clean:
        raise HTTPException(status_code=400, detail="title은 필수입니다.")
    if len(title_clean) > 200:
        raise HTTPException(status_code=400, detail="title 길이가 너무 깁니다.")
    category_clean = _normalize_text(category) or "general"
    status_clean = _normalize_enum(status, POLICY_STATUS, "status", "draft")
    scope_clean = _normalize_enum(owner_scope, POLICY_OWNER_SCOPE, "owner_scope", "party")
    summary_clean = _normalize_optional_text(summary)
    body_clean = _normalize_optional_text(body)
    effective_from_clean = _validate_date(effective_from, "effective_from")
    effective_to_clean = _validate_date(effective_to, "effective_to")
    if effective_from_clean and effective_to_clean and effective_from_clean > effective_to_clean:
        raise HTTPException(status_code=400, detail="effective_from은 effective_to보다 늦을 수 없습니다.")
    version_clean = _normalize_optional_text(version_label)
    base_slug = slugify(title_clean)
    slug = _ensure_slug_unique("policy_positions", base_slug, position_id)

    conn = get_connection()
    try:
        if position_id is None:
            cur = conn.execute(
                """
                INSERT INTO policy_positions (
                    title, slug, category, summary, body, status, owner_scope,
                    effective_from, effective_to, version_label, created_by, updated_by, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    title_clean,
                    slug,
                    category_clean,
                    summary_clean,
                    body_clean,
                    status_clean,
                    scope_clean,
                    effective_from_clean,
                    effective_to_clean,
                    version_clean,
                    actor_id,
                    actor_id,
                ),
            )
            position_id = int(cur.lastrowid)
        else:
            row = conn.execute("SELECT id FROM policy_positions WHERE id = ?", (position_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="정책 항목을 찾을 수 없습니다.")
            conn.execute(
                """
                UPDATE policy_positions
                SET title = ?, slug = ?, category = ?, summary = ?, body = ?, status = ?, owner_scope = ?,
                    effective_from = ?, effective_to = ?, version_label = ?, updated_by = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (
                    title_clean,
                    slug,
                    category_clean,
                    summary_clean,
                    body_clean,
                    status_clean,
                    scope_clean,
                    effective_from_clean,
                    effective_to_clean,
                    version_clean,
                    actor_id,
                    position_id,
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_policy_position(position_id)


def upsert_policy_document(
    *,
    document_id: Optional[int],
    title: str,
    doc_type: str,
    summary: Optional[str],
    body: Optional[str],
    speaker: Optional[str],
    speaker_name: Optional[str],
    owner_name: Optional[str],
    source_url: Optional[str],
    source_ref: Optional[str],
    published_at: Optional[str],
    status: str,
    metadata: Optional[dict],
    actor_id: Optional[int],
) -> dict:
    title_clean = _normalize_text(title)
    if not title_clean:
        raise HTTPException(status_code=400, detail="title은 필수입니다.")
    if len(title_clean) > 200:
        raise HTTPException(status_code=400, detail="title 길이가 너무 깁니다.")
    doc_type_clean = _normalize_enum(doc_type, DOC_TYPES, "doc_type", "other")
    status_clean = _normalize_enum(status, DOC_STATUS, "status", "active")
    summary_clean = _normalize_optional_text(summary)
    body_clean = _normalize_optional_text(body)
    speaker_clean = _normalize_optional_text(speaker)
    speaker_name_clean = _normalize_optional_text(speaker_name)
    owner_clean = _normalize_optional_text(owner_name)
    url_clean = _normalize_optional_text(source_url)
    ref_clean = _normalize_optional_text(source_ref)
    published_clean = _validate_date(published_at, "published_at")
    metadata_json = json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":"))
    base_slug = slugify(title_clean)
    slug = _ensure_slug_unique("policy_documents", base_slug, document_id)

    conn = get_connection()
    try:
        if document_id is None:
            cur = conn.execute(
                """
                INSERT INTO policy_documents (
                    title, slug, doc_type, summary, body, speaker, owner_name, source_url,
                    source_ref, published_at, status, metadata_json, speaker_name, created_by, updated_by, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    title_clean,
                    slug,
                    doc_type_clean,
                    summary_clean,
                    body_clean,
                    speaker_clean,
                    owner_clean,
                    url_clean,
                    ref_clean,
                    published_clean,
                    status_clean,
                    metadata_json,
                    speaker_name_clean,
                    actor_id,
                    actor_id,
                ),
            )
            document_id = int(cur.lastrowid)
        else:
            row = conn.execute("SELECT id FROM policy_documents WHERE id = ?", (document_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")
            conn.execute(
                """
                UPDATE policy_documents
                SET title = ?, slug = ?, doc_type = ?, summary = ?, body = ?, speaker = ?, speaker_name = ?, owner_name = ?,
                    source_url = ?, source_ref = ?, published_at = ?, status = ?, metadata_json = ?,
                    updated_by = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (
                    title_clean,
                    slug,
                    doc_type_clean,
                    summary_clean,
                    body_clean,
                    speaker_clean,
                    speaker_name_clean,
                    owner_clean,
                    url_clean,
                    ref_clean,
                    published_clean,
                    status_clean,
                    metadata_json,
                    actor_id,
                    document_id,
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_policy_document(document_id)


def find_policy_document_by_source(*, source_ref: Optional[str] = None, source_url: Optional[str] = None) -> Optional[dict]:
    ref_clean = _normalize_optional_text(source_ref)
    url_clean = _normalize_optional_text(source_url)
    if not ref_clean and not url_clean:
        return None

    conn = get_connection()
    try:
        row = None
        if ref_clean:
            row = conn.execute(
                "SELECT * FROM policy_documents WHERE source_ref = ? ORDER BY id DESC LIMIT 1",
                (ref_clean,),
            ).fetchone()
        if row is None and url_clean:
            row = conn.execute(
                "SELECT * FROM policy_documents WHERE source_url = ? ORDER BY id DESC LIMIT 1",
                (url_clean,),
            ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None
    return _row_to_document(row)


def link_policy_document(
    *,
    position_id: int,
    document_id: int,
    relation_type: str,
    notes: Optional[str],
    actor_id: Optional[int],
) -> dict:
    rel_clean = _normalize_enum(relation_type, RELATION_TYPES, "relation_type", "references")
    notes_clean = _normalize_optional_text(notes)
    conn = get_connection()
    try:
        position = conn.execute("SELECT id FROM policy_positions WHERE id = ?", (position_id,)).fetchone()
        if position is None:
            raise HTTPException(status_code=404, detail="정책 항목을 찾을 수 없습니다.")
        document = conn.execute("SELECT id FROM policy_documents WHERE id = ?", (document_id,)).fetchone()
        if document is None:
            raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")
        conn.execute(
            """
            INSERT INTO policy_document_links (position_id, document_id, relation_type, notes, created_by)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(position_id, document_id, relation_type) DO UPDATE SET
                notes = excluded.notes
            """,
            (position_id, document_id, rel_clean, notes_clean, actor_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    links = list_policy_links(position_id=position_id)
    for item in links:
        if item["document_id"] == document_id and item["relation_type"] == rel_clean:
            return item
    raise HTTPException(status_code=500, detail="연결 저장 후 조회에 실패했습니다.")


def delete_policy_position(position_id: int) -> None:
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM policy_positions WHERE id = ?", (position_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="정책 항목을 찾을 수 없습니다.")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_policy_document(document_id: int) -> None:
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM policy_documents WHERE id = ?", (document_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def replace_policy_document_people(document_id: int, people: list[dict]) -> list[dict]:
    normalized_people: list[dict] = []
    for item in people:
        person_name = _normalize_text(item.get("person_name") or item.get("name"))
        person_role = _normalize_text(item.get("person_role") or item.get("role"))
        if not person_name or not person_role:
            continue
        normalized_people.append(
            {
                "person_name": person_name,
                "person_role": person_role,
                "party_affiliation": _normalize_optional_text(item.get("party_affiliation")),
                "is_reform_party": 1 if item.get("is_reform_party") else 0,
                "is_primary": 1 if item.get("is_primary") else 0,
                "metadata_json": json.dumps(item.get("metadata") or {}, ensure_ascii=False, separators=(",", ":")),
            }
        )

    conn = get_connection()
    try:
        exists = conn.execute("SELECT id FROM policy_documents WHERE id = ?", (document_id,)).fetchone()
        if exists is None:
            raise HTTPException(status_code=404, detail="臾몄꽌瑜?李얠쓣 ???놁뒿?덈떎.")
        conn.execute("DELETE FROM policy_document_people WHERE document_id = ?", (document_id,))
        for person in normalized_people:
            conn.execute(
                """
                INSERT INTO policy_document_people (
                    document_id, person_name, person_role, party_affiliation,
                    is_reform_party, is_primary, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    person["person_name"],
                    person["person_role"],
                    person["party_affiliation"],
                    person["is_reform_party"],
                    person["is_primary"],
                    person["metadata_json"],
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return list_policy_document_people(document_id=document_id)


def list_policy_document_people(document_id: Optional[int] = None) -> list[dict]:
    conn = get_connection()
    try:
        sql = """
            SELECT id, document_id, person_name, person_role, party_affiliation,
                   is_reform_party, is_primary, metadata_json, created_at
            FROM policy_document_people
            WHERE 1=1
        """
        params: list[object] = []
        if document_id is not None:
            sql += " AND document_id = ?"
            params.append(document_id)
        sql += " ORDER BY document_id ASC, is_primary DESC, person_role ASC, person_name ASC"
        rows = conn.execute(sql, tuple(params)).fetchall()
    finally:
        conn.close()

    items = []
    for row in rows:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            metadata = {}
        items.append(
            {
                "id": int(row["id"]),
                "document_id": int(row["document_id"]),
                "person_name": row["person_name"],
                "person_role": row["person_role"],
                "party_affiliation": row["party_affiliation"] or "",
                "is_reform_party": bool(row["is_reform_party"]),
                "is_primary": bool(row["is_primary"]),
                "metadata": metadata,
                "created_at": row["created_at"],
            }
        )
    return items


def unlink_policy_document(link_id: int) -> None:
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM policy_document_links WHERE id = ?", (link_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="연결을 찾을 수 없습니다.")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _row_to_position(row) -> dict:
    return {
        "id": int(row["id"]),
        "title": row["title"],
        "slug": row["slug"],
        "category": row["category"],
        "summary": row["summary"] or "",
        "body": row["body"] or "",
        "status": row["status"],
        "owner_scope": row["owner_scope"],
        "effective_from": row["effective_from"],
        "effective_to": row["effective_to"],
        "version_label": row["version_label"] or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _row_to_document(row) -> dict:
    metadata_raw = row["metadata_json"] or "{}"
    try:
        metadata = json.loads(metadata_raw)
    except json.JSONDecodeError:
        metadata = {}
    return {
        "id": int(row["id"]),
        "title": row["title"],
        "slug": row["slug"],
        "doc_type": row["doc_type"],
        "summary": row["summary"] or "",
        "body": row["body"] or "",
        "speaker": row["speaker"] or "",
        "speaker_name": row["speaker_name"] or "",
        "owner_name": row["owner_name"] or "",
        "source_url": row["source_url"] or "",
        "source_ref": row["source_ref"] or "",
        "published_at": row["published_at"],
        "status": row["status"],
        "metadata": metadata,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_policy_position(position_id: int) -> dict:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM policy_positions WHERE id = ?", (position_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="정책 항목을 찾을 수 없습니다.")
    item = _row_to_position(row)
    item["links"] = list_policy_links(position_id=position_id)
    return item


def get_policy_document(document_id: int) -> dict:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM policy_documents WHERE id = ?", (document_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")
    item = _row_to_document(row)
    item["people"] = list_policy_document_people(document_id=document_id)
    item["linked_positions"] = list_policy_links(document_id=document_id)
    return item


def list_policy_positions(status: Optional[str] = None, category: Optional[str] = None) -> list[dict]:
    conn = get_connection()
    try:
        sql = "SELECT * FROM policy_positions WHERE 1=1"
        params: list[object] = []
        status_clean = _normalize_optional_text(status)
        category_clean = _normalize_optional_text(category)
        if status_clean:
            sql += " AND status = ?"
            params.append(status_clean.lower())
        if category_clean:
            sql += " AND category = ?"
            params.append(category_clean)
        sql += " ORDER BY CASE status WHEN 'approved' THEN 1 WHEN 'review' THEN 2 WHEN 'draft' THEN 3 ELSE 4 END, title ASC"
        rows = conn.execute(sql, tuple(params)).fetchall()
    finally:
        conn.close()
    return [_row_to_position(row) for row in rows]


def list_policy_documents(doc_type: Optional[str] = None, status: Optional[str] = None) -> list[dict]:
    conn = get_connection()
    try:
        sql = "SELECT * FROM policy_documents WHERE 1=1"
        params: list[object] = []
        type_clean = _normalize_optional_text(doc_type)
        status_clean = _normalize_optional_text(status)
        if type_clean:
            sql += " AND doc_type = ?"
            params.append(type_clean.lower())
        if status_clean:
            sql += " AND status = ?"
            params.append(status_clean.lower())
        sql += " ORDER BY COALESCE(published_at, '0000-00-00') DESC, title ASC"
        rows = conn.execute(sql, tuple(params)).fetchall()
    finally:
        conn.close()
    items = [_row_to_document(row) for row in rows]
    people_map: dict[int, list[dict]] = {}
    for person in list_policy_document_people():
        people_map.setdefault(person["document_id"], []).append(person)
    for item in items:
        item["people"] = people_map.get(item["id"], [])
    return items


def list_policy_links(position_id: Optional[int] = None, document_id: Optional[int] = None) -> list[dict]:
    conn = get_connection()
    try:
        sql = """
            SELECT l.id, l.position_id, l.document_id, l.relation_type, l.notes, l.created_at,
                   p.title AS position_title, p.slug AS position_slug,
                   d.title AS document_title, d.slug AS document_slug, d.doc_type AS document_type
            FROM policy_document_links l
            JOIN policy_positions p ON p.id = l.position_id
            JOIN policy_documents d ON d.id = l.document_id
            WHERE 1=1
        """
        params: list[object] = []
        if position_id is not None:
            sql += " AND l.position_id = ?"
            params.append(position_id)
        if document_id is not None:
            sql += " AND l.document_id = ?"
            params.append(document_id)
        sql += " ORDER BY p.title ASC, d.title ASC, l.relation_type ASC"
        rows = conn.execute(sql, tuple(params)).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": int(row["id"]),
            "position_id": int(row["position_id"]),
            "position_title": row["position_title"],
            "position_slug": row["position_slug"],
            "document_id": int(row["document_id"]),
            "document_title": row["document_title"],
            "document_slug": row["document_slug"],
            "document_type": row["document_type"],
            "relation_type": row["relation_type"],
            "notes": row["notes"] or "",
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def get_policy_ssot_summary() -> dict:
    conn = get_connection()
    try:
        position_count = conn.execute("SELECT COUNT(*) AS n FROM policy_positions").fetchone()["n"]
        document_count = conn.execute("SELECT COUNT(*) AS n FROM policy_documents").fetchone()["n"]
        link_count = conn.execute("SELECT COUNT(*) AS n FROM policy_document_links").fetchone()["n"]
        doc_rows = conn.execute(
            "SELECT doc_type, COUNT(*) AS n FROM policy_documents GROUP BY doc_type ORDER BY n DESC, doc_type ASC"
        ).fetchall()
        status_rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM policy_positions GROUP BY status ORDER BY n DESC, status ASC"
        ).fetchall()
    finally:
        conn.close()
    return {
        "positions": int(position_count),
        "documents": int(document_count),
        "links": int(link_count),
        "document_types": {row["doc_type"]: int(row["n"]) for row in doc_rows},
        "position_statuses": {row["status"]: int(row["n"]) for row in status_rows},
    }
