import json
import re
from dataclasses import dataclass
from html import unescape
from typing import Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from backend.database import get_connection
from backend.policy_ssot import (
    find_policy_document_by_source,
    replace_policy_document_people,
    upsert_policy_document,
)
from backend.policy_suggestions import rebuild_link_suggestions

SOURCE_KEY = "assembly_reform_party_bills"
BASE_URL = "https://likms.assembly.go.kr/bill"
MEMBER_ID_URL = f"{BASE_URL}/bi/bill/sch/checkSameNm.do"
BILL_SEARCH_URL = f"{BASE_URL}/bi/bill/sch/findSchPaging.do"
BILL_DETAIL_URL = f"{BASE_URL}/bi/billDetailPage.do"
BILL_INFO_URL = f"{BASE_URL}/bi/bill/detail/billInfo.do"
SUMMARY_POPUP_URL = f"{BASE_URL}/bi/popup/billSummary.do"
PAL_LINK_RE = re.compile(
    r'<a[^>]+href="(?P<url>https://pal\.assembly\.go\.kr/napal/[^"]+lgsltPaId=[^"]+)"[^>]*>\s*입법예고\s*</a>',
    re.I | re.S,
)
PAL_PERIOD_RE = re.compile(
    r"입법예고기간\s*:\s*(?P<start>\d{4}-\d{2}-\d{2})\s*[~～]\s*(?P<end>\d{4}-\d{2}-\d{2})"
)
PAL_STATUS_RE = re.compile(
    r'<span[^>]*class="[^"]*bill_state[^"]*"[^>]*>\s*(?P<status>[^<]+?)\s*</span>',
    re.I | re.S,
)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
)
DEFAULT_MEMBERS = [
    {"name": "이준석"},
    {"name": "이주영"},
    {"name": "천하람"},
]

ROW_RE = re.compile(r"<tr>\s*(?P<row>.*?)\s*</tr>", re.S)
BADGE_RE = re.compile(r"<i[^>]*>(?P<badge>[^<]+)</i>", re.S)
ANCHOR_RE = re.compile(
    r'<a[^>]*data-bill_no="(?P<bill_no>[^"]+)"[^>]*data-bill-id="(?P<bill_id>[^"]+)"[^>]*title="(?P<title_attr>[^"]+)"[^>]*>\s*(?P<anchor_inner>.*?)</a>',
    re.S,
)
TD_RE = re.compile(r"<td[^>]*title=\"(?P<title>[^\"]*)\"[^>]*>(?P<inner>.*?)</td>", re.S)
H3_TITLE_RE = re.compile(r'<h3[^>]*title="\[(?P<bill_no>\d+)\]\s*(?P<title>[^"]+)"', re.S)
PROPOSER_RE = re.compile(r'<span id="proposerName">(?P<name>[^<]+)</span>')
COMMITTEE_RE = re.compile(r"<strong>소관위원회</strong>\s*<div>\s*(?P<value>.*?)\s*</div>", re.S)
PROPOSE_DATE_RE = re.compile(r"<strong>제안일자</strong>\s*<div>\s*(?P<value>.*?)\s*</div>", re.S)
DECISION_DATE_RE = re.compile(r"<strong>의결일자</strong>\s*<div>\s*(?P<value>.*?)\s*</div>", re.S)
DECISION_RESULT_RE = re.compile(r"<strong>의결결과</strong>\s*<div>\s*(?P<value>.*?)\s*</div>", re.S)

HIDDEN_INPUT_RE = re.compile(
    r'<input[^>]*type="hidden"[^>]*name="(?P<name>[^"]+)"[^>]*value="(?P<value>[^"]*)"[^>]*>',
    re.I,
)
STEP_NODE_RE = re.compile(
    r'<div\s+class="(?P<class_name>[^"]*(?:proc|on)[^"]*)"\s+[^>]*data-gbn="(?P<code>[^"]+)"[^>]*>'
    r'\s*<div class="title">(?P<title>.*?)</div>\s*'
    r'<div class="stepdate">(?P<date>.*?)</div>\s*</div>',
    re.S,
)


@dataclass
class BillListItem:
    bill_no: str
    bill_id: str
    title: str
    proposer_kind: str
    proposed_at: Optional[str]
    decision_at: Optional[str]
    decision_result: Optional[str]
    stage: Optional[str]
    status_badge: Optional[str]
    representative_name: str


