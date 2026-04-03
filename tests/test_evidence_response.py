"""
_enrich_verify_result()가 summary.scores / label /
improvements 정규화를 올바르게 수행하는지 검증.
"""
import copy
from backend.analysis_service import _enrich_verify_result, _quick_verify_result

SAMPLE_RESULT = {
    "summary": {
        "fit_score": 72.5,
        "fit_verdict": "부합",
        "confidence": 0.85,
    },
    "platform": [
        {"item": "정강정책 1항", "score_0_5": 4, "note": "높은 부합"},
        {"item": "정강정책 2항", "score_0_5": 3, "note": "보통"},
    ],
    "pledges": [
        {"item": "공약 A", "score_0_5": 2, "note": "부분 일치"},
    ],
    "conflicts": [
        {"item": "충돌 항목", "score_0_5": 1, "note": "경미한 충돌"},
    ],
    "improvements": [
        {"title": "구체성 보완", "detail": "수치/이행계획 추가 필요", "evidence": []},
        "문자열 형태 개선안",
    ],
}


def test_summary_scores_exist():
    data = _enrich_verify_result(copy.deepcopy(SAMPLE_RESULT))
    s = data["summary"]
    assert "scores" in s
    sc = s["scores"]
    assert "alignment" in sc
    assert "conflict_risk" in sc
    assert "differentiation" in sc
    assert all(isinstance(sc[k], (int, float)) for k in sc)


def test_summary_label():
    data = _enrich_verify_result(copy.deepcopy(SAMPLE_RESULT))
    assert data["summary"]["label"] == "부합"


def test_improvements_normalized():
    data = _enrich_verify_result(copy.deepcopy(SAMPLE_RESULT))
    imps = data["improvements"]
    assert isinstance(imps, list)
    for imp in imps:
        assert isinstance(imp, dict)
        assert "title" in imp


def test_backward_compat_fields():
    data = _enrich_verify_result(copy.deepcopy(SAMPLE_RESULT))
    assert "total_score" in data
    assert "signal_light" in data
    assert "pdf_eligible" in data
    assert data["summary"]["fit_score"] == data["total_score"]


def test_empty_result_safe():
    data = _enrich_verify_result({})
    assert data["total_score"] == 0.0
    assert isinstance(data["summary"]["scores"], dict)


def test_quick_verify_concrete_numeric_pledge_is_not_red():
    pledge = (
        "강남구 어르신 돌봄 사각지대 해소를 위해 동별 1개소 이상 주간보호센터를 설치하고, "
        "독거노인 2000명을 대상으로 안부 확인 시스템을 도입하겠습니다."
    )
    result = _enrich_verify_result(_quick_verify_result(pledge, {}))
    assert 35.0 <= result["total_score"] <= 85.0
    assert result["signal_light"] in {"yellow", "green"}
    assert result["pdf_eligible"] is False


def test_quick_verify_authority_mismatch_stays_low():
    pledge = "FTA 재협상과 교육부 권한 조정은 구청 공약 범위를 넘습니다."
    result = _enrich_verify_result(_quick_verify_result(pledge, {}))
    assert result["total_score"] <= 40.0
    assert result["signal_light"] == "red"
