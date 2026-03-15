from pathlib import Path
from uuid import uuid4

from backend import database
from backend import pdf_pledges_import
from backend.pdf_pledges_import import _extract_people_from_filename, _normalize_title_from_filename, sync_pdf_pledges
from backend.policy_featured import get_current_featured_issue, recommend_featured_issues, set_featured_issue
from backend.policy_ssot import (
    get_policy_position_by_slug,
    get_public_person_detail,
    get_policy_position_detail,
    link_policy_document,
    list_policy_links,
    upsert_policy_document,
    upsert_policy_position,
    replace_policy_document_people,
)


def _workspace_db_path(name: str) -> Path:
    root = Path(".test_tmp")
    root.mkdir(exist_ok=True)
    return root / f"{name}-{uuid4().hex}.db"


def test_featured_issue_recommendation_and_selection(monkeypatch):
    db_file = _workspace_db_path("featured-issue")
    monkeypatch.setattr(database, "DB_PATH", db_file)
    database.init_db()

    position = upsert_policy_position(
        position_id=None,
        title="사법개혁",
        category="justice",
        summary="사법개혁 정책",
        body="사법개혁 정책 본문",
        status="approved",
        owner_scope="party",
        effective_from=None,
        effective_to=None,
        version_label=None,
        actor_id=None,
    )
    document = upsert_policy_document(
        document_id=None,
        title="사법개혁 관련 논평",
        doc_type="statement",
        summary="최근 논평",
        body="사법개혁 관련 입장",
        speaker="대변인",
        speaker_name="문성호",
        owner_name="개혁신당",
        source_url="https://example.com/statement",
        source_ref="test:statement:1",
        published_at="2026-03-15",
        status="active",
        metadata={},
        actor_id=None,
    )
    link_policy_document(
        position_id=position["id"],
        document_id=document["id"],
        relation_type="explains",
        notes=None,
        actor_id=None,
    )

    recommendations = recommend_featured_issues(limit=5)
    assert recommendations
    assert recommendations[0]["position_id"] == position["id"]
    assert recommendations[0]["score"] > 0

    featured = set_featured_issue(
        position_id=position["id"],
        reason="최근 논평 집중",
        start_at="2026-03-15",
        end_at="2026-03-16",
        manual_weight=5,
        actor_id=None,
    )
    assert featured["position_id"] == position["id"]
    assert featured["reason"] == "최근 논평 집중"

    current = get_current_featured_issue()
    assert current is not None
    assert current["position_id"] == position["id"]


def test_policy_position_detail_sorts_bills_before_statements(monkeypatch):
    db_file = _workspace_db_path("policy-position-detail")
    monkeypatch.setattr(database, "DB_PATH", db_file)
    database.init_db()

    position = upsert_policy_position(
        position_id=None,
        title="연금개혁",
        category="welfare",
        summary="연금개혁 정책",
        body="연금개혁 본문",
        status="approved",
        owner_scope="party",
        effective_from=None,
        effective_to=None,
        version_label=None,
        actor_id=None,
    )
    statement = upsert_policy_document(
        document_id=None,
        title="연금개혁 관련 논평",
        doc_type="statement",
        summary="논평",
        body="논평 본문",
        speaker="대변인",
        speaker_name="문성호",
        owner_name="개혁신당",
        source_url="https://example.com/statement",
        source_ref="test:statement:2",
        published_at="2026-03-15",
        status="active",
        metadata={},
        actor_id=None,
    )
    bill = upsert_policy_document(
        document_id=None,
        title="연금개혁 법안",
        doc_type="bill",
        summary="법안",
        body="법안 본문",
        speaker=None,
        speaker_name=None,
        owner_name="개혁신당",
        source_url="https://example.com/bill",
        source_ref="test:bill:1",
        published_at="2026-03-14",
        status="active",
        metadata={},
        actor_id=None,
    )
    link_policy_document(position_id=position["id"], document_id=statement["id"], relation_type="explains", notes=None, actor_id=None)
    link_policy_document(position_id=position["id"], document_id=bill["id"], relation_type="implements", notes=None, actor_id=None)

    detail = get_policy_position_detail(position["slug"])
    assert [item["doc_type"] for item in detail["documents"]] == ["bill", "statement"]


def test_public_person_detail_collects_documents_and_positions(monkeypatch):
    db_file = _workspace_db_path("public-person-detail")
    monkeypatch.setattr(database, "DB_PATH", db_file)
    database.init_db()

    position = upsert_policy_position(
        position_id=None,
        title="과학기술 혁신",
        category="science",
        summary="과학기술 정책",
        body="과학기술 본문",
        status="approved",
        owner_scope="party",
        effective_from=None,
        effective_to=None,
        version_label=None,
        actor_id=None,
    )
    bill = upsert_policy_document(
        document_id=None,
        title="과학기술 혁신 법안",
        doc_type="bill",
        summary="법안",
        body="법안 본문",
        speaker=None,
        speaker_name=None,
        owner_name="개혁신당",
        source_url="https://example.com/bill2",
        source_ref="test:bill:2",
        published_at="2026-03-15",
        status="active",
        metadata={},
        actor_id=None,
    )
    link_policy_document(position_id=position["id"], document_id=bill["id"], relation_type="implements", notes=None, actor_id=None)
    replace_policy_document_people(
        bill["id"],
        [
            {
                "person_name": "이준석",
                "person_role": "proposer",
                "party_affiliation": "개혁신당",
                "is_reform_party": True,
                "is_primary": True,
            }
        ],
    )

    detail = get_public_person_detail("이준석")
    assert detail["person_name"] == "이준석"
    assert detail["bill_count"] == 1
    assert detail["linked_positions"][0]["position_title"] == "과학기술 혁신"


