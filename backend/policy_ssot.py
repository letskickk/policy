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
    "party_rule",
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
EXCLUDED_PUBLIC_PEOPLE = {"양향자"}
PUBLIC_PEOPLE_PRIORITY = {
    "이준석": 1,
    "천하람": 2,
    "이주영": 3,
}
PERSON_ROLE_LABELS = {
    "proposer": "대표발의",
    "co_proposer": "공동발의",
    "spokesperson": "대변인",
    "deputy_spokesperson": "부대변인",
    "chief_spokesperson": "수석대변인",
    "member": "국회의원",
    "policy_owner": "정책 담당",
}
PUBLIC_MESSAGE_DOC_TYPES = {"statement", "press_release", "briefing"}


def _normalize_text(value: Optional[str]) -> str:
    return unicodedata.normalize("NFC", (value or "").strip())


def _normalize_optional_text(value: Optional[str]) -> Optional[str]:
    text = _normalize_text(value)
    return text or None


def _normalize_enum(value: Optional[str], allowed: set[str], field_name: str, default: str) -> str:
    text = (_normalize_text(value) or default).lower()
    if text not in allowed:
        raise HTTPException(status_code=400, detail=f"invalid {field_name}")
    return text


def _is_verified_public_pledge(document: dict) -> bool:
    if document.get("doc_type") != "pledge":
        return True
    metadata = document.get("metadata") or {}
    return bool(metadata.get("verified_public_source"))


def _decorate_public_documents(items: list[dict]) -> list[dict]:
    if not items:
        return []
    link_map: dict[int, list[dict]] = {int(item["id"]): [] for item in items}
    for link in list_policy_links():
        document_id = int(link["document_id"])
        if document_id not in link_map:
            continue
        link_map[document_id].append(
            {
                "position_id": link["position_id"],
                "position_title": link["position_title"],
                "position_slug": link["position_slug"],
                "relation_type": link["relation_type"],
            }
        )

    decorated: list[dict] = []
    for item in items:
        cloned = dict(item)
        cloned["linked_positions"] = link_map.get(int(item["id"]), [])[:3]
        cloned["primary_people"] = [
            {
                "person_name": person["person_name"],
                "person_role": person["person_role"],
            }
            for person in (item.get("people") or [])
            if person.get("is_primary")
        ][:3]
        decorated.append(cloned)
    return decorated


def _string_list(value: object) -> list[str]:
    items: list[str] = []
    if isinstance(value, list):
        for entry in value:
            text = _normalize_text(str(entry))
            if text:
                items.append(text)
    return items


def _meeting_participants(document: dict) -> list[str]:
    metadata = document.get("metadata") or {}
    participants = _string_list(metadata.get("participants"))
    if participants:
        return participants
    seen: set[str] = set()
    names: list[str] = []
    for person in document.get("people") or []:
        name = _normalize_text(person.get("person_name"))
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def _meeting_agenda_items(document: dict) -> list[str]:
    metadata = document.get("metadata") or {}
    agenda = _string_list(metadata.get("agenda_items"))
    if agenda:
        return agenda
    topic = _normalize_text(metadata.get("topic"))
    return [topic] if topic else []


def _meeting_decisions(document: dict) -> list[str]:
    metadata = document.get("metadata") or {}
    decisions = _string_list(metadata.get("decisions"))
    if decisions:
        return decisions
    decision_summary = _normalize_text(metadata.get("decision_summary"))
    return [decision_summary] if decision_summary else []


def _build_meeting_timeline(document: dict) -> list[dict]:
    metadata = document.get("metadata") or {}
    held_at = _normalize_text(metadata.get("held_at") or document.get("published_at"))
    meeting_type = _normalize_text(metadata.get("meeting_type")) or "회의"
    timeline: list[dict] = []
    if held_at:
        timeline.append(
            {
                "kind": "meeting_event",
                "at": held_at,
                "title": f"{meeting_type} 개최",
                "summary": document.get("summary") or "회의 기록이 등록됐습니다.",
            }
        )
    for decision in _meeting_decisions(document)[:5]:
        timeline.append(
            {
                "kind": "meeting_event",
                "at": held_at,
                "title": "결정사항",
                "summary": decision,
            }
        )
    return timeline


def _build_rule_timeline(document: dict) -> list[dict]:
    metadata = document.get("metadata") or {}
    timeline: list[dict] = []
    effective_from = _normalize_text(metadata.get("effective_from") or document.get("published_at"))
    if effective_from:
        timeline.append(
            {
                "kind": "rule_revision",
                "at": effective_from,
                "title": metadata.get("rule_kind_label") or "규정 시행",
                "summary": metadata.get("version_label") or document.get("summary") or "규정 본문이 반영됐습니다.",
            }
        )
    revisions = metadata.get("revision_history") or []
    if isinstance(revisions, list):
        for entry in revisions[:8]:
            if not isinstance(entry, dict):
                continue
            timeline.append(
                {
                    "kind": "rule_revision",
                    "at": _normalize_text(entry.get("at")),
                    "title": _normalize_text(entry.get("title")) or "규정 개정",
                    "summary": _normalize_text(entry.get("summary")) or "",
                }
            )
    timeline.sort(key=lambda item: ((item.get("at") or "9999-99-99"), item.get("title") or ""))
    return timeline


TOPIC_RULES = [
    {
        "key": "judicial",
        "label": "사법·검찰",
        "categories": {"사법", "정치개혁", "judicial", "politics", "reform", "치안·사법"},
        "keywords": {"사법", "검찰", "재판", "공소", "법원", "헌법", "탄핵", "수사", "형사"},
    },
    {
        "key": "political_reform",
        "label": "정치개혁",
        "categories": {"정치개혁", "정치", "politics", "reform"},
        "keywords": {"정치개혁", "개헌", "선거", "국회", "정당", "공천", "권력구조", "헌법개정"},
    },
    {
        "key": "economy",
        "label": "경제",
        "categories": {"경제", "economy", "industry", "startup"},
        "keywords": {"경제", "세금", "법인세", "규제", "투자", "노동", "기업", "창업", "벤처"},
    },
    {
        "key": "welfare",
        "label": "복지",
        "categories": {"복지", "welfare", "housing"},
        "keywords": {"복지", "연금", "청년", "주거", "주택", "출산", "돌봄", "아동"},
    },
    {
        "key": "education",
        "label": "교육",
        "categories": {"교육", "education"},
        "keywords": {"교육", "학교", "교사", "대학", "입시", "학습"},
    },
    {
        "key": "science",
        "label": "과학기술",
        "categories": {"과학기술", "science", "technology", "ai", "digital"},
        "keywords": {"과학", "기술", "ai", "인공지능", "디지털", "데이터", "연구개발", "반도체"},
    },
    {
        "key": "safety",
        "label": "안전·치안",
        "categories": {"안전", "치안", "safety", "transport", "치안·사법"},
        "keywords": {"치안", "안전", "범죄", "응급", "재난", "교통", "재해", "경찰", "교정"},
    },
    {
        "key": "defense",
        "label": "국방",
        "categories": {"국방", "defense", "security"},
        "keywords": {"국방", "병역", "안보", "군", "장병"},
    },
    {
        "key": "healthcare",
        "label": "의료",
        "categories": {"의료", "보건", "healthcare", "보건의료"},
        "keywords": {"의료", "보건", "응급", "건강보험", "건보", "병원", "의사"},
    },
]

TOPIC_TITLE_GATES = {
    "사법·검찰": {"사법", "검찰", "재판", "공소", "법원", "치안", "경찰", "교정"},
    "정치개혁": {"정치", "개혁", "개헌", "선거", "국회", "정당"},
    "의료": {"의료", "보건", "응급", "건보", "건강보험"},
}

PUBLIC_TEXT_STOPWORDS = {
    "개혁신당", "대통령", "대통령의", "대한민국", "정부", "정치", "국민", "문제", "문제가",
    "이번", "이미", "대한", "위한", "중심", "중심으로", "대해", "요구", "사건", "제도",
    "제도를", "것", "것은", "되는", "입니다", "있습니다", "아닌", "아니라", "그러나",
    "매우", "만든", "두고", "곳바로", "개인", "내부", "심각한", "보여주는", "운영하는",
}


def _classify_commentary_topic(item: dict) -> str:
    haystack = " ".join([item.get("title") or "", item.get("summary") or "", item.get("body") or ""]).lower()
    for rule in TOPIC_RULES:
        if any(keyword.lower() in haystack for keyword in rule["keywords"]):
            return rule["label"]
    return "기타 현안"


