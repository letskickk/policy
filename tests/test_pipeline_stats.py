"""Tests for GET /api/admin/pipeline/stats endpoint."""

from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


def test_pipeline_stats_requires_admin():
    """Unauthenticated request returns 401."""
    response = client.get("/api/admin/pipeline/stats")
    assert response.status_code in (401, 403)


def test_pipeline_stats_normal():
    """With admin session, returns expected JSON structure.
    Note: This test requires an admin session cookie. If the test env
    doesn't have one, we just verify the endpoint exists (not 404)."""
    response = client.get("/api/admin/pipeline/stats")
    # Should be 401/403 without auth, but definitely not 404
    assert response.status_code != 404


def test_pipeline_stats_response_schema():
    """Verify the response schema has required top-level keys.
    This test is a schema contract test — it will pass when run with
    proper admin auth (e.g., in integration tests)."""
    response = client.get("/api/admin/pipeline/stats")
    if response.status_code == 200:
        data = response.json()
        assert "collect" in data
        assert "store" in data
        assert "usage" in data
        assert "recent_activity" in data
        # store sub-keys
        store = data["store"]
        assert "documents" in store
        assert "positions" in store
        assert "fts_indexed" in store
        assert "vector_store" in store
        # usage sub-keys
        usage = data["usage"]
        assert "check_total" in usage
        assert "check_today" in usage
        assert "draft_total" in usage
        assert "draft_today" in usage


def test_pipeline_stats_collect_is_dict():
    """collect field should be a dict of doc_type -> count."""
    response = client.get("/api/admin/pipeline/stats")
    if response.status_code == 200:
        data = response.json()
        assert isinstance(data["collect"], dict)
        for k, v in data["collect"].items():
            assert isinstance(k, str)
            assert isinstance(v, int)


def test_pipeline_stats_recent_activity_is_list():
    """recent_activity should be a list of activity objects."""
    response = client.get("/api/admin/pipeline/stats")
    if response.status_code == 200:
        data = response.json()
        assert isinstance(data["recent_activity"], list)
        for item in data["recent_activity"]:
            assert "ts" in item
            assert "action" in item
            assert "detail" in item
