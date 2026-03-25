import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from html import unescape
from typing import Optional
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

from backend.database import get_connection
from backend.policy_ssot import find_policy_document_by_source, upsert_policy_document

SOURCE_KEY = "nesdc_reform_party_polls"
BASE_URL = "https://www.nesdc.go.kr"
LIST_URL = f"{BASE_URL}/portal/bbs/B0000005/list.do"
DETAIL_URL = f"{BASE_URL}/portal/bbs/B0000005/view.do"
FILE_URL = f"{BASE_URL}/portal/cmm/fms/FileDown.do"
OWNER_NAME = "중앙선거여론조사심의위원회"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
)
REFORM_KEYWORDS = [
    "개혁신당",
    "개혁신당 후보",
    "개혁신당 지지",
    "개혁신당 정당지지도",
    "이준석",
    "천하람",
    "이주영",
    "전성균",
    "허은아",
]

LIST_ROW_RE = re.compile(
    r'<a\s+href="(?P<href>/portal/bbs/B0000005/view\.do\?[^"]*nttId=(?P<ntt_id>\d+)[^"]*)"[^>]*class="row tr"[^>]*>'
    r'(?P<inner>.*?)</a>',
    re.S,
)
SPAN_RE = re.compile(r"<span[^>]*class=\"col\"[^>]*>(?P<value>.*?)</span>", re.S)
STRIP_TAG_RE = re.compile(r"<[^>]+>")
ATTACHMENT_RE = re.compile(
    r"<th[^>]*>(?P<section>질문지|결과분석 자료)</th>\s*<td>(?P<body>.*?)</td>",
    re.S,
)
FILE_ONCLICK_RE = re.compile(
    r"view\('(?P<atch>[^']+)',\s*'(?P<file_sn>[^']+)',\s*'(?P<bbs_id>[^']+)',\s*'(?P<bbs_key>[^']+)'\)"
)
FILE_NAME_RE = re.compile(r"<p class=\"file\"><a[^>]*>(?P<name>.*?)</a></p>", re.S)
TITLE_RE = re.compile(r"<h4[^>]*>\s*여론조사결과 등록현황 상세보기\s*</h4>", re.S)
SUPPORT_LINE_RE = re.compile(
    r"(?P<line>[^\n]{0,60}개혁신당[^\n]{0,80}?\d{1,2}(?:\.\d+)?\s*%)",
    re.I,
)


@dataclass
class PollListItem:
    ntt_id: str
    href: str
    registration_no: str
    pollster: str
    client_name: str
    survey_method: str
    sample_frame: str
    title_region: str
    registered_at: Optional[str]
    region_name: str


