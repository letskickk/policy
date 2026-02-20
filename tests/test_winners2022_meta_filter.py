from backend.openai_vector_store import (
    _build_winners2022_queries_for_vector_simple as _build_winners2022_queries_for_vector,
    _choose_winners_items,
    _is_winners_meta_match,
    _keyword_boost_winner_items,
    _score_winner_relevance,
    _search_tokens,
)


def test_education_election_excludes_mayor_position_in_strict_mode():
    user_meta = {"election_type": "교육감", "region_province": "서울특별시", "region_city": "강남구"}
    mayor_hit = {"canonical_position": "강남구청장", "canonical_region": "서울특별시", "sggName": "강남구"}
    assert _is_winners_meta_match(mayor_hit, user_meta, mode="strict") is False


def test_education_election_accepts_education_position_in_strict_mode():
    user_meta = {"election_type": "교육감", "region_province": "서울특별시"}
    edu_hit = {"canonical_position": "교육감", "canonical_region": "서울특별시", "sggName": ""}
    assert _is_winners_meta_match(edu_hit, user_meta, mode="strict") is True


def test_region_only_does_not_over_filter_by_city_for_education():
    user_meta = {"election_type": "교육감", "region_province": "서울특별시", "region_city": "강남구"}
    education_hit = {"canonical_position": "교육감", "canonical_region": "서울특별시", "sggName": ""}
    assert _is_winners_meta_match(education_hit, user_meta, mode="region_only") is True


def test_no_filter_fallback_should_not_be_used_when_election_type_exists():
    user_meta = {"election_type": "교육감"}
    no_filter_items = [(1.0, "API", "text", {"canonical_position": "서울특별시장"})]
    chosen_items = _choose_winners_items([], [], no_filter_items, user_meta)
    assert chosen_items == []


def test_no_filter_fallback_allowed_without_election_type():
    chosen_items = _choose_winners_items([], [], [(0.9, "API", "text", {})], {})
    assert len(chosen_items) == 1


def test_local_mayor_city_constraint_remains_in_strict_mode():
    user_meta = {"election_type": "기초단체장", "region_province": "경상남도", "region_city": "남해군"}
    wrong_city_hit = {"canonical_position": "거제시장", "canonical_region": "경상남도", "sggName": "거제시"}
    assert _is_winners_meta_match(wrong_city_hit, user_meta, mode="strict") is False


def test_local_mayor_city_constraint_accepts_correct_city():
    user_meta = {"election_type": "기초단체장", "region_province": "경상남도", "region_city": "남해군"}
    ok_hit = {"canonical_position": "남해군수", "canonical_region": "경상남도", "sggName": "남해군"}
    assert _is_winners_meta_match(ok_hit, user_meta, mode="strict") is True


def test_query_builder_adds_role_region_hint_and_dedups():
    user_meta = {"election_type": "기초단체장", "region_province": "경상남도"}
    queries = _build_winners2022_queries_for_vector("망운산 산림휴양벨리 조성", user_meta)
    assert any("경상남도" in q and "기초단체장" in q for q in queries)
    assert len(queries) == len(set(queries))


def test_query_builder_contains_default_query_when_empty_pledge():
    queries = _build_winners2022_queries_for_vector("", {"election_type": "교육감"})
    assert any("제8회 전국동시지방선거 당선인 공약" in q for q in queries)


def test_choose_winners_items_prefers_strict_then_region_only():
    strict = [(0.9, "API", "strict", {"canonical_position": "남해군수"})]
    region_only = [(0.8, "API", "region", {"canonical_position": "남해군수"})]
    no_filter = [(0.7, "API", "nofilter", {"canonical_position": "서울특별시장"})]
    assert _choose_winners_items(strict, region_only, no_filter, {"election_type": "기초단체장"}) == strict
    assert _choose_winners_items([], region_only, no_filter, {"election_type": "기초단체장"}) == region_only


def test_relevance_score_prefers_matching_pledge_text():
    pledge = "망운산 산림휴양벨리 조성"
    strong = _score_winner_relevance(pledge, "남해 망운산 산림휴양벨리 조성을 추진합니다", "망운산 산림휴양벨리 조성")
    weak = _score_winner_relevance(pledge, "도시 교통체계 개선 및 주차장 확충", "교통 혼잡 완화")
    assert strong > weak


def test_search_tokens_extracts_korean_keywords():
    toks = _search_tokens("경상남도 남해군 망운산 산림휴양벨리 조성")
    assert "남해군" in toks
    assert "망운산" in toks


def test_keyword_boost_returns_matching_items_only():
    items = [
        (0.1, "API", "남해 망운산 산림휴양벨리 조성 추진", {"pledge_title": "망운산 산림휴양벨리 조성"}),
        (0.9, "API", "도시 교통체계 개선", {"pledge_title": "교통"}),
    ]
    boosted = _keyword_boost_winner_items("망운산 산림휴양벨리 조성", items, min_score=0.25, limit=2)
    assert boosted
    assert "망운산" in boosted[0][2] or "망운산" in boosted[0][3].get("pledge_title", "")


def test_keyword_boost_empty_when_no_match():
    items = [(0.5, "API", "교통 개선", {"pledge_title": "교통"})]
    boosted = _keyword_boost_winner_items("안심소득", items, min_score=0.5, limit=1)
    assert boosted == []


def test_meta_match_without_user_meta_is_true():
    hit = {"canonical_position": "남해군수", "canonical_region": "경상남도", "sggName": "남해군"}
    assert _is_winners_meta_match(hit, {}, mode="strict") is True
