"""Integration tests for the review generation pipeline.

These tests exercise the full pipeline: config loading → profile loading →
BM25 retrieval → prompt construction → (mocked) LLM inference → self-critique
→ filtering → output. Only the LLM and GitHub API are mocked.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from legit.config import LegitConfig, load_config
from legit.models import InlineComment, RetrievalDocument, ReviewOutput
from legit.retrieval import build_index, retrieve, construct_queries, format_examples
from legit.review import (
    CritiqueItem,
    CritiqueOutput,
    _build_system_prompt,
    _build_user_prompt,
    _parse_diff_hunks,
    generate_review,
    load_profile,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def integration_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fully wired .legit directory suitable for pipeline integration tests."""
    root = tmp_path / ".legit"
    for subdir in ("profiles", "data", "index", "cache", "calibration"):
        (root / subdir).mkdir(parents=True)

    # Config with two profiles
    config = {
        "model": {"provider": "gemini", "temperature": 0.3},
        "github": {"token_env": "GITHUB_TOKEN"},
        "profiles": [
            {
                "name": "alice-reviewer",
                "sources": [
                    {"type": "primary", "repo": "acme/webapp", "username": "alice"}
                ],
                "chunk_size": 20,
                "temporal_half_life": 365,
            },
            {
                "name": "bob-reviewer",
                "sources": [
                    {"type": "primary", "repo": "acme/api", "username": "bob"}
                ],
                "chunk_size": 20,
            },
        ],
        "retrieval": {"top_k": 5, "type_weights": {"pr_review": 2.0, "issue_comment": 1.0}},
        "review": {"post_to_github": False, "abstention_threshold": 0.4, "max_comments": 8},
    }
    (root / "config.yaml").write_text(yaml.dump(config, default_flow_style=False))

    # Alice's profile
    (root / "profiles" / "alice-reviewer.md").write_text(
        "# Reviewer Profile: alice-reviewer\n\n"
        "## Style Summary\n"
        "Alice is meticulous about error handling, logging, and test coverage.\n"
        "She uses a direct, collaborative tone and frequently asks clarifying questions.\n\n"
        "## Common Patterns\n"
        "- Always requests explicit error handling for nil/null cases\n"
        "- Prefers structured logging over fmt.Printf\n"
        "- Flags missing test cases for edge conditions\n"
        "- Uses 'nit:' prefix for style-only feedback\n"
        "- Often suggests extracting helpers for repeated logic\n\n"
        "## Representative Quotes\n"
        '- "Can we add a test for the empty-input case?"\n'
        '- "nit: I\'d prefer structured logging here"\n'
        '- "What happens if ctx is already cancelled at this point?"\n'
    )

    # Bob's profile
    (root / "profiles" / "bob-reviewer.md").write_text(
        "# Reviewer Profile: bob-reviewer\n\n"
        "## Style Summary\n"
        "Bob focuses on API design, backwards compatibility, and performance.\n"
    )

    monkeypatch.setattr("legit.config.LEGIT_DIR", str(root))
    monkeypatch.setattr("legit.retrieval.LEGIT_DIR", str(root))
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_integration_test_token")

    return root


