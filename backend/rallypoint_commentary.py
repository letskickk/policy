import json
import re
from dataclasses import dataclass
from datetime import datetime
from html import unescape
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from backend.config import ROOT_DIR
from backend.database import get_connection
from backend.policy_ssot import find_policy_document_by_source, upsert_policy_document
from backend.policy_suggestions import rebuild_link_suggestions

SOURCE_KEY = "rallypoint_commentary"
LIST_URL = "https://rallypoint.kr/board/commentary"
DETAIL_URL_TEMPLATE = "https://rallypoint.kr/board/commentary/{doc_id}"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
)

ROW_RE = re.compile(
    r'<tr[^>]*class="">\s*'
    r'<td[^>]*class="admin-td">\s*(?P<row_no>\d+)\s*</td>\s*'
    r'<td[^>]*class="title readable">(?P<title_cell>.*?)</td>\s*'
    r'<td[^>]*class="tbl-date">.*?</td>\s*'
    r'<td[^>]*class="tbl-date">\s*(?P<published_at>\d{4}\.\d{2}\.\d{2}\s+\d{2}:\d{2})\s*</td>',
    re.S,
)
TAG_RE = re.compile(r"<[^>]+>")
TITLE_PREFIX_RE = re.compile(
    r"^\[(?P<ref_date>\d{6})_(?P<party>.+?)\s+(?P<role>수석대변인|부대변인|대변인)\s+논평\]\s*(?P<title>.*)$"
)
STATE_RE = re.compile(
    r'<script id="serverApp-state" type="application/json">\s*(?P<state>.*?)\s*</script>',
    re.S,
)
BODY_SPEAKER_PATTERNS = [
    re.compile(r"개혁신당\s+(?P<role>수석대변인|부대변인|대변인)\s+(?P<name>[가-힣]{2,10})"),
    re.compile(r"(?P<name>[가-힣]{2,10})\s+(?P<role>수석대변인|부대변인|대변인)"),
]


@dataclass
class CommentaryItem:
    row_no: str
    title: str
    published_at: Optional[str]
    source_url: str
    source_ref: str
    speaker: str
    speaker_name: Optional[str]
    body: Optional[str]
    summary: Optional[str]
    metadata: dict


