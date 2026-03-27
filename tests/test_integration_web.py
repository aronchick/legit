"""Integration tests for the legit web UI.

These tests use FastAPI's TestClient to exercise the full HTTP layer
(GET /, POST /review) with mocked LLM inference but real config/profile loading.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from fastapi.testclient import TestClient

from legit.models import InlineComment, ReviewOutput
from legit.review import CritiqueItem, CritiqueOutput
from legit.web import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def _legit_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set up a complete .legit directory with config, profile, and BM25 index."""
    root = tmp_path / ".legit"
    for subdir in ("profiles", "data", "index/sample-reviewer", "cache", "calibration"):
        (root / subdir).mkdir(parents=True)

    # Config
    config = {
        "model": {"provider": "gemini"},
        "github": {"token_env": "GITHUB_TOKEN"},
        "profiles": [
            {
                "name": "sample-reviewer",
                "sources": [
                    {"type": "primary", "repo": "octocat/hello-world", "username": "octoreviewer"}
                ],
                "chunk_size": 50,
            }
        ],
        "retrieval": {"top_k": 3},
        "review": {"post_to_github": False, "abstention_threshold": 0.3, "max_comments": 5},
    }
    (root / "config.yaml").write_text(yaml.dump(config, default_flow_style=False))

    # Profile document
    (root / "profiles" / "sample-reviewer.md").write_text(
        "# Reviewer Profile: sample-reviewer\n\n"
        "## Style\n"
        "- Concise, to-the-point feedback\n"
        "- Focuses on error handling and edge cases\n"
        "- Uses 'nit:' prefix for minor style issues\n"
    )

    # Minimal BM25 index
    bm25_index = {
        "k1": 1.5,
        "b": 0.75,
        "doc_count": 2,
        "avg_dl": 15.0,
        "doc_lengths": [12, 18],
        "inverted": {
            "error": {"0": 2},
            "handling": {"0": 1},
            "nil": {"0": 1, "1": 1},
            "check": {"1": 2},
            "nit": {"1": 1},
        },
        "documents": [
            {
                "comment_text": "Please add error handling for the nil case here.",
                "file_path": "pkg/auth/handler.go",
                "code_context": "func handleAuth() error {",
                "comment_type": "pr_review",
                "severity": "suggestion",
                "timestamp": "2025-06-15T10:00:00Z",
                "reviewer_username": "octoreviewer",
                "pr_number": 42,
            },
            {
                "comment_text": "nit: prefer explicit nil checks over relying on zero-values.",
                "file_path": "pkg/api/server.go",
                "code_context": "if resp != nil {",
                "comment_type": "pr_review",
                "severity": "nit",
                "timestamp": "2025-07-20T14:30:00Z",
                "reviewer_username": "octoreviewer",
                "pr_number": 55,
            },
        ],
    }
    (root / "index" / "sample-reviewer" / "bm25.json").write_text(json.dumps(bm25_index))

    monkeypatch.setattr("legit.config.LEGIT_DIR", str(root))
    monkeypatch.setattr("legit.retrieval.LEGIT_DIR", str(root))
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_integration_token")

    return root


SAMPLE_PR_DATA = {
    "metadata": {
        "title": "Add nil check to auth middleware",
        "user": {"login": "contributor-alice"},
        "body": "This PR adds defensive nil checks to the auth middleware.\n\nFixes #101.",
        "number": 200,
    },
    "diff": (
        "diff --git a/pkg/auth/middleware.go b/pkg/auth/middleware.go\n"
        "--- a/pkg/auth/middleware.go\n"
        "+++ b/pkg/auth/middleware.go\n"
        "@@ -15,6 +15,12 @@ func AuthMiddleware(next http.Handler) http.Handler {\n"
        "+    if claims == nil {\n"
        "+        http.Error(w, \"unauthorized\", http.StatusUnauthorized)\n"
        "+        return\n"
        "+    }\n"
        "+    if claims.UserID == \"\" {\n"
        "+        http.Error(w, \"invalid token\", http.StatusForbidden)\n"
        "+        return\n"
        "+    }\n"
    ),
    "files": [
        {"filename": "pkg/auth/middleware.go", "additions": 8, "deletions": 0},
    ],
    "comments": [],
    "reviews": [],
    "linked_issues": [{"number": 101, "title": "Auth panic on nil claims"}],
}

