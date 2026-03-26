import json

from backend import database
from backend.main import (
    _build_rule_based_pledge_share_summary,
    _fetch_public_pledge_share_payload,
    _get_or_create_persisted_pledge_share_summary,
)


def test_share_summary_is_persisted_and_reused(tmp_path, monkeypatch):
    db_file = tmp_path / "policy_share_summary.db"
    monkeypatch.setattr(database, "DB_PATH", db_file)
    database.init_db()

    conn = database.get_connection()
    try:
        conn.execute(
            """
            INSERT INTO candidates (
                id, name, district_name, district_code, region_code, election_type, approval_status, created_at
            ) VALUES (
                1, 'Tester', 'Gangnam', '11:Gangnam', '11', 'local_council', 'APPROVED', '2026-03-26 10:00:00'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO users (
                id, email, password_hash, applicant_match_id, region_name, district_name
            ) VALUES (
                1, 'tester@example.com', 'hashed', 123, 'Seoul', 'Gangnam'
            )
            """
        )
        conn.execute("UPDATE candidates SET user_id = 1 WHERE id = 1")
        conn.execute(
            """
            INSERT INTO candidate_pledges (
                id, candidate_id, title, content, priority, approval_status, created_at
            ) VALUES (
                86,
                1,
                '드론을 통한 치안/교통/구급 개선',
                '1. 드론으로 야간 순찰과 교통 대응을 강화합니다.\n2. 응급 상황에 드론을 투입해 골든타임을 줄입니다.\n시민 안전 대응 체계를 더 빠르게 만들겠습니다.',
                1,
                'APPROVED',
                '2026-03-26 10:10:00'
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    payload = _fetch_public_pledge_share_payload(86)
    assert payload is not None
    summary = _get_or_create_persisted_pledge_share_summary(payload)
    assert summary.headline.endswith(("다.", "요.", "니다."))
    assert summary.bullets

    conn = database.get_connection()
    try:
        row = conn.execute(
            """
            SELECT share_summary_title, share_summary_headline, share_summary_bullets, share_summary_version
            FROM candidate_pledges
            WHERE id = 86
            """
        ).fetchone()
    finally:
        conn.close()

    assert row["share_summary_title"] == summary.title
    assert row["share_summary_headline"] == summary.headline
    assert row["share_summary_version"] == "rule-v1"
    assert json.loads(row["share_summary_bullets"]) == summary.bullets

    payload_again = _fetch_public_pledge_share_payload(86)
    summary_again = _get_or_create_persisted_pledge_share_summary(payload_again)
    assert summary_again == summary


def test_rule_based_share_summary_prefers_actionable_lines():
    summary = _build_rule_based_pledge_share_summary(
        "청년 교통비 부담 완화",
        "1. 청년 교통비를 월 단위로 지원합니다.\n2. 환승 할인 범위를 넓혀 출퇴근 부담을 줄입니다.\n청년 이동권을 실질적으로 개선하겠습니다.",
    )

    assert summary.title == "청년 교통비 부담 완화"
    assert "지원" in summary.headline or "할인" in summary.headline
    assert len(summary.bullets) <= 2