def _strip_html(value: str) -> str:
    text = STRIP_TAG_RE.sub(" ", value or "")
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_date(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    text = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    matched = re.search(r"(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})", text)
    if matched:
        year, month, day = matched.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"
    return None


def _fetch_text(url: str, params: Optional[dict] = None, timeout: int = 30) -> str:
    full_url = url
    if params:
        full_url = f"{url}?{urlencode(params)}"
    last_error: Optional[Exception] = None
    for attempt in range(3):
        req = Request(
            full_url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,*/*"},
        )
        try:
            with urlopen(req, timeout=timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    raise last_error  # type: ignore[misc]


def parse_list_page(html: str) -> list[PollListItem]:
    items: list[PollListItem] = []
    for match in LIST_ROW_RE.finditer(html):
        spans = [_strip_html(m.group("value")) for m in SPAN_RE.finditer(match.group("inner"))]
        if len(spans) < 7:
            continue
        items.append(
            PollListItem(
                ntt_id=match.group("ntt_id").strip(),
                href=urljoin(BASE_URL, unescape(match.group("href"))),
                registration_no=spans[0],
                pollster=spans[1],
                client_name=spans[2],
                survey_method=spans[3],
                sample_frame=spans[4],
                title_region=spans[5],
                registered_at=_normalize_date(spans[6]),
                region_name=spans[7] if len(spans) > 7 else "",
            )
        )
    return items


def fetch_search_page(*, search_wrd: str, page_index: int) -> list[PollListItem]:
    html = _fetch_text(
        LIST_URL,
        params={
            "menuNo": "200467",
            "searchWrd": search_wrd,
            "pageIndex": str(page_index),
        },
    )
    return parse_list_page(html)


def _extract_attachments(html: str) -> list[dict]:
    attachments: list[dict] = []
    for section_match in ATTACHMENT_RE.finditer(html):
        section = _strip_html(section_match.group("section"))
        body = section_match.group("body")
        names = FILE_NAME_RE.findall(body)
        onclicks = FILE_ONCLICK_RE.findall(body)
        for index, name_html in enumerate(names):
            file_name = _strip_html(name_html)
            file_meta = onclicks[index] if index < len(onclicks) else None
            if file_meta:
                atch_file_id, file_sn, bbs_id, bbs_key = file_meta
                download_url = f"{FILE_URL}?{urlencode({'atchFileId': atch_file_id, 'fileSn': file_sn, 'bbsId': bbs_id, 'bbsKey': bbs_key})}"
            else:
                download_url = ""
            attachments.append(
                {
                    "section": section,
                    "file_name": file_name,
                    "download_url": download_url,
                }
            )
    return attachments


def _extract_detail_text(html: str) -> str:
    start = TITLE_RE.search(html)
    text = html[start.start():] if start else html
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = STRIP_TAG_RE.sub("\n", text)
    lines = [re.sub(r"\s+", " ", unescape(line)).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def _extract_support_lines(text: str) -> list[str]:
    seen: list[str] = []
    for match in SUPPORT_LINE_RE.finditer(text):
        line = re.sub(r"\s+", " ", match.group("line")).strip()
        if line and line not in seen:
            seen.append(line)
    return seen[:20]


def _contains_reform_party(text: str) -> bool:
    lowered = text.lower()
    # "정당지지도" polls cover all parties including 개혁신당
    if "정당지지도" in lowered:
        return True
    return any(keyword.lower() in lowered for keyword in REFORM_KEYWORDS)


def fetch_poll_detail(ntt_id: str) -> dict:
    html = _fetch_text(DETAIL_URL, params={"menuNo": "200467", "nttId": ntt_id})
    detail_text = _extract_detail_text(html)
    return {
        "detail_url": f"{DETAIL_URL}?menuNo=200467&nttId={ntt_id}",
        "body_text": detail_text[:50000],
        "attachments": _extract_attachments(html),
        "support_lines": _extract_support_lines(detail_text),
        "contains_reform_party": _contains_reform_party(detail_text),
    }


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


def _build_summary(item: PollListItem, detail: dict) -> str:
    parts = [
        item.title_region,
        item.pollster,
        item.client_name,
        item.survey_method,
        item.sample_frame,
    ]
    if detail["support_lines"]:
        parts.append(" | ".join(detail["support_lines"][:3]))
    return " / ".join(part for part in parts if part)[:5000]


def sync_reform_party_polls(
    *,
    actor_id: Optional[int],
    since: str = "2024-02-01",
    search_terms: Optional[list[str]] = None,
    max_pages_per_term: int = 30,
) -> dict:
    since_date = datetime.strptime(since, "%Y-%m-%d").date()
    run_id = _create_ingest_run()
    imported_count = 0
    updated_count = 0
    skipped_count = 0
    processed_count = 0
    touched_ids: set[str] = set()
    search_terms = search_terms or ["개혁신당", "이준석", "천하람", "이주영", "전성균", "허은아"]

    try:
        for search_term in search_terms:
            for page_index in range(1, max_pages_per_term + 1):
                items = fetch_search_page(search_wrd=search_term, page_index=page_index)
                if not items:
                    break
                reached_older_rows = False
                for item in items:
                    if item.ntt_id in touched_ids:
                        continue
                    if item.registered_at:
                        row_date = datetime.strptime(item.registered_at, "%Y-%m-%d").date()
                        if row_date < since_date:
                            reached_older_rows = True
                            continue
                    detail = fetch_poll_detail(item.ntt_id)
                    if not detail["contains_reform_party"] and not _contains_reform_party(item.title_region):
                        skipped_count += 1
                        touched_ids.add(item.ntt_id)
                        continue

                    processed_count += 1
                    touched_ids.add(item.ntt_id)
                    source_ref = f"nesdc_poll:{item.ntt_id}"
                    source_url = detail["detail_url"]
                    summary = _build_summary(item, detail)
                    metadata = {
                        "source_key": SOURCE_KEY,
                        "ntt_id": item.ntt_id,
                        "registration_no": item.registration_no,
                        "pollster": item.pollster,
                        "client_name": item.client_name,
                        "survey_method": item.survey_method,
                        "sample_frame": item.sample_frame,
                        "title_region": item.title_region,
                        "region_name": item.region_name,
                        "registered_at": item.registered_at,
                        "attachments": detail["attachments"],
                        "support_lines": detail["support_lines"],
                        "search_term": search_term,
                        "keywords": REFORM_KEYWORDS,
                    }
                    existing = find_policy_document_by_source(source_ref=source_ref, source_url=source_url)
                    if existing is None:
                        upsert_policy_document(
                            document_id=None,
                            title=item.title_region,
                            doc_type="poll_result",
                            summary=summary,
                            body=detail["body_text"],
                            speaker=None,
                            speaker_name=item.pollster,
                            owner_name=OWNER_NAME,
                            source_url=source_url,
                            source_ref=source_ref,
                            published_at=item.registered_at,
                            status="active",
                            metadata=metadata,
                            actor_id=actor_id,
                        )
                        imported_count += 1
                        continue

                    existing_metadata = existing.get("metadata") or {}
                    merged_metadata = dict(existing_metadata)
                    merged_metadata.update(metadata)
                    changed = any(
                        [
                            item.title_region != existing["title"],
                            summary != (existing.get("summary") or ""),
                            detail["body_text"] != (existing.get("body") or ""),
                            item.pollster != (existing.get("speaker_name") or ""),
                            item.registered_at != existing.get("published_at"),
                            merged_metadata != existing_metadata,
                        ]
                    )
                    if not changed:
                        skipped_count += 1
                        continue

                    upsert_policy_document(
                        document_id=existing["id"],
                        title=item.title_region,
                        doc_type=existing["doc_type"],
                        summary=summary,
                        body=detail["body_text"],
                        speaker=existing.get("speaker"),
                        speaker_name=item.pollster,
                        owner_name=existing.get("owner_name") or OWNER_NAME,
                        source_url=source_url,
                        source_ref=source_ref,
                        published_at=item.registered_at,
                        status=existing["status"],
                        metadata=merged_metadata,
                        actor_id=actor_id,
                    )
                    updated_count += 1
                if reached_older_rows:
                    break

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
            "processed_count": processed_count,
            "search_terms": search_terms,
            "since": since,
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
