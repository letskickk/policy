from backend.openai_vector_store import (
    WINNERS2022_MIN_QUERIES,
    _build_winners2022_queries_for_vector,
    _dedup_winners_vector_hits,
    choose_winners_items,
    is_meta_match_for_winners,
    reconstruct_winner_identity,
)


def _row(score: float, text: str, position: str = "", region: str = "경상남도", name: str = ""):
    return (
        score,
        "doc.txt",
        text,
        {
            "position": position,
            "region": region,
            "name": name,
            "canonical_position": position,
            "canonical_region": region,
            "canonical_name": name,
            "pledge_title": "",
        },
    )


def test_A_mangun_retrieval_queries_include_region_and_election_hint():
    pledge = "망운산 산림휴양벨리 조성"
    user_meta = {"region_province": "경상남도", "election_type": "기초단체장"}
    queries = _build_winners2022_queries_for_vector(pledge, user_meta, max_queries=8)
    joined = " | ".join(queries)
    assert len(queries) >= WINNERS2022_MIN_QUERIES
    assert "망운산" in joined
    assert "경상남도" in joined
    assert "기초단체장" in joined


def test_B_multiqueue_dedup_keeps_pledge_centered_candidate():
    hits = [
        (0.78, "a.txt", "망운산 산림휴양벨리 조성 및 관광 인프라 확충"),
        (0.81, "a_dup.txt", "망운산  산림휴양벨리 조성 및 관광 인프라 확충"),
        (0.42, "b.txt", "전혀 다른 공약"),
    ]
    dedup = _dedup_winners_vector_hits(hits)
    texts = [t for _, _, t in dedup]
    assert len(dedup) == 2
    assert any("망운산" in t for t in texts)


def test_C_education_user_meta_rejects_mayor_in_strict():
    user_meta = {"election_type": "education", "region_province": "서울특별시"}
    mayor_meta = {"position": "서울특별시장", "region": "서울특별시", "sggName": ""}
    assert is_meta_match_for_winners(mayor_meta, user_meta, mode="strict") is False


def test_D_choose_items_keeps_role_safe_similar_when_strict_region_empty():
    user_meta = {"election_type": "교육감", "region_province": "서울특별시"}
    strict = []
    region_only = []
    enhanced = [
        _row(0.65, "서울 교육감 공약 - 기초학력 책임 보장", position="교육감", region="서울특별시", name="홍길동"),
        _row(0.77, "서울특별시장 재개발 공약", position="서울특별시장", region="서울특별시", name="김시장"),
    ]
    chosen = choose_winners_items(strict, region_only, enhanced, user_meta)
    assert len(chosen) >= 1
    assert all("시장" not in ((r[3].get("position") or "") + (r[3].get("canonical_position") or "")) for r in chosen)


def test_E_no_none_when_similar_hit_exists_minimal_rule():
    user_meta = {"election_type": "기초단체장", "region_province": "경상남도"}
    strict = []
    region_only = []
    enhanced = [
        _row(0.72, "망운산 산림휴양벨리 조성", position="남해군수", region="경상남도 남해군", name="장충남"),
    ]
    chosen = choose_winners_items(strict, region_only, enhanced, user_meta)
    assert chosen, "유사 hit가 있으면 선택 결과가 비면 안 된다."


def test_F_identity_fallback_marks_unknown_when_missing():
    meta = {"position": "", "name": "", "canonical_position": "", "canonical_name": ""}
    pos, name = reconstruct_winner_identity(meta, "근거발췌: 공약 내용만 있고 인명 표기가 없음")
    assert pos == "확인불가"
    assert name == "확인불가"