@pytest.fixture()
def alice_retrieval_docs() -> list[RetrievalDocument]:
    """Realistic retrieval documents for alice-reviewer."""
    return [
        RetrievalDocument(
            comment_text="Can we add a test for the empty-input case? This seems like it could panic.",
            file_path="pkg/handler/auth.go",
            code_context="func HandleAuth(ctx context.Context, req *Request) (*Response, error) {",
            comment_type="pr_review",
            severity="suggestion",
            timestamp="2025-08-10T09:00:00Z",
            reviewer_username="alice",
            pr_number=100,
        ),
        RetrievalDocument(
            comment_text="nit: I'd prefer structured logging here instead of fmt.Printf.",
            file_path="pkg/handler/auth.go",
            code_context='fmt.Printf("auth failed: %v", err)',
            comment_type="pr_review",
            severity="nit",
            timestamp="2025-08-10T09:05:00Z",
            reviewer_username="alice",
            pr_number=100,
        ),
        RetrievalDocument(
            comment_text="What happens if ctx is already cancelled at this point? We should check ctx.Err() first.",
            file_path="pkg/middleware/timeout.go",
            code_context="select {\ncase <-ctx.Done():",
            comment_type="pr_review",
            severity="concern",
            timestamp="2025-09-15T14:20:00Z",
            reviewer_username="alice",
            pr_number=150,
        ),
        RetrievalDocument(
            comment_text="This error message isn't helpful for debugging. Can we include the request ID?",
            file_path="pkg/api/errors.go",
            code_context='return fmt.Errorf("request failed")',
            comment_type="pr_review",
            severity="suggestion",
            timestamp="2025-10-01T11:00:00Z",
            reviewer_username="alice",
            pr_number=175,
        ),
        RetrievalDocument(
            comment_text="Nice cleanup! The extraction of this helper makes the flow much clearer.",
            file_path="pkg/handler/utils.go",
            code_context="func extractUserID(claims *Claims) (string, error) {",
            comment_type="pr_review",
            severity="praise",
            timestamp="2025-11-05T16:30:00Z",
            reviewer_username="alice",
            pr_number=200,
        ),
    ]


SAMPLE_PR = {
    "metadata": {
        "title": "Add request validation middleware",
        "user": {"login": "dev-charlie"},
        "body": (
            "This PR adds input validation middleware that checks request bodies "
            "before they reach handlers.\n\n"
            "## Changes\n"
            "- New `ValidateRequest` middleware\n"
            "- Validation rules from struct tags\n"
            "- Error responses with field-level details\n\n"
            "Closes #250"
        ),
        "number": 300,
    },
    "diff": (
        "diff --git a/pkg/middleware/validate.go b/pkg/middleware/validate.go\n"
        "--- /dev/null\n"
        "+++ b/pkg/middleware/validate.go\n"
        "@@ -0,0 +1,45 @@ \n"
        "+package middleware\n"
        "+\n"
        "+import (\n"
        '+    "encoding/json"\n'
        '+    "net/http"\n'
        "+)\n"
        "+\n"
        "+// ValidateRequest checks the request body against struct tag rules.\n"
        "+func ValidateRequest(next http.Handler) http.Handler {\n"
        "+    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {\n"
        "+        if r.Body == nil {\n"
        "+            http.Error(w, \"missing request body\", http.StatusBadRequest)\n"
        "+            return\n"
        "+        }\n"
        "+        var body map[string]interface{}\n"
        "+        if err := json.NewDecoder(r.Body).Decode(&body); err != nil {\n"
        "+            http.Error(w, \"invalid JSON\", http.StatusBadRequest)\n"
        "+            return\n"
        "+        }\n"
        "+        // TODO: validate fields\n"
        "+        next.ServeHTTP(w, r)\n"
        "+    })\n"
        "+}\n"
        "diff --git a/pkg/middleware/validate_test.go b/pkg/middleware/validate_test.go\n"
        "--- /dev/null\n"
        "+++ b/pkg/middleware/validate_test.go\n"
        "@@ -0,0 +1,20 @@\n"
        "+package middleware\n"
        "+\n"
        "+import \"testing\"\n"
        "+\n"
        "+func TestValidateRequest_NilBody(t *testing.T) {\n"
        "+    // test nil body returns 400\n"
        "+}\n"
    ),
    "files": [
        {"filename": "pkg/middleware/validate.go", "additions": 25, "deletions": 0},
        {"filename": "pkg/middleware/validate_test.go", "additions": 8, "deletions": 0},
    ],
    "comments": [],
    "reviews": [],
    "linked_issues": [{"number": 250, "title": "Add request body validation"}],
}


# ---------------------------------------------------------------------------
# Config loading integration
# ---------------------------------------------------------------------------