def _strip_html(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", value))).strip()


def _normalize_date(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    text = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    return None


def _fetch_text(url: str, params: Optional[dict] = None, timeout: int = 20) -> str:
    full_url = url
    if params:
        full_url = f"{url}?{urlencode(params)}"
    req = Request(
        full_url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,*/*"},
    )
    with urlopen(req, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def _post_text(url: str, data: dict, timeout: int = 20) -> str:
    body = urlencode(data).encode("utf-8")
    req = Request(
        url,
        data=body,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,*/*",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": f"{BASE_URL}/bi/bill/sch/detailedSchPage.do?detailedTab=billDtl",
        },
        method="POST",
    )
    with urlopen(req, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


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


def resolve_member_id(member_name: str, age_from: str = "22", age_to: str = "22") -> str:
    raw = _post_text(
        MEMBER_ID_URL,
        {
            "ageFrom": age_from,
            "ageTo": age_to,
            "memName": member_name,
            "classId": "bill",
        },
    )
    payload = json.loads(raw)
    member_id = str(payload.get("membId") or "").strip()
    if not member_id:
        raise ValueError(f"member id not found: {member_name}")
    return member_id


def parse_bill_list(html: str, representative_name: str) -> list[BillListItem]:
    items: list[BillListItem] = []
    for match in ROW_RE.finditer(html):
        row_html = match.group("row")
        anchor = ANCHOR_RE.search(row_html)
        if not anchor:
            continue
        tds = TD_RE.findall(row_html)
        if len(tds) < 4:
            continue
        badge_match = BADGE_RE.search(anchor.group("anchor_inner"))
        title_html = BADGE_RE.sub("", anchor.group("anchor_inner"))
        title = _strip_html(title_html)
        proposer_kind = _strip_html(tds[1][1]) if len(tds) > 1 else ""
        proposed_at = _normalize_date(tds[2][0] or _strip_html(tds[2][1])) if len(tds) > 2 else None
        decision_at = None
        decision_result = None
        stage = None
        if len(tds) >= 6:
            decision_at = _normalize_date(tds[3][0] or _strip_html(tds[3][1]))
            decision_result = _strip_html(tds[4][1]) or None
            stage = _strip_html(tds[5][1]) or None
        elif len(tds) == 5:
            decision_result = _strip_html(tds[3][1]) or None
            stage = _strip_html(tds[4][1]) or None
        else:
            stage = _strip_html(tds[3][1]) or None
        items.append(
            BillListItem(
                bill_no=anchor.group("bill_no").strip(),
                bill_id=anchor.group("bill_id").strip(),
                title=title,
                proposer_kind=proposer_kind,
                proposed_at=proposed_at,
                decision_at=decision_at,
                decision_result=decision_result,
                stage=stage,
                status_badge=_strip_html(badge_match.group("badge")) if badge_match else None,
                representative_name=representative_name,
            )
        )
    return items


def fetch_member_bill_list(member_name: str, member_id: Optional[str] = None, age_from: str = "22", age_to: str = "22") -> list[BillListItem]:
    resolved_member_id = member_id or resolve_member_id(member_name, age_from=age_from, age_to=age_to)
    html = _post_text(
        BILL_SEARCH_URL,
        {
            "reqPageId": "billSrch",
            "detailedTab": "billDtl",
            "billKindCode": "",
            "billName": "",
            "ageFrom": age_from,
            "ageTo": age_to,
            "ageCmtId": "전체",
            "proposerKind": "",
            "proposer": "",
            "representKindCd": "대표발의",
            "represent": member_name,
            "representId": resolved_member_id,
            "repreOpenId": resolved_member_id,
            "repreNm": member_name,
            "isPopSelect": "N",
            "procResultCd": "",
            "proposalDtFrom": "",
            "proposalDtTo": "",
            "committeeCd": "",
            "detailSearchYN": "Y",
            "sort": "",
            "page": "1",
            "rows": "100",
        },
    )
    return parse_bill_list(html, representative_name=member_name)


def fetch_bill_detail(bill_id: str) -> dict:
    html = _fetch_text(BILL_DETAIL_URL, params={"billId": bill_id})
    title_match = H3_TITLE_RE.search(html)
    proposer_match = PROPOSER_RE.search(html)
    committee_match = COMMITTEE_RE.search(html)
    propose_date_match = PROPOSE_DATE_RE.search(html)
    decision_date_match = DECISION_DATE_RE.search(html)
    decision_result_match = DECISION_RESULT_RE.search(html)
    return {
        "detail_html": html,
        "bill_no": title_match.group("bill_no").strip() if title_match else "",
        "title": _strip_html(title_match.group("title")) if title_match else "",
        "proposer_name": _strip_html(proposer_match.group("name")) if proposer_match else "",
        "committee": _strip_html(committee_match.group("value")) if committee_match else "",
        "proposed_at": _normalize_date(_strip_html(propose_date_match.group("value"))) if propose_date_match else None,
        "decision_at": _normalize_date(_strip_html(decision_date_match.group("value"))) if decision_date_match else None,
        "decision_result": _strip_html(decision_result_match.group("value")) if decision_result_match else "",
    }


def extract_bill_info_payload(detail_html: str) -> dict[str, str]:
    payload: dict[str, str] = {}
    for match in HIDDEN_INPUT_RE.finditer(detail_html):
        name = match.group("name").strip()
        if not name:
            continue
        payload[name] = unescape(match.group("value") or "").strip()
    if not {"billId", "billNo", "billKindCd"}.issubset(payload):
        return {}
    return payload


def parse_bill_info_timeline(html: str) -> list[dict]:
    timeline: list[dict] = []
    for match in STEP_NODE_RE.finditer(html):
        title = _strip_html(match.group("title"))
        if not title:
            continue
        timeline.append(
            {
                "kind": "bill_event",
                "code": match.group("code").strip(),
                "title": title,
                "at": _normalize_date(_strip_html(match.group("date"))) or "",
                "summary": "",
                "is_current": "on" in (match.group("class_name") or "").split(),
            }
        )
    return timeline


def fetch_bill_timeline(detail_html: str) -> list[dict]:
    payload = extract_bill_info_payload(detail_html)
    if not payload:
        return []
    fragment = _post_text(BILL_INFO_URL, payload)
    return parse_bill_info_timeline(fragment)


def extract_legislation_notice_url(detail_html: str) -> str:
    match = PAL_LINK_RE.search(detail_html)
    return unescape(match.group("url")).strip() if match else ""


def parse_legislation_notice(html: str, *, source_url: str = "") -> dict:
    period_match = PAL_PERIOD_RE.search(html)
    status_match = PAL_STATUS_RE.search(html)
    period_start = _normalize_date(period_match.group("start")) if period_match else None
    period_end = _normalize_date(period_match.group("end")) if period_match else None
    status = _strip_html(status_match.group("status")) if status_match else ""
    if not status:
        if "lgsltpaOngoing" in source_url:
            status = "입법예고중"
        elif "lgsltpaDone" in source_url:
            status = "입법예고 종료"
    return {
        "url": source_url,
        "status": status,
        "start_at": period_start,
        "end_at": period_end,
    }


def fetch_legislation_notice(detail_html: str) -> dict:
    notice_url = extract_legislation_notice_url(detail_html)
    if not notice_url:
        return {"url": "", "status": "", "start_at": None, "end_at": None}
    html = _fetch_text(notice_url)
    return parse_legislation_notice(html, source_url=notice_url)


def parse_bill_summary_popup(html: str) -> str:
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", "\n", text)
    lines = [re.sub(r"\s+", " ", unescape(line)).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]

    heading_indexes = [idx for idx, line in enumerate(lines) if line == "제안이유 및 주요내용"]
    start_index = heading_indexes[-1] + 1 if heading_indexes else None
    if start_index is None:
        return ""

    body_lines: list[str] = []
    for line in lines[start_index:]:
        if line in {"의안 상세정보", "인쇄", "창닫기"}:
            break
        if line.startswith("[") and "]" in line:
            continue
        if re.search(r"의원 등 \d+인$", line):
            continue
        body_lines.append(line)
    return "\n".join(body_lines).strip()


def fetch_bill_summary(bill_id: str) -> str:
    html = _fetch_text(SUMMARY_POPUP_URL, params={"billId": bill_id})
    return parse_bill_summary_popup(html)


def sync_reform_party_bills(
    *,
    actor_id: Optional[int],
    members: Optional[list[dict]] = None,
    age_from: str = "22",
    age_to: str = "22",
) -> dict:
    run_id = _create_ingest_run()
    imported_count = 0
    updated_count = 0
    skipped_count = 0
    member_configs = members or DEFAULT_MEMBERS

    try:
        touched_document_ids: list[int] = []
        processed = 0
        for member in member_configs:
            member_name = str(member.get("name") or "").strip()
            if not member_name:
                continue
            bill_items = fetch_member_bill_list(
                member_name,
                member_id=member.get("member_id"),
                age_from=age_from,
                age_to=age_to,
            )
            for bill in bill_items:
                detail = fetch_bill_detail(bill.bill_id)
                bill_timeline = fetch_bill_timeline(detail.get("detail_html") or "")
                legislation_notice = fetch_legislation_notice(detail.get("detail_html") or "")
                summary_body = fetch_bill_summary(bill.bill_id)
                processed += 1
                title = detail["title"] or bill.title
                source_url = f"{BILL_DETAIL_URL}?billId={bill.bill_id}"
                source_ref = f"assembly_bill:{bill.bill_id}"
                metadata = {
                    "source_key": SOURCE_KEY,
                    "bill_no": detail["bill_no"] or bill.bill_no,
                    "bill_id": bill.bill_id,
                    "committee": detail["committee"] or "",
                    "proposed_at": detail["proposed_at"] or bill.proposed_at,
                    "decision_at": detail["decision_at"] or bill.decision_at,
                    "decision_result": detail["decision_result"] or bill.decision_result or "",
                    "bill_stage": bill.stage or "",
                    "bill_timeline": bill_timeline,
                    "legislation_notice": legislation_notice,
                    "status_badge": bill.status_badge or "",
                    "proposer_kind": bill.proposer_kind or "",
                    "representative_member_name": member_name,
                    "party_alignment": "party_bill",
                }
                summary_parts = [bill.stage or "", detail["committee"] or "", detail["decision_result"] or bill.decision_result or ""]
                summary = " / ".join([part for part in summary_parts if part]) or None
                existing = find_policy_document_by_source(source_ref=source_ref, source_url=source_url)
                if existing is None:
                    created = upsert_policy_document(
                        document_id=None,
                        title=title,
                        doc_type="bill",
                        summary=summary,
                        body=summary_body or None,
                        speaker="의원",
                        speaker_name=member_name,
                        owner_name="개혁신당",
                        source_url=source_url,
                        source_ref=source_ref,
                        published_at=detail["proposed_at"] or bill.proposed_at,
                        status="active",
                        metadata=metadata,
                        actor_id=actor_id,
                    )
                    replace_policy_document_people(
                        created["id"],
                        [
                            {
                                "person_name": member_name,
                                "person_role": "proposer",
                                "party_affiliation": "개혁신당",
                                "is_reform_party": True,
                                "is_primary": True,
                                "metadata": {"bill_id": bill.bill_id, "bill_no": metadata["bill_no"]},
                            }
                        ],
                    )
                    imported_count += 1
                    touched_document_ids.append(int(created["id"]))
                    continue

                new_metadata = dict(existing.get("metadata") or {})
                new_metadata.update(metadata)
                published_at = detail["proposed_at"] or bill.proposed_at or existing.get("published_at")
                changed = any(
                    [
                        title != existing["title"],
                        summary != (existing.get("summary") or None),
                        (summary_body or None) != (existing.get("body") or None),
                        member_name != (existing.get("speaker_name") or None),
                        published_at != existing.get("published_at"),
                        new_metadata != (existing.get("metadata") or {}),
                    ]
                )
                replace_policy_document_people(
                    existing["id"],
                    [
                        {
                            "person_name": member_name,
                            "person_role": "proposer",
                            "party_affiliation": "개혁신당",
                            "is_reform_party": True,
                            "is_primary": True,
                            "metadata": {"bill_id": bill.bill_id, "bill_no": metadata["bill_no"]},
                        }
                    ],
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
                    body=summary_body or existing.get("body") or None,
                    speaker=existing.get("speaker") or "의원",
                    speaker_name=member_name,
                    owner_name=existing.get("owner_name") or "개혁신당",
                    source_url=source_url,
                    source_ref=source_ref,
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
            "items": processed,
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
