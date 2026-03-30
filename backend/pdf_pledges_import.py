from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from backend.config import PDF_DIR, _nfc
from backend.database import get_connection
from backend.pdf_loader import extract_text_from_file
from backend.policy_ssot import (
    find_policy_document_by_source,
    get_policy_position,
    link_policy_document,
    replace_policy_document_people,
    slugify,
    upsert_policy_document,
    upsert_policy_position,
)
from backend.policy_suggestions import rebuild_link_suggestions


SOURCE_KEY = "pdf_party_pledges"
PARTY_NAME = "개혁신당"
CAMPAIGN_OWNER_NAME = "개혁신당 대선공약"
CAMPAIGN_VERSION_LABEL = "개혁신당 대선공약"
KNOWN_PEOPLE = ["이준석", "이주영", "천하람"]
REMOVED_NAME_TOKENS = KNOWN_PEOPLE + ["양향자"]
CATEGORY_KEYWORDS = [
    ("정치개혁", ["정부부처", "헌법개정", "언론민주화", "정치개혁"]),
    ("치안·사법", ["치안", "교정", "경찰", "사법", "범죄"]),
    ("국방", ["병역", "군간부", "군인", "국방"]),
    ("교통", ["고속철", "철도", "통학버스", "교통"]),
    ("보건의료", ["의료", "응급", "외상", "건보", "건강보험"]),
    ("교육", ["교육", "수학교육", "교사", "학교"]),
    ("복지", ["노후", "주택연금", "노인", "복지", "생애주기", "다자녀"]),
    ("경제", ["법인세", "대출", "가맹", "플랫폼", "리쇼어링", "방송광고", "투자자"]),
    ("과학기술", ["ai", "데이터", "과학", "기술", "특구"]),
]


def _pledge_dir() -> Path:
    return PDF_DIR / _nfc("공약")


