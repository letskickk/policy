"""정책 생성 시스템 통합 테스트.

새 기능의 핵심 경로를 검증:
- 정책 초안 저장 → status 전환 워크플로
- 시민 제안 CRUD + 상태 변경
- 리뷰 코멘트
- 상태 전이 가드
- Assembly API rate limit 백오프
"""

from pathlib import Path
from uuid import uuid4

from backend import database
from backend.policy_ssot import (
    list_policy_positions,
    upsert_policy_position,
    update_policy_position_status,
    get_policy_position,
)


def _workspace_db(name: str) -> Path:
    root = Path(".test_tmp")
    root.mkdir(exist_ok=True)
    return root / f"{name}-{uuid4().hex}.db"


# ---------------------------------------------------------------------------
# 1. 정책 초안 저장 + status 전환 워크플로
# ---------------------------------------------------------------------------
def test_draft_workflow_happy_path(monkeypatch):
    """draft → review → approved 워크플로."""
    db_file = _workspace_db("draft-workflow")
    monkeypatch.setattr(database, "DB_PATH", db_file)
    database.init_db()

    # 초안 생성
    result = upsert_policy_position(
        position_id=None,
        title="청년 주거 지원",
        category="housing",
        summary="청년 월세 지원 확대",
        body="청년 주거 안정을 위한 정책 초안.",
        status="draft",
        owner_scope="party",
        effective_from=None,
        effective_to=None,
        version_label=None,
        actor_id=None,
    )
    pid = result["id"]
    assert pid > 0

    pos = get_policy_position(pid)
    assert pos["status"] == "draft"

    # draft → review
    update_policy_position_status(pid, "review")
    pos = get_policy_position(pid)
    assert pos["status"] == "review"

    # review → approved
    update_policy_position_status(pid, "approved")
    pos = get_policy_position(pid)
    assert pos["status"] == "approved"


def test_invalid_status_transition_blocked(monkeypatch):
    """approved → draft 직접 전환은 차단되어야 함."""
    import pytest

    db_file = _workspace_db("invalid-transition")
    monkeypatch.setattr(database, "DB_PATH", db_file)
    database.init_db()

    result = upsert_policy_position(
        position_id=None,
        title="테스트 포지션",
        category="test",
        summary="test",
        body="test body",
        status="approved",
        owner_scope="party",
        effective_from=None,
        effective_to=None,
        version_label=None,
        actor_id=None,
    )
    pid = result["id"]

    # approved → draft 는 허용되지 않음 (approved → archived만 가능)
    with pytest.raises(Exception):
        update_policy_position_status(pid, "draft")


def test_archive_and_restore(monkeypatch):
    """archived → draft 복원 워크플로."""
    db_file = _workspace_db("archive-restore")
    monkeypatch.setattr(database, "DB_PATH", db_file)
    database.init_db()

    result = upsert_policy_position(
        position_id=None,
        title="보관 테스트",
        category="test",
        summary="test",
        body="test body",
        status="draft",
        owner_scope="party",
        effective_from=None,
        effective_to=None,
        version_label=None,
        actor_id=None,
    )
    pid = result["id"]

    # draft → archived
    update_policy_position_status(pid, "archived")
    pos = get_policy_position(pid)
    assert pos["status"] == "archived"

    # archived → draft (복원)
    update_policy_position_status(pid, "draft")
    pos = get_policy_position(pid)
    assert pos["status"] == "draft"


# ---------------------------------------------------------------------------
# 2. 시민 제안 CRUD + 상태 변경
# ---------------------------------------------------------------------------
def test_citizen_proposal_crud(monkeypatch):
    """시민 제안 생성·조회·상태 변경."""
    db_file = _workspace_db("proposals")
    monkeypatch.setattr(database, "DB_PATH", db_file)
    database.init_db()

    conn = database.get_connection()
    try:
        # 제안 등록
        conn.execute(
            """INSERT INTO citizen_proposals (author_name, topic, title, body, status)
               VALUES (?, ?, ?, ?, ?)""",
            ("홍길동", "경제", "소상공인 임대료 지원", "소상공인 월 임대료 50만원 지원.", "new"),
        )
        conn.commit()

        # 조회
        row = conn.execute("SELECT * FROM citizen_proposals WHERE title = ?", ("소상공인 임대료 지원",)).fetchone()
        assert row is not None
        assert row["status"] == "new"
        assert row["topic"] == "경제"
        pid = row["id"]

        # 상태 변경: new → reviewing
        conn.execute("UPDATE citizen_proposals SET status = ? WHERE id = ?", ("reviewing", pid))
        conn.commit()

        row = conn.execute("SELECT status FROM citizen_proposals WHERE id = ?", (pid,)).fetchone()
        assert row["status"] == "reviewing"

        # 상태 변경: reviewing → adopted
        conn.execute("UPDATE citizen_proposals SET status = ?, review_note = ? WHERE id = ?", ("adopted", "좋은 제안", pid))
        conn.commit()

        row = conn.execute("SELECT status, review_note FROM citizen_proposals WHERE id = ?", (pid,)).fetchone()
        assert row["status"] == "adopted"
        assert row["review_note"] == "좋은 제안"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 3. 리뷰 코멘트
