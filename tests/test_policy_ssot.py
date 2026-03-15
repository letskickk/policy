from pathlib import Path
from uuid import uuid4

from backend import database
from backend.policy_ssot import (
    get_policy_document,
    get_policy_position,
    get_policy_ssot_summary,
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
