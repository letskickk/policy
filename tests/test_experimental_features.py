from backend.main import (
    CandidateDetailResponse,
    CandidatePledgeResponse,
    RapidIssueRequest,
    build_rapid_issue_response,
    get_candidate_one_page_briefing,
    get_candidate_regional_tailor,
)
import backend.main as main


def _sample_detail() -> CandidateDetailResponse:
    return CandidateDetailResponse(
        candidate_id=7,
        name="테스트후보",
        district_name="강남구",
        district_code="11:강남구",
        region_code="11",
        region_name="서울",
        election_type="local",
        election_level="regional",
        pledges=[
            CandidatePledgeResponse(title="교통 혼잡 완화", category="교통"),
            CandidatePledgeResponse(title="청년 주거 안정", category="청년"),
        ],
    )


def test_regional_tailor_returns_recommendations(monkeypatch):
    monkeypatch.setattr(main, "get_candidate_detail", lambda _cid: _sample_detail())
    res = get_candidate_regional_tailor(7)

    assert res.candidate_id == 7
    assert res.region_profile == "metro"
    assert len(res.recommendations) == 2
    assert "KPI" in res.recommendations[0].recommendation


def test_one_page_briefing_builds_talk_track(monkeypatch):
    monkeypatch.setattr(main, "get_candidate_detail", lambda _cid: _sample_detail())
    res = get_candidate_one_page_briefing(7)

    assert res.candidate_id == 7
    assert res.top_pledges[0] == "교통 혼잡 완화"
    assert len(res.talk_track) == 3
    assert "중앙당 승인 문서가 아닌" in res.disclaimer


def test_rapid_issue_response_contains_messages():
    req = RapidIssueRequest(
        issue_title="버스 노선 개편 논란",
        issue_summary="일부 지역에서 버스 노선 조정으로 통학 시간이 늘었다는 민원이 확산되고 있다.",
        requested_stance="생활권 이동권 보장 중심 대응",
    )
    res = build_rapid_issue_response(req)

    assert res.first_response_window.startswith("2시간")
    assert set(res.response_messages.keys()) == {"short", "medium", "long"}
    assert len(res.fact_check_items) >= 3
