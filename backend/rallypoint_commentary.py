import json
import re
from dataclasses import dataclass
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT_DIR = Path(__file__).resolve().parents[1]

SOURCE_KEY = "rallypoint_commentary"
PRESS_SOURCE_KEY = "rallypoint_press"
# 허은아 대표 → 이준석 대표 전환 이후 자료만 사용
CUTOFF_DATE = "2025-02-01"
API_BASE_URL = "https://api-main.rallypoint.kr/v1/document"
LIST_URL = "https://rallypoint.kr/board/commentary"
PRESS_LIST_URL = "https://rallypoint.kr/board/press"
DETAIL_URL_TEMPLATE = "https://rallypoint.kr/board/commentary/{doc_id}"
PRESS_DETAIL_URL_TEMPLATE = "https://rallypoint.kr/board/press/{doc_id}"
OFFICIAL_BRIEFING_URL = "https://www.reformparty.kr/briefing"
OFFICIAL_BRIEFING_MAX_PAGES = 5
COMMENTARY_PAGE_SIZE = 20
COMMENTARY_MAX_PAGES = 200
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
MOBILE_META_RE = re.compile(r'<div[^>]*class="mob-view"[^>]*>.*?</div>', re.S)
TITLE_PREFIX_RE = re.compile(
    r"^\[(?P<ref_date>\d{6,8})[\s_.-]*(?P<party>.+?)\s+"
    r"(?P<role>수석대변인|부대변인|대변인)\s+논평\]\s*(?P<title>.*)$"
)
STATE_RE = re.compile(
    r'<script id="serverApp-state" type="application/json">\s*(?P<state>.*?)\s*</script>',
    re.S,
)
OFFICIAL_BRIEFING_ROW_RE = re.compile(
    r'<a href="(?P<url>https://www\.reformparty\.kr/briefing/\d+(?:\?page=\d+)?)">\s*(?P<label>.*?)\s*</a>.*?'
    r'<span class="date">(?P<published_at>\d{4}-\d{2}-\d{2})\s+\d{2}:\d{2}:\d{2}</span>',
    re.S,
)
OFFICIAL_BRIEFING_LABEL_RE = re.compile(
    r"^(?P<title>.+?)ㅣ(?P<name>[가-힣]{2,10})\s+(?P<role>수석대변인|부대변인|대변인)$"
)
BODY_SPEAKER_PATTERNS = [
    re.compile(
        r"개혁신당(?:\s+[가-힣A-Za-z]+){0,4}\s+(?P<role>수석대변인|부대변인|대변인)\s+"
        r"(?P<name>[가-힣]{2,10})"
    ),
    re.compile(
        r"개혁신당(?:\s+[가-힣A-Za-z]+){0,4}\s+(?P<role>수석대변인|부대변인|대변인)\s+"
        r"(?P<name>[가-힣](?:\s*[가-힣]){1,9})"
    ),
    re.compile(r"(?P<name>[가-힣](?:\s*[가-힣]){1,9})\s+(?P<role>수석대변인|부대변인|대변인)"),
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


def _fetch_json(url: str, timeout: int = 15) -> dict:
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json,text/plain,*/*",
            "Referer": LIST_URL,
        },
    )
    with urlopen(req, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        raw = response.read().decode(charset, errors="replace")
    return json.loads(raw)


def _strip_html(value: str) -> str:
    text = TAG_RE.sub(" ", value)
    text = unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _clean_title_cell_html(value: str) -> str:
    cleaned = MOBILE_META_RE.sub(" ", value or "")
    cleaned = _strip_html(cleaned)
    cleaned = re.sub(r"^\s*■\s*", "", cleaned)
    return cleaned.strip()


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


def _normalize_match_title(value: Optional[str]) -> str:
    text = (value or "").strip()
    text = text.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    text = re.sub(r"[\W_]+", "", text, flags=re.UNICODE)
    return text.lower()


def _normalize_person_name(value: Optional[str]) -> Optional[str]:
    text = _normalize_optional_space(value)
    if not text:
        return None
    collapsed = re.sub(r"(?<=[가-힣])\s+(?=[가-힣])", "", text)
    return collapsed or None


def _normalize_optional_space(value: Optional[str]) -> str:
    text = (value or "").strip()
    return re.sub(r"\s+", " ", text)


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
        name = _normalize_person_name(match.group("name"))
        if name:
            return name
    return None


def _decode_api_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {}
    raw = payload.get("data")
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return raw if isinstance(raw, dict) else {}


def _fetch_official_briefing_lookup() -> dict[str, dict]:
    lookup: dict[str, dict] = {}
    for page in range(1, OFFICIAL_BRIEFING_MAX_PAGES + 1):
        url = OFFICIAL_BRIEFING_URL if page == 1 else f"{OFFICIAL_BRIEFING_URL}?page={page}"
        try:
            html = _fetch_text(url)
        except (HTTPError, URLError, TimeoutError, OSError):
            break

        page_matches = 0
        for match in OFFICIAL_BRIEFING_ROW_RE.finditer(html):
            page_matches += 1
            label = _strip_html(match.group("label"))
            parsed = OFFICIAL_BRIEFING_LABEL_RE.match(label)
            if not parsed:
                continue
            title = parsed.group("title").strip()
            key = _normalize_match_title(title)
            if not key or key in lookup:
                continue
            lookup[key] = {
                "title": title,
                "speaker_name": parsed.group("name").strip(),
                "speaker_role": parsed.group("role").strip(),
                "published_at": match.group("published_at").strip(),
                "briefing_url": match.group("url").strip(),
                "source": "official_briefing",
            }
        if page_matches == 0:
            break
    return lookup


def _resolve_speaker_from_briefing_lookup(
    title: str,
    role: Optional[str],
    published_at: Optional[str],
    briefing_lookup: dict[str, dict],
) -> tuple[Optional[str], Optional[dict]]:
    item = briefing_lookup.get(_normalize_match_title(title))
    if not item:
        return None, None
    if role and item.get("speaker_role") and item["speaker_role"] != role:
        return None, None
    return item.get("speaker_name") or None, item


def _parse_title_metadata(raw_title: str, published_at: Optional[str]) -> tuple[str, str, Optional[str], dict]:
    cleaned = _clean_title_cell_html(raw_title)
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


def parse_commentary_api_list(payload: dict, limit: Optional[int] = None) -> list[CommentaryItem]:
    decoded = _decode_api_payload(payload)
    doc_list = decoded.get("docList")
    if not isinstance(doc_list, list):
        return []

    items: list[CommentaryItem] = []
    for doc in doc_list:
        if not isinstance(doc, dict):
            continue
        document_srl = str(doc.get("document_srl", "")).strip()
        raw_title = str(doc.get("title", "")).strip()
        if not document_srl or not raw_title:
            continue
        published_at = _normalize_date(str(doc.get("regdate", "")).strip()[:10].replace(".", "-")) or _normalize_date(
            str(doc.get("regdate", "")).strip()[:8].replace(".", "-")
        )
        if published_at is None:
            regdate = str(doc.get("regdate", "")).strip()
            if len(regdate) >= 8 and regdate[:8].isdigit():
                published_at = f"{regdate[:4]}-{regdate[4:6]}-{regdate[6:8]}"
        title_text, speaker, speaker_name, metadata = _parse_title_metadata(raw_title, published_at)
        items.append(
            CommentaryItem(
                row_no=document_srl,
                title=title_text,
                published_at=published_at,
                source_url=DETAIL_URL_TEMPLATE.format(doc_id=document_srl),
                source_ref=f"{SOURCE_KEY}:{document_srl}",
                speaker=speaker,
                speaker_name=speaker_name,
                body=None,
                summary=_extract_summary(title_text),
                metadata={
                    **metadata,
                    "source_key": SOURCE_KEY,
                    "board_url": LIST_URL,
                    "document_srl": document_srl,
                    "module_srl": str(doc.get("module_srl", "")).strip(),
                    "comment_count": str(doc.get("comment_count", "")).strip(),
                    "readed_count": str(doc.get("readed_count", "")).strip(),
                    "board_row_no": str(doc.get("list_order", "")).strip(),
                },
            )
        )
        if limit is not None and len(items) >= limit:
            break
    return items


def _fetch_commentary_list_items(limit: int) -> list[CommentaryItem]:
    items: list[CommentaryItem] = []
    seen_refs: set[str] = set()

    max_items = limit if limit > 0 else COMMENTARY_MAX_PAGES * COMMENTARY_PAGE_SIZE
    for page in range(COMMENTARY_MAX_PAGES):
        skip = page * COMMENTARY_PAGE_SIZE
        url = (
            f"{API_BASE_URL}?mid=commentary&skip={skip}&take={COMMENTARY_PAGE_SIZE}"
            "&keyword=&searchType=-1"
        )
        payload = _fetch_json(url)
        page_items = parse_commentary_api_list(payload)
        if not page_items:
            break

        added_on_page = 0
        hit_cutoff = False
        for item in page_items:
            if item.published_at and item.published_at < CUTOFF_DATE:
                hit_cutoff = True
                break
            if item.source_ref in seen_refs:
                continue
            seen_refs.add(item.source_ref)
            items.append(item)
            added_on_page += 1
            if len(items) >= max_items:
                return items

        if hit_cutoff or added_on_page == 0:
            break
        if len(page_items) < COMMENTARY_PAGE_SIZE:
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


def _extract_body_from_detail_payload(detail: dict, expected_title: str) -> tuple[Optional[str], dict]:
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


def _fetch_detail_payload_by_document_srl(document_srl: str) -> Optional[dict]:
    payload = _fetch_json(f"{API_BASE_URL}/detail?documentSrl={document_srl}")
    decoded = _decode_api_payload(payload)
    detail = decoded.get("docDetail")
    return detail if isinstance(detail, dict) else None


def _find_existing_commentary_document(item: CommentaryItem) -> Optional[dict]:
    from backend.database import get_connection
    from backend.policy_ssot import find_policy_document_by_source

    existing = find_policy_document_by_source(source_ref=item.source_ref, source_url=item.source_url)
    if existing is not None:
        return existing

    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT *
            FROM policy_documents
            WHERE doc_type = 'statement'
              AND title = ?
              AND COALESCE(published_at, '') = COALESCE(?, '')
              AND (
                    source_ref LIKE ? OR
                    source_url LIKE ?
                  )
            ORDER BY id DESC
            LIMIT 1
            """,
            (item.title, item.published_at, f"{SOURCE_KEY}:%", f"{LIST_URL}%"),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None

    from backend.policy_ssot import get_policy_document

    return get_policy_document(int(row["id"]))


def fetch_commentary_items(limit: int = 20, include_body: bool = True) -> list[CommentaryItem]:
    items = _fetch_commentary_list_items(limit)
    briefing_lookup = _fetch_official_briefing_lookup()

    for item in items:
        if item.speaker_name:
            continue
        matched_name, briefing_item = _resolve_speaker_from_briefing_lookup(
            item.title,
            item.speaker,
            item.published_at,
            briefing_lookup,
        )
        if matched_name:
            item.speaker_name = matched_name
            item.metadata["speaker_name_source"] = "official_briefing"
            item.metadata["official_briefing_url"] = briefing_item.get("briefing_url", "")

    if not include_body:
        return items

    for item in items:
        try:
            document_srl = str(item.metadata.get("document_srl", "")).strip()
            detail = _fetch_detail_payload_by_document_srl(document_srl) if document_srl else None
            if detail is not None:
                body, detail_meta = _extract_body_from_detail_payload(detail, item.title)
            else:
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
    from backend.database import get_connection

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
    from backend.database import get_connection

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
    from backend.database import get_connection

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
    from backend.policy_ssot import upsert_policy_document
    from backend.policy_suggestions import rebuild_link_suggestions

    run_id = _create_ingest_run()
    imported_count = 0
    updated_count = 0
    skipped_count = 0

    try:
        items = fetch_commentary_items(limit=limit, include_body=include_body)
        touched_document_ids: list[int] = []

        for item in items:
            if item.published_at and item.published_at < CUTOFF_DATE:
                skipped_count += 1
                continue
            existing = _find_existing_commentary_document(item)
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


# ─────────────────────────────────────────────
# 보도자료 (rallypoint.kr/board/press) 수집
# ─────────────────────────────────────────────

PRESS_TITLE_PREFIX_RE = re.compile(
    r"^\[(?P<ref_date>\d{6,8})[\s_.-]*(?P<party>.+?)\s+"
    r"(?P<label>보도자료)\]\s*(?P<title>.*)$"
)


def _parse_press_title(raw_title: str) -> tuple[str, dict]:
    """보도자료 제목에서 메타데이터 추출."""
    cleaned = _clean_title_cell_html(raw_title)
    match = PRESS_TITLE_PREFIX_RE.match(cleaned)
    if not match:
        return cleaned, {"title_prefix_date": ""}
    title = re.sub(r"^\s*■\s*", "", match.group("title").strip()).strip()
    return title or cleaned, {
        "party_name": match.group("party").strip(),
        "title_prefix_date": match.group("ref_date"),
    }


def _parse_press_api_list(payload: dict, limit: Optional[int] = None) -> list[CommentaryItem]:
    """보도자료 API 응답 파싱 → CommentaryItem 리스트."""
    decoded = _decode_api_payload(payload)
    doc_list = decoded.get("docList")
    if not isinstance(doc_list, list):
        return []

    items: list[CommentaryItem] = []
    for doc in doc_list:
        if not isinstance(doc, dict):
            continue
        document_srl = str(doc.get("document_srl", "")).strip()
        raw_title = str(doc.get("title", "")).strip()
        if not document_srl or not raw_title:
            continue
        published_at = None
        regdate = str(doc.get("regdate", "")).strip()
        if len(regdate) >= 8 and regdate[:8].isdigit():
            published_at = f"{regdate[:4]}-{regdate[4:6]}-{regdate[6:8]}"
        title_text, metadata = _parse_press_title(raw_title)
        if not title_text.strip():
            continue
        items.append(
            CommentaryItem(
                row_no=document_srl,
                title=title_text,
                published_at=published_at,
                source_url=PRESS_DETAIL_URL_TEMPLATE.format(doc_id=document_srl),
                source_ref=f"{PRESS_SOURCE_KEY}:{document_srl}",
                speaker="",
                speaker_name=None,
                body=None,
                summary=_extract_summary(title_text),
                metadata={
                    **metadata,
                    "source_key": PRESS_SOURCE_KEY,
                    "board_url": PRESS_LIST_URL,
                    "document_srl": document_srl,
                    "module_srl": str(doc.get("module_srl", "")).strip(),
                    "comment_count": str(doc.get("comment_count", "")).strip(),
                    "readed_count": str(doc.get("readed_count", "")).strip(),
                },
            )
        )
        if limit is not None and len(items) >= limit:
            break
    return items


def _fetch_press_list_items(limit: int) -> list[CommentaryItem]:
    """보도자료 API에서 리스트 수집."""
    items: list[CommentaryItem] = []
    seen_refs: set[str] = set()
    max_items = limit if limit > 0 else COMMENTARY_MAX_PAGES * COMMENTARY_PAGE_SIZE

    for page in range(COMMENTARY_MAX_PAGES):
        skip = page * COMMENTARY_PAGE_SIZE
        url = (
            f"{API_BASE_URL}?mid=press&skip={skip}&take={COMMENTARY_PAGE_SIZE}"
            "&keyword=&searchType=-1"
        )
        payload = _fetch_json(url)
        page_items = _parse_press_api_list(payload)
        if not page_items:
            break

        added_on_page = 0
        hit_cutoff = False
        for item in page_items:
            if item.published_at and item.published_at < CUTOFF_DATE:
                hit_cutoff = True
                break
            if item.source_ref in seen_refs:
                continue
            seen_refs.add(item.source_ref)
            items.append(item)
            added_on_page += 1
            if len(items) >= max_items:
                return items

        if hit_cutoff or added_on_page == 0:
            break
        if len(page_items) < COMMENTARY_PAGE_SIZE:
            break

    return items


def fetch_press_items(limit: int = 20, include_body: bool = True) -> list[CommentaryItem]:
    """보도자료 수집 (리스트 + 본문)."""
    items = _fetch_press_list_items(limit)

    if not include_body:
        return items

    for item in items:
        try:
            document_srl = str(item.metadata.get("document_srl", "")).strip()
            detail = _fetch_detail_payload_by_document_srl(document_srl) if document_srl else None
            if detail is not None:
                body, detail_meta = _extract_body_from_detail_payload(detail, item.title)
            else:
                body, detail_meta = _extract_body_from_detail(_fetch_text(item.source_url), item.title)
        except (HTTPError, URLError, TimeoutError, OSError):
            body, detail_meta = None, {"detail_status": "fetch_error"}
        item.body = body
        item.metadata.update(detail_meta)
    return items


def sync_press(*, actor_id: Optional[int], limit: int = 20, include_body: bool = True) -> dict:
    """보도자료 동기화 — DB에 upsert."""
    from backend.policy_ssot import upsert_policy_document
    from backend.policy_suggestions import rebuild_link_suggestions

    run_id = _create_ingest_run_for(PRESS_SOURCE_KEY)
    imported_count = 0
    updated_count = 0
    skipped_count = 0

    try:
        items = fetch_press_items(limit=limit, include_body=include_body)
        touched_document_ids: list[int] = []

        for item in items:
            if item.published_at and item.published_at < CUTOFF_DATE:
                skipped_count += 1
                continue
            existing = _find_existing_press_document(item)
            if existing is None:
                created = upsert_policy_document(
                    document_id=None,
                    title=item.title,
                    doc_type="press_release",
                    summary=item.summary,
                    body=item.body,
                    speaker="",
                    speaker_name=None,
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
            title = item.title or existing["title"]
            published_at = item.published_at or existing.get("published_at")

            changed = any([
                title != existing["title"],
                summary != (existing.get("summary") or None),
                body != (existing.get("body") or None),
                published_at != existing.get("published_at"),
                new_metadata != (existing.get("metadata") or {}),
            ])
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
                speaker=existing.get("speaker") or "",
                speaker_name=existing.get("speaker_name"),
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
            "source_key": PRESS_SOURCE_KEY,
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


def _create_ingest_run_for(source_key: str) -> int:
    """범용 ingest run 생성."""
    from backend.database import get_connection
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO policy_ingest_runs (source_key, status) VALUES (?, 'running')",
            (source_key,),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _find_existing_press_document(item: CommentaryItem) -> Optional[dict]:
    """보도자료 기존 문서 검색 (source_ref 또는 제목+날짜 매칭)."""
    from backend.database import get_connection
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM policy_documents WHERE source_ref = ? AND status != 'deleted' LIMIT 1",
            (item.source_ref,),
        ).fetchone()
        if row:
            d = dict(row)
            raw = d.get("metadata_json")
            d["metadata"] = json.loads(raw) if raw else {}
            return d

        row = conn.execute(
            "SELECT * FROM policy_documents WHERE doc_type = 'press_release' AND title = ? AND published_at = ? AND status != 'deleted' LIMIT 1",
            (item.title, item.published_at),
        ).fetchone()
        if row:
            d = dict(row)
            raw = d.get("metadata_json")
            d["metadata"] = json.loads(raw) if raw else {}
            return d
    finally:
        conn.close()
    return None
