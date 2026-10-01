"""Private-preview boundaries and durable feedback attribution."""

import json
import sqlite3
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from legit import web
from legit.config import LegitConfig, ProfileConfig
from legit.feedback import save_review
from legit.models import ReviewOutput


@pytest.fixture()
def preview(tmp_path, monkeypatch):
    monkeypatch.setattr("legit.config.LEGIT_DIR", str(tmp_path))
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.delenv("LEGIT_BASIC_AUTH", raising=False)
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles" / "ready.md").write_text("Public review style")
    cfg = LegitConfig(profiles=[ProfileConfig(name=n, sources=[]) for n in ["ready", "missing"]])
    monkeypatch.setattr(web, "_load_cfg", lambda: cfg)
    return TestClient(web.app), tmp_path


def test_only_existing_profiles_selectable(preview):
    client, _ = preview
    html = client.get("/").text
    assert '<option value="ready"' in html
    assert '<option value="missing"' not in html
    assert "Not available yet: missing" in html
    assert "not written or endorsed" in html


@pytest.mark.parametrize("endpoint", ["/review", "/review/stream"])
def test_unavailable_and_traversal_profiles_never_start(preview, monkeypatch, endpoint):
    client, _ = preview
    run = Mock()
    monkeypatch.setattr(web, "generate_review", run)
    for profile in ["missing", "../ready"]:
        response = client.get(
            endpoint,
            params={"pr_url": "https://github.com/org/repo/pull/1", "profile_name": profile},
        )
        assert response.status_code == 400
    run.assert_not_called()


def test_feedback_requires_actual_review_and_persists(preview):
    client, root = preview
    assert (
        client.post("/feedback", json={"review_id": str(uuid4()), "rating": "useful"}).status_code
        == 404
    )
    review_id = save_review("ready", "https://github.com/org/repo/pull/1", {"summary": "Check"})
    for rating in ["useful", "wrong", "missed-something"]:
        assert client.post(
            "/feedback", json={"review_id": review_id, "rating": rating, "note": "An edge case"}
        ).json() == {"saved": True}
    assert (
        client.post("/feedback", json={"review_id": review_id, "rating": "invented"}).status_code
        == 422
    )
    with sqlite3.connect(root / "preview" / "feedback.sqlite3") as db:
        rows = db.execute(
            "SELECT r.persona, r.output, f.rating FROM feedback f "
            "JOIN reviews r ON r.id=f.review_id"
        ).fetchall()
    assert len(rows) == 3
    assert rows[0][0] == "ready"
    assert json.loads(rows[0][1])["summary"] == "Check"


def test_completed_review_has_feedback_id_and_escapes_output(preview, monkeypatch):
    client, root = preview
    run = Mock(return_value=ReviewOutput(summary="<script>alert('x')</script>"))
    monkeypatch.setattr(web, "generate_review", run)
    response = client.post(
        "/review", data={"pr_url": "https://github.com/org/repo/pull/1", "profile_name": "ready"}
    )
    assert response.status_code == 200
    assert "&lt;script&gt;" in response.text
    assert run.call_args.kwargs["dry_run"] is True
    with sqlite3.connect(root / "preview" / "feedback.sqlite3") as db:
        review_id = db.execute("SELECT id FROM reviews").fetchone()[0]
    assert review_id in response.text


def test_stream_uses_persisted_review_id(preview, monkeypatch):
    client, _ = preview

    def worker(cfg, profile, pr_url, progress):
        output = {"summary": "A simulated review"}
        output["review_id"] = save_review(profile, pr_url, output)
        progress.put({"step": "done", "review": output})

    monkeypatch.setattr(web, "_run_review_with_progress", worker)
    response = client.get(
        "/review/stream",
        params={"pr_url": "https://github.com/org/repo/pull/1", "profile_name": "ready"},
    )
    result = json.loads(response.text.removeprefix("data: ").strip())
    assert (
        client.post(
            "/feedback", json={"review_id": result["review"]["review_id"], "rating": "useful"}
        ).status_code
        == 200
    )
