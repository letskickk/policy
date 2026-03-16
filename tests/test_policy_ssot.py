from pathlib import Path
from uuid import uuid4

from backend import database
from backend.policy_ssot import (
    _policy_execution_stage,
    get_policy_operations_overview,
    get_public_messages_overview,
    get_public_overview,
    get_public_person_detail,
    list_public_people,
    list_public_meetings,
    list_public_messages,
    list_public_rules,
    get_policy_document,
    get_policy_position,
    get_policy_position_timeline,
    get_policy_ssot_summary,
    list_policy_position_versions,
    link_policy_document,
    list_policy_document_people,
    list_policy_documents,
    list_policy_links,
    list_policy_positions,
    replace_policy_document_people,
    upsert_policy_document,
    upsert_policy_position,
)


def _workspace_db_path(name: str) -> Path:
    root = Path(".test_tmp")
    root.mkdir(exist_ok=True)
    return root / f"{name}-{uuid4().hex}.db"


def test_policy_ssot_crud_and_linking(monkeypatch):
    db_file = _workspace_db_path("policy-ssot")
    monkeypatch.setattr(database, "DB_PATH", db_file)
    database.init_db()

    position = upsert_policy_position(
        position_id=None,
        title="Youth housing expansion",
        category="housing",
        summary="Transit-oriented housing supply",
        body="Expand youth public housing supply.",
        status="approved",
        owner_scope="party",
        effective_from="2026-03-15",
        effective_to=None,
        version_label="v1",
        actor_id=None,
    )

    document = upsert_policy_document(
        document_id=None,
        title="Housing bill 001",
        doc_type="bill",
        summary="Legislation for housing support",
        body="Expand supply and financing support.",
        speaker="의원",
        speaker_name="이준석",
        owner_name="개혁신당",
        source_url="https://example.com/bill",
        source_ref="bill-001",
        published_at="2026-03-14",
        status="active",
        metadata={"bill_no": "220001"},
        actor_id=None,
    )

    people = replace_policy_document_people(
        document["id"],
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
    assert len(people) == 1

    link = link_policy_document(
        position_id=position["id"],
        document_id=document["id"],
        relation_type="implements",
        notes="Legislative implementation",
        actor_id=None,
    )
    assert link["relation_type"] == "implements"

    fetched_position = get_policy_position(position["id"])
    assert len(fetched_position["links"]) == 1

    fetched_document = get_policy_document(document["id"])
    assert fetched_document["people"][0]["person_name"] == "이준석"
    assert len(fetched_document["linked_positions"]) == 1

    summary = get_policy_ssot_summary()
    assert summary["positions"] == 1
    assert summary["documents"] == 1
    assert summary["links"] == 1


def test_policy_ssot_listing_filters(monkeypatch):
    db_file = _workspace_db_path("policy-ssot-filters")
    monkeypatch.setattr(database, "DB_PATH", db_file)
    database.init_db()

    upsert_policy_position(
        position_id=None,
        title="Pension reform",
        category="welfare",
        summary=None,
        body=None,
        status="draft",
        owner_scope="party",
        effective_from=None,
        effective_to=None,
        version_label=None,
        actor_id=None,
    )
    upsert_policy_position(
        position_id=None,
        title="Science investment expansion",
        category="science",
        summary=None,
        body=None,
        status="approved",
        owner_scope="party",
        effective_from=None,
        effective_to=None,
        version_label=None,
        actor_id=None,
    )
    doc = upsert_policy_document(
        document_id=None,
        title="Statement on science investment",
        doc_type="statement",
        summary=None,
        body=None,
        speaker="대변인",
        speaker_name="홍길동",
        owner_name="개혁신당",
        source_url=None,
        source_ref=None,
        published_at="2026-03-15",
        status="active",
        metadata={},
        actor_id=None,
    )
    replace_policy_document_people(
        doc["id"],
        [
            {
                "person_name": "홍길동",
                "person_role": "spokesperson",
                "party_affiliation": "개혁신당",
                "is_reform_party": True,
                "is_primary": True,
            }
        ],
    )

    approved_positions = list_policy_positions(status="approved")
    assert len(approved_positions) == 1
    assert approved_positions[0]["category"] == "science"

    statement_docs = list_policy_documents(doc_type="statement", status="active")
    assert len(statement_docs) == 1
    assert statement_docs[0]["people"][0]["person_name"] == "홍길동"

    listed_people = list_policy_document_people(document_id=doc["id"])
    assert listed_people[0]["person_role"] == "spokesperson"
    assert list_policy_links() == []


def test_policy_execution_stage_uses_bill_status(monkeypatch):
    db_file = _workspace_db_path("policy-stage")
    monkeypatch.setattr(database, "DB_PATH", db_file)
    database.init_db()

    disposed_bill = upsert_policy_document(
        document_id=None,
        title="Disposed bill",
        doc_type="bill",
        summary="Disposed",
        body=None,
        speaker=None,
        speaker_name=None,
        owner_name="Reform Party",
        source_url=None,
        source_ref="disposed-bill",
        published_at="2026-03-15",
        status="active",
        metadata={"bill_stage": "임기만료폐기"},
        actor_id=None,
    )
    active_bill = upsert_policy_document(
        document_id=None,
        title="Active bill",
        doc_type="bill",
        summary="Active",
        body=None,
        speaker=None,
        speaker_name=None,
        owner_name="Reform Party",
        source_url=None,
        source_ref="active-bill",
        published_at="2026-03-15",
        status="active",
        metadata={"bill_stage": "소관위 심사중"},
        actor_id=None,
    )

    disposed_stage = _policy_execution_stage([get_policy_document(disposed_bill["id"])])
    active_stage = _policy_execution_stage([get_policy_document(active_bill["id"])])

    assert disposed_stage["code"] == "legislation_history"
    assert active_stage["code"] == "legislation"


def test_bill_document_exposes_progress(monkeypatch):
    db_file = _workspace_db_path("bill-progress")
    monkeypatch.setattr(database, "DB_PATH", db_file)
    database.init_db()

    document = upsert_policy_document(
        document_id=None,
        title="Disposed bill",
        doc_type="bill",
        summary="Disposed",
        body=None,
        speaker=None,
        speaker_name=None,
        owner_name="Reform Party",
        source_url=None,
        source_ref="bill-progress",
        published_at="2026-03-15",
        status="active",
        metadata={"bill_stage": "임기만료폐기", "decision_result": "폐기"},
        actor_id=None,
    )

    detail = get_policy_document(document["id"])
    assert detail["bill_progress"]["code"] == "disposed"
    assert detail["bill_progress"]["label"] == "입법 종료"
    assert any(item["title"] == "법안 접수" for item in detail["timeline"])
    assert any(item["title"] == "의결 결과" for item in detail["timeline"])
    assert "임기만료폐기" in detail["derived_key_points"]
    assert "실제 입법" in detail["derived_relevance_note"] or "입법" in detail["derived_relevance_note"]
    assert not all(item["at"] == detail["timeline"][0]["at"] for item in detail["timeline"] if item["title"] != "법안 접수")


def test_public_overview_includes_bills_statements_pledges_and_people(monkeypatch):
    db_file = _workspace_db_path("policy-public-overview")
    monkeypatch.setattr(database, "DB_PATH", db_file)
    database.init_db()

    position = upsert_policy_position(
        position_id=None,
        title="AI regulation",
        category="science",
        summary="AI governance baseline",
        body="Official AI policy.",
        status="approved",
        owner_scope="party",
        effective_from=None,
        effective_to=None,
        version_label=None,
        actor_id=None,
    )
    bill = upsert_policy_document(
        document_id=None,
        title="AI bill",
        doc_type="bill",
        summary="Bill summary",
        body="Bill body",
        speaker=None,
        speaker_name=None,
        owner_name="Reform Party",
        source_url="https://example.com/bill",
        source_ref="overview:bill",
        published_at="2026-03-15",
        status="active",
        metadata={},
        actor_id=None,
    )
    statement = upsert_policy_document(
        document_id=None,
        title="AI statement",
        doc_type="statement",
        summary="Statement summary",
        body="Statement body",
        speaker="spokesperson",
        speaker_name="Kim Example",
        owner_name="Reform Party",
        source_url="https://example.com/statement",
        source_ref="overview:statement",
        published_at="2026-03-14",
        status="active",
        metadata={},
        actor_id=None,
    )
    pledge = upsert_policy_document(
        document_id=None,
        title="AI pledge",
        doc_type="pledge",
        summary="Pledge summary",
        body="Pledge body",
        speaker=None,
        speaker_name=None,
        owner_name="Reform Party campaign",
        source_url="https://example.com/pledge",
        source_ref="overview:pledge",
        published_at="2026-03-13",
        status="active",
        metadata={"verified_public_source": True},
        actor_id=None,
    )
    link_policy_document(position_id=position["id"], document_id=bill["id"], relation_type="implements", notes=None, actor_id=None)
    replace_policy_document_people(
        bill["id"],
        [
            {
                "person_name": "Lee Example",
                "person_role": "proposer",
                "party_affiliation": "Reform Party",
                "is_reform_party": True,
                "is_primary": True,
            }
        ],
    )

    overview = get_public_overview()
    assert overview["counts"]["positions"] == 1
    assert overview["counts"]["bills"] == 1
    assert overview["counts"]["statements"] == 1
    assert overview["counts"]["messages"] == 1
    assert overview["counts"]["pledges"] == 1
    assert overview["counts"]["people"] == 1


def test_public_messages_meetings_and_rules_are_listed(monkeypatch):
    db_file = _workspace_db_path("policy-hub-extended")
    monkeypatch.setattr(database, "DB_PATH", db_file)
    database.init_db()

    position = upsert_policy_position(
        position_id=None,
        title="Party governance reform",
        category="politics",
        summary="Governance summary",
        body="Governance body",
        status="approved",
        owner_scope="party",
        effective_from=None,
        effective_to=None,
        version_label=None,
        actor_id=None,
    )
    message = upsert_policy_document(
        document_id=None,
        title="Governance briefing",
        doc_type="briefing",
        summary="Briefing summary",
        body="Briefing body",
        speaker="briefing",
        speaker_name="Kim Example",
        owner_name="Reform Party",
        source_url=None,
        source_ref="hub:briefing",
        published_at="2026-03-15",
        status="active",
        metadata={},
        actor_id=None,
    )
    meeting = upsert_policy_document(
        document_id=None,
        title="Supreme Council Meeting 12",
        doc_type="meeting_note",
        summary="Discussed governance roadmap",
        body="Agenda and decisions",
        speaker=None,
        speaker_name=None,
        owner_name="Reform Party",
        source_url=None,
        source_ref="hub:meeting",
        published_at="2026-03-16",
        status="active",
        metadata={"meeting_type": "Supreme Council", "held_at": "2026-03-16", "agenda_items": ["Organization reform"], "decisions": ["Proceed with review"]},
        actor_id=None,
    )
    rule = upsert_policy_document(
        document_id=None,
        title="Bylaw No. 1 Organization Rules",
        doc_type="party_rule",
        summary="Organization operating standard",
        body="Rule body",
        speaker=None,
        speaker_name=None,
        owner_name="Reform Party",
        source_url=None,
        source_ref="hub:rule",
        published_at="2026-03-14",
        status="active",
        metadata={"rule_kind": "bylaw", "rule_kind_label": "Bylaw", "version_label": "2026-03", "effective_from": "2026-03-14", "key_articles": ["Article 1", "Article 2"]},
        actor_id=None,
    )
    link_policy_document(position_id=position["id"], document_id=message["id"], relation_type="explains", notes=None, actor_id=None)
    link_policy_document(position_id=position["id"], document_id=meeting["id"], relation_type="references", notes=None, actor_id=None)
    link_policy_document(position_id=position["id"], document_id=rule["id"], relation_type="supports", notes=None, actor_id=None)

    messages = list_public_messages()
    meetings = list_public_meetings()
    rules = list_public_rules()
    messages_overview = get_public_messages_overview()
    overview = get_public_overview()

    assert any(item["id"] == message["id"] for item in messages)
    assert any(item["id"] == meeting["id"] for item in meetings)
    assert any(item["id"] == rule["id"] for item in rules)
    assert messages_overview["counts"]["messages"] >= 1
    assert overview["counts"]["meetings"] >= 1
    assert overview["counts"]["rules"] >= 1


def test_public_person_detail_infers_focus_from_documents(monkeypatch):
    db_file = _workspace_db_path("person-detail")
    monkeypatch.setattr(database, "DB_PATH", db_file)
    database.init_db()

    position = upsert_policy_position(
        position_id=None,
        title="데이터 특구 제도 도입",
        category="science",
        summary="데이터 특구 확대",
        body="공식 정책",
        status="approved",
        owner_scope="party",
        effective_from=None,
        effective_to=None,
        version_label=None,
        actor_id=None,
    )
    bill = upsert_policy_document(
        document_id=None,
        title="데이터 특구 조성에 관한 법률안",
        doc_type="bill",
        summary="과학기술정보방송통신위원회 / 소관위심사",
        body="데이터 특구를 도입하려는 법안.",
        speaker="의원",
        speaker_name="이준석",
        owner_name="개혁신당",
        source_url=None,
        source_ref="person-detail:bill",
        published_at="2026-03-15",
        status="active",
        metadata={"committee": "과학기술정보방송통신위원회", "bill_stage": "소관위심사"},
        actor_id=None,
    )
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
    link_policy_document(position_id=position["id"], document_id=bill["id"], relation_type="implements", notes=None, actor_id=None)

    detail = get_public_person_detail("이준석")
    assert "대표발의 법안" in detail["brief"]
    assert "주요 의제" in detail["derived_key_points"]
    assert len(detail["timeline"]) >= 1
    assert detail["role_labels"]
    assert detail["message_count"] >= detail["statement_count"]
    assert detail["focus_positions"]
    assert detail["featured_bills"]
    assert isinstance(detail["featured_messages"], list)


def test_policy_versions_timeline_and_operations(monkeypatch):
    db_file = _workspace_db_path("policy-versions")
    monkeypatch.setattr(database, "DB_PATH", db_file)
    database.init_db()

    position = upsert_policy_position(
        position_id=None,
        title="Judicial reform",
        category="politics",
        summary="First version",
        body="Version one body",
        status="approved",
        owner_scope="party",
        effective_from=None,
        effective_to=None,
        version_label="v1",
        actor_id=None,
    )
    position = upsert_policy_position(
        position_id=position["id"],
        title="Judicial reform",
        category="politics",
        summary="Second version",
        body="Version two body",
        status="approved",
        owner_scope="party",
        effective_from=None,
        effective_to=None,
        version_label="v2",
        actor_id=None,
    )
    document = upsert_policy_document(
        document_id=None,
        title="Judicial statement",
        doc_type="statement",
        summary="Statement summary",
        body="Statement body",
        speaker="spokesperson",
        speaker_name="Kim Example",
        owner_name="Reform Party",
        source_url="https://example.com/statement",
        source_ref="timeline:statement",
        published_at="2026-03-15",
        status="active",
        metadata={},
        actor_id=None,
    )
    link_policy_document(position_id=position["id"], document_id=document["id"], relation_type="explains", notes=None, actor_id=None)

    versions = list_policy_position_versions(position["id"])
    assert len(versions) == 2
    assert versions[0]["version_label"] == "v2"

    timeline = get_policy_position_timeline(position["id"])
    assert any(item["kind"] == "version" for item in timeline)
    assert any(item["kind"] == "document" for item in timeline)

    operations = get_policy_operations_overview()
    assert "suggestions" in operations
    assert "ingest_sources" in operations


def test_public_people_excludes_former_members(monkeypatch):
    db_file = _workspace_db_path("policy-public-people-filter")
    monkeypatch.setattr(database, "DB_PATH", db_file)
    database.init_db()

    doc = upsert_policy_document(
        document_id=None,
        title="Legacy pledge owner",
        doc_type="pledge",
        summary="Legacy data",
        body="Legacy body",
        speaker=None,
        speaker_name=None,
        owner_name="Reform Party campaign",
        source_url=None,
        source_ref="overview:legacy",
        published_at="2026-03-01",
        status="active",
        metadata={},
        actor_id=None,
    )
    replace_policy_document_people(
        doc["id"],
        [
            {
                "person_name": "양향자",
                "person_role": "policy_owner",
                "party_affiliation": "Reform Party",
                "is_reform_party": True,
                "is_primary": True,
            }
        ],
    )

    assert list_public_people() == []