def _create_ingest_run() -> int:
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO policy_ingest_runs (source_key, status) VALUES (?, 'running')",
            (SOURCE_KEY,),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def _finish_ingest_run(
    run_id: int,
    *,
    status: str,
    imported_count: int,
    updated_count: int,
    skipped_count: int,
    error_message: Optional[str] = None,
) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE policy_ingest_runs
            SET status = ?, imported_count = ?, updated_count = ?, skipped_count = ?,
                error_message = ?, finished_at = datetime('now')
            WHERE id = ?
            """,
            (status, imported_count, updated_count, skipped_count, error_message, run_id),
        )
        conn.commit()
    finally:
        conn.close()


def list_ingest_runs(limit: int = 20) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, source_key, status, imported_count, updated_count, skipped_count,
                   error_message, started_at, finished_at
            FROM policy_ingest_runs
            WHERE source_key = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (SOURCE_KEY, max(1, min(limit, 100))),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": int(row["id"]),
            "source_key": row["source_key"],
            "status": row["status"],
            "imported_count": int(row["imported_count"]),
            "updated_count": int(row["updated_count"]),
            "skipped_count": int(row["skipped_count"]),
            "error_message": row["error_message"] or "",
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
        }
        for row in rows
    ]


def _normalize_title_from_filename(filename: str) -> str:
    title = _nfc(Path(filename).stem)
    title = title.replace("_", " ")
    title = re.sub(r"^\d+(?:-\d+)?\.\s*", "", title)
    title = re.sub(r"^정책발표\d+\(\d+\)\s*", "", title)
    title = re.sub(r"^정책발표\d+\(\d+\)_", "", title)
    title = re.sub(r"^이준석\s*공약[\s_]*", "", title)
    title = re.sub(r"^이준석공약[\s_]*", "", title)
    title = re.sub(r"^개혁신당\s*", "", title)
    title = re.sub(r"\(\s*설명자료\s*\)$", "", title)
    title = re.sub(r"\s*설명자료$", "", title)
    title = re.sub(r"\s+", " ", title).strip(" ,-_")

    if REMOVED_NAME_TOKENS:
        suffix_pattern = "|".join(re.escape(name) for name in REMOVED_NAME_TOKENS)
        title = re.sub(
            rf"(?:\s|,|-)*(?:{suffix_pattern})(?:\s*(?:,|/|·)\s*(?:{suffix_pattern}))*$",
            "",
            title,
        ).strip(" ,-_")

    title = re.sub(r"\s+", " ", title).strip()
    return title or _nfc(Path(filename).stem)


def _extract_people_from_filename(filename: str) -> list[dict]:
    normalized = _nfc(filename)
    people: list[dict] = []
    for name in KNOWN_PEOPLE:
        if name not in normalized:
            continue
        people.append(
            {
                "person_name": name,
                "person_role": "policy_owner",
                "party_affiliation": PARTY_NAME,
                "is_reform_party": True,
                "is_primary": not people,
            }
        )
    return people


def _extract_summary(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "")).strip()
    return cleaned[:280]


def _categorize_title(title: str, body: str) -> str:
    title_haystack = (title or "").lower()
    body_haystack = (body or "").lower()
    for category, keywords in CATEGORY_KEYWORDS:
        if any(keyword in title_haystack for keyword in keywords):
            return category
    for category, keywords in CATEGORY_KEYWORDS:
        if any(keyword in body_haystack for keyword in keywords):
            return category
    return "대선공약"


def _campaign_canonical_key(title: str) -> str:
    normalized = _normalize_title_from_filename(title)
    normalized = normalized.replace("·", " ")
    normalized = re.sub(r"[^\w가-힣]+", "", normalized.lower())
    return normalized


def _get_campaign_position_by_slug(slug: str) -> Optional[dict]:
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT id
            FROM policy_positions
            WHERE slug = ? AND owner_scope = 'campaign'
            ORDER BY id DESC
            LIMIT 1
            """,
            (slug,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return get_policy_position(int(row["id"]))


def _upsert_campaign_position(*, title: str, summary: str, body: str, actor_id: Optional[int]) -> dict:
    slug = slugify(title)
    existing = _get_campaign_position_by_slug(slug)
    return upsert_policy_position(
        position_id=existing["id"] if existing else None,
        title=title,
        category=_categorize_title(title, body),
        summary=summary,
        body=body,
        status="approved",
        owner_scope="campaign",
        effective_from=None,
        effective_to=None,
        version_label=CAMPAIGN_VERSION_LABEL,
        actor_id=actor_id,
    )


def _delete_txt_campaign_links() -> tuple[int, set[int]]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT l.id, l.position_id, d.metadata_json
            FROM policy_document_links l
            JOIN policy_positions p ON p.id = l.position_id
            JOIN policy_documents d ON d.id = l.document_id
            WHERE p.owner_scope = 'campaign'
              AND COALESCE(p.version_label, '') = ?
              AND d.doc_type = 'pledge'
              AND d.source_ref LIKE ?
            """,
            (CAMPAIGN_VERSION_LABEL, f"{SOURCE_KEY}:%"),
        ).fetchall()

        link_ids: list[int] = []
        position_ids: set[int] = set()
        for row in rows:
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except json.JSONDecodeError:
                metadata = {}
            if str(metadata.get("file_type", "")).lower() != "txt":
                continue
            link_ids.append(int(row["id"]))
            position_ids.add(int(row["position_id"]))

        if link_ids:
            conn.executemany(
                "DELETE FROM policy_document_links WHERE id = ?",
                [(link_id,) for link_id in link_ids],
            )
            conn.commit()
        return len(link_ids), position_ids
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _delete_orphan_campaign_positions(candidate_position_ids: set[int]) -> int:
    if not candidate_position_ids:
        return 0

    conn = get_connection()
    deleted = 0
    try:
        for position_id in sorted(candidate_position_ids):
            row = conn.execute(
                """
                SELECT p.id, COUNT(l.id) AS link_count
                FROM policy_positions p
                LEFT JOIN policy_document_links l ON l.position_id = p.id
                WHERE p.id = ?
                  AND p.owner_scope = 'campaign'
                  AND COALESCE(p.version_label, '') = ?
                GROUP BY p.id
                """,
                (position_id, CAMPAIGN_VERSION_LABEL),
            ).fetchone()
            if row is None or int(row["link_count"] or 0) > 0:
                continue
            conn.execute("DELETE FROM policy_positions WHERE id = ?", (position_id,))
            deleted += 1
        conn.commit()
        return deleted
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _cleanup_txt_only_campaign_positions() -> dict:
    removed_links, candidate_position_ids = _delete_txt_campaign_links()
    removed_positions = _delete_orphan_campaign_positions(candidate_position_ids)
    return {
        "removed_txt_links": removed_links,
        "removed_txt_only_positions": removed_positions,
    }


def _dedupe_campaign_positions() -> dict:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT p.id, p.title, p.category, p.summary, p.body,
                   d.title AS document_title, d.body AS document_body, d.metadata_json
            FROM policy_positions p
            JOIN policy_document_links l ON l.position_id = p.id
            JOIN policy_documents d ON d.id = l.document_id
            WHERE p.owner_scope = 'campaign'
              AND COALESCE(p.version_label, '') = ?
              AND d.doc_type = 'pledge'
              AND d.source_ref LIKE ?
            ORDER BY p.id ASC
            """,
            (CAMPAIGN_VERSION_LABEL, f"{SOURCE_KEY}:%"),
        ).fetchall()

        groups: dict[str, list[dict]] = {}
        for row in rows:
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except json.JSONDecodeError:
                metadata = {}
            key = _campaign_canonical_key(row["document_title"] or row["title"])
            groups.setdefault(key, []).append(
                {
                    "position_id": int(row["id"]),
                    "position_title": row["title"],
                    "position_category": row["category"] or "대선공약",
                    "position_summary": row["summary"] or "",
                    "position_body": row["body"] or "",
                    "document_title": row["document_title"] or row["title"],
                    "document_body": row["document_body"] or row["body"] or "",
                    "is_verified_pdf": bool(metadata.get("verified_public_source")),
                }
            )

        merged_positions = 0
        renamed_positions = 0
        for entries in groups.values():
            unique_ids = sorted({entry["position_id"] for entry in entries})
            if not unique_ids:
                continue

            by_id: dict[int, dict] = {}
            for entry in entries:
                current = by_id.get(entry["position_id"])
                if current is None or (entry["is_verified_pdf"] and not current["is_verified_pdf"]):
                    by_id[entry["position_id"]] = entry
            candidates = list(by_id.values())
            candidates.sort(key=lambda item: item["position_title"])
            candidates.sort(key=lambda item: len(item["position_title"]))
            candidates.sort(key=lambda item: item["position_category"] == "대선공약")
            candidates.sort(key=lambda item: not item["is_verified_pdf"])
            primary = candidates[0]
            canonical_title = primary["document_title"]
            canonical_body = primary["document_body"] or primary["position_body"]
            canonical_summary = _extract_summary(canonical_body)
            canonical_category = _categorize_title(canonical_title, canonical_body)

            conn.execute(
                """
                UPDATE policy_positions
                SET title = ?, category = ?, summary = ?, body = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (canonical_title, canonical_category, canonical_summary, canonical_body, primary["position_id"]),
            )
            renamed_positions += 1

            for duplicate_id in unique_ids:
                if duplicate_id == primary["position_id"]:
                    continue
                conn.execute(
                    """
                    INSERT INTO policy_document_links (position_id, document_id, relation_type, notes, created_by)
                    SELECT ?, document_id, relation_type, notes, created_by
                    FROM policy_document_links
                    WHERE position_id = ?
                    ON CONFLICT(position_id, document_id, relation_type) DO UPDATE SET
                        notes = COALESCE(policy_document_links.notes, excluded.notes)
                    """,
                    (primary["position_id"], duplicate_id),
                )
                conn.execute(
                    "DELETE FROM policy_positions WHERE id = ?",
                    (duplicate_id,),
                )
                merged_positions += 1

        conn.commit()
        return {"merged_campaign_positions": merged_positions, "renamed_campaign_positions": renamed_positions}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def sync_pdf_pledges(*, actor_id: Optional[int]) -> dict:
    pledge_dir = _pledge_dir()
    run_id = _create_ingest_run()
    imported_count = 0
    updated_count = 0
    skipped_count = 0

    try:
        if not pledge_dir.exists():
            raise FileNotFoundError(f"pledge dir not found: {pledge_dir}")

        touched_document_ids: list[int] = []
        for path in sorted(pledge_dir.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in {".pdf", ".txt"}:
                continue

            rel_path = str(path.relative_to(pledge_dir)).replace("\\", "/")
            source_ref = f"{SOURCE_KEY}:{rel_path}"
            source_url = f"/data/pdf/{_nfc('공약')}/{rel_path}"
            title = _normalize_title_from_filename(path.name)
            body = extract_text_from_file(path).strip()
            if not body:
                skipped_count += 1
                continue

            file_type = path.suffix.lower().lstrip(".")
            summary = _extract_summary(body)
            metadata = {
                "source_key": SOURCE_KEY,
                "file_name": path.name,
                "relative_path": rel_path,
                "file_type": file_type,
                "verified_public_source": file_type == "pdf",
            }
            existing = find_policy_document_by_source(source_ref=source_ref, source_url=source_url)

            if existing is None:
                created = upsert_policy_document(
                    document_id=None,
                    title=title,
                    doc_type="pledge",
                    summary=summary,
                    body=body,
                    speaker=None,
                    speaker_name=None,
                    owner_name=CAMPAIGN_OWNER_NAME,
                    source_url=source_url,
                    source_ref=source_ref,
                    published_at=None,
                    status="active",
                    metadata=metadata,
                    actor_id=actor_id,
                )
                document_id = int(created["id"])
                imported_count += 1
            else:
                changed = any(
                    [
                        title != existing["title"],
                        summary != (existing.get("summary") or ""),
                        body != (existing.get("body") or ""),
                        metadata != (existing.get("metadata") or {}),
                        CAMPAIGN_OWNER_NAME != (existing.get("owner_name") or ""),
                    ]
                )
                if not changed:
                    document_id = int(existing["id"])
                    skipped_count += 1
                else:
                    updated = upsert_policy_document(
                        document_id=existing["id"],
                        title=title,
                        doc_type=existing["doc_type"],
                        summary=summary,
                        body=body,
                        speaker=existing.get("speaker") or None,
                        speaker_name=existing.get("speaker_name") or None,
                        owner_name=CAMPAIGN_OWNER_NAME,
                        source_url=source_url,
                        source_ref=source_ref,
                        published_at=existing.get("published_at"),
                        status=existing["status"],
                        metadata=metadata,
                        actor_id=actor_id,
                    )
                    document_id = int(updated["id"])
                    updated_count += 1

            people = _extract_people_from_filename(path.name)
            if people:
                replace_policy_document_people(document_id, people)

            if file_type == "pdf":
                position = _upsert_campaign_position(
                    title=title,
                    summary=summary,
                    body=body,
                    actor_id=actor_id,
                )
                link_policy_document(
                    position_id=int(position["id"]),
                    document_id=document_id,
                    relation_type="references",
                    notes="대선공약 PDF 자동 연결",
                    actor_id=actor_id,
                )

            touched_document_ids.append(document_id)

        cleanup = _cleanup_txt_only_campaign_positions()
        dedupe = _dedupe_campaign_positions()

        for document_id in sorted(set(touched_document_ids)):
            rebuild_link_suggestions(document_id=document_id)

        _finish_ingest_run(
            run_id,
            status="completed",
            imported_count=imported_count,
            updated_count=updated_count,
            skipped_count=skipped_count,
        )
        return {
            "run_id": run_id,
            "source_key": SOURCE_KEY,
            "imported_count": imported_count,
            "updated_count": updated_count,
            "skipped_count": skipped_count,
            **cleanup,
            **dedupe,
        }
    except Exception as exc:
        _finish_ingest_run(
            run_id,
            status="failed",
            imported_count=imported_count,
            updated_count=updated_count,
            skipped_count=skipped_count,
            error_message=str(exc),
        )
        raise

def _campaign_position_has_verified_pdf(position_id: int) -> bool:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT d.metadata_json
            FROM policy_document_links l
            JOIN policy_documents d ON d.id = l.document_id
            WHERE l.position_id = ?
              AND d.doc_type = 'pledge'
              AND d.source_ref LIKE ?
            """,
            (position_id, f"{SOURCE_KEY}:%"),
        ).fetchall()
    finally:
        conn.close()

    for row in rows:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            metadata = {}
        if bool(metadata.get("verified_public_source")) or str(metadata.get("file_type", "")).lower() == "pdf":
            return True
    return False


def _upsert_campaign_position(*, title: str, summary: str, body: str, actor_id: Optional[int]) -> dict:
    slug = slugify(title)
    existing = _get_campaign_position_by_slug(slug)
    if existing and not _campaign_position_has_verified_pdf(int(existing["id"])):
        existing = None
    return upsert_policy_position(
        position_id=existing["id"] if existing else None,
        title=title,
        category=_categorize_title(title, body),
        summary=summary,
        body=body,
        status="approved",
        owner_scope="campaign",
        effective_from=None,
        effective_to=None,
        version_label=CAMPAIGN_VERSION_LABEL,
        actor_id=actor_id,
    )