SAMPLE_REVIEW = ReviewOutput(
    summary="Good defensive additions. The nil check on claims is exactly what we need.",
    inline_comments=[
        InlineComment(
            file="pkg/auth/middleware.go",
            hunk_header="@@ -15,6 +15,12 @@",
            diff_snippet='+    if claims == nil {\n+        http.Error(w, "unauthorized", http.StatusUnauthorized)',
            side="addition",
            comment="nit: consider logging the unauthorized attempt before returning — helps with debugging auth issues in production.",
            confidence=0.82,
        ),
        InlineComment(
            file="pkg/auth/middleware.go",
            hunk_header="@@ -15,6 +15,12 @@",
            diff_snippet='+    if claims.UserID == "" {',
            side="addition",
            comment="Should we also check for whitespace-only UserIDs? strings.TrimSpace might be safer.",
            confidence=0.65,
        ),
    ],
    abstained_files=[],
    abstention_reason="",
)

SAMPLE_CRITIQUE = CritiqueOutput(
    assessments=[
        CritiqueItem(
            comment_index=0,
            would_reviewer_leave_this="yes",
            phrasing_sounds_like_them="yes",
            already_covered="no",
        ),
        CritiqueItem(
            comment_index=1,
            would_reviewer_leave_this="probably",
            phrasing_sounds_like_them="close",
            already_covered="no",
        ),
    ]
)


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------


class TestIndexPage:
    def test_returns_200(self, _legit_env: Path):
        client = TestClient(app)
        resp = client.get("/")
        assert resp.status_code == 200

    def test_contains_profile_dropdown(self, _legit_env: Path):
        client = TestClient(app)
        resp = client.get("/")
        assert "sample-reviewer" in resp.text

    def test_contains_form_elements(self, _legit_env: Path):
        client = TestClient(app)
        resp = client.get("/")
        html = resp.text
        assert "<form" in html.lower()
        assert "pr_url" in html
        assert "profile_name" in html

    def test_no_review_on_initial_load(self, _legit_env: Path):
        client = TestClient(app)
        resp = client.get("/")
        # The review section should not be populated
        assert "## Summary" not in resp.text


# ---------------------------------------------------------------------------
# POST /review — success path
# ---------------------------------------------------------------------------


class TestReviewEndpoint:
    @patch("legit.review.retrieve", return_value=[])
    @patch("legit.review.run_inference")
    @patch("legit.review.GitHubClient")
    def test_successful_review(
        self,
        mock_gh_cls: MagicMock,
        mock_inference: MagicMock,
        mock_retrieve: MagicMock,
        _legit_env: Path,
    ):
        # Mock GitHub client
        mock_gh = MagicMock()
        mock_gh.fetch_pr_for_review.return_value = SAMPLE_PR_DATA
        mock_gh.__enter__ = MagicMock(return_value=mock_gh)
        mock_gh.__exit__ = MagicMock(return_value=False)
        mock_gh_cls.return_value = mock_gh

        # First call = review generation, second = self-critique
        mock_inference.side_effect = [SAMPLE_REVIEW, SAMPLE_CRITIQUE]

        client = TestClient(app)
        resp = client.post(
            "/review",
            data={"pr_url": "https://github.com/octocat/hello-world/pull/200", "profile_name": "sample-reviewer"},
        )

        assert resp.status_code == 200
        html = resp.text
        # Review summary should appear
        assert "Good defensive additions" in html
        # Inline comments should appear
        assert "nit: consider logging" in html
        # Confidence badges should appear
        assert "0.82" in html or "82" in html

    @patch("legit.review.retrieve", return_value=[])
    @patch("legit.review.run_inference")
    @patch("legit.review.GitHubClient")
    def test_review_with_all_comments_filtered(
        self,
        mock_gh_cls: MagicMock,
        mock_inference: MagicMock,
        mock_retrieve: MagicMock,
        _legit_env: Path,
    ):
        mock_gh = MagicMock()
        mock_gh.fetch_pr_for_review.return_value = SAMPLE_PR_DATA
        mock_gh.__enter__ = MagicMock(return_value=mock_gh)
        mock_gh.__exit__ = MagicMock(return_value=False)
        mock_gh_cls.return_value = mock_gh

        # Return a review where all comments are low-confidence (below 0.3 threshold)
        low_confidence_review = ReviewOutput(
            summary="LGTM — no significant issues found.",
            inline_comments=[
                InlineComment(
                    file="pkg/auth/middleware.go",
                    hunk_header="@@",
                    diff_snippet="+x",
                    comment="Maybe add a comment?",
                    confidence=0.1,
                ),
            ],
        )
        critique = CritiqueOutput(
            assessments=[
                CritiqueItem(
                    comment_index=0,
                    would_reviewer_leave_this="no",
                    phrasing_sounds_like_them="no",
                    already_covered="no",
                ),
            ]
        )
        mock_inference.side_effect = [low_confidence_review, critique]

        client = TestClient(app)
        resp = client.post(
            "/review",
            data={"pr_url": "https://github.com/octocat/hello-world/pull/200", "profile_name": "sample-reviewer"},
        )

        assert resp.status_code == 200
        assert "LGTM" in resp.text

    @patch("legit.review.retrieve", return_value=[])
    @patch("legit.review.run_inference")
    @patch("legit.review.GitHubClient")
    def test_review_with_abstentions(
        self,
        mock_gh_cls: MagicMock,
        mock_inference: MagicMock,
        mock_retrieve: MagicMock,
        _legit_env: Path,
    ):
        mock_gh = MagicMock()
        pr_data = {**SAMPLE_PR_DATA}
        pr_data["files"] = [
            {"filename": "pkg/auth/middleware.go", "additions": 8, "deletions": 0},
            {"filename": "vendor/lib/crypto.go", "additions": 100, "deletions": 50},
        ]
        mock_gh.fetch_pr_for_review.return_value = pr_data
        mock_gh.__enter__ = MagicMock(return_value=mock_gh)
        mock_gh.__exit__ = MagicMock(return_value=False)
        mock_gh_cls.return_value = mock_gh

        review_with_abstentions = ReviewOutput(
            summary="Reviewed the auth middleware changes. Skipping vendor code.",
            inline_comments=[
                InlineComment(
                    file="pkg/auth/middleware.go",
                    hunk_header="@@",
                    diff_snippet="+if claims == nil {",
                    comment="Good nil check.",
                    confidence=0.9,
                ),
            ],
            abstained_files=["vendor/lib/crypto.go"],
            abstention_reason="Vendor code — outside reviewer's domain",
        )
        critique = CritiqueOutput(
            assessments=[
                CritiqueItem(comment_index=0, would_reviewer_leave_this="yes", phrasing_sounds_like_them="yes", already_covered="no"),
            ]
        )
        mock_inference.side_effect = [review_with_abstentions, critique]

        client = TestClient(app)
        resp = client.post(
            "/review",
            data={"pr_url": "https://github.com/octocat/hello-world/pull/200", "profile_name": "sample-reviewer"},
        )

        assert resp.status_code == 200
        html = resp.text
        assert "vendor/lib/crypto.go" in html
        assert "Vendor code" in html or "outside reviewer" in html