def _fetch_text(url: str, timeout: int = 15) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": LIST_URL,
        },
    )
    with urlopen(req, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def _strip_html(value: str) -> str:
    text = TAG_RE.sub(" ", value)
    text = unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _normalize_date(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    for fmt in ("%Y.%m.%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _extract_summary(title: str) -> str:
    return title.replace("■", "").strip()[:300]


def _load_spokesperson_registry() -> list[dict]:
    path = ROOT_DIR / "data" / "spokesperson_registry.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = data.get("items", [])
    return items if isinstance(items, list) else []


def _resolve_speaker_name(role: str, published_at: Optional[str]) -> Optional[str]:
    if not role:
        return None
    target = _normalize_date(published_at)
    for item in _load_spokesperson_registry():
        if str(item.get("role", "")).strip() != role:
            continue
        start = _normalize_date(item.get("effective_from"))
        end = _normalize_date(item.get("effective_to"))
        if target and start and target < start:
            continue
        if target and end and target > end:
            continue
        name = str(item.get("name", "")).strip()
        if name:
            return name
    return None


def _extract_speaker_name_from_body(body: Optional[str], role: Optional[str]) -> Optional[str]:
    if not body:
        return None
    for pattern in BODY_SPEAKER_PATTERNS:
        match = pattern.search(body)
        if not match:
            continue
        if role and match.group("role") != role:
            continue
        name = match.group("name").strip()
        if name:
            return name
    return None


def _parse_title_metadata(raw_title: str, published_at: Optional[str]) -> tuple[str, str, Optional[str], dict]:
    cleaned = _strip_html(raw_title)
    match = TITLE_PREFIX_RE.match(cleaned)
    if not match:
        return cleaned, "논평", None, {"speaker_role": "", "title_prefix_date": ""}

    role = match.group("role").strip()
    title = re.sub(r"^\s*■\s*", "", match.group("title").strip()).strip()
    speaker_name = _resolve_speaker_name(role, published_at)
    return title or cleaned, role, speaker_name, {
        "speaker_role": role,
        "party_name": match.group("party").strip(),
        "title_prefix_date": match.group("ref_date"),
        "speaker_name_source": "registry" if speaker_name else "",
    }


def parse_commentary_list(html: str, limit: Optional[int] = None) -> list[CommentaryItem]:
    items: list[CommentaryItem] = []
    for match in ROW_RE.finditer(html):
        row_no = match.group("row_no")
        published_at = _normalize_date(match.group("published_at"))
        title_text, speaker, speaker_name, metadata = _parse_title_metadata(match.group("title_cell"), published_at)
        items.append(
            CommentaryItem(
                row_no=row_no,
                title=title_text,
                published_at=published_at,
                source_url=DETAIL_URL_TEMPLATE.format(doc_id=row_no),
                source_ref=f"{SOURCE_KEY}:{row_no}",
                speaker=speaker,
                speaker_name=speaker_name,
                body=None,
                summary=_extract_summary(title_text),
                metadata={
                    **metadata,
                    "source_key": SOURCE_KEY,
                    "board_url": LIST_URL,
                    "board_row_no": row_no,
                },
            )
        )
        if limit is not None and len(items) >= limit:
            break
    return items


def _extract_detail_payload(html: str) -> Optional[dict]:
    match = STATE_RE.search(html)
    if not match:
        return None
    raw = match.group("state").replace("&q;", '"').replace("&l;", "<").replace("&g;", ">")
    try:
        state = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(state, dict) and isinstance(state.get("parsedArticleMain"), dict):
        detail = state["parsedArticleMain"].get("docDetail")
        return detail if isinstance(detail, dict) else None
    if isinstance(state, dict) and isinstance(state.get("docDetail"), dict):
        return state["docDetail"]
    return None


def _extract_body_from_detail(html: str, expected_title: str) -> tuple[Optional[str], dict]:
    detail = _extract_detail_payload(html)
    if not detail:
        return None, {"detail_status": "missing"}

    detail_title = _strip_html(str(detail.get("title", "")))
    body_html = detail.get("content")
    detail_meta = {
        "detail_document_srl": str(detail.get("document_srl", "")).strip(),
        "detail_title": detail_title,
        "detail_regdate": str(detail.get("regdate", "")).strip(),
    }
    if expected_title and detail_title and expected_title not in detail_title and detail_title not in expected_title:
        detail_meta["detail_status"] = "title_mismatch"
        return None, detail_meta
    if not isinstance(body_html, str) or not body_html.strip():
        detail_meta["detail_status"] = "empty_content"
        return None, detail_meta

    detail_meta["detail_status"] = "matched"
    return _strip_html(body_html) or None, detail_meta


def fetch_commentary_items(limit: int = 20, include_body: bool = True) -> list[CommentaryItem]:
    html = _fetch_text(LIST_URL)
    items = parse_commentary_list(html, limit=limit)
    if not include_body:
        return items

    for item in items:
        try:
            body, detail_meta = _extract_body_from_detail(_fetch_text(item.source_url), item.title)
        except (HTTPError, URLError, TimeoutError, OSError):
            body, detail_meta = None, {"detail_status": "fetch_error"}
        item.body = body
        item.metadata.update(detail_meta)
        if not item.speaker_name:
            body_name = _extract_speaker_name_from_body(body, item.speaker)
            if body_name:
                item.speaker_name = body_name
                item.metadata["speaker_name_source"] = "body"
    return items


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


def sync_commentary(*, actor_id: Optional[int], limit: int = 20, include_body: bool = True) -> dict:
    run_id = _create_ingest_run()
    imported_count = 0
    updated_count = 0
    skipped_count = 0

    try:
        items = fetch_commentary_items(limit=limit, include_body=include_body)
        touched_document_ids: list[int] = []

        for item in items:
            existing = find_policy_document_by_source(source_ref=item.source_ref, source_url=item.source_url)
            if existing is None:
                created = upsert_policy_document(
                    document_id=None,
                    title=item.title,
                    doc_type="statement",
                    summary=item.summary,
                    body=item.body,
                    speaker=item.speaker,
                    speaker_name=item.speaker_name,
                    owner_name="개혁신당",
                    source_url=item.source_url,
                    source_ref=item.source_ref,
                    published_at=item.published_at,
                    status="active",
                    metadata=item.metadata,
                    actor_id=actor_id,
                )
                imported_count += 1
                touched_document_ids.append(int(created["id"]))
                continue

            new_metadata = dict(existing.get("metadata") or {})
            new_metadata.update(item.metadata)
            body = item.body or existing.get("body") or None
            summary = item.summary or existing.get("summary") or None
            speaker = item.speaker or existing.get("speaker") or None
            speaker_name = item.speaker_name or existing.get("speaker_name") or None
            if not speaker_name:
                speaker_name = _extract_speaker_name_from_body(body, speaker)
                if speaker_name:
                    new_metadata["speaker_name_source"] = "body"
            title = item.title or existing["title"]
            published_at = item.published_at or existing.get("published_at")

            changed = any(
                [
                    title != existing["title"],
                    summary != (existing.get("summary") or None),
                    body != (existing.get("body") or None),
                    speaker != (existing.get("speaker") or None),
                    speaker_name != (existing.get("speaker_name") or None),
                    published_at != existing.get("published_at"),
                    new_metadata != (existing.get("metadata") or {}),
                ]
            )
            if not changed:
                skipped_count += 1
                touched_document_ids.append(int(existing["id"]))
                continue

            updated = upsert_policy_document(
                document_id=existing["id"],
                title=title,
                doc_type=existing["doc_type"],
                summary=summary,
                body=body,
                speaker=speaker,
                speaker_name=speaker_name,
                owner_name=existing.get("owner_name") or "개혁신당",
                source_url=item.source_url,
                source_ref=item.source_ref,
                published_at=published_at,
                status=existing["status"],
                metadata=new_metadata,
                actor_id=actor_id,
            )
            updated_count += 1
            touched_document_ids.append(int(updated["id"]))

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
            "items": len(items),
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