# ---------------------------------------------------------------------------
def test_review_comments(monkeypatch):
    """리뷰 코멘트 추가·조회."""
    db_file = _workspace_db("comments")
    monkeypatch.setattr(database, "DB_PATH", db_file)
    database.init_db()

    # 포지션 생성
    result = upsert_policy_position(
        position_id=None,
        title="코멘트 테스트",
        category="test",
        summary="test",
        body="test body",
        status="draft",
        owner_scope="party",
        effective_from=None,
        effective_to=None,
        version_label=None,
        actor_id=None,
    )
    pid = result["id"]

    conn = database.get_connection()
    try:
        # 코멘트 추가
        conn.execute(
            "INSERT INTO policy_review_comments (position_id, comment, comment_type) VALUES (?, ?, ?)",
            (pid, "내용 보충 필요", "review"),
        )
        conn.execute(
            "INSERT INTO policy_review_comments (position_id, comment, comment_type) VALUES (?, ?, ?)",
            (pid, "정강정책 연결 부분 강화", "suggestion"),
        )
        conn.commit()

        # 조회
        rows = conn.execute(
            "SELECT * FROM policy_review_comments WHERE position_id = ? ORDER BY created_at",
            (pid,),
        ).fetchall()
        assert len(rows) == 2
        assert rows[0]["comment"] == "내용 보충 필요"
        assert rows[1]["comment_type"] == "suggestion"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 4. Assembly API rate limit 백오프
# ---------------------------------------------------------------------------
def test_assembly_rate_limit_backoff(monkeypatch):
    """Rate limit 백오프가 작동하는지 검증."""
    db_file = _workspace_db("ratelimit")
    monkeypatch.setattr(database, "DB_PATH", db_file)
    database.init_db()

    from backend.assembly_api import _set_rate_limit_backoff, _is_rate_limited

    test_url = "https://example.com/api/test"

    # 초기에는 rate limited가 아님
    assert _is_rate_limited(test_url) is False

    # 백오프 설정
    _set_rate_limit_backoff(test_url)

    # 이제 rate limited
    assert _is_rate_limited(test_url) is True

    # 다른 URL은 영향 없음
    assert _is_rate_limited("https://example.com/api/other") is False


# ---------------------------------------------------------------------------
# 5. 이슈 레이더 캐시 테이블 존재 확인
# ---------------------------------------------------------------------------
def test_issue_radar_cache_table_exists(monkeypatch):
    """issue_radar_cache 테이블이 init_db에서 생성되는지 확인."""
    db_file = _workspace_db("radar-table")
    monkeypatch.setattr(database, "DB_PATH", db_file)
    database.init_db()

    conn = database.get_connection()
    try:
        # 테이블 존재 여부 확인
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='issue_radar_cache'"
        ).fetchone()
        assert row is not None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 6. 정책 드래프터 캐시 TTL 확인
# ---------------------------------------------------------------------------
def test_drafter_cache_ttl_is_1hour():
    """드래프터 캐시 TTL이 1시간으로 설정되어 있는지 확인."""
    # policy_drafter.py의 _set_cached_draft 함수가 timedelta(hours=1)을 사용하는지 소스 검증
    from pathlib import Path

    drafter_source = Path("backend/policy_drafter.py").read_text(encoding="utf-8")
    assert "timedelta(hours=1)" in drafter_source


# ---------------------------------------------------------------------------
# 7. 상태 전이 가드 맵 확인
# ---------------------------------------------------------------------------
def test_valid_status_transitions_map():
    """VALID_STATUS_TRANSITIONS가 올바른 전이만 허용하는지 확인."""
    from backend.policy_ssot import VALID_STATUS_TRANSITIONS

    # draft에서 갈 수 있는 곳
    assert "review" in VALID_STATUS_TRANSITIONS["draft"]
    assert "archived" in VALID_STATUS_TRANSITIONS["draft"]
    assert "approved" not in VALID_STATUS_TRANSITIONS["draft"]

    # review에서 갈 수 있는 곳
    assert "approved" in VALID_STATUS_TRANSITIONS["review"]
    assert "draft" in VALID_STATUS_TRANSITIONS["review"]

    # approved에서는 archived만
    assert VALID_STATUS_TRANSITIONS["approved"] == {"archived"}

    # archived에서는 draft만
    assert VALID_STATUS_TRANSITIONS["archived"] == {"draft"}


