import pytest
from fastapi import HTTPException

from backend import database
import backend.main as main
from backend.main import (
    AdminCandidatePledgeInput,
    AdminCandidateUpsertBody,
    get_candidate_detail,
    get_candidates,
    get_districts,
    get_regions,
)


@pytest.fixture()
def test_db(tmp_path, monkeypatch):
    db_file = tmp_path / "policy_test.db"
    monkeypatch.setattr(database, "DB_PATH", db_file)
    database.init_db()

    conn = database.get_connection()
    try:
        conn.execute(
            """
            INSERT INTO candidates (name, district_name, district_code, region_code, election_type, approval_status, created_at)
            VALUES ('홍길동', '강남구', '11:강남구', '11', 'local', 'APPROVED', '2026-01-10 12:00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO candidates (name, district_name, district_code, region_code, election_type, approval_status, created_at)
            VALUES ('김철수', '종로구', '11:종로구', '11', 'local', 'APPROVED', '2026-01-12 12:00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO candidate_pledges (candidate_id, title, category, priority, created_at)
            VALUES
              (1, '청년 주거 지원 확대', '주거', 1, '2026-01-11 09:00:00'),
              (1, '교통 혼잡 완화', '교통', 2, '2026-01-11 09:10:00'),
              (1, '소상공인 금융 지원', '경제', 3, '2026-01-11 09:20:00'),
              (1, '노후 인프라 개선', '도시', 4, '2026-01-11 09:30:00'),
              (2, '돌봄센터 확충', '복지', 1, '2026-01-13 09:00:00')
            """
        )
        conn.commit()
    finally:
        conn.close()

    return db_file


def test_get_regions_counts_registered_candidates(test_db):
    regions = get_regions()
    seoul = next(r for r in regions if r.region_code == "11")
    assert seoul.candidate_count == 2


def test_get_candidates_returns_empty_list_for_empty_region(test_db):
    result = get_candidates("26")
    assert result == []


def test_get_candidates_raises_400_for_invalid_region_code(test_db):
    with pytest.raises(HTTPException) as exc:
        get_candidates("XX")
    assert exc.value.status_code == 400


def test_get_candidates_returns_top_3_pledges_only(test_db):
    result = get_candidates("11")
    target = next(c for c in result if c.candidate_id == 1)
    assert len(target.pledges) == 3
    assert [p.title for p in target.pledges] == [
        "청년 주거 지원 확대",
        "교통 혼잡 완화",
        "소상공인 금융 지원",
    ]


def test_get_districts_groups_candidates_by_district(test_db):
    districts = get_districts("11")
    codes = {d.district_code for d in districts}
    assert "11:강남구" in codes
    assert "11:종로구" in codes


def test_get_candidates_filters_by_district_code(test_db):
    result = get_candidates("11", "11:강남구")
    assert len(result) == 1
    assert result[0].district_name == "강남구"


def test_get_candidates_filters_by_election_type(test_db):
    conn = database.get_connection()
    try:
        conn.execute(
            """
            INSERT INTO candidates (name, district_name, district_code, region_code, election_type, approval_status, created_at)
            VALUES ('이단체장', '강남구', '11:강남구', '11', 'mayor', 'APPROVED', '2026-01-15 12:00:00')
            """
        )
        conn.commit()
    finally:
        conn.close()

    local_items = get_candidates("11", None, "local")
    mayor_items = get_candidates("11", None, "mayor")
    assert all(item.election_type == "local" for item in local_items)
    assert len(mayor_items) == 1
    assert mayor_items[0].name == "이단체장"


def test_get_districts_filters_by_election_type(test_db):
    conn = database.get_connection()
    try:
        conn.execute(
            """
            INSERT INTO candidates (name, district_name, district_code, region_code, election_type, approval_status, created_at)
            VALUES ('오광역', '강남구', '11:강남구', '11', 'mayor', 'APPROVED', '2026-01-16 12:00:00')
            """
        )
        conn.commit()
    finally:
        conn.close()

    local_districts = get_districts("11", "local")
    mayor_districts = get_districts("11", "mayor")
    assert len(local_districts) >= 2
    assert len(mayor_districts) == 1
    assert mayor_districts[0].district_code == "11:강남구"


def test_get_candidate_detail_returns_all_pledges(test_db):
    detail = get_candidate_detail(1)
    assert detail.region_code == "11"
    assert len(detail.pledges) == 4


def test_admin_create_candidate_rejects_invalid_region_code(test_db, monkeypatch):
    monkeypatch.setattr(main, "_ensure_startup", lambda: None)
    monkeypatch.setattr(main, "require_user", lambda _request: {"id": 1, "role": "ADMIN"})
    body = AdminCandidateUpsertBody(
        name="테스트후보",
        district_name="테스트구",
        region_code="XX",
        election_type="local",
        pledges=[AdminCandidatePledgeInput(title="공약1", category="기타", priority=1)],
    )
    with pytest.raises(HTTPException) as exc:
        main.admin_create_candidate(body, request=None)
    assert exc.value.status_code == 400


def test_admin_create_and_update_candidate_with_valid_region_code(test_db, monkeypatch):
    monkeypatch.setattr(main, "_ensure_startup", lambda: None)
    monkeypatch.setattr(main, "require_user", lambda _request: {"id": 1, "role": "ADMIN"})

    created = main.admin_create_candidate(
        AdminCandidateUpsertBody(
            name="박후보",
            district_name="서초구",
            region_code="11",
            election_type="local",
            pledges=[AdminCandidatePledgeInput(title="교통 개선", category="교통", priority=1)],
        ),
        request=None,
    )
    assert created.name == "박후보"
    assert created.region_code == "11"
    assert len(created.pledges) == 1

    updated = main.admin_update_candidate(
        created.candidate_id,
        AdminCandidateUpsertBody(
            name="박후보(수정)",
            district_name="강남구",
            region_code="11",
            election_type="local",
            pledges=[
                AdminCandidatePledgeInput(title="주거 안정", category="주거", priority=1),
                AdminCandidatePledgeInput(title="청년 일자리", category="일자리", priority=2),
            ],
        ),
        request=None,
    )
    assert updated.name == "박후보(수정)"
    assert updated.district_name == "강남구"
    assert [p.title for p in updated.pledges] == ["주거 안정", "청년 일자리"]
