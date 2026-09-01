"""Tests for the optional basic-auth gate on the web UI."""

import base64

from fastapi.testclient import TestClient

from legit.web import app


def _auth_header(creds: str) -> dict[str, str]:
    return {"Authorization": "Basic " + base64.b64encode(creds.encode()).decode()}


def test_no_auth_configured_allows_requests(monkeypatch):
    monkeypatch.delenv("LEGIT_BASIC_AUTH", raising=False)
    client = TestClient(app)
    # Unknown path passes the middleware and 404s — proves no auth wall
    assert client.get("/nonexistent").status_code == 404


def test_missing_credentials_rejected(monkeypatch):
    monkeypatch.setenv("LEGIT_BASIC_AUTH", "team:hunter2")
    client = TestClient(app)
    resp = client.get("/nonexistent")
    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"].startswith("Basic")


def test_wrong_credentials_rejected(monkeypatch):
    monkeypatch.setenv("LEGIT_BASIC_AUTH", "team:hunter2")
    client = TestClient(app)
    assert client.get("/nonexistent", headers=_auth_header("team:wrong")).status_code == 401


def test_correct_credentials_pass(monkeypatch):
    monkeypatch.setenv("LEGIT_BASIC_AUTH", "team:hunter2")
    client = TestClient(app)
    assert client.get("/nonexistent", headers=_auth_header("team:hunter2")).status_code == 404
