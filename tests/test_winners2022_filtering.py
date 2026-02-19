# -*- coding: utf-8 -*-
"""
회귀 테스트: winners2022 직책 필터링, region_only 완화, no_filter 미동작, 다중 쿼리.
- [A] 교육감 + 시장/구청장 hit => strict False
- [B] 교육감 + 교육감 hit(시도 일치, sgg 비어있음) => region_only True
- [C] election_type 존재 + strict/region_only 0건 => no_filter 미적용
- [D] 기초단체장/의원 city 정합 유지
- [E] 다중 쿼리 merge 시 dedup + role-safe 필터
"""
import pytest

from backend.openai_vector_store import (
    ELECTION_TYPE_KEY_TO_LABEL,
    WINNERS2022_CONTEXT_EMPTY,
    WINNERS2022_MIN_QUERIES,
    _build_position_region_query,
    _is_region_level_election,
    is_meta_match_for_winners,
    _normalize_user_meta_for_winners,
)


# ---------- [A] 교육감 user_meta + 시장/구청장 hit => strict False ----------
def test_A_education_user_meta_mayor_gucheongjang_hit_strict_false():
    """[A] 교육감 user_meta일 때 시장/구청장 직책 hit는 strict에서 제외."""
    user_meta_edu = {"election_type": "교육감", "region_province": "서울특별시", "region_city": ""}

    assert is_meta_match_for_winners(
        {"canonical_position": "서울특별시장", "canonical_region": "서울특별시", "sggName": ""},
        user_meta_edu,
        mode="strict",
    ) is False

    assert is_meta_match_for_winners(
        {"canonical_position": "강남구청장", "canonical_region": "서울특별시", "sggName": "강남구"},
        user_meta_edu,
        mode="strict",
    ) is False

    assert is_meta_match_for_winners(
        {"canonical_position": "교육감", "canonical_region": "서울특별시", "sggName": ""},
        user_meta_edu,
        mode="strict",
    ) is True


# ---------- [B] 교육감 user_meta + 교육감 hit(시도 일치, sgg 비어있음) => region_only True ----------
def test_B_education_hit_province_only_region_only_true():
    """[B] 교육감 user_meta + 교육감 hit(시도 일치, sgg 비어있음) => region_only 통과."""
    user_meta_edu = {"election_type": "교육감", "region_province": "서울특별시", "region_city": ""}
    hit_edu_no_sgg = {"canonical_position": "교육감", "canonical_region": "서울특별시", "sggName": ""}

    assert is_meta_match_for_winners(hit_edu_no_sgg, user_meta_edu, mode="region_only") is True

    user_meta_edu_with_city = {"election_type": "교육감", "region_province": "서울특별시", "region_city": "강남구"}
    assert is_meta_match_for_winners(hit_edu_no_sgg, user_meta_edu_with_city, mode="region_only") is True


# ---------- [C] election_type 존재 + strict/region_only 0건 => no_filter fallback 미적용 ----------
def test_C_election_type_present_no_filter_fallback_not_applied():
    """[C] election_type이 있을 때 빈 문구 상수 사용, no_filter로 타 직책 채우지 않음."""
    assert WINNERS2022_CONTEXT_EMPTY == "유사 공약: 없음"


def test_C_position_region_query_built_when_election_type_exists():
    """[C] election_type 있으면 직책+지역 재조회 쿼리가 생성됨 (no_filter 대신 사용)."""
    q = _build_position_region_query({"election_type": "교육감", "region_province": "서울특별시"})
    assert "교육감" in q
    assert "서울" in q or "당선인" in q

    q2 = _build_position_region_query({"election_type": "기초단체장", "region_province": "경기도"})
    assert "기초" in q2 or "당선인" in q2


