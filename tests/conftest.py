"""Shared fixtures for legit test suite."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from legit.config import LegitConfig, ProfileConfig, ProfileSource
from legit.models import (
    ChunkObservation,
    InlineComment,
    RetrievalDocument,
    ReviewOutput,
)


# ---------------------------------------------------------------------------
# Temporary .legit directory
# ---------------------------------------------------------------------------


@pytest.fixture()
def legit_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a temporary .legit directory structure and patch legit_path() to use it."""
    root = tmp_path / ".legit"
    for subdir in ("profiles", "data", "index", "cache", "calibration"):
        (root / subdir).mkdir(parents=True)

    # Patch the module-level constant and function
    monkeypatch.setattr("legit.config.LEGIT_DIR", str(root))
    monkeypatch.setattr("legit.retrieval.LEGIT_DIR", str(root))

    return root


@pytest.fixture()
def legit_dir_with_config(legit_dir: Path) -> Path:
    """A .legit directory with a valid config.yaml already present."""
    config = {
        "model": {"provider": "gemini"},
        "github": {"token_env": "GITHUB_TOKEN"},
        "profiles": [
            {
                "name": "testuser",
                "sources": [
                    {"type": "primary", "repo": "octocat/hello-world", "username": "testuser"}
                ],
                "chunk_size": 50,
            }
        ],
        "retrieval": {"top_k": 5},
        "review": {"post_to_github": False, "abstention_threshold": 0.5, "max_comments": 10},
    }
    config_path = legit_dir / "config.yaml"
    config_path.write_text(yaml.dump(config, default_flow_style=False))
    return legit_dir


# ---------------------------------------------------------------------------
# Sample config
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_config() -> LegitConfig:
    return LegitConfig(
        profiles=[
            ProfileConfig(
                name="testuser",
                sources=[
                    ProfileSource(type="primary", repo="octocat/hello-world", username="testuser")
                ],
                chunk_size=50,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Sample retrieval documents
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_retrieval_docs() -> list[RetrievalDocument]:
    return [
        RetrievalDocument(
            comment_text="This function should handle the error case explicitly.",
            file_path="pkg/compute/controller.go",
            code_context="func handleRequest() error {",
            comment_type="pr_review",
            severity="suggestion",
            timestamp="2025-01-15T10:00:00Z",
            reviewer_username="testuser",
            pr_number=42,
        ),
        RetrievalDocument(
            comment_text="Nit: prefer using constants for magic numbers.",
            file_path="pkg/compute/utils.go",
            code_context="timeout := 30",
            comment_type="pr_review",
            severity="nit",
            timestamp="2025-02-20T14:30:00Z",
            reviewer_username="testuser",
            pr_number=55,
        ),
        RetrievalDocument(
            comment_text="Good catch on the race condition here.",
            file_path="pkg/api/server.go",
            code_context="mu.Lock()\ndefer mu.Unlock()",
            comment_type="issue_comment",
            severity="unknown",
            timestamp="2024-06-01T08:00:00Z",
            reviewer_username="testuser",
            pr_number=10,
        ),
    ]


# ---------------------------------------------------------------------------
# Mock GitHub data
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_pr_data() -> dict:
    return {
        "metadata": {
            "title": "Fix null pointer in auth handler",
            "user": {"login": "contributor"},
            "body": "This PR fixes #42 and addresses the nil check.\n\nRelated: #99",
            "number": 123,
        },
        "diff": (
            "diff --git a/pkg/auth/handler.go b/pkg/auth/handler.go\n"
            "--- a/pkg/auth/handler.go\n"
            "+++ b/pkg/auth/handler.go\n"
            "@@ -10,6 +10,9 @@ func Authenticate(ctx context.Context) error {\n"
            "+    if user == nil {\n"
            "+        return ErrNullUser\n"
            "+    }\n"
        ),
        "files": [
            {"filename": "pkg/auth/handler.go", "additions": 3, "deletions": 0},
        ],
        "comments": [],
        "reviews": [],
        "linked_issues": [],
    }


# ---------------------------------------------------------------------------
# Mock LLM responses
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_review_output() -> ReviewOutput:
    return ReviewOutput(
        summary="Looks good overall. The nil check is a solid defensive addition.",
        inline_comments=[
            InlineComment(
                file="pkg/auth/handler.go",
                hunk_header="@@ -10,6 +10,9 @@",
                diff_snippet="+    if user == nil {",
                side="addition",
                comment="Consider logging the nil user for debugging.",
                confidence=0.85,
            ),
        ],
        abstained_files=[],
        abstention_reason="",
    )


@pytest.fixture()
def mock_chunk_observation() -> ChunkObservation:
    return ChunkObservation(
        date_range_start="2025-01-01T00:00:00Z",
        date_range_end="2025-03-01T00:00:00Z",
        observations=[
            {
                "situation": "when reviewing error handling",
                "behaviors": ["requests explicit nil checks", "prefers sentinel errors"],
            }
        ],
        representative_quotes=[
            {"quote": "Always handle the nil case", "context": "PR review on auth handler"}
        ],
    )


# ---------------------------------------------------------------------------
# Mock GitHub token
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_github_token(monkeypatch: pytest.MonkeyPatch) -> str:
    token = "ghp_test_token_1234567890"
    monkeypatch.setenv("GITHUB_TOKEN", token)
    return token


# ---------------------------------------------------------------------------
# Sample raw GitHub items (as returned by API)
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_github_items() -> list[dict]:
    """Sample items as they'd appear in a downloaded JSON file."""
    return [
        {
            "body": "Please add error handling here.",
            "path": "pkg/compute/controller.go",
            "diff_hunk": "@@ -5,3 +5,8 @@\n+func doWork() {\n+    result := compute()",
            "created_at": "2025-01-10T12:00:00Z",
            "html_url": "https://github.com/octocat/hello-world/pull/42#discussion_r1",
            "user": {"login": "testuser"},
            "_source_file": "pr_comments.json",
        },
        {
            "body": "This looks good to me, nice refactor.",
            "created_at": "2025-02-15T09:00:00Z",
            "html_url": "https://github.com/octocat/hello-world/issues/50#issuecomment-1",
            "user": {"login": "testuser"},
            "_source_file": "issue_comments.json",
        },
        {
            "body": "",
            "created_at": "2025-03-01T00:00:00Z",
            "user": {"login": "testuser"},
            "_source_file": "pr_comments.json",
        },
    ]