# ---------------------------------------------------------------------------
# POST /review — error paths
# ---------------------------------------------------------------------------


class TestReviewErrors:
    @patch("legit.review.retrieve", return_value=[])
    @patch("legit.review.run_inference")
    @patch("legit.review.GitHubClient")
    def test_github_fetch_failure(
        self,
        mock_gh_cls: MagicMock,
        mock_inference: MagicMock,
        mock_retrieve: MagicMock,
        _legit_env: Path,
    ):
        mock_gh = MagicMock()
        mock_gh.fetch_pr_for_review.side_effect = RuntimeError("GitHub API rate limited (403)")
        mock_gh.__enter__ = MagicMock(return_value=mock_gh)
        mock_gh.__exit__ = MagicMock(return_value=False)
        mock_gh_cls.return_value = mock_gh

        client = TestClient(app)
        resp = client.post(
            "/review",
            data={"pr_url": "https://github.com/octocat/hello-world/pull/999", "profile_name": "sample-reviewer"},
        )

        assert resp.status_code == 200  # Page renders, error shown inline
        assert "rate limited" in resp.text.lower() or "403" in resp.text

    @patch("legit.review.retrieve", return_value=[])
    @patch("legit.review.run_inference")
    @patch("legit.review.GitHubClient")
    def test_llm_inference_failure(
        self,
        mock_gh_cls: MagicMock,
        mock_inference: MagicMock,
        mock_retrieve: MagicMock,
        _legit_env: Path,
    ):
        mock_gh = MagicMock()
        mock_gh.fetch_pr_for_review.return_value = SAMPLE_PR_DATA
        mock_gh.__enter__ = MagicMock(return_value=mock_gh)
        mock_gh.__exit__ = MagicMock(return_value=False)
        mock_gh_cls.return_value = mock_gh

        mock_inference.side_effect = RuntimeError("Model provider timeout")

        client = TestClient(app)
        resp = client.post(
            "/review",
            data={"pr_url": "https://github.com/octocat/hello-world/pull/200", "profile_name": "sample-reviewer"},
        )

        assert resp.status_code == 200
        assert "timeout" in resp.text.lower() or "Model provider" in resp.text

    def test_missing_profile(self, _legit_env: Path):
        """POST with a profile that doesn't exist should show an error."""
        client = TestClient(app)
        resp = client.post(
            "/review",
            data={"pr_url": "https://github.com/octocat/hello-world/pull/1", "profile_name": "nonexistent-reviewer"},
        )
        assert resp.status_code == 200
        assert "not found" in resp.text.lower() or "error" in resp.text.lower()

    @patch("legit.review.retrieve", return_value=[])
    @patch("legit.review.run_inference")
    @patch("legit.review.GitHubClient")
    def test_invalid_pr_url(
        self,
        mock_gh_cls: MagicMock,
        mock_inference: MagicMock,
        mock_retrieve: MagicMock,
        _legit_env: Path,
    ):
        mock_gh = MagicMock()
        mock_gh.fetch_pr_for_review.side_effect = ValueError("Invalid PR URL: not-a-url")
        mock_gh.__enter__ = MagicMock(return_value=mock_gh)
        mock_gh.__exit__ = MagicMock(return_value=False)
        mock_gh_cls.return_value = mock_gh

        client = TestClient(app)
        resp = client.post(
            "/review",
            data={"pr_url": "not-a-url", "profile_name": "sample-reviewer"},
        )
        assert resp.status_code == 200
        assert "invalid" in resp.text.lower() or "error" in resp.text.lower()