def test_pdf_pledge_filename_parsing_and_people_detection():
    assert _normalize_title_from_filename("1. 이준석 공약_정부부처개편공약(설명자료).pdf") == "정부부처개편공약"
    people = _extract_people_from_filename("정책발표10(240204)_저가고속철 도입_이준석,양향자.txt")
    assert [item["person_name"] for item in people] == ["이준석"]


def test_sync_pdf_pledges_uses_pdf_only_for_approved_positions(monkeypatch, tmp_path):
    db_file = _workspace_db_path("pdf-pledge-sync")
    monkeypatch.setattr(database, "DB_PATH", db_file)
    database.init_db()

    pdf_root = tmp_path / "pdf"
    pledge_dir = pdf_root / "공약"
    pledge_dir.mkdir(parents=True)
    sample_txt = pledge_dir / "1. 이준석 공약_AI 산업 혁신.txt"
    sample_pdf = pledge_dir / "1. 이준석 공약_AI 산업 혁신.pdf"
    sample_txt.write_text("TXT 공약 본문", encoding="utf-8")
    sample_pdf.write_text("PDF 공약 본문", encoding="utf-8")

    monkeypatch.setattr(pdf_pledges_import, "PDF_DIR", pdf_root)
    monkeypatch.setattr(
        pdf_pledges_import,
        "extract_text_from_file",
        lambda path: f"{path.suffix} 형식 공약 본문",
    )

    result = sync_pdf_pledges(actor_id=None)
    assert result["imported_count"] == 2

    position = get_policy_position_by_slug("ai-산업-혁신")
    assert position["status"] == "approved"
    assert position["owner_scope"] == "campaign"
    assert position["version_label"] == "개혁신당 대선공약"

    links = list_policy_links(position_id=position["id"])
    assert len(links) == 1
    assert links[0]["document_type"] == "pledge"

    conn = database.get_connection()
    try:
        document_count = conn.execute("SELECT COUNT(*) AS n FROM policy_documents WHERE doc_type = 'pledge'").fetchone()["n"]
        position_count = conn.execute("SELECT COUNT(*) AS n FROM policy_positions WHERE owner_scope = 'campaign'").fetchone()["n"]
    finally:
        conn.close()
    assert int(document_count) == 2
    assert int(position_count) == 1


def test_sync_pdf_pledges_removes_legacy_txt_only_campaign_positions(monkeypatch, tmp_path):
    db_file = _workspace_db_path("pdf-pledge-cleanup")
    monkeypatch.setattr(database, "DB_PATH", db_file)
    database.init_db()

    legacy_position = upsert_policy_position(
        position_id=None,
        title="과학기술혁신",
        category="science",
        summary="레거시 TXT 정책",
        body="레거시 TXT 정책 본문",
        status="approved",
        owner_scope="campaign",
        effective_from=None,
        effective_to=None,
        version_label="개혁신당 대선공약",
        actor_id=None,
    )
    legacy_document = upsert_policy_document(
        document_id=None,
        title="과학기술혁신",
        doc_type="pledge",
        summary="TXT 공약",
        body="TXT 공약 본문",
        speaker=None,
        speaker_name=None,
        owner_name="개혁신당 대선공약",
        source_url="/data/pdf/공약/legacy.txt",
        source_ref="pdf_party_pledges:legacy.txt",
        published_at=None,
        status="active",
        metadata={"file_type": "txt", "source_key": "pdf_party_pledges"},
        actor_id=None,
    )
    link_policy_document(
        position_id=legacy_position["id"],
        document_id=legacy_document["id"],
        relation_type="references",
        notes="레거시 TXT 연결",
        actor_id=None,
    )

    pdf_root = tmp_path / "pdf"
    pledge_dir = pdf_root / "공약"
    pledge_dir.mkdir(parents=True)
    sample_pdf = pledge_dir / "1. 이준석 공약_과학기술혁신.pdf"
    sample_pdf.write_text("PDF 공약 본문", encoding="utf-8")

    monkeypatch.setattr(pdf_pledges_import, "PDF_DIR", pdf_root)
    monkeypatch.setattr(pdf_pledges_import, "extract_text_from_file", lambda path: "PDF 공약 본문")

    result = sync_pdf_pledges(actor_id=None)
    assert result["removed_txt_links"] >= 1
    assert result["removed_txt_only_positions"] >= 1

    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM policy_positions WHERE id = ?",
            (legacy_position["id"],),
        ).fetchone()
    finally:
        conn.close()
    assert int(row["n"]) == 0
