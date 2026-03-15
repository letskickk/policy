from pathlib import Path
from uuid import uuid4

from backend import database
from backend.policy_ssot import upsert_policy_position
from backend.policy_suggestions import list_link_suggestions
from backend.rallypoint_commentary import (
    _resolve_speaker_from_briefing_lookup,
    _extract_speaker_name_from_body,
    parse_commentary_api_list,
    parse_commentary_list,
    sync_commentary,
)


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


def test_parse_commentary_list_strips_mobile_metadata_and_bullet():
    html = '''
    <tr class="">
      <td class="admin-td">1317</td>
      <td class="title readable">[260311_개혁신당 수석대변인 논평]  ■ 이재명 대통령님, 공소 취소와 검찰 보완수사권 ‘바꿔 먹기’ 사실입니까?<div class="mob-view">2026.03.13 15:04 | 관리자 | 조회 6</div></td>
      <td class="tbl-date">관리자</td>
      <td class="tbl-date">2026.03.13 15:04</td>
    </tr>
    '''
    items = parse_commentary_list(html, limit=1)
    assert len(items) == 1
    assert items[0].title == "이재명 대통령님, 공소 취소와 검찰 보완수사권 ‘바꿔 먹기’ 사실입니까?"
    assert items[0].speaker == "수석대변인"
    assert items[0].published_at == "2026-03-13"


def test_parse_commentary_list_accepts_space_separator_in_prefix():
    html = """
    <tr class="">
      <td class="admin-td">999</td>
      <td class="title readable">[20251203 개혁신당 대변인 논평] ■ 빛 좋은 개살구</td>
      <td class="tbl-date">관리자</td>
      <td class="tbl-date">2025.12.08 09:00</td>
    </tr>
    """
    items = parse_commentary_list(html, limit=1)
    assert len(items) == 1
    assert items[0].title == "빛 좋은 개살구"
    assert items[0].speaker == "대변인"


def test_official_briefing_match_resolves_name():
    name, meta = _resolve_speaker_from_briefing_lookup(
        title="이재명 대통령님, 공소 취소와 검찰 보완수사권 ‘바꿔 먹기’ 사실입니까?",
        role="수석대변인",
        published_at="2026-03-13",
        briefing_lookup={
            "이재명대통령님공소취소와검찰보완수사권바꿔먹기사실입니까": {
                "speaker_name": "이동훈",
                "speaker_role": "수석대변인",
                "published_at": "2026-03-13",
                "briefing_url": "https://www.reformparty.kr/briefing/1420",
            }
        },
    )
    assert name == "이동훈"
    assert meta["briefing_url"].endswith("/1420")


def test_parse_commentary_api_list_uses_document_srl_and_role():
    payload = {
        "code": "RQSC",
        "data": (
            '{"docList":[{"document_srl":"227510","module_srl":"2428",'
            '"title":"[260313_개혁신당 부대변인 논평]  ■ 첫날부터 드러난 졸속 입법 부작용",'
            '"regdate":"20260313150536","comment_count":"0","readed_count":"24","list_order":"1320"}]}'
        ),
    }

    items = parse_commentary_api_list(payload, limit=1)

    assert len(items) == 1
    assert items[0].source_ref == "rallypoint_commentary:227510"
    assert items[0].source_url.endswith("/227510")
    assert items[0].speaker == "부대변인"
    assert items[0].published_at == "2026-03-13"


def test_extract_speaker_name_from_body_normalizes_spaced_name():
    body = "2026. 03. 13. 개혁신당 부대변인 신 정 욱"
    assert _extract_speaker_name_from_body(body, "부대변인") == "신정욱"


def test_extract_speaker_name_from_body_allows_intermediate_org_labels():
    body = "2025. 5. 30. 개혁신당 선대본 대변인 김 민 규"
    assert _extract_speaker_name_from_body(body, "대변인") == "김민규"


def test_sync_commentary_imports_updates_and_builds_suggestions(monkeypatch):
    db_file = _workspace_db_path("commentary-sync")
    monkeypatch.setattr(database, "DB_PATH", db_file)
    database.init_db()

    upsert_policy_position(
        position_id=None,
        title="사법개혁과 입법 견제",
        category="justice",
        summary="사법 개혁과 졸속 입법 견제를 다루는 정책",
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
        items[0].body = "첫날부터 드러난 졸속 입법 부작용을 지적합니다. 개혁신당 부대변인 신정욱"
        items[0].speaker_name = None
        if calls["n"] > 1:
            items[0].body = "updated body 개혁신당 부대변인 신정욱"
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