class TestConfigIntegration:
    def test_load_config_from_disk(self, integration_env: Path):
        cfg = load_config(integration_env / "config.yaml")
        assert len(cfg.profiles) == 2
        assert cfg.profiles[0].name == "alice-reviewer"
        assert cfg.profiles[1].name == "bob-reviewer"
        assert cfg.retrieval.top_k == 5
        assert cfg.review.abstention_threshold == 0.4

    def test_profile_loading(self, integration_env: Path):
        profile_text = load_profile("alice-reviewer")
        assert "alice-reviewer" in profile_text
        assert "meticulous about error handling" in profile_text
        assert "nit:" in profile_text


# ---------------------------------------------------------------------------
# BM25 index build + retrieval integration
# ---------------------------------------------------------------------------


class TestRetrievalIntegration:
    def test_build_and_query_index(self, integration_env: Path, alice_retrieval_docs: list[RetrievalDocument]):
        # Build index from documents
        index_path = build_index("alice-reviewer", alice_retrieval_docs)
        assert index_path.exists()

        # Parse a diff to get queries
        hunks = _parse_diff_hunks(SAMPLE_PR["diff"])
        assert len(hunks) == 2  # validate.go and validate_test.go

        queries = construct_queries(hunks)
        assert len(queries) > 0

        # Retrieve with the built index
        results = retrieve(
            profile_name="alice-reviewer",
            queries=queries,
            top_k=3,
            type_weights={"pr_review": 2.0, "issue_comment": 1.0},
            temporal_half_life=365,
            pr_changed_files=["pkg/middleware/validate.go", "pkg/middleware/validate_test.go"],
        )

        assert len(results) > 0
        assert len(results) <= 3
        # All results should be RetrievalDocuments
        for doc in results:
            assert isinstance(doc, RetrievalDocument)
            assert doc.comment_text != ""

    def test_format_examples_roundtrip(self, integration_env: Path, alice_retrieval_docs: list[RetrievalDocument]):
        build_index("alice-reviewer", alice_retrieval_docs)

        hunks = _parse_diff_hunks(SAMPLE_PR["diff"])
        queries = construct_queries(hunks)
        results = retrieve(
            profile_name="alice-reviewer",
            queries=queries,
            top_k=5,
            type_weights={"pr_review": 2.0},
            temporal_half_life=365,
        )

        examples_text = format_examples(results)
        assert isinstance(examples_text, str)
        # Should contain actual comment content from retrieved docs
        if results:
            assert results[0].comment_text[:30] in examples_text


# ---------------------------------------------------------------------------
# Prompt construction integration
# ---------------------------------------------------------------------------


class TestPromptIntegration:
    def test_full_prompt_construction(self, integration_env: Path, alice_retrieval_docs: list[RetrievalDocument]):
        profile_text = load_profile("alice-reviewer")
        examples_text = format_examples(alice_retrieval_docs[:3])

        system = _build_system_prompt("alice-reviewer", profile_text, examples_text)
        user = _build_user_prompt("alice-reviewer", SAMPLE_PR)

        # System prompt should contain profile + examples
        assert "alice-reviewer" in system
        assert "meticulous" in system
        assert "empty-input case" in system  # from example doc

        # User prompt should contain PR data
        assert "Add request validation middleware" in user
        assert "dev-charlie" in user
        assert "pkg/middleware/validate.go" in user
        assert "ValidateRequest" in user

    def test_prompt_with_existing_threads(self, integration_env: Path):
        pr_data_with_threads = {**SAMPLE_PR}
        pr_data_with_threads["comments"] = [
            {"body": "Have you considered using a validation library?", "user": {"login": "reviewer-x"}, "path": "pkg/middleware/validate.go"},
        ]
        pr_data_with_threads["reviews"] = [
            {"body": "Needs tests for malformed JSON.", "user": {"login": "reviewer-y"}},
        ]

        user = _build_user_prompt("alice-reviewer", pr_data_with_threads)
        assert "validation library" in user
        assert "malformed JSON" in user
        assert "reviewer-x" in user or "reviewer-y" in user


# ---------------------------------------------------------------------------
# Full pipeline integration (LLM + GitHub mocked)
# ---------------------------------------------------------------------------


