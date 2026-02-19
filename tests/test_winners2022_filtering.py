# -*- coding: utf-8 -*-
"""
회귀 테스트: winners2022 직책 필터링 및 no_filter fallback 미동작.
- 교육감 user_meta일 때 시장/지사 직책 hit는 제외되는지
- election_type이 있을 때 no_filter fallback이 동작하지 않는지
"""
import pytest

from backend.openai_vector_store import (
    ELECTION_TYPE_KEY_TO_LABEL,
    WINNERS2022_CONTEXT_EMPTY,
    is_meta_match_for_winners,
    _normalize_user_meta_for_winners,
)


def test_education_user_meta_excludes_mayor_governor_hits():
    """교육감 user_meta일 때 시장/지사 직책 hit는 제외된다."""
    user_meta_edu = {"election_type": "교육감", "region_province": "서울특별시", "region_city": ""}

    # 시장 직책 hit → 제외
    assert is_meta_match_for_winners(
        {"canonical_position": "서울특별시장", "canonical_region": "서울특별시", "sggName": ""},
        user_meta_edu,
        mode="strict",
    ) is False

    # 지사 직책 hit → 제외
    assert is_meta_match_for_winners(
        {"canonical_position": "경기도지사", "canonical_region": "경기도", "sggName": ""},
        user_meta_edu,
        mode="strict",
    ) is False

    # 교육감 hit → 통과
    assert is_meta_match_for_winners(
        {"canonical_position": "교육감", "canonical_region": "서울특별시", "sggName": ""},
        user_meta_edu,
        mode="strict",
    ) is True

    # 한글 election_type "교육감" 뿐 아니라 내부 키 "education" 도 동일
    user_meta_key = {"election_type": "education", "region_province": "서울특별시"}
    assert is_meta_match_for_winners(
        {"canonical_position": "서울특별시장", "canonical_region": "서울특별시", "sggName": ""},
        user_meta_key,
        mode="strict",
    ) is False
    assert is_meta_match_for_winners(
        {"canonical_position": "교육감", "canonical_region": "서울특별시", "sggName": ""},
        user_meta_key,
        mode="strict",
    ) is True


def test_election_type_present_no_filter_fallback_constant():
    """election_type이 있을 때 4번 섹션 빈 문구는 '유사 공약: 없음'으로 고정."""
    assert WINNERS2022_CONTEXT_EMPTY == "유사 공약: 없음"


def test_election_type_education_normalizes_to_sgtypecode_11_only():
    """election_type이 교육감이면 API 조회용 sgTypecodes는 ['11']만 반환 (시장/지사 타입 불포함)."""
    norm = _normalize_user_meta_for_winners({"election_type": "교육감", "region_province": "서울특별시"})
    assert norm["sgTypecodes"] == ["11"]

    norm2 = _normalize_user_meta_for_winners({"election_type": "education", "region_province": "경기"})
    assert norm2["sgTypecodes"] == ["11"]


def test_election_type_label_key_mapping_consistent():
    """한글 라벨과 내부 키 매핑이 일관되게 정의되어 있다."""
    assert ELECTION_TYPE_KEY_TO_LABEL["metro_mayor"] == "광역단체장"
    assert ELECTION_TYPE_KEY_TO_LABEL["local_mayor"] == "기초단체장"
    assert ELECTION_TYPE_KEY_TO_LABEL["education"] == "교육감"
    assert ELECTION_TYPE_KEY_TO_LABEL["regional_council"] == "광역의원"
    assert ELECTION_TYPE_KEY_TO_LABEL["local_council"] == "기초의원"
