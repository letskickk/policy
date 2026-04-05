import io

import openpyxl
import pytest

from backend import applicant_verify, database
import backend.main as main


@pytest.fixture()
def test_db(tmp_path, monkeypatch):
    db_file = tmp_path / "policy_test.db"
    monkeypatch.setattr(database, "DB_PATH", db_file)
    monkeypatch.setattr(main, "_db_ready", False)
    database.init_db()
    return db_file


def _build_workbook(rows):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


def test_extract_applicants_from_workbook_uses_header_names_and_skips_blank_names(test_db):
    content = _build_workbook(
        [
            ["지원자명", "이메일", "휴대전화", "시도", "선거구", "출마직위", "서류제출", "면접완료", "비고"],
            ["홍길동", "hong@example.com", "010-1234-5678", "서울특별시", "중랑구", "기초의원", "예", "", "공천 확정"],
            ["", "skip@example.com", "010-0000-0000", "서울특별시", "중랑구", "기초의원", "", "", ""],
        ]
    )

    applicants, skipped = main._extract_applicants_from_workbook(content, "applicants.xlsx")

    assert skipped == 1
    assert applicants == [
        {
            "name": "홍길동",
            "phone": "010-1234-5678",
            "email": "hong@example.com",
            "region_province": "서울특별시",
            "district_info": "중랑구",
            "election_position": "기초의원",
            "doc_submitted": 1,
            "interview_done": 0,
            "status_note": "공천 확정",
        }
    ]


def test_verify_user_against_applicants_matches_phone_and_name(test_db):
    conn = database.get_connection()
    try:
        conn.execute(
            """
            INSERT INTO users (email, password_hash, name, phone, region_name)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("candidate@example.com", "hash", "홍 길동", "01012345678", "서울특별시"),
        )
        conn.execute(
            """
            INSERT INTO party_applicants (name, phone, email, region_province, district_info, status_note)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("홍길동", "010-1234-5678", "other@example.com", "서울특별시", "중랑구", "공천 확정"),
        )
        user_id = conn.execute("SELECT id FROM users WHERE email = ?", ("candidate@example.com",)).fetchone()["id"]
        conn.commit()
    finally:
        conn.close()

    result = applicant_verify.verify_user_against_applicants(
        user_id,
        user_phone="01012345678",
        user_email="candidate@example.com",
        user_name="홍 길동",
        user_region="서울특별시",
    )

    assert result["verified"] == 1
    assert result["note"].startswith("phone+name")


def test_get_regions_includes_public_candidate_with_confirmed_nomination_fallback(test_db):
    conn = database.get_connection()
    try:
        conn.execute(
            """
            INSERT INTO users (email, password_hash, status, role, name, phone, region_name)
            VALUES (?, ?, 'APPROVED', 'USER', ?, ?, ?)
            """,
            ("runner@example.com", "hash", "홍길동", "010-9999-0000", "서울특별시"),
        )
        user_id = conn.execute("SELECT id FROM users WHERE email = ?", ("runner@example.com",)).fetchone()["id"]
        conn.execute(
            """
            INSERT INTO candidates (name, district_name, district_code, region_code, election_type, approval_status, user_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("홍길동", "중랑구", "11:중랑구", "11", "local", "APPROVED", user_id, "2026-01-10 12:00:00"),
        )
        candidate_id = conn.execute("SELECT id FROM candidates WHERE user_id = ?", (user_id,)).fetchone()["id"]
        conn.execute(
            """
            INSERT INTO candidate_pledges (candidate_id, title, category, priority, approval_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (candidate_id, "교통 개선", "교통", 1, "APPROVED", "2026-01-11 09:00:00"),
        )
        conn.execute(
            """
            INSERT INTO party_applicants (name, phone, email, region_province, district_info, status_note)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("홍길동", "01099990000", "different@example.com", "서울특별시", "중랑구", "공천 확정"),
        )
        conn.commit()
    finally:
        conn.close()

    regions = main.get_regions()
    seoul = next(region for region in regions if region.region_code == "11")
    assert seoul.candidate_count == 1
