from pathlib import Path
from uuid import uuid4

from backend import database
from backend.policy_ssot import upsert_policy_position
from backend.policy_suggestions import list_link_suggestions
from backend.rallypoint_commentary import parse_commentary_list, sync_commentary


def _workspace_db_path(name: str) -> Path:
    root = Path(".test_tmp")
    root.mkdir(exist_ok=True)
    return root / f"{name}-{uuid4().hex}.db"


def test_parse_commentary_list_extracts_rows():
    sample_html = Path("data/rallypoint_commentary_sample.html").read_text(encoding="utf-8")

    items = parse_commentary_list(sample_html, limit=3)

    assert len(items) == 3
    assert items[0].source_ref == "rallypoint_commentary:1320"
    assert items[0].speaker == "부대변인"
    assert items[0].title.startswith("첫날부터 드러난 졸속 입법")
    assert items[0].published_at == "2026-03-13"


def test_sync_commentary_imports_updates_and_builds_suggestions(monkeypatch):
    db_file = _workspace_db_path("commentary-sync")
    monkeypatch.setattr(database, "DB_PATH", db_file)
    database.init_db()

    upsert_policy_position(
        position_id=None,
        title="사법개혁과 입법 견제",
        category="justice",
        summary="사법 개혁과 입법 견제를 다루는 당 정책",
        body="사법개혁 입법과 졸속 입법 견제가 핵심이다.",
        status="approved",
        owner_scope="party",
        effective_from=None,
        effective_to=None,
        version_label=None,
        actor_id=None,
    )

    calls = {"n": 0}

    def fake_fetch(limit: int = 20, include_body: bool = True):
        calls["n"] += 1
        sample_html = Path("data/rallypoint_commentary_sample.html").read_text(encoding="utf-8")
        items = parse_commentary_list(sample_html, limit=2)
        items[0].body = "첫날부터 드러난 졸속 입법 부작용을 지적합니다. 개혁신당 부대변인 김개혁"
        items[0].speaker_name = None
        if calls["n"] > 1:
            items[0].body = "updated body 개혁신당 부대변인 김개혁"
        return items

    monkeypatch.setattr("backend.rallypoint_commentary.fetch_commentary_items", fake_fetch)

    first = sync_commentary(actor_id=None, limit=2, include_body=True)
    assert first["imported_count"] == 2
    assert first["updated_count"] == 0

    suggestions = list_link_suggestions(status="pending", limit=20)
    assert suggestions
    assert suggestions[0]["position_title"] == "사법개혁과 입법 견제"

    second = sync_commentary(actor_id=None, limit=2, include_body=True)
    assert second["imported_count"] == 0
    assert second["updated_count"] == 1
    assert second["skipped_count"] == 1