class TestFullPipeline:
    @patch("legit.review.run_inference")
    @patch("legit.review.GitHubClient")
    def test_end_to_end_dry_run(
        self,
        mock_gh_cls: MagicMock,
        mock_inference: MagicMock,
        integration_env: Path,
        alice_retrieval_docs: list[RetrievalDocument],
    ):
        # Build the BM25 index so retrieval works
        build_index("alice-reviewer", alice_retrieval_docs)

        # Mock GitHub
        mock_gh = MagicMock()
        mock_gh.fetch_pr_for_review.return_value = SAMPLE_PR
        mock_gh.__enter__ = MagicMock(return_value=mock_gh)
        mock_gh.__exit__ = MagicMock(return_value=False)
        mock_gh_cls.return_value = mock_gh

        # Mock LLM: review generation + self-critique
        review = ReviewOutput(
            summary="The validation middleware is a good addition. A few things to tighten up.",
            inline_comments=[
                InlineComment(
                    file="pkg/middleware/validate.go",
                    hunk_header="@@ -0,0 +1,45 @@",
                    diff_snippet="+        // TODO: validate fields",
                    side="addition",
                    comment="This TODO should be resolved before merging — the validation is the whole point of the PR.",
                    confidence=0.95,
                ),
                InlineComment(
                    file="pkg/middleware/validate.go",
                    hunk_header="@@ -0,0 +1,45 @@",
                    diff_snippet='+            http.Error(w, "invalid JSON", http.StatusBadRequest)',
                    side="addition",
                    comment="Can we include the parse error in the response? It helps callers fix their payloads.",
                    confidence=0.80,
                ),
                InlineComment(
                    file="pkg/middleware/validate_test.go",
                    hunk_header="@@ -0,0 +1,20 @@",
                    diff_snippet="+func TestValidateRequest_NilBody(t *testing.T) {",
                    side="addition",
                    comment="This test has no assertions yet. Can we add the actual test logic?",
                    confidence=0.88,
                ),
            ],
            abstained_files=[],
            abstention_reason="",
        )

        critique = CritiqueOutput(
            assessments=[
                CritiqueItem(comment_index=0, would_reviewer_leave_this="yes", phrasing_sounds_like_them="yes", already_covered="no"),
                CritiqueItem(comment_index=1, would_reviewer_leave_this="yes", phrasing_sounds_like_them="yes", already_covered="no"),
                CritiqueItem(comment_index=2, would_reviewer_leave_this="yes", phrasing_sounds_like_them="close", already_covered="no"),
            ]
        )
        mock_inference.side_effect = [review, critique]

        cfg = load_config(integration_env / "config.yaml")
        result = generate_review(
            config=cfg,
            profile_name="alice-reviewer",
            pr_url="https://github.com/acme/webapp/pull/300",
            dry_run=True,
        )

        # Verify the full pipeline output
        assert isinstance(result, ReviewOutput)
        assert "validation middleware" in result.summary.lower() or "good addition" in result.summary.lower()
        assert len(result.inline_comments) == 3  # all passed critique + above 0.4 threshold
        assert result.inline_comments[0].confidence == 0.95

        # Verify LLM was called twice (review + critique)
        assert mock_inference.call_count == 2

        # Verify GitHub client was used
        mock_gh.fetch_pr_for_review.assert_called_once()

    @patch("legit.review.run_inference")
    @patch("legit.review.GitHubClient")
    def test_pipeline_with_output_file(
        self,
        mock_gh_cls: MagicMock,
        mock_inference: MagicMock,
        integration_env: Path,
        alice_retrieval_docs: list[RetrievalDocument],
        tmp_path: Path,
    ):
        build_index("alice-reviewer", alice_retrieval_docs)

        mock_gh = MagicMock()
        mock_gh.fetch_pr_for_review.return_value = SAMPLE_PR
        mock_gh.__enter__ = MagicMock(return_value=mock_gh)
        mock_gh.__exit__ = MagicMock(return_value=False)
        mock_gh_cls.return_value = mock_gh

        review = ReviewOutput(
            summary="LGTM with minor suggestions.",
            inline_comments=[
                InlineComment(
                    file="pkg/middleware/validate.go",
                    hunk_header="@@",
                    diff_snippet="+func ValidateRequest",
                    comment="Consider adding a content-type check.",
                    confidence=0.75,
                ),
            ],
        )
        critique = CritiqueOutput(
            assessments=[
                CritiqueItem(comment_index=0, would_reviewer_leave_this="yes", phrasing_sounds_like_them="yes", already_covered="no"),
            ]
        )
        mock_inference.side_effect = [review, critique]

        output_file = tmp_path / "review_output.md"
        cfg = load_config(integration_env / "config.yaml")

        result = generate_review(
            config=cfg,
            profile_name="alice-reviewer",
            pr_url="https://github.com/acme/webapp/pull/300",
            dry_run=True,
            output_path=output_file,
        )

        assert output_file.exists()
        content = output_file.read_text()
        assert "Review by alice-reviewer" in content
        assert "LGTM" in content
        assert "content-type check" in content

    @patch("legit.review.run_inference")
    @patch("legit.review.GitHubClient")
    def test_pipeline_filters_low_confidence(
        self,
        mock_gh_cls: MagicMock,
        mock_inference: MagicMock,
        integration_env: Path,
        alice_retrieval_docs: list[RetrievalDocument],
    ):
        """Verify that the threshold filter (0.4) removes low-confidence comments."""
        build_index("alice-reviewer", alice_retrieval_docs)

        mock_gh = MagicMock()
        mock_gh.fetch_pr_for_review.return_value = SAMPLE_PR
        mock_gh.__enter__ = MagicMock(return_value=mock_gh)
        mock_gh.__exit__ = MagicMock(return_value=False)
        mock_gh_cls.return_value = mock_gh

        review = ReviewOutput(
            summary="Mixed confidence review.",
            inline_comments=[
                InlineComment(file="a.go", hunk_header="@@", diff_snippet="+x", comment="High", confidence=0.9),
                InlineComment(file="b.go", hunk_header="@@", diff_snippet="+y", comment="Low", confidence=0.2),
                InlineComment(file="c.go", hunk_header="@@", diff_snippet="+z", comment="Borderline", confidence=0.4),
            ],
        )
        critique = CritiqueOutput(
            assessments=[
                CritiqueItem(comment_index=0, would_reviewer_leave_this="yes", phrasing_sounds_like_them="yes", already_covered="no"),
                CritiqueItem(comment_index=1, would_reviewer_leave_this="yes", phrasing_sounds_like_them="yes", already_covered="no"),
                CritiqueItem(comment_index=2, would_reviewer_leave_this="yes", phrasing_sounds_like_them="yes", already_covered="no"),
            ]
        )
        mock_inference.side_effect = [review, critique]

        cfg = load_config(integration_env / "config.yaml")
        result = generate_review(
            config=cfg,
            profile_name="alice-reviewer",
            pr_url="https://github.com/acme/webapp/pull/300",
            dry_run=True,
        )

        # 0.2 should be filtered (below 0.4), 0.4 and 0.9 should remain
        assert len(result.inline_comments) == 2
        confidences = [c.confidence for c in result.inline_comments]
        assert 0.2 not in confidences
        assert 0.9 in confidences
        assert 0.4 in confidences

    @patch("legit.review.run_inference")
    @patch("legit.review.GitHubClient")
    def test_pipeline_max_comments_cap(
        self,
        mock_gh_cls: MagicMock,
        mock_inference: MagicMock,
        integration_env: Path,
        alice_retrieval_docs: list[RetrievalDocument],
    ):
        """Verify max_comments config caps the output (set to 8 in fixture config)."""
        build_index("alice-reviewer", alice_retrieval_docs)

        mock_gh = MagicMock()
        mock_gh.fetch_pr_for_review.return_value = SAMPLE_PR
        mock_gh.__enter__ = MagicMock(return_value=mock_gh)
        mock_gh.__exit__ = MagicMock(return_value=False)
        mock_gh_cls.return_value = mock_gh

        # Generate 15 comments, all high confidence
        comments = [
            InlineComment(
                file=f"f{i}.go", hunk_header="@@", diff_snippet=f"+line{i}",
                comment=f"Comment {i}", confidence=0.95 - i * 0.01,
            )
            for i in range(15)
        ]
        review = ReviewOutput(summary="Many comments.", inline_comments=comments)

        # Critique keeps all
        assessments = [
            CritiqueItem(comment_index=i, would_reviewer_leave_this="yes", phrasing_sounds_like_them="yes", already_covered="no")
            for i in range(15)
        ]
        critique = CritiqueOutput(assessments=assessments)
        mock_inference.side_effect = [review, critique]

        cfg = load_config(integration_env / "config.yaml")
        result = generate_review(
            config=cfg,
            profile_name="alice-reviewer",
            pr_url="https://github.com/acme/webapp/pull/300",
            dry_run=True,
        )

        # max_comments=8 in config
        assert len(result.inline_comments) <= 8
        # Should keep the highest confidence ones
        for i in range(len(result.inline_comments) - 1):
            assert result.inline_comments[i].confidence >= result.inline_comments[i + 1].confidence


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestPipelineEdgeCases:
    @patch("legit.review.run_inference")
    @patch("legit.review.GitHubClient")
    def test_empty_diff_pr(
        self,
        mock_gh_cls: MagicMock,
        mock_inference: MagicMock,
        integration_env: Path,
        alice_retrieval_docs: list[RetrievalDocument],
    ):
        build_index("alice-reviewer", alice_retrieval_docs)

        empty_pr = {
            "metadata": {
                "title": "Update README",
                "user": {"login": "dev"},
                "body": "Just a README update.",
                "number": 1,
            },
            "diff": "",
            "files": [{"filename": "README.md", "additions": 1, "deletions": 1}],
            "comments": [],
            "reviews": [],
            "linked_issues": [],
        }

        mock_gh = MagicMock()
        mock_gh.fetch_pr_for_review.return_value = empty_pr
        mock_gh.__enter__ = MagicMock(return_value=mock_gh)
        mock_gh.__exit__ = MagicMock(return_value=False)
        mock_gh_cls.return_value = mock_gh

        review = ReviewOutput(summary="LGTM — just a docs change.")
        mock_inference.side_effect = [review]  # No critique needed (no comments)

        cfg = load_config(integration_env / "config.yaml")
        result = generate_review(
            config=cfg,
            profile_name="alice-reviewer",
            pr_url="https://github.com/acme/webapp/pull/1",
            dry_run=True,
        )

        assert result.summary == "LGTM — just a docs change."
        assert len(result.inline_comments) == 0

    @patch("legit.review.run_inference")
    @patch("legit.review.GitHubClient")
    def test_llm_returns_raw_text_fallback(
        self,
        mock_gh_cls: MagicMock,
        mock_inference: MagicMock,
        integration_env: Path,
        alice_retrieval_docs: list[RetrievalDocument],
    ):
        """When the LLM returns raw text instead of structured output, pipeline should gracefully fallback."""
        build_index("alice-reviewer", alice_retrieval_docs)

        mock_gh = MagicMock()
        mock_gh.fetch_pr_for_review.return_value = SAMPLE_PR
        mock_gh.__enter__ = MagicMock(return_value=mock_gh)
        mock_gh.__exit__ = MagicMock(return_value=False)
        mock_gh_cls.return_value = mock_gh

        # LLM returns a plain string instead of ReviewOutput
        mock_inference.return_value = "This looks fine to me, no major issues."

        cfg = load_config(integration_env / "config.yaml")
        result = generate_review(
            config=cfg,
            profile_name="alice-reviewer",
            pr_url="https://github.com/acme/webapp/pull/300",
            dry_run=True,
        )

        assert isinstance(result, ReviewOutput)
        assert "fine to me" in result.summary or "no major issues" in result.summary
        assert len(result.inline_comments) == 0