# ---------------------------------------------------------------------------
# POST /review — multi-file PR
# ---------------------------------------------------------------------------


class TestMultiFilePR:
    @patch("legit.review.retrieve", return_value=[])
    @patch("legit.review.run_inference")
    @patch("legit.review.GitHubClient")
    def test_large_pr_with_many_files(
        self,
        mock_gh_cls: MagicMock,
        mock_inference: MagicMock,
        mock_retrieve: MagicMock,
        _legit_env: Path,
    ):
        """Simulate a PR with 10+ files and verify the pipeline handles it."""
        files = [{"filename": f"pkg/module{i}/handler.go", "additions": i * 5, "deletions": i} for i in range(12)]
        diff_parts = []
        for i in range(12):
            diff_parts.append(
                f"diff --git a/pkg/module{i}/handler.go b/pkg/module{i}/handler.go\n"
                f"--- a/pkg/module{i}/handler.go\n"
                f"+++ b/pkg/module{i}/handler.go\n"
                f"@@ -1,3 +1,5 @@ func Handler{i}() {{\n"
                f"+    // module {i} change\n"
            )

        large_pr = {
            "metadata": {
                "title": "Refactor all handler modules",
                "user": {"login": "contributor-bob"},
                "body": "Mass refactor of handler modules for consistency.",
                "number": 500,
            },
            "diff": "\n".join(diff_parts),
            "files": files,
            "comments": [
                {"body": "Have you tested all 12 modules?", "user": {"login": "reviewer-x"}, "path": ""},
            ],
            "reviews": [
                {"body": "Needs another pass.", "user": {"login": "reviewer-y"}},
            ],
            "linked_issues": [],
        }

        mock_gh = MagicMock()
        mock_gh.fetch_pr_for_review.return_value = large_pr
        mock_gh.__enter__ = MagicMock(return_value=mock_gh)
        mock_gh.__exit__ = MagicMock(return_value=False)
        mock_gh_cls.return_value = mock_gh

        # Generate comments for 3 of the 12 files
        multi_file_review = ReviewOutput(
            summary="The refactor looks consistent. A few modules need attention.",
            inline_comments=[
                InlineComment(
                    file=f"pkg/module{i}/handler.go",
                    hunk_header="@@",
                    diff_snippet=f"+    // module {i} change",
                    comment=f"Module {i}: consider adding error handling.",
                    confidence=0.9 - i * 0.1,
                )
                for i in range(6)
            ],
            abstained_files=[f"pkg/module{i}/handler.go" for i in range(6, 12)],
            abstention_reason="Unfamiliar with these modules",
        )

        # Self-critique keeps the first 4, drops the last 2
        assessments = []
        for i in range(6):
            assessments.append(
                CritiqueItem(
                    comment_index=i,
                    would_reviewer_leave_this="yes" if i < 4 else "no",
                    phrasing_sounds_like_them="yes",
                    already_covered="no",
                )
            )
        critique = CritiqueOutput(assessments=assessments)

        mock_inference.side_effect = [multi_file_review, critique]

        client = TestClient(app)
        resp = client.post(
            "/review",
            data={"pr_url": "https://github.com/octocat/hello-world/pull/500", "profile_name": "sample-reviewer"},
        )

        assert resp.status_code == 200
        html = resp.text
        assert "refactor looks consistent" in html.lower() or "The refactor" in html
        # max_comments=5 in config, 4 passed critique, all above 0.3 threshold
        assert "module0" in html.lower() or "Module 0" in html