# ---------------------------------------------------------------------------
# 8. 정책 허브 통합 테스트
# ---------------------------------------------------------------------------
def test_policy_lab_html_has_four_tabs():
    """policy-lab.html에 4개 탭이 모두 존재하는지 확인."""
    from pathlib import Path

    html = Path("static/policy-lab.html").read_text(encoding="utf-8")
    assert 'data-tab="create"' in html
    assert 'data-tab="radar"' in html
    assert 'data-tab="proposals"' in html
    assert 'data-tab="review"' in html
    # Dashboard strip
    assert "dash-strip" in html
    # Lab namespace
    assert "Lab.switchTab" in html


def test_policy_lab_html_has_deep_link_support():
    """policy-lab.html이 URL 딥링크를 지원하는지 확인."""
    from pathlib import Path

    html = Path("static/policy-lab.html").read_text(encoding="utf-8")
    assert "pushState" in html or "replaceState" in html
    assert "popstate" in html


def test_policy_lab_html_has_sse_streaming():
    """policy-lab.html이 SSE 스트리밍을 사용하는지 확인."""
    from pathlib import Path

    html = Path("static/policy-lab.html").read_text(encoding="utf-8")
    assert "/api/policy/draft/stream" in html
    assert "getReader" in html or "ReadableStream" in html


def test_redirects_in_routes():
    """정책 허브로의 리다이렉트가 올바르게 설정되었는지 확인."""
    from pathlib import Path

    source = Path("backend/policy_admin_routes.py").read_text(encoding="utf-8")
    # /policy-create → /policy-lab?tab=create
    assert 'policy-lab?tab=create' in source
    # /admin/issue-radar → /policy-lab?tab=radar
    assert 'policy-lab?tab=radar' in source
    # /admin/policy-review → /policy-lab?tab=review
    assert 'policy-lab?tab=review' in source
    # /admin/proposals → /policy-lab?tab=proposals
    assert 'policy-lab?tab=proposals' in source


def test_policy_lab_requires_admin():
    """policy-lab 라우트에 require_admin이 적용되었는지 확인."""
    from pathlib import Path

    source = Path("backend/policy_admin_routes.py").read_text(encoding="utf-8")
    # Find the policy_lab_page function and check it calls require_admin
    idx = source.index("def policy_lab_page")
    snippet = source[idx:idx + 200]
    assert "require_admin" in snippet


def test_policy_lab_redirects_anonymous_users_to_login():
    from fastapi.testclient import TestClient
    from backend.main import app

    client = TestClient(app)
    response = client.get("/policy-lab", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/login?next=/policy-lab"


def test_dashboard_api_exists():
    """대시보드 API 엔드포인트가 존재하는지 확인."""
    from pathlib import Path

    source = Path("backend/policy_admin_routes.py").read_text(encoding="utf-8")
    assert "/api/policy/lab/dashboard" in source
    assert "draft_count" in source
    assert "review_count" in source
    assert "proposal_new" in source
    assert "gap_count" in source


def test_proposals_page_still_public():
    """/proposals 라우트가 여전히 공개 상태인지 확인."""
    from pathlib import Path

    source = Path("backend/policy_admin_routes.py").read_text(encoding="utf-8")
    idx = source.index("def proposals_page")
    snippet = source[idx:idx + 200]
    # Should NOT have require_admin
    assert "require_admin" not in snippet


def test_main_page_has_admin_hub_button():
    """메인 페이지에 관리자 전용 정책 허브 버튼이 있는지 확인."""
    from pathlib import Path

    html = Path("static/index.html").read_text(encoding="utf-8")
    assert 'id="btnPolicyHub"' in html
    assert "policy-lab" in html
    assert "role" in html and "admin" in html  # admin label update in script
    # 시민 정책 제안 버튼도 존재
    assert "/proposals" in html


def test_admin_index_has_hub_link():
    """관리자 페이지에 통합 정책 허브 링크가 있는지 확인."""
    from pathlib import Path

    html = Path("static/admin/index.html").read_text(encoding="utf-8")
    assert "정책 허브" in html
    assert "/policy-lab" in html
    # 개별 링크가 제거되었는지 확인
    assert "/admin/issue-radar" not in html
    assert "/policy-create" not in html
    assert "/admin/policy-review" not in html
    assert "/admin/proposals" not in html
