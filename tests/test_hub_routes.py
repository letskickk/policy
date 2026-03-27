"""Tests for hub page routes: /hub, /hub/archive, /hub-briefing redirect."""

from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


def test_hub_page_returns_200():
    response = client.get("/hub")
    assert response.status_code == 200
    assert "정책 자료 허브" in response.text


def test_hub_archive_page_returns_200():
    response = client.get("/hub/archive")
    assert response.status_code == 200
    assert "자료 허브" in response.text


def test_hub_briefing_redirects_to_hub():
    response = client.get("/hub-briefing", follow_redirects=False)
    assert response.status_code == 301
    assert response.headers["location"] == "/hub"