def _topic_to_categories(topic_label: str) -> set[str]:
    for rule in TOPIC_RULES:
        if rule["label"] == topic_label:
            return set(rule["categories"])
    return set()


def _topic_keywords(topic_label: str) -> set[str]:
    for rule in TOPIC_RULES:
        if rule["label"] == topic_label:
            return set(rule["keywords"])
    return set()


def _canonical_policy_key(title: str) -> str:
    text = _normalize_text(title)
    text = re.sub(r"^개혁신당\s*", "", text)
    text = re.sub(r"^대선\s*공약\s*", "", text)
    text = re.sub(r"^공약\s*", "", text)
    text = re.sub(r"[^0-9a-zA-Z가-힣]+", "", text.lower())
    return text


def _tokenize_public_text(*values: Optional[str]) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        if not value:
            continue
        for token in re.findall(r"[0-9A-Za-z가-힣]{2,}", value.lower()):
            if token in PUBLIC_TEXT_STOPWORDS or token.isdigit():
                continue
            tokens.add(token)
    return tokens


def _infer_related_positions_for_document(item: dict, approved_positions: list[dict]) -> list[dict]:
    topic_label = item.get("topic_label") or _classify_commentary_topic(item)
    preferred_categories = {value.lower() for value in _topic_to_categories(topic_label)}
    preferred_keywords = {value.lower() for value in _topic_keywords(topic_label)}
    doc_type = item.get("doc_type") or ""
    title_tokens = _tokenize_public_text(item.get("title"))
    doc_tokens = _tokenize_public_text(item.get("title"), item.get("summary"), item.get("body"))
    ranked: list[tuple[int, int, int, str, dict]] = []

    for position in approved_positions:
        category = (position.get("category") or "").lower()
        position_title = position.get("title") or ""
        position_summary = position.get("summary") or ""
        position_body = position.get("body") or ""
        pos_title_tokens = _tokenize_public_text(position_title)
        pos_tokens = _tokenize_public_text(position_title, position_summary, position_body, position.get("category"))
        overlap_title = title_tokens & pos_title_tokens
        overlap_all = doc_tokens & pos_tokens
        keyword_overlap = preferred_keywords & pos_tokens
        gate_keywords = TOPIC_TITLE_GATES.get(topic_label, set())
        if gate_keywords and not (gate_keywords & pos_title_tokens):
            continue

        score = 0
        if category and category in preferred_categories:
            score += 3
        if keyword_overlap:
            score += 2 + min(len(keyword_overlap), 3)
        score += min(len(overlap_title) * 3, 9)
        score += min(len(overlap_all), 5)
        if position_title and item.get("title") and _normalize_text(position_title) in _normalize_text(item["title"]):
            score += 4

        if doc_type == "bill":
            if not overlap_title and not keyword_overlap and len(overlap_all) < 2:
                continue
            score += min(len(overlap_title), 2)
        elif doc_type in {"statement", "press_release", "briefing"}:
            if len(overlap_all) < 2 and not keyword_overlap and not overlap_title:
                continue

        if score < 4:
            continue

        specificity = 1 if category not in {"공통공약", "general", "common"} else 0
        explicit_hint = 1 if overlap_title else 0
        ranked.append((score, explicit_hint, specificity, position_title, position))

    ranked.sort(key=lambda entry: entry[3])
    ranked.sort(key=lambda entry: entry[2], reverse=True)
    ranked.sort(key=lambda entry: entry[1], reverse=True)
    ranked.sort(key=lambda entry: entry[0], reverse=True)

    results: list[dict] = []
    seen_keys: set[str] = set()
    for score, _, _, _, position in ranked:
        key = _canonical_policy_key(position.get("title") or "")
        if key in seen_keys:
            continue
        seen_keys.add(key)
        results.append(
            {
                "position_id": int(position["id"]),
                "position_title": position["title"],
                "position_slug": position["slug"],
                "relation_type": "related",
                "is_inferred": True,
                "score": score,
            }
        )
        if len(results) >= 3:
            break
    return results


def _validate_date(value: Optional[str], field_name: str) -> Optional[str]:
    text = _normalize_optional_text(value)
    if text is None:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise HTTPException(status_code=400, detail=f"{field_name} must use YYYY-MM-DD format")
    return text