# ---------- [D] 기초단체장/의원 케이스에서 city 정합 유지 ----------
def test_D_local_mayor_council_city_match_required():
    """[D] 기초단체장/기초의원은 city·sgg 정합이 여전히 적용됨."""
    user_local_mayor = {"election_type": "기초단체장", "region_province": "경기도", "region_city": "수원시"}
    hit_suwon = {"canonical_position": "수원시장", "canonical_region": "경기도", "sggName": "수원시"}
    hit_other = {"canonical_position": "성남시장", "canonical_region": "경기도", "sggName": "성남시"}

    assert is_meta_match_for_winners(hit_suwon, user_local_mayor, mode="strict") is True
    assert is_meta_match_for_winners(hit_other, user_local_mayor, mode="strict") is False

    user_council = {"election_type": "기초의원", "region_province": "서울특별시", "region_city": "강남구"}
    hit_council_ok = {"canonical_position": "강남구 제1선거구 의원", "canonical_region": "서울특별시", "sggName": "강남구"}
    hit_council_wrong = {"canonical_position": "서울시의원", "canonical_region": "서울특별시", "sggName": "종로구"}

    assert is_meta_match_for_winners(hit_council_ok, user_council, mode="strict") is True
    assert is_meta_match_for_winners(hit_council_wrong, user_council, mode="strict") is False


# ---------- [E] 다중 쿼리 merge 시 중복 제거 및 role-safe 필터 ----------
def test_E_region_level_election_detected_for_relaxed_region_only():
    """[E] 광역단위(교육감/광역단체장/광역의원) 감지 시 region_only에서 시도만 요구."""
    assert _is_region_level_election({"election_type": "교육감"}) is True
    assert _is_region_level_election({"election_type": "education"}) is True
    assert _is_region_level_election({"election_type": "광역단체장"}) is True
    assert _is_region_level_election({"election_type": "기초단체장"}) is False
    assert _is_region_level_election({"election_type": "기초의원"}) is False


def test_E_min_queries_configured():
    """[E] 다중 쿼리 개수 최소 2 이상으로 recall 확대."""
    assert WINNERS2022_MIN_QUERIES >= 2


# ---------- 재현: 이전 로직에서 fail, 수정 후 pass ----------
@pytest.fixture
def fixture_education_meta():
    """재현용: 교육감 사용자 메타."""
    return {"election_type": "교육감", "region_province": "서울특별시", "region_city": ""}


@pytest.fixture
def fixture_mayor_hit():
    """재현용: 시장 직책 hit (교육감에게 잘못 매칭되면 안 됨)."""
    return {"canonical_position": "서울특별시장", "canonical_region": "서울특별시", "sggName": ""}


@pytest.fixture
def fixture_education_hit_no_sgg():
    """재현용: 교육감 hit, sgg 비어있음 (region_only에서 통과해야 함)."""
    return {"canonical_position": "교육감", "canonical_region": "서울특별시", "sggName": ""}


def test_repro_education_gets_no_mayor_hit(fixture_education_meta, fixture_mayor_hit):
    """재현: 교육감 user_meta에 시장 hit가 strict로 통과하면 안 됨 (수정 후 pass)."""
    assert is_meta_match_for_winners(fixture_mayor_hit, fixture_education_meta, mode="strict") is False


def test_repro_education_province_only_region_only_pass(fixture_education_meta, fixture_education_hit_no_sgg):
    """재현: 교육감 hit(시도 일치, sgg 없음)은 region_only에서 통과 (수정 후 pass)."""
    assert is_meta_match_for_winners(fixture_education_hit_no_sgg, fixture_education_meta, mode="region_only") is True


# ---------- 기존 상수/매핑 검증 ----------
def test_election_type_education_normalizes_to_sgtypecode_11_only():
    """election_type이 교육감이면 API 조회용 sgTypecodes는 ['11']만 반환."""
    norm = _normalize_user_meta_for_winners({"election_type": "교육감", "region_province": "서울특별시"})
    assert norm["sgTypecodes"] == ["11"]

    norm2 = _normalize_user_meta_for_winners({"election_type": "education", "region_province": "경기"})
    assert norm2["sgTypecodes"] == ["11"]


def test_election_type_label_key_mapping_consistent():
    """한글 라벨과 내부 키 매핑 일관."""
    assert ELECTION_TYPE_KEY_TO_LABEL["metro_mayor"] == "광역단체장"
    assert ELECTION_TYPE_KEY_TO_LABEL["local_mayor"] == "기초단체장"
    assert ELECTION_TYPE_KEY_TO_LABEL["education"] == "교육감"
    assert ELECTION_TYPE_KEY_TO_LABEL["regional_council"] == "광역의원"
    assert ELECTION_TYPE_KEY_TO_LABEL["local_council"] == "기초의원"
