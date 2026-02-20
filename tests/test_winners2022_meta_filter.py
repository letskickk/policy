"""winners2022 검색·필터·선택 로직 유닛 테스트."""
from backend.openai_vector_store import (
    WINNERS2022_MIN_QUERIES,
    WINNERS2022_MIN_SIMILARITY_SCORE,
    _build_winners2022_queries_for_vector,
    _dedup_winners_vector_hits,
    _rank_api_items_by_pledge_keywords,
    choose_winners_items,
    is_meta_match_for_winners,
    reconstruct_winner_identity,
    rerank_winners_hits_by_similarity,
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


# ── A: 쿼리 빌더 ──

def test_A1_queries_include_region_and_election_hint():
    pledge = "망운산 산림휴양벨리 조성"
    user_meta = {"region_province": "경상남도", "election_type": "기초단체장"}
    queries = _build_winners2022_queries_for_vector(pledge, user_meta, max_queries=8)
    joined = " | ".join(queries)
    assert len(queries) >= WINNERS2022_MIN_QUERIES
    assert "망운산" in joined
    assert "경상남도" in joined
    assert "기초단체장" in joined


def test_A2_spelling_variant_belly():
    """'밸리' 입력 → 쿼리에 '벨리' 변형 포함."""
    queries = _build_winners2022_queries_for_vector("망운산 산림휴양밸리 조성", None, max_queries=10)
    joined = " ".join(queries)
    assert "밸리" in joined
    assert "벨리" in joined


def test_A3_query_dedup_no_duplicates():
    queries = _build_winners2022_queries_for_vector("서울 안심소득", None, max_queries=10)
    assert len(queries) == len(set(queries)), "쿼리에 중복이 있다"


# ── B: dedup / rerank ──

def test_B1_dedup_keeps_highest_score():
    hits = [
        (0.78, "a.txt", "망운산 산림휴양벨리 조성 및 관광 인프라 확충"),
        (0.81, "a_dup.txt", "망운산  산림휴양벨리 조성 및 관광 인프라 확충"),
        (0.42, "b.txt", "전혀 다른 공약"),
    ]
    dedup = _dedup_winners_vector_hits(hits)
    texts = [t for _, _, t in dedup]
    assert len(dedup) == 2
    assert any("망운산" in t for t in texts)


def test_B2_rerank_relevant_text_scores_higher_than_irrelevant():
    """공약과 관련 있는 텍스트가 무관 텍스트보다 높은 점수를 받아야 함."""
    hits = [
        (0.55, "irr.txt", "서울-연천간 고속도로 조기착공"),
        (0.55, "rel.txt", "안심소득으로 복지 사각지대 없는 서울"),
    ]
    ranked = rerank_winners_hits_by_similarity(hits, "서울 안심소득")
    assert "안심소득" in ranked[0][2], "안심소득 관련 텍스트가 1위여야 한다"


# ── C: 메타 매칭 ──

def test_C1_education_strict_rejects_mayor():
    user_meta = {"election_type": "education", "region_province": "서울특별시"}
    mayor_meta = {"position": "서울특별시장", "region": "서울특별시", "sggName": ""}
    assert is_meta_match_for_winners(mayor_meta, user_meta, mode="strict") is False


def test_C2_region_only_relaxation():
    """region_only 모드에서는 광역 단위 일치만으로 통과."""
    user_meta = {"election_type": "metro_mayor", "region_province": "서울특별시"}
    meta = {"position": "서울특별시장", "region": "서울특별시", "sggName": ""}
    assert is_meta_match_for_winners(meta, user_meta, mode="region_only") is True


# ── D: choose_winners_items ──

def test_D1_role_safe_keeps_education_drops_mayor():
    user_meta = {"election_type": "교육감", "region_province": "서울특별시"}
    enhanced = [
        _row(0.65, "교육감 기초학력 보장", position="교육감", region="서울특별시", name="홍길동"),
        _row(0.77, "서울특별시장 재개발", position="서울특별시장", region="서울특별시", name="김시장"),
    ]
    chosen = choose_winners_items([], [], enhanced, user_meta)
    assert len(chosen) >= 1
    assert all("시장" not in (r[3].get("position", "") + r[3].get("canonical_position", "")) for r in chosen)


def test_D2_similar_hit_never_returns_empty():
    """유사 hit가 있으면 choose_winners_items 결과가 비면 안 된다."""
    user_meta = {"election_type": "기초단체장", "region_province": "경상남도"}
    enhanced = [
        _row(0.72, "망운산 산림휴양벨리 조성", position="남해군수", region="경상남도 남해군", name="장충남"),
    ]
    chosen = choose_winners_items([], [], enhanced, user_meta)
    assert chosen, "유사 hit가 있으면 비면 안 된다"


def test_D3_below_threshold_still_returns_one():
    """유사도 임계 미달이어도 비충돌 후보가 있으면 최소 1건."""
    user_meta = {"election_type": "기초단체장", "region_province": "경상남도"}
    enhanced = [
        _row(0.05, "망운산 산림휴양벨리 조성", position="남해군수", region="경상남도 남해군", name="장충남"),
    ]
    chosen = choose_winners_items([], [], enhanced, user_meta)
    assert len(chosen) >= 1
    assert "망운산" in chosen[0][2]


# ── E: API 키워드 정렬 ──

def test_E1_rank_api_items_relevant_first():
    api_items = [
        (1.0, "API", "다른 지역 공원 조성", {"pledge_title": "공원", "position": "A", "region": "B"}),
        (1.0, "API", "망운산 산림휴양벨리 조성 사업", {"pledge_title": "망운산", "position": "남해군수", "region": "경남"}),
        (1.0, "API", "일반 복지 확대", {"pledge_title": "복지", "position": "C", "region": "D"}),
    ]
    ranked = _rank_api_items_by_pledge_keywords(api_items, "망운산 산림휴양밸리 조성")
    assert ranked[0][2] == "망운산 산림휴양벨리 조성 사업"


def test_E2_rank_api_ansim_income():
    api_items = [
        (1.0, "API", "교통 정책", {"pledge_title": "교통", "position": "X", "region": "Y"}),
        (1.0, "API", "서울 안심소득으로 복지 사각지대 해소", {"pledge_title": "안심소득", "position": "시장", "region": "서울"}),
    ]
    ranked = _rank_api_items_by_pledge_keywords(api_items, "서울 안심소득")
    assert "안심소득" in ranked[0][2]


# ── F: 메타 fallback ──

def test_F1_identity_fallback_unknown():
    meta = {"position": "", "name": "", "canonical_position": "", "canonical_name": ""}
    pos, name = reconstruct_winner_identity(meta, "공약 내용만 있고 인명 표기가 없음")
    assert pos == "확인불가"
    assert name == "확인불가"


# ── G: 안정성 테스트 ──

def test_G1_main_py_importable():
    """backend/main.py가 문법 오류 없이 컴파일 가능."""
    import py_compile
    py_compile.compile("backend/main.py", doraise=True)


def test_G2_test_nec_api_importable():
    """scripts/test_nec_api.py가 import 시 sys.exit() 하지 않음."""
    import importlib
    mod = importlib.import_module("scripts.test_nec_api")
    assert hasattr(mod, "call"), "call 함수가 존재해야 한다"