def slugify(value: str) -> str:
    text = _normalize_text(value).lower()
    text = re.sub(r"\s+", "-", text)
    text = "".join(
        ch for ch in text
        if (ch.isascii() and (ch.isalnum() or ch == "-")) or (0xAC00 <= ord(ch) <= 0xD7A3)
    )
    text = re.sub(r"-{2,}", "-", text).strip("-")
    if not text:
        raise HTTPException(status_code=400, detail="slug is empty after normalization")
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
    official_summary: Optional[str] = None,
    key_points: Optional[str] = None,
    relevance_note: Optional[str] = None,
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
    official_summary_clean = _normalize_optional_text(official_summary)
    key_points_clean = _normalize_optional_text(key_points)
    relevance_note_clean = _normalize_optional_text(relevance_note)
    body_clean = _normalize_optional_text(body)
    effective_from_clean = _validate_date(effective_from, "effective_from")
    effective_to_clean = _validate_date(effective_to, "effective_to")
    if effective_from_clean and effective_to_clean and effective_from_clean > effective_to_clean:
        raise HTTPException(status_code=400, detail="effective_from은 effective_to보다 앞을 수 없습니다.")
    version_clean = _normalize_optional_text(version_label)
    base_slug = slugify(title_clean)
    slug = _ensure_slug_unique("policy_positions", base_slug, position_id)

    conn = get_connection()
    try:
        if position_id is None:
            cur = conn.execute(
                """
                INSERT INTO policy_positions (
                    title, slug, category, summary, official_summary, key_points, relevance_note, body, status, owner_scope,
                    effective_from, effective_to, version_label, created_by, updated_by, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    title_clean,
                    slug,
                    category_clean,
                    summary_clean,
                    official_summary_clean,
                    key_points_clean,
                    relevance_note_clean,
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
            _insert_position_version(
                conn,
                position_id=position_id,
                version_label=version_clean,
                title=title_clean,
                category=category_clean,
                summary=summary_clean,
                official_summary=official_summary_clean,
                key_points=key_points_clean,
                relevance_note=relevance_note_clean,
                body=body_clean,
                status=status_clean,
                owner_scope=scope_clean,
                effective_from=effective_from_clean,
                effective_to=effective_to_clean,
                snapshot_type="create",
                actor_id=actor_id,
            )
        else:
            row = conn.execute("SELECT * FROM policy_positions WHERE id = ?", (position_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="정책 포지션을 찾을 수 없습니다.")
            previous = _row_to_position(row)
            conn.execute(
                """
                UPDATE policy_positions
                SET title = ?, slug = ?, category = ?, summary = ?, official_summary = ?, key_points = ?, relevance_note = ?, body = ?, status = ?, owner_scope = ?,
                    effective_from = ?, effective_to = ?, version_label = ?, updated_by = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (
                    title_clean,
                    slug,
                    category_clean,
                    summary_clean,
                    official_summary_clean,
                    key_points_clean,
                    relevance_note_clean,
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
            changed = any(
                [
                    previous["title"] != title_clean,
                    previous["category"] != category_clean,
                    previous["summary"] != (summary_clean or ""),
                    previous["official_summary"] != (official_summary_clean or ""),
                    previous["key_points"] != (key_points_clean or ""),
                    previous["relevance_note"] != (relevance_note_clean or ""),
                    previous["body"] != (body_clean or ""),
                    previous["status"] != status_clean,
                    previous["owner_scope"] != scope_clean,
                    (previous["effective_from"] or "") != (effective_from_clean or ""),
                    (previous["effective_to"] or "") != (effective_to_clean or ""),
                    previous["version_label"] != (version_clean or ""),
                ]
            )
            if changed:
                _insert_position_version(
                    conn,
                    position_id=position_id,
                    version_label=version_clean,
                    title=title_clean,
                    category=category_clean,
                    summary=summary_clean,
                    official_summary=official_summary_clean,
                    key_points=key_points_clean,
                    relevance_note=relevance_note_clean,
                    body=body_clean,
                    status=status_clean,
                    owner_scope=scope_clean,
                    effective_from=effective_from_clean,
                    effective_to=effective_to_clean,
                    snapshot_type="update",
                    actor_id=actor_id,
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_policy_position(position_id)


VALID_STATUS_TRANSITIONS = {
    "draft": {"review", "archived"},
    "review": {"draft", "approved", "archived"},
    "approved": {"archived"},
    "archived": {"draft"},
}


def update_policy_position_status(position_id: int, new_status: str, actor_id: Optional[int] = None) -> dict:
    """포지션 상태만 변경. 허용된 전이만 가능 (draft→review→approved)."""
    status_clean = _normalize_enum(new_status, POLICY_STATUS, "status", "draft")
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM policy_positions WHERE id = ?", (position_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="position not found")
        old_status = row["status"]
        if old_status == status_clean:
            return dict(row)
        allowed = VALID_STATUS_TRANSITIONS.get(old_status, set())
        if status_clean not in allowed:
            raise HTTPException(
                status_code=400,
                detail=f"상태 전이 불가: {old_status} → {status_clean} (허용: {', '.join(sorted(allowed))})",
            )
        conn.execute(
            "UPDATE policy_positions SET status = ?, updated_by = ?, updated_at = datetime('now') WHERE id = ?",
            (status_clean, actor_id, position_id),
        )
        # Re-read the updated row for version snapshot
        updated_row = dict(conn.execute("SELECT * FROM policy_positions WHERE id = ?", (position_id,)).fetchone())
        _insert_position_version(
            conn,
            position_id=position_id,
            version_label=None,
            title=updated_row["title"],
            category=updated_row["category"],
            summary=updated_row.get("summary"),
            official_summary=updated_row.get("official_summary"),
            key_points=updated_row.get("key_points"),
            relevance_note=updated_row.get("relevance_note"),
            body=updated_row.get("body"),
            status=status_clean,
            owner_scope=updated_row["owner_scope"],
            effective_from=updated_row.get("effective_from"),
            effective_to=updated_row.get("effective_to"),
            snapshot_type="update",
            actor_id=actor_id,
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
            raise HTTPException(status_code=404, detail="정책 포지션을 찾을 수 없습니다.")
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
    raise HTTPException(status_code=500, detail="관련 문서 조회에 실패했습니다.")


def delete_policy_position(position_id: int) -> None:
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM policy_positions WHERE id = ?", (position_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="정책 포지션을 찾을 수 없습니다.")
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
            raise HTTPException(status_code=404, detail="?얜챷苑뚨몴?筌≪뼚??????곷뮸??덈뼄.")
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
            raise HTTPException(status_code=404, detail="관련 정보를 찾을 수 없습니다.")
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
        "official_summary": row["official_summary"] or "",
        "key_points": row["key_points"] or "",
        "relevance_note": row["relevance_note"] or "",
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
        raise HTTPException(status_code=404, detail="policy position not found")
    item = _row_to_position(row)
    item["links"] = list_policy_links(position_id=position_id)
    item["versions"] = list_policy_position_versions(position_id)
    return item


def list_policy_position_versions(position_id: int) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, position_id, version_label, title, category, summary, official_summary, key_points, relevance_note, body, status, owner_scope,
                   effective_from, effective_to, snapshot_type, created_by, created_at
            FROM policy_position_versions
            WHERE position_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (position_id,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": int(row["id"]),
            "position_id": int(row["position_id"]),
            "version_label": row["version_label"] or "",
            "title": row["title"],
            "category": row["category"],
            "summary": row["summary"] or "",
            "official_summary": row["official_summary"] or "",
            "key_points": row["key_points"] or "",
            "relevance_note": row["relevance_note"] or "",
            "body": row["body"] or "",
            "status": row["status"],
            "owner_scope": row["owner_scope"],
            "effective_from": row["effective_from"],
            "effective_to": row["effective_to"],
            "snapshot_type": row["snapshot_type"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def get_policy_position_timeline(position_id: int) -> list[dict]:
    position = get_policy_position(position_id)
    entries: list[dict] = []
    for version in position.get("versions", []):
        entries.append(
            {
                "kind": "version",
                "title": version.get("version_label") or position["title"],
                "summary": version.get("summary") or "",
                "at": version.get("created_at") or "",
                "status": version.get("status") or "",
                "snapshot_type": version.get("snapshot_type") or "update",
            }
        )
    for document in list_documents_for_position(position_id):
        entries.append(
            {
                "kind": "document",
                "title": document["title"],
                "summary": document.get("summary") or "",
                "at": document.get("published_at") or document.get("created_at") or "",
                "doc_type": document.get("doc_type") or "",
                "relation_type": (document.get("link") or {}).get("relation_type") or "",
            }
        )
    entries.sort(key=lambda item: (item.get("at") or "", item.get("title") or ""), reverse=True)
    return entries


def get_policy_position_by_slug(slug_or_id: str) -> dict:
    key = _normalize_text(slug_or_id)
    if not key:
        raise HTTPException(status_code=404, detail="policy position not found")
    conn = get_connection()
    try:
        if key.isdigit():
            row = conn.execute("SELECT * FROM policy_positions WHERE id = ?", (int(key),)).fetchone()
        else:
            row = conn.execute("SELECT * FROM policy_positions WHERE slug = ?", (key,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="policy position not found")
    item = _row_to_position(row)
    item["links"] = list_policy_links(position_id=item["id"])
    item["versions"] = list_policy_position_versions(item["id"])
    return item


def _insert_position_version(
    conn,
    *,
    position_id: int,
    version_label: Optional[str],
    title: str,
    category: str,
    summary: Optional[str],
    official_summary: Optional[str],
    key_points: Optional[str],
    relevance_note: Optional[str],
    body: Optional[str],
    status: str,
    owner_scope: str,
    effective_from: Optional[str],
    effective_to: Optional[str],
    snapshot_type: str,
    actor_id: Optional[int],
) -> None:
    conn.execute(
        """
        INSERT INTO policy_position_versions (
            position_id, version_label, title, category, summary, official_summary, key_points, relevance_note,
            body, status, owner_scope, effective_from, effective_to, snapshot_type, created_by
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            position_id,
            version_label,
            title,
            category,
            summary,
            official_summary,
            key_points,
            relevance_note,
            body,
            status,
            owner_scope,
            effective_from,
            effective_to,
            snapshot_type,
            actor_id,
        ),
    )

def get_policy_document(document_id: int) -> dict:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM policy_documents WHERE id = ?", (document_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="policy document not found")
    item = _row_to_document(row)
    item["people"] = list_policy_document_people(document_id=document_id)
    item["linked_positions"] = list_policy_links(document_id=document_id)
    if item["doc_type"] == "bill":
        item["bill_progress"] = _bill_progress_stage(item)
        item["timeline"] = _build_bill_timeline(item)
    if item["doc_type"] in {"statement", "bill", "press_release", "briefing"}:
        item["topic_label"] = _classify_commentary_topic(item)
        approved_positions = list_policy_positions(status="approved")
        item["related_positions"] = item["linked_positions"] or _infer_related_positions_for_document(item, approved_positions)
    elif item["doc_type"] == "meeting_note":
        approved_positions = list_policy_positions(status="approved")
        item["participants"] = _meeting_participants(item)
        item["agenda_items"] = _meeting_agenda_items(item)
        item["decisions"] = _meeting_decisions(item)
        item["timeline"] = _build_meeting_timeline(item)
        item["related_positions"] = item["linked_positions"] or _infer_related_positions_for_document(item, approved_positions)
    elif item["doc_type"] == "party_rule":
        item["timeline"] = _build_rule_timeline(item)
        item["related_positions"] = item["linked_positions"]
    else:
        item["related_positions"] = item["linked_positions"]
    if "timeline" not in item:
        item["timeline"] = []
    item["derived_key_points"] = _build_document_key_points(item)
    item["derived_relevance_note"] = _build_document_relevance_note(item)
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


def get_policy_operations_overview() -> dict:
    conn = get_connection()
    try:
        suggestion_rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM policy_link_suggestions GROUP BY status"
        ).fetchall()
        source_rows = conn.execute(
            """
            SELECT source_key, status, imported_count, updated_count, skipped_count, error_message, started_at, finished_at
            FROM policy_ingest_runs
            ORDER BY started_at DESC, id DESC
            """
        ).fetchall()
    finally:
        conn.close()

    latest_by_source: dict[str, dict] = {}
    for row in source_rows:
        source_key = row["source_key"]
        if source_key in latest_by_source:
            continue
        latest_by_source[source_key] = {
            "source_key": source_key,
            "status": row["status"],
            "imported_count": int(row["imported_count"] or 0),
            "updated_count": int(row["updated_count"] or 0),
            "skipped_count": int(row["skipped_count"] or 0),
            "error_message": row["error_message"] or "",
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
        }

    return {
        "suggestions": {row["status"]: int(row["n"]) for row in suggestion_rows},
        "ingest_sources": list(latest_by_source.values()),
    }


def list_documents_for_position(position_id: int) -> list[dict]:
    links = list_policy_links(position_id=position_id)
    if not links:
        return []
    document_ids = [item["document_id"] for item in links]
    relation_by_document_id = {
        item["document_id"]: {
            "relation_type": item["relation_type"],
            "notes": item["notes"],
            "link_id": item["id"],
        }
        for item in links
    }
    conn = get_connection()
    try:
        placeholders = ",".join("?" for _ in document_ids)
        rows = conn.execute(
            f"SELECT * FROM policy_documents WHERE id IN ({placeholders})",
            tuple(document_ids),
        ).fetchall()
    finally:
        conn.close()

    people_map: dict[int, list[dict]] = {}
    for person in list_policy_document_people():
        people_map.setdefault(person["document_id"], []).append(person)

    doc_type_rank = {
        "bill": 1,
        "statement": 2,
        "policy": 3,
        "press_release": 4,
        "briefing": 5,
    }
    items = []
    for row in rows:
        item = _row_to_document(row)
        item["people"] = people_map.get(item["id"], [])
        item["link"] = relation_by_document_id.get(item["id"], {})
        items.append(item)
    items.sort(
        key=lambda item: (
            doc_type_rank.get(item["doc_type"], 99),
            item.get("published_at") or "",
            item["title"],
        ),
        reverse=False,
    )
    items.sort(key=lambda item: item.get("published_at") or "", reverse=True)
    items.sort(key=lambda item: doc_type_rank.get(item["doc_type"], 99))
    return items


def _build_policy_brief(item: dict, documents: list[dict]) -> dict:
    bill_count = sum(1 for entry in documents if entry.get("doc_type") == "bill")
    statement_count = sum(1 for entry in documents if entry.get("doc_type") == "statement")
    pledge_count = sum(1 for entry in documents if entry.get("doc_type") == "pledge")
    summary = item.get("official_summary") or item.get("summary") or item.get("body") or ""
    body = item.get("body") or ""
    summary_lines: list[str] = []
    if summary:
        summary_lines.append(summary.strip()[:160])
    if item.get("relevance_note"):
        summary_lines.append(item["relevance_note"].strip()[:120])
    if bill_count:
        summary_lines.append(f"관련 의원실 법안 {bill_count}건이 연결돼 있습니다.")
    elif statement_count:
        summary_lines.append(f"관련 대변인 논평 {statement_count}건으로 입장이 확인됩니다.")
    elif pledge_count:
        summary_lines.append(f"대선공약 원문 {pledge_count}건이 근거 문서입니다.")
    if not summary_lines and body:
        summary_lines.append(body.strip()[:160])
    return {
        "headline": item.get("title") or "",
        "summary": " ".join(summary_lines[:2]).strip(),
        "official_summary": item.get("official_summary") or "",
        "key_points": item.get("key_points") or "",
        "relevance_note": item.get("relevance_note") or "",
        "bill_count": bill_count,
        "statement_count": statement_count,
        "pledge_count": pledge_count,
    }


def _bill_progress_stage(document: dict) -> dict:
    metadata = document.get("metadata") or {}
    raw_values = [
        str(metadata.get("bill_stage") or "").strip(),
        str(metadata.get("decision_result") or "").strip(),
        str(metadata.get("status_badge") or "").strip(),
    ]
    raw = " / ".join([value for value in raw_values if value])

    ended_keywords = ("폐기", "철회", "임기만료", "대안반영")
    passed_keywords = ("가결", "이송", "공포", "통과")
    in_progress_keywords = (
        "접수",
        "회부",
        "상정",
        "심사",
        "소위",
        "위원회",
        "법사위",
        "본회의부의",
        "계류",
        "보류",
        "제안설명",
    )

    if any(keyword in raw for keyword in ended_keywords):
        return {
            "code": "disposed",
            "label": "입법 종료",
            "description": "이 법안은 현재 폐기·철회 또는 임기만료 상태입니다.",
            "raw": raw,
            "is_active": False,
        }

    if any(keyword in raw for keyword in passed_keywords):
        return {
            "code": "passed",
            "label": "입법 반영",
            "description": "이 법안은 가결 또는 후속 이송 단계까지 진행된 이력이 확인됩니다.",
            "raw": raw,
            "is_active": False,
        }

    if any(keyword in raw for keyword in in_progress_keywords):
        return {
            "code": "in_progress",
            "label": "입법 추진",
            "description": "이 법안은 현재 심사·회부·상정 등 진행 단계에 있습니다.",
            "raw": raw,
            "is_active": True,
        }

    return {
        "code": "filed",
        "label": "법안 발의",
        "description": "대표발의 이력은 확인되지만 세부 진행 상태는 추가 확인이 필요합니다.",
        "raw": raw,
        "is_active": True,
    }


def _build_bill_timeline(document: dict) -> list[dict]:
    metadata = document.get("metadata") or {}
    progress = _bill_progress_stage(document)
    stored_timeline = metadata.get("bill_timeline") or []
    legislation_notice = metadata.get("legislation_notice") or {}
    if isinstance(stored_timeline, list) and stored_timeline:
        normalized: list[dict] = []
        for entry in stored_timeline:
            if not isinstance(entry, dict):
                continue
            title = str(entry.get("title") or "").strip()
            if not title:
                continue
            at = str(entry.get("at") or "").strip()
            code = str(entry.get("code") or "").strip()
            summary = str(entry.get("summary") or "").strip()
            is_current = bool(entry.get("is_current"))
            if not at:
                continue
            if not summary:
                summary = f"{title} 단계가 {at} 기준으로 확인됩니다."
            normalized.append(
                {
                    "kind": "bill_event",
                    "code": code,
                    "at": at,
                    "title": title,
                    "summary": summary,
                    "is_current": is_current,
                }
            )
        notice_status = str(legislation_notice.get("status") or "").strip()
        notice_start = str(legislation_notice.get("start_at") or "").strip()
        notice_end = str(legislation_notice.get("end_at") or "").strip()
        if notice_start:
            notice_summary = notice_status or "입법예고"
            if notice_end:
                notice_summary = f"{notice_summary} ({notice_start} ~ {notice_end})"
            normalized.append(
                {
                    "kind": "bill_event",
                    "code": "LEG_NOTICE",
                    "at": notice_start,
                    "title": "입법예고",
                    "summary": notice_summary,
                    "is_current": notice_status == "입법예고중",
                }
            )
        decision_at = str(metadata.get("decision_at") or "").strip()
        decision_result = str(metadata.get("decision_result") or "").strip()
        has_terminal = any(item.get("code") in {"RESULT", "DISPOSED", "PASSED"} for item in normalized)
        if decision_result and decision_at and not has_terminal:
            normalized.append(
                {
                    "kind": "bill_event",
                    "code": "RESULT",
                    "at": decision_at,
                    "title": "최종 결과",
                    "summary": decision_result,
                    "is_current": False,
                }
            )
        if normalized:
            normalized.sort(key=lambda item: ((item.get("at") or "9999-99-99"), item.get("title") or ""))
            return normalized

    timeline: list[dict] = []
    proposed_at = metadata.get("proposed_at") or document.get("published_at")
    decision_at = metadata.get("decision_at")
    representative_name = metadata.get("representative_member_name") or document.get("speaker_name") or ""
    stage_text = str(metadata.get("bill_stage") or "").strip()
    committee = str(metadata.get("committee") or "").strip()

    if proposed_at:
        summary = "대표발의 법안이 국회에 접수됐습니다."
        if representative_name:
            summary = f"{representative_name} 의원 대표발의 법안이 국회에 접수됐습니다."
        timeline.append({"kind": "bill_event", "at": proposed_at, "title": "법안 접수", "summary": summary})

    notice_status = str(legislation_notice.get("status") or "").strip()
    notice_start = str(legislation_notice.get("start_at") or "").strip()
    notice_end = str(legislation_notice.get("end_at") or "").strip()
    if notice_start:
        notice_summary = notice_status or "입법예고"
        if notice_end:
            notice_summary = f"{notice_summary} ({notice_start} ~ {notice_end})"
        timeline.append(
            {
                "kind": "bill_event",
                "at": notice_start,
                "title": "입법예고",
                "summary": notice_summary,
            }
        )

    if committee:
        timeline.append(
            {
                "kind": "bill_event",
                "at": "",
                "title": "상임위 회부",
                "summary": f"{committee}에서 다루는 법안입니다.",
            }
        )

    stage_events: list[tuple[str, str]] = []
    if stage_text:
        if "접수" in stage_text and not committee:
            stage_events.append(("접수 완료", stage_text))
        if any(keyword in stage_text for keyword in ("회부", "위원회")) and committee:
            stage_events.append(("상임위 심사", f"{committee} 기준 단계: {stage_text}"))
        elif any(keyword in stage_text for keyword in ("회부", "위원회", "심사", "소위", "법사위")):
            stage_events.append(("상임위 심사", stage_text))
        if any(keyword in stage_text for keyword in ("상정", "제안설명")):
            stage_events.append(("안건 상정", stage_text))
        if any(keyword in stage_text for keyword in ("본회의", "부의")):
            stage_events.append(("본회의 단계", stage_text))
        if any(keyword in stage_text for keyword in ("보류", "계류")):
            stage_events.append(("계류·보류", stage_text))

    seen_titles: set[str] = set()
    for title, summary in stage_events:
        if title in seen_titles:
            continue
        seen_titles.add(title)
        timeline.append(
            {
                "kind": "bill_event",
                "at": decision_at or "",
                "title": title,
                "summary": summary,
            }
        )

    if progress.get("raw") and progress["label"] not in seen_titles and progress["label"] not in {"입법 추진", "법안 발의"}:
        timeline.append(
            {
                "kind": "bill_event",
                "at": decision_at or "",
                "title": progress["label"],
                "summary": progress["raw"],
            }
        )

    if metadata.get("decision_result") or decision_at:
        timeline.append(
            {
                "kind": "bill_event",
                "at": decision_at or proposed_at or "",
                "title": "의결 결과",
                "summary": metadata.get("decision_result") or progress.get("raw") or "최종 처리 결과가 확인됐습니다.",
            }
        )

    timeline.sort(key=lambda item: ((item.get("at") or "9999-99-99"), item.get("title") or ""))
    return timeline


def _build_document_key_points(document: dict) -> str:
    metadata = document.get("metadata") or {}
    linked_positions = document.get("linked_positions") or []
    related_positions = document.get("related_positions") or []

    if document.get("doc_type") == "bill":
        parts = []
        committee = str(metadata.get("committee") or "").strip()
        if committee:
            parts.append(f"소관 상임위는 {committee}입니다.")
        legislation_notice = metadata.get("legislation_notice") or {}
        notice_status = str(legislation_notice.get("status") or "").strip()
        notice_start = str(legislation_notice.get("start_at") or "").strip()
        notice_end = str(legislation_notice.get("end_at") or "").strip()
        if notice_start and notice_end:
            parts.append(f"입법예고는 {notice_status or '진행'} 상태로 {notice_start}부터 {notice_end}까지 진행됩니다.")
        elif notice_status:
            parts.append(f"입법예고 상태는 {notice_status}입니다.")
        progress = document.get("bill_progress") or _bill_progress_stage(document)
        if progress.get("raw"):
            parts.append(f"현재 국회 처리 단계는 {progress['raw']}입니다.")
        representative = str(metadata.get("representative_member_name") or document.get("speaker_name") or "").strip()
        if representative:
            parts.append(f"대표발의 의원은 {representative}입니다.")
        if linked_positions:
            titles = ", ".join(link.get("position_title") or "" for link in linked_positions[:2] if link.get("position_title"))
            if titles:
                parts.append(f"연결 정책은 {titles}입니다.")
        return " · ".join([part for part in parts if part]) or "법안 핵심 쟁점 정보가 아직 정리되지 않았습니다."

    if document.get("doc_type") in {"statement", "press_release", "briefing"}:
        parts = []
        topic = document.get("topic_label")
        if topic:
            parts.append(topic)
        speaker = " ".join([value for value in [document.get("speaker"), document.get("speaker_name")] if value]).strip()
        if speaker:
            parts.append(f"발화 주체 {speaker}")
        links = linked_positions or related_positions
        if links:
            parts.append("연결 정책 " + ", ".join(link.get("position_title") or "" for link in links[:2] if link.get("position_title")))
        return " · ".join([part for part in parts if part]) or "논평 핵심 쟁점 정보가 아직 정리되지 않았습니다."

    if document.get("doc_type") == "meeting_note":
        meeting_type = _normalize_text((document.get("metadata") or {}).get("meeting_type")) or "회의"
        agenda = _meeting_agenda_items(document)
        participants = _meeting_participants(document)
        parts = [meeting_type]
        if agenda:
            parts.append("주요 안건 " + ", ".join(agenda[:2]))
        if participants:
            parts.append("참석 " + ", ".join(participants[:3]))
        if linked_positions:
            parts.append("연결 정책 " + ", ".join(link.get("position_title") or "" for link in linked_positions[:2] if link.get("position_title")))
        return " · ".join([part for part in parts if part]) or "회의록 핵심 쟁점 정보가 아직 정리되지 않았습니다."

    if document.get("doc_type") == "party_rule":
        metadata = document.get("metadata") or {}
        rule_kind = _normalize_text(metadata.get("rule_kind_label") or metadata.get("rule_kind")) or "규정"
        version_label = _normalize_text(metadata.get("version_label"))
        key_articles = _string_list(metadata.get("key_articles"))
        parts = [rule_kind]
        if version_label:
            parts.append(version_label)
        if key_articles:
            parts.append("핵심 조항 " + ", ".join(key_articles[:3]))
        return " · ".join([part for part in parts if part]) or "규정 핵심 쟁점 정보가 아직 정리되지 않았습니다."

    return document.get("summary") or "핵심 쟁점 정보가 아직 정리되지 않았습니다."


def _build_document_relevance_note(document: dict) -> str:
    metadata = document.get("metadata") or {}
    linked_positions = document.get("linked_positions") or []
    related_positions = document.get("related_positions") or []

    if document.get("doc_type") == "bill":
        progress = document.get("bill_progress") or _bill_progress_stage(document)
        legislation_notice = metadata.get("legislation_notice") or {}
        notice_status = str(legislation_notice.get("status") or "").strip()
        notice_end = str(legislation_notice.get("end_at") or "").strip()
        if linked_positions:
            return f"연결된 정책 {len(linked_positions)}건을 실제 제도화하려는 입법 시도라서 중요합니다."
        if notice_status == "입법예고중":
            if notice_end:
                return f"현재 입법예고가 진행 중이며 의견수렴 마감일은 {notice_end}입니다. 실제 입법 추진의 현재성을 보여주는 자료입니다."
            return "현재 입법예고가 진행 중인 법안으로, 실제 입법 추진이 살아 있는지 확인하는 데 중요합니다."
        if progress.get("code") == "disposed":
            return "법안은 종료됐지만, 이 의제가 실제 국회 입법으로 시도된 이력을 보여주기 때문에 중요합니다."
        if progress.get("code") == "passed":
            return "정책이 실제 입법 반영 단계까지 이어진 사례라서 중요합니다."
        committee = str(metadata.get("committee") or "").strip()
        if committee:
            return f"{committee} 소관 현안으로 실제 국회 심사 흐름에 올라온 법안이라 지금 확인할 가치가 있습니다."
        return "공식 정책과 연결 가능한 실제 입법 자료라서 중요합니다."

    if document.get("doc_type") in {"statement", "press_release", "briefing"}:
        if linked_positions:
            return f"공식 연결 정책 {len(linked_positions)}건과 직접 이어지는 메시지입니다."
        if related_positions:
            return "정책과 함께 읽어야 맥락이 보이는 공식 메시지입니다."
        return "당의 현재 메시지 방향을 보여주는 공식 발화라서 중요합니다."

    if document.get("doc_type") == "meeting_note":
        decisions = _meeting_decisions(document)
        if linked_positions:
            return f"연결 정책 {len(linked_positions)}건이 어떤 회의 맥락에서 논의됐는지 보여주는 기록입니다."
        if decisions:
            return "실제 의사결정 또는 논의 결과가 담긴 회의 기록이라서 중요합니다."
        return "정책과 메시지의 내부 논의 맥락을 확인할 수 있는 회의 기록입니다."

    if document.get("doc_type") == "party_rule":
        metadata = document.get("metadata") or {}
        rule_kind = _normalize_text(metadata.get("rule_kind_label") or metadata.get("rule_kind")) or "규정"
        if linked_positions:
            return f"{rule_kind}이 연결 정책 {len(linked_positions)}건의 제도적 기준과 운영 근거를 보여줍니다."
        return f"{rule_kind}은 당 운영과 의사결정의 기준을 확인하는 기본 문서라서 중요합니다."

    return "공개 문서 맥락에서 함께 확인할 가치가 있습니다."


def _human_person_role_labels(roles: set[str]) -> list[str]:
    labels = []
    for role in sorted(roles):
        if role == "policy_owner":
            continue
        labels.append(PERSON_ROLE_LABELS.get(role, role))
    return labels


def _sort_person_documents(documents_list: list[dict]) -> list[dict]:
    doc_type_rank = {"bill": 1, "statement": 2, "press_release": 3, "briefing": 4, "pledge": 5}
    items = list(documents_list)
    items.sort(key=lambda item: item.get("title") or "")
    items.sort(key=lambda item: item.get("published_at") or "", reverse=True)
    items.sort(key=lambda item: doc_type_rank.get(item.get("doc_type") or "", 99))
    return items


def _build_person_focus_positions(documents_list: list[dict], linked_positions: list[dict]) -> list[dict]:
    stats: dict[int, dict] = {}
    for item in documents_list:
        for link in (item.get("linked_positions") or []):
            position_id = int(link["position_id"])
            entry = stats.setdefault(
                position_id,
                {
                    "position_id": position_id,
                    "position_title": link.get("position_title") or "",
                    "position_slug": link.get("position_slug") or "",
                    "count": 0,
                    "latest_at": "",
                    "explicit": False,
                },
            )
            entry["count"] += 1
            entry["latest_at"] = max(entry["latest_at"], item.get("published_at") or "")
            if not link.get("is_inferred"):
                entry["explicit"] = True
    for item in linked_positions:
        position_id = int(item["position_id"])
        entry = stats.setdefault(
            position_id,
            {
                "position_id": position_id,
                "position_title": item.get("position_title") or "",
                "position_slug": item.get("position_slug") or "",
                "count": 0,
                "latest_at": "",
                "explicit": True,
            },
        )
        entry["explicit"] = True
    results = list(stats.values())
    results.sort(key=lambda item: item["position_title"])
    results.sort(key=lambda item: item["latest_at"], reverse=True)
    results.sort(key=lambda item: item["count"], reverse=True)
    results.sort(key=lambda item: item["explicit"], reverse=True)
    return results


def _build_person_detail_payload(person_name: str, roles: set[str], documents_list: list[dict], linked_positions: list[dict]) -> dict:
    ordered_documents = _sort_person_documents(documents_list)
    bill_docs = [item for item in ordered_documents if item["doc_type"] == "bill"]
    statement_docs = [item for item in ordered_documents if item["doc_type"] == "statement"]
    press_docs = [item for item in ordered_documents if item["doc_type"] in {"press_release", "briefing"}]
    message_docs = [item for item in ordered_documents if item["doc_type"] in {"statement", "press_release", "briefing"}]
    pledge_docs = [item for item in ordered_documents if item["doc_type"] == "pledge"]
    role_labels = _human_person_role_labels(roles)
    focus_positions = _build_person_focus_positions(ordered_documents, linked_positions)
    focus_titles = [item["position_title"] for item in focus_positions if item.get("position_title")][:4]

    active_bill_count = 0
    ended_bill_count = 0
    for item in bill_docs:
        progress = item.get("bill_progress") or _bill_progress_stage(item)
        if progress.get("is_active"):
            active_bill_count += 1
        else:
            ended_bill_count += 1

    if bill_docs and focus_titles:
        brief = f"{person_name}의 대표발의 법안과 핵심 정책 의제를 한 번에 볼 수 있습니다. 현재 집중 의제는 {", ".join(focus_titles[:2])}입니다."
    elif bill_docs:
        brief = f"{person_name}의 대표발의 법안과 최근 국회 활동을 한 화면에서 확인할 수 있습니다."
    elif statement_docs:
        brief = f"{person_name}이 공식 메시지에서 어떤 정책 의제를 설명하는지 확인할 수 있습니다."
    else:
        brief = f"{person_name}과 연결된 공개 정책 자료를 한 번에 볼 수 있습니다."

    key_points_parts = []
    if role_labels:
        key_points_parts.append("역할: " + ", ".join(role_labels))
    if bill_docs:
        key_points_parts.append(f"대표발의 법안 {len(bill_docs)}건")
    if statement_docs:
        key_points_parts.append(f"공식 논평 {len(statement_docs)}건")
    if focus_titles:
        key_points_parts.append("주요 의제: " + ", ".join(focus_titles[:3]))
    if bill_docs:
        key_points_parts.append(f"최근 입법: {bill_docs[0]['title']}")
    elif statement_docs:
        key_points_parts.append(f"최근 메시지: {statement_docs[0]['title']}")

    if active_bill_count:
        relevance = f"현재 국회에서 진행 중인 대표발의 법안이 {active_bill_count}건 있어 정책 입장을 실제 입법으로 이어가는 흐름이 보입니다."
    elif bill_docs:
        relevance = f"대표발의 법안 {len(bill_docs)}건의 이력이 남아 있어 주요 정책 의제에 실제로 참여한 흔적을 확인할 수 있습니다."
    elif statement_docs:
        relevance = "공식 논평과 브리핑을 통해 당의 정책 메시지를 대외적으로 설명하는 역할이 분명히 드러납니다."
    else:
        relevance = "연결된 공개 문서가 아직 많지 않아 추가 데이터가 쌓일수록 활동 맥락이 더 선명해집니다."

    timeline = []
    for item in ordered_documents[:16]:
        summary = item.get("summary") or ""
        if item.get("doc_type") == "bill":
            progress = item.get("bill_progress") or _bill_progress_stage(item)
            raw = progress.get("raw") or progress.get("label") or ""
            if raw:
                summary = raw
        timeline.append({"kind": "document", "doc_type": item["doc_type"], "at": item.get("published_at") or "", "title": item.get("title") or "", "summary": summary})

    body_sections = []
    overview_lines = []
    if role_labels:
        overview_lines.append("- 역할: " + ", ".join(role_labels))
    if bill_docs:
        overview_lines.append(f"- 대표발의 법안: {len(bill_docs)}건")
        if active_bill_count or ended_bill_count:
            overview_lines.append(f"- 진행 중 법안: {active_bill_count}건 / 종료 이력: {ended_bill_count}건")
    if statement_docs:
        overview_lines.append(f"- 공식 논평: {len(statement_docs)}건")
    if press_docs:
        overview_lines.append(f"- 브리핑·보도자료: {len(press_docs)}건")
    if pledge_docs:
        overview_lines.append(f"- 공약 문서: {len(pledge_docs)}건")
    if overview_lines:
        body_sections.append("활동 개요\n" + "\n".join(overview_lines))

    if focus_positions:
        body_sections.append("주요 정책 의제\n" + "\n".join(f"- {item['position_title']}" + (f" · 연결 문서 {item['count']}건" if item.get("count") else "") for item in focus_positions[:5]))

    if bill_docs:
        bill_lines = []
        for item in bill_docs[:5]:
            progress = item.get("bill_progress") or _bill_progress_stage(item)
            raw = progress.get("raw") or ""
            bill_lines.append(f"- {item['title']}" + (f" · {raw}" if raw else ""))
        body_sections.append("대표 법안\n" + "\n".join(bill_lines))

    if statement_docs:
        body_sections.append("최근 공식 논평\n" + "\n".join(f"- {item['title']}" + (f" · {item.get('topic_label')}" if item.get("topic_label") else "") for item in statement_docs[:5]))

    return {
        "brief": brief,
        "role_labels": role_labels,
        "active_bill_count": active_bill_count,
        "ended_bill_count": ended_bill_count,
        "message_count": len(message_docs),
        "focus_positions": focus_positions[:6],
        "featured_bills": bill_docs[:5],
        "featured_commentary": statement_docs[:5],
        "featured_messages": message_docs[:5],
        "derived_key_points": " · ".join(key_points_parts) if key_points_parts else "핵심 쟁점 정보가 아직 없습니다.",
        "derived_relevance_note": relevance,
        "timeline": timeline,
        "body": "\n\n".join(body_sections).strip(),
    }

def _policy_execution_stage(documents: list[dict]) -> dict:
    bill_documents = [entry for entry in documents if entry.get("doc_type") == "bill"]
    has_statement = any(entry.get("doc_type") == "statement" for entry in documents)
    has_pledge = any(entry.get("doc_type") == "pledge" for entry in documents)
    if bill_documents:
        bill_states = [_bill_progress_stage(entry) for entry in bill_documents]
        if any(state["code"] == "passed" for state in bill_states):
            return {
                "code": "legislation_passed",
                "label": "입법 반영 단계",
                "description": "관련 법안이 가결 또는 후속 이송 단계까지 진행된 이력이 확인됩니다.",
            }
        if any(state["is_active"] for state in bill_states):
            return {
                "code": "legislation",
                "label": "입법 추진 단계",
                "description": "관련 법안이 현재 진행 중이어서 실제 제도화 시도가 이어지고 있습니다.",
            }
        return {
            "code": "legislation_history",
            "label": "입법 이력 확인",
            "description": "관련 법안 발의 이력은 있지만 현재는 폐기·철회 등으로 진행이 종료된 상태입니다.",
        }
    if has_statement:
        return {
            "code": "public_message",
            "label": "공식 메시지 단계",
            "description": "대변인 논평 등 공식 메시지로 입장이 반복 확인됩니다.",
        }
    if has_pledge:
        return {
            "code": "campaign_commitment",
            "label": "공약 제시 단계",
            "description": "대선공약 원문에 담긴 공식 약속입니다.",
        }
    return {
        "code": "policy_only",
        "label": "정책 정리 단계",
        "description": "정책 원문은 있으나 연결 문서는 아직 많지 않습니다.",
    }


def get_policy_position_detail(slug_or_id: str) -> dict:
    item = get_policy_position_by_slug(slug_or_id)
    item["documents"] = list_documents_for_position(item["id"])
    item["timeline"] = get_policy_position_timeline(item["id"])
    item["brief"] = _build_policy_brief(item, item["documents"])
    item["execution_stage"] = _policy_execution_stage(item["documents"])
    return item


def get_public_overview() -> dict:
    approved_positions = list_policy_positions(status="approved")
    active_documents = list_policy_documents(status="active")
    public_documents = [item for item in active_documents if _is_verified_public_pledge(item)]
    public_people = list_public_people()
    latest_messages = list_public_messages(limit=6)
    latest_meetings = list_public_meetings(limit=6)
    latest_rules = list_public_rules(limit=6)

    latest_positions = sorted(
        approved_positions,
        key=lambda item: ((item.get("updated_at") or ""), item["title"]),
        reverse=True,
    )[:6]
    latest_bills = [item for item in public_documents if item["doc_type"] == "bill"][:6]
    latest_pledges = [item for item in public_documents if item["doc_type"] == "pledge"][:6]

    curated_positions = []
    for position in approved_positions[:20]:
        docs = list_documents_for_position(position["id"])
        brief = _build_policy_brief(position, docs)
        stage = _policy_execution_stage(docs)
        curated_positions.append(
            {
                "id": position["id"],
                "slug": position["slug"],
                "title": position["title"],
                "category": position["category"],
                "summary": position["summary"] or "",
                "brief": brief,
                "execution_stage": stage,
                "bill_count": brief["bill_count"],
                "statement_count": brief["statement_count"],
                "pledge_count": brief["pledge_count"],
                "updated_at": position.get("updated_at") or "",
            }
        )
    curated_positions.sort(key=lambda entry: entry["title"])
    curated_positions.sort(key=lambda entry: entry["updated_at"], reverse=True)
    curated_positions.sort(key=lambda entry: entry["statement_count"], reverse=True)
    curated_positions.sort(key=lambda entry: entry["bill_count"], reverse=True)

    return {
        "counts": {
            "positions": len(approved_positions),
            "bills": sum(1 for item in public_documents if item["doc_type"] == "bill"),
            "messages": sum(1 for item in public_documents if item["doc_type"] in PUBLIC_MESSAGE_DOC_TYPES),
            "statements": sum(1 for item in public_documents if item["doc_type"] == "statement"),
            "pledges": sum(1 for item in public_documents if item["doc_type"] == "pledge"),
            "meetings": sum(1 for item in public_documents if item["doc_type"] == "meeting_note"),
            "rules": sum(1 for item in public_documents if item["doc_type"] == "party_rule"),
            "people": len(public_people),
        },
        "latest_positions": latest_positions,
        "latest_bills": _decorate_public_documents(latest_bills),
        "latest_statements": latest_messages,
        "latest_messages": latest_messages,
        "latest_meetings": latest_meetings,
        "latest_rules": latest_rules,
        "latest_pledges": _decorate_public_documents(latest_pledges),
        "top_people": public_people[:8],
        "curated_positions": curated_positions[:6],
    }


def list_public_messages(*, q: Optional[str] = None, speaker_name: Optional[str] = None, limit: int = 60) -> list[dict]:
    query = _normalize_text(q).lower()
    speaker_filter = _normalize_text(speaker_name)
    items = [
        item
        for item in list_policy_documents(status="active")
        if item["doc_type"] in PUBLIC_MESSAGE_DOC_TYPES and _is_verified_public_pledge(item)
    ]
    approved_positions = list_policy_positions(status="approved")

    if speaker_filter:
        items = [item for item in items if (item.get("speaker_name") or "") == speaker_filter]
    if query:
        items = [
            item
            for item in items
            if query in (item.get("title") or "").lower()
            or query in (item.get("summary") or "").lower()
            or query in (item.get("body") or "").lower()
            or query in (item.get("speaker_name") or "").lower()
        ]

    items = items[: max(1, min(limit, 200))]
    decorated = _decorate_public_documents(items)
    for item in decorated:
        item["topic_label"] = _classify_commentary_topic(item)
        explicit = item.get("linked_positions") or []
        related = explicit or _infer_related_positions_for_document(item, approved_positions)
        if explicit:
            for link in related:
                link["is_inferred"] = False
        item["related_positions"] = related
    return decorated


def list_public_commentary(*, q: Optional[str] = None, speaker_name: Optional[str] = None, limit: int = 60) -> list[dict]:
    return [item for item in list_public_messages(q=q, speaker_name=speaker_name, limit=limit) if item["doc_type"] == "statement"]


def get_public_messages_overview(*, limit: int = 120) -> dict:
    items = list_public_messages(limit=limit)

    topic_counts: dict[str, int] = {}
    speaker_counts: dict[str, int] = {}
    linked = []
    for item in items:
        topic = item.get("topic_label") or "기타 현안"
        topic_counts[topic] = topic_counts.get(topic, 0) + 1

        speaker = _normalize_text(item.get("speaker_name") or item.get("speaker"))
        if speaker:
            speaker_counts[speaker] = speaker_counts.get(speaker, 0) + 1

        if item.get("related_positions"):
            linked.append(item)

    topic_items = [
        {"topic_label": topic, "count": count}
        for topic, count in sorted(topic_counts.items(), key=lambda pair: (-pair[1], pair[0]))[:8]
    ]
    speaker_items = [
        {"speaker_name": speaker, "count": count}
        for speaker, count in sorted(speaker_counts.items(), key=lambda pair: (-pair[1], pair[0]))[:6]
    ]
    linked.sort(key=lambda item: item.get("title") or "")
    linked.sort(key=lambda item: item.get("published_at") or "", reverse=True)
    linked.sort(key=lambda item: len(item.get("linked_positions") or item.get("related_positions") or []), reverse=True)

    return {
        "counts": {
            "messages": len(items),
            "topics": len(topic_counts),
            "speakers": len(speaker_counts),
            "linked_messages": len(linked),
        },
        "topics": topic_items,
        "speakers": speaker_items,
        "featured": items[:3],
        "linked": linked[:6],
    }


def get_public_commentary_overview(*, limit: int = 120) -> dict:
    return get_public_messages_overview(limit=limit)


def list_public_meetings(*, q: Optional[str] = None, limit: int = 60) -> list[dict]:
    query = _normalize_text(q).lower()
    items = [item for item in list_policy_documents(doc_type="meeting_note", status="active") if _is_verified_public_pledge(item)]
    approved_positions = list_policy_positions(status="approved")
    if query:
        items = [
            item for item in items
            if query in (item.get("title") or "").lower()
            or query in (item.get("summary") or "").lower()
            or query in (item.get("body") or "").lower()
        ]
    decorated = _decorate_public_documents(items[: max(1, min(limit, 200))])
    for item in decorated:
        item["participants"] = _meeting_participants(item)
        item["agenda_items"] = _meeting_agenda_items(item)
        item["decisions"] = _meeting_decisions(item)
        item["related_positions"] = item.get("linked_positions") or _infer_related_positions_for_document(item, approved_positions)
        item["timeline"] = _build_meeting_timeline(item)
    return decorated


def list_public_rules(*, q: Optional[str] = None, limit: int = 60) -> list[dict]:
    query = _normalize_text(q).lower()
    items = [item for item in list_policy_documents(doc_type="party_rule", status="active") if _is_verified_public_pledge(item)]
    if query:
        items = [
            item for item in items
            if query in (item.get("title") or "").lower()
            or query in (item.get("summary") or "").lower()
            or query in (item.get("body") or "").lower()
        ]
    decorated = _decorate_public_documents(items[: max(1, min(limit, 200))])
    for item in decorated:
        item["timeline"] = _build_rule_timeline(item)
    return decorated


def get_public_meetings_overview(*, limit: int = 60) -> dict:
    items = list_public_meetings(limit=limit)
    return {
        "counts": {"meetings": len(items)},
        "featured": items[:3],
        "latest": items[:6],
    }


def get_public_rules_overview(*, limit: int = 60) -> dict:
    items = list_public_rules(limit=limit)
    return {
        "counts": {"rules": len(items)},
        "featured": items[:3],
        "latest": items[:6],
    }


def auto_link_public_commentary(*, actor_id: Optional[int], limit: int = 300, min_score: int = 4) -> dict:
    items = list_public_messages(limit=max(1, min(limit, 500)))
    created = 0
    skipped = 0

    for item in items:
        if item.get("linked_positions"):
            skipped += 1
            continue
        related = item.get("related_positions") or []
        if not related:
            skipped += 1
            continue
        top = related[0]
        if int(top.get("score") or 0) < min_score:
            skipped += 1
            continue
        if len((item.get("body") or "").strip()) < 40 and len((item.get("summary") or "").strip()) < 20:
            skipped += 1
            continue
        link_policy_document(
            position_id=int(top["position_id"]),
            document_id=int(item["id"]),
            relation_type="explains",
            notes="논평 주제 기반 자동 연결",
            actor_id=actor_id,
        )
        created += 1

    return {
        "created": created,
        "skipped": skipped,
        "limit": limit,
        "min_score": min_score,
    }


def list_public_people() -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT pp.person_name, pp.person_role, pp.is_reform_party, pp.document_id,
                   d.doc_type, d.published_at, d.metadata_json
            FROM policy_document_people pp
            JOIN policy_documents d ON d.id = pp.document_id
            ORDER BY pp.person_name ASC
            """,
        ).fetchall()
    finally:
        conn.close()

    stats: dict[str, dict] = {}
    for row in rows:
        person_name = row["person_name"]
        if not person_name or person_name in EXCLUDED_PUBLIC_PEOPLE or not int(row["is_reform_party"] or 0):
            continue
        metadata = {}
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            metadata = {}
        if row["doc_type"] == "pledge" and not metadata.get("verified_public_source"):
            continue

        item = stats.setdefault(
            person_name,
            {
                "person_name": person_name,
                "document_ids": set(),
                "proposer_count": 0,
                "co_proposer_count": 0,
                "latest_published_at": "",
            },
        )
        item["document_ids"].add(int(row["document_id"]))
        if row["person_role"] == "proposer":
            item["proposer_count"] += 1
        if row["person_role"] == "co_proposer":
            item["co_proposer_count"] += 1
        item["latest_published_at"] = max(item["latest_published_at"], row["published_at"] or "")

    items = [
        {
            "person_name": person_name,
            "document_count": len(item["document_ids"]),
            "proposer_count": item["proposer_count"],
            "co_proposer_count": item["co_proposer_count"],
            "latest_published_at": item["latest_published_at"],
            "display_priority": PUBLIC_PEOPLE_PRIORITY.get(person_name, 999),
        }
        for person_name, item in stats.items()
    ]
    items.sort(key=lambda item: item["person_name"])
    items.sort(key=lambda item: item["document_count"], reverse=True)
    items.sort(key=lambda item: item["proposer_count"], reverse=True)
    items.sort(key=lambda item: item["display_priority"])
    return items


def get_public_person_detail(person_name: str) -> dict:
    target = _normalize_text(person_name)
    if not target or target in EXCLUDED_PUBLIC_PEOPLE:
        raise HTTPException(status_code=404, detail="인물을 찾을 수 없습니다.")
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT pp.person_name, pp.person_role, pp.party_affiliation, pp.is_primary,
                   d.id AS document_id, d.title, d.slug, d.doc_type, d.summary, d.body,
                   d.speaker, d.speaker_name, d.owner_name, d.source_url, d.published_at, d.metadata_json,
                   p.id AS position_id, p.title AS position_title, p.slug AS position_slug,
                   l.relation_type
            FROM policy_document_people pp
            JOIN policy_documents d ON d.id = pp.document_id
            LEFT JOIN policy_document_links l ON l.document_id = d.id
            LEFT JOIN policy_positions p ON p.id = l.position_id
            WHERE pp.person_name = ?
              AND pp.is_reform_party = 1
            ORDER BY COALESCE(d.published_at, '0000-00-00') DESC, d.doc_type ASC, d.title ASC
            """,
            (target,),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        raise HTTPException(status_code=404, detail="인물을 찾을 수 없습니다.")

    approved_positions = list_policy_positions(status="approved")
    documents: dict[int, dict] = {}
    linked_positions: dict[int, dict] = {}
    roles: set[str] = set()
    for row in rows:
        metadata = {}
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            metadata = {}
        if row["doc_type"] == "pledge" and not metadata.get("verified_public_source"):
            continue
        roles.add(row["person_role"])
        doc = documents.setdefault(
            int(row["document_id"]),
            {
                "id": int(row["document_id"]),
                "title": row["title"],
                "slug": row["slug"],
                "doc_type": row["doc_type"],
                "summary": row["summary"] or "",
                "body": row["body"] or "",
                "speaker": row["speaker"] or "",
                "speaker_name": row["speaker_name"] or "",
                "owner_name": row["owner_name"] or "",
                "source_url": row["source_url"] or "",
                "published_at": row["published_at"],
                "person_role": row["person_role"],
                "linked_positions": [],
                "related_positions": [],
            },
        )
        if row["position_id"] is not None:
            link_item = {
                "position_id": int(row["position_id"]),
                "position_title": row["position_title"],
                "position_slug": row["position_slug"],
                "relation_type": row["relation_type"] or "",
            }
            if link_item not in doc["linked_positions"]:
                doc["linked_positions"].append(link_item)
            linked_positions[int(row["position_id"])] = {
                "position_id": int(row["position_id"]),
                "position_title": row["position_title"],
                "position_slug": row["position_slug"],
            }

    for doc in documents.values():
        if doc["doc_type"] == "bill":
            doc["bill_progress"] = _bill_progress_stage(doc)
        if doc["doc_type"] in {"statement", "press_release", "briefing"}:
            doc["topic_label"] = _classify_commentary_topic(doc)
        if doc["linked_positions"]:
            for link in doc["linked_positions"]:
                link["is_inferred"] = False
            doc["related_positions"] = doc["linked_positions"]
            continue
        if doc["doc_type"] in {"bill", "statement", "press_release", "briefing"}:
            inferred = _infer_related_positions_for_document(doc, approved_positions)
            doc["related_positions"] = inferred

    documents_list = _sort_person_documents(list(documents.values()))

    payload = {
        "person_name": target,
        "roles": sorted(roles),
        "documents": documents_list,
        "linked_positions": list(linked_positions.values()),
        "bill_count": sum(1 for item in documents_list if item["doc_type"] == "bill"),
        "statement_count": sum(1 for item in documents_list if item["doc_type"] == "statement"),
        "pledge_count": sum(1 for item in documents_list if item["doc_type"] == "pledge"),
    }
    payload.update(_build_person_detail_payload(target, roles, documents_list, payload["linked_positions"]))
    return payload
