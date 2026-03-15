from pathlib import Path
from uuid import uuid4

from backend import assembly_bills, database
from backend.policy_ssot import get_policy_document, list_policy_documents


def _workspace_db_path(name: str) -> Path:
    root = Path(".test_tmp")
    root.mkdir(exist_ok=True)
    return root / f"{name}-{uuid4().hex}.db"


def test_parse_bill_list_extracts_rows():
    sample_html = Path("data/assembly_bill_search_result_lee2.html").read_text(encoding="utf-8")

    items = assembly_bills.parse_bill_list(sample_html, representative_name="이준석")

    assert items
    assert items[0].bill_no == "2217044"
    assert items[0].bill_id == "PRC_Y2U5U0S7T1R6Q1Q0Y3Z4X0Y5W0X8V0"
    assert items[0].representative_name == "이준석"


def test_parse_bill_summary_popup_extracts_body():
    html = """
    <html><body>
      <h1>제안이유 및 주요내용</h1>
      <div>[2217044] 전자상거래 등에서의 소비자보호에 관한 법률 일부개정법률안</div>
      <div>이준석의원 등 10인</div>
      <div>제안이유 및 주요내용</div>
      <p>플랫폼 거래에서 후기 조작 피해가 발생하고 있음.</p>
      <p>이에 후기 정보 보존과 공정한 관리체계를 마련하려는 것임.</p>
      <button>의안 상세정보</button>
    </body></html>
    """

    parsed = assembly_bills.parse_bill_summary_popup(html)

    assert "후기 조작 피해" in parsed
    assert "공정한 관리체계" in parsed


def test_parse_bill_info_timeline_extracts_stage_nodes():
    html = """
    <div id="tab_billInfo_sect">
      <div class="bill_step_list">
        <div class="proc" data-gbn="RECEIVE">
          <div class="title">접수</div>
          <div class="stepdate">2026-02-25</div>
        </div>
        <div class="on" title="위원회 심사 (현재 진행중)" data-gbn="CMT">
          <div class="title">위원회 심사</div>
          <div class="stepdate"></div>
        </div>
        <div class="proc" data-gbn="MAIN">
          <div class="title">본회의 심의</div>
          <div class="stepdate"></div>
        </div>
      </div>
    </div>
    """

    timeline = assembly_bills.parse_bill_info_timeline(html)

    assert [entry["title"] for entry in timeline] == ["접수", "위원회 심사", "본회의 심의"]
    assert timeline[0]["at"] == "2026-02-25"
    assert timeline[1]["is_current"] is True
    assert timeline[2]["code"] == "MAIN"


def test_parse_legislation_notice_extracts_status_and_period():
    html = Path("data/assembly_legislation_notice_sample.html").read_text(encoding="utf-8")

    parsed = assembly_bills.parse_legislation_notice(
        html,
        source_url="https://pal.assembly.go.kr/napal/lgsltpa/lgsltpaOngoing/view.do?lgsltPaId=sample",
    )

    assert parsed["status"] == "입법예고중"
    assert parsed["start_at"] == "2026-03-12"
    assert parsed["end_at"] == "2026-03-21"


def test_sync_reform_party_bills_imports_documents(monkeypatch):
    db_file = _workspace_db_path("assembly-sync")
    monkeypatch.setattr(database, "DB_PATH", db_file)
    database.init_db()

    sample_list_html = Path("data/assembly_bill_search_result_lee2.html").read_text(encoding="utf-8")
    sample_detail_html = Path("data/assembly_bill_detail_sample.html").read_text(encoding="utf-8")
    sample_info_html = Path("data/assembly_bill_info_fragment_sample.html").read_text(encoding="utf-8")
    sample_notice_html = Path("data/assembly_legislation_notice_sample.html").read_text(encoding="utf-8")

    monkeypatch.setattr(assembly_bills, "resolve_member_id", lambda *args, **kwargs: "QWL7778X")
    monkeypatch.setattr(
        assembly_bills,
        "_post_text",
        lambda url, data, timeout=20: (
            sample_list_html
            if "findSchPaging" in url
            else sample_info_html
            if "bill/detail/billInfo.do" in url
            else '{"membId":"QWL7778X"}'
        ),
    )
    monkeypatch.setattr(
        assembly_bills,
        "_fetch_text",
        lambda url, params=None, timeout=20: sample_notice_html if "pal.assembly.go.kr" in url else sample_detail_html,
    )

    result = assembly_bills.sync_reform_party_bills(
        actor_id=None,
        members=[{"name": "이준석"}],
    )

    assert result["imported_count"] >= 1
    docs = list_policy_documents(doc_type="bill", status="active")
    assert docs
    assert docs[0]["speaker_name"] == "이준석"
    assert docs[0]["people"][0]["person_role"] == "proposer"

    fetched = get_policy_document(docs[0]["id"])
    assert fetched["metadata"]["bill_id"]
    assert fetched["metadata"]["committee"] == "정무위원회"
    assert fetched["timeline"]
    assert fetched["timeline"][0]["title"] == "접수"
    assert all(item["at"] for item in fetched["timeline"])
    assert any(item["title"] == "입법예고" for item in fetched["timeline"])
    assert fetched["metadata"]["legislation_notice"]["status"] == "입법예고중"
