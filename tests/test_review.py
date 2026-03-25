"""Tests for legit.review — diff parsing, filtering, dry-run output, review pipeline."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from legit.config import LegitConfig, ProfileConfig, ProfileSource, ReviewConfig
from legit.models import InlineComment, ReviewOutput
from legit.review import (
    CritiqueItem,
    CritiqueOutput,
    _apply_filters,
    _build_system_prompt,
    _build_user_prompt,
    _format_dry_run,
    _format_existing_threads,
    _parse_diff_hunks,
    _run_self_critique,
    generate_review,
)


# ---------------------------------------------------------------------------
# _parse_diff_hunks()
# ---------------------------------------------------------------------------


class TestParseDiffHunks:
    def test_single_file(self):
        diff = (
            "diff --git a/pkg/auth/handler.go b/pkg/auth/handler.go\n"
            "--- a/pkg/auth/handler.go\n"
            "+++ b/pkg/auth/handler.go\n"
            "@@ -10,6 +10,9 @@ func Authenticate(ctx context.Context) error {\n"
            "+    if user == nil {\n"
            "+        return ErrNullUser\n"
            "+    }\n"
            " unchanged line\n"
        )
        hunks = _parse_diff_hunks(diff)
        assert len(hunks) == 1
        assert hunks[0]["file_path"] == "pkg/auth/handler.go"
        assert "+    if user == nil {" in hunks[0]["content"]
        # unchanged lines should not be included
        assert " unchanged line" not in hunks[0]["content"]

    def test_multiple_files(self):
        diff = (
            "diff --git a/file1.go b/file1.go\n"
            "--- a/file1.go\n"
            "+++ b/file1.go\n"
            "@@ -1,3 +1,5 @@\n"
            "+new line in file1\n"
            "diff --git a/file2.go b/file2.go\n"
            "--- a/file2.go\n"
            "+++ b/file2.go\n"
            "@@ -5,3 +5,4 @@\n"
            "+new line in file2\n"
            "-removed line in file2\n"
        )
        hunks = _parse_diff_hunks(diff)
        assert len(hunks) == 2
        assert hunks[0]["file_path"] == "file1.go"
        assert hunks[1]["file_path"] == "file2.go"
        assert "+new line in file1" in hunks[0]["content"]
        assert "-removed line in file2" in hunks[1]["content"]

    def test_empty_diff(self):
        assert _parse_diff_hunks("") == []

    def test_diff_with_hunk_headers(self):
        diff = (
            "diff --git a/main.py b/main.py\n"
            "--- a/main.py\n"
            "+++ b/main.py\n"
            "@@ -1,3 +1,5 @@ class Main:\n"
            "+    pass\n"
            "@@ -10,3 +12,5 @@ def method():\n"
            "-    old_code()\n"
            "+    new_code()\n"
        )
        hunks = _parse_diff_hunks(diff)
        assert len(hunks) == 1
        content = hunks[0]["content"]
        # Both hunk headers should be included
        assert content.count("@@") >= 2

    def test_nested_file_path(self):
        diff = (
            "diff --git a/src/pkg/deep/nested/file.go b/src/pkg/deep/nested/file.go\n"
            "+++ b/src/pkg/deep/nested/file.go\n"
            "@@ -1 +1 @@\n"
            "+changed\n"
        )
        hunks = _parse_diff_hunks(diff)
        assert hunks[0]["file_path"] == "src/pkg/deep/nested/file.go"


# ---------------------------------------------------------------------------
# _format_dry_run()
# ---------------------------------------------------------------------------


class TestFormatDryRun:
    def test_basic_review(self):
        review = ReviewOutput(
            summary="Looks good overall.",
            inline_comments=[
                InlineComment(
                    file="main.py",
                    hunk_header="@@",
                    diff_snippet="+    new_code()",
                    comment="Consider adding a docstring.",
                    confidence=0.85,
                ),
            ],
        )
        result = _format_dry_run(review, "alice")
        assert "# Review by alice" in result
        assert "## Summary" in result
        assert "Looks good overall." in result
        assert "## Inline Comments" in result
        assert "main.py" in result
        assert "0.85" in result
        assert "> +    new_code()" in result
        assert "Consider adding a docstring." in result

    def test_no_comments(self):
        review = ReviewOutput(summary="LGTM")
        result = _format_dry_run(review, "bob")
        assert "no comments" in result.lower()

    def test_with_abstentions(self):
        review = ReviewOutput(
            summary="Partial review.",
            abstained_files=["vendor/lib.go", "generated.pb.go"],
            abstention_reason="Generated code",
        )
        result = _format_dry_run(review, "charlie")
        assert "## Abstained Files" in result
        assert "`vendor/lib.go`" in result
        assert "Generated code" in result

    def test_multiline_diff_snippet(self):
        review = ReviewOutput(
            summary="Test",
            inline_comments=[
                InlineComment(
                    file="a.py",
                    hunk_header="@@",
                    diff_snippet="+line1\n+line2\n+line3",
                    comment="nit",
                    confidence=0.9,
                ),
            ],
        )
        result = _format_dry_run(review, "dave")
        # Each line should be blockquoted
        assert "> +line1" in result
        assert "> +line2" in result
        assert "> +line3" in result


# ---------------------------------------------------------------------------
# _format_existing_threads()
# ---------------------------------------------------------------------------


class TestFormatExistingThreads:
    def test_empty(self):
        result = _format_existing_threads([], [])
        assert "No existing review comments" in result

    def test_with_reviews(self):
        reviews = [{"body": "LGTM", "user": {"login": "reviewer1"}}]
        result = _format_existing_threads([], reviews)
        assert "[reviewer1]" in result
        assert "LGTM" in result

    def test_with_comments(self):
        comments = [
            {"body": "Fix this typo", "user": {"login": "reviewer2"}, "path": "main.py"}
        ]
        result = _format_existing_threads(comments, [])
        assert "[reviewer2]" in result
        assert "(main.py)" in result
        assert "Fix this typo" in result

    def test_empty_body_skipped(self):
        reviews = [{"body": "", "user": {"login": "reviewer1"}}]
        result = _format_existing_threads([], reviews)
        assert "No existing review comments" in result


# ---------------------------------------------------------------------------
# Self-critique filtering
# ---------------------------------------------------------------------------


class TestSelfCritique:
    @patch("legit.review.run_inference")
    def test_drops_unwanted_comments(self, mock_inference: MagicMock):
        review = ReviewOutput(
            summary="Test",
            inline_comments=[
                InlineComment(file="a.py", hunk_header="@@", diff_snippet="+x", comment="Good comment", confidence=0.9),
                InlineComment(file="b.py", hunk_header="@@", diff_snippet="+y", comment="Bad comment", confidence=0.8),
                InlineComment(file="c.py", hunk_header="@@", diff_snippet="+z", comment="Covered comment", confidence=0.7),
            ],
        )

        critique = CritiqueOutput(
            assessments=[
                CritiqueItem(comment_index=0, would_reviewer_leave_this="yes", phrasing_sounds_like_them="yes", already_covered="no"),
                CritiqueItem(comment_index=1, would_reviewer_leave_this="no", phrasing_sounds_like_them="close", already_covered="no"),
                CritiqueItem(comment_index=2, would_reviewer_leave_this="probably", phrasing_sounds_like_them="yes", already_covered="yes"),
            ]
        )
        mock_inference.return_value = critique

        config = LegitConfig()
        result = _run_self_critique(config, review, "examples", "threads")

        # Comment 1 dropped (wouldn't leave), Comment 2 dropped (already covered)
        assert len(result.inline_comments) == 1
        assert result.inline_comments[0].comment == "Good comment"

    @patch("legit.review.run_inference")
    def test_keeps_all_when_no_drops(self, mock_inference: MagicMock):
        review = ReviewOutput(
            summary="Test",
            inline_comments=[
                InlineComment(file="a.py", hunk_header="@@", diff_snippet="+x", comment="OK", confidence=0.9),
            ],
        )
        critique = CritiqueOutput(
            assessments=[
                CritiqueItem(comment_index=0, would_reviewer_leave_this="yes", phrasing_sounds_like_them="yes", already_covered="no"),
            ]
        )
        mock_inference.return_value = critique
        config = LegitConfig()
        result = _run_self_critique(config, review, "", "")
        assert len(result.inline_comments) == 1

    def test_empty_comments_skips_critique(self):
        review = ReviewOutput(summary="LGTM")
        config = LegitConfig()
        # Should return as-is without calling LLM
        result = _run_self_critique(config, review, "", "")
        assert result.summary == "LGTM"

    @patch("legit.review.run_inference")
    def test_raw_text_response_keeps_all(self, mock_inference: MagicMock):
        review = ReviewOutput(
            summary="Test",
            inline_comments=[
                InlineComment(file="a.py", hunk_header="@@", diff_snippet="+x", comment="OK", confidence=0.9),
            ],
        )
        mock_inference.return_value = "raw text not structured"
        config = LegitConfig()
        result = _run_self_critique(config, review, "", "")
        assert len(result.inline_comments) == 1


# ---------------------------------------------------------------------------
# Confidence threshold and max_comments
# ---------------------------------------------------------------------------


class TestApplyFilters:
    def test_confidence_threshold(self):
        review = ReviewOutput(
            summary="Test",
            inline_comments=[
                InlineComment(file="a.py", hunk_header="@@", diff_snippet="+x", comment="High", confidence=0.9),
                InlineComment(file="b.py", hunk_header="@@", diff_snippet="+y", comment="Low", confidence=0.3),
                InlineComment(file="c.py", hunk_header="@@", diff_snippet="+z", comment="Mid", confidence=0.5),
            ],
        )
        config = LegitConfig(review=ReviewConfig(abstention_threshold=0.5))
        result = _apply_filters(review, config)
        assert len(result.inline_comments) == 2
        assert all(c.confidence >= 0.5 for c in result.inline_comments)

    def test_max_comments_cap(self):
        comments = [
            InlineComment(file=f"f{i}.py", hunk_header="@@", diff_snippet=f"+{i}", comment=f"C{i}", confidence=0.9 - i * 0.05)
            for i in range(10)
        ]
        review = ReviewOutput(summary="Test", inline_comments=comments)
        config = LegitConfig(review=ReviewConfig(max_comments=3, abstention_threshold=0.0))
        result = _apply_filters(review, config)
        assert len(result.inline_comments) == 3
        # Should keep the highest confidence ones
        confidences = [c.confidence for c in result.inline_comments]
        assert confidences == sorted(confidences, reverse=True)

    def test_threshold_and_cap_combined(self):
        comments = [
            InlineComment(file="a.py", hunk_header="@@", diff_snippet="+a", comment="A", confidence=0.9),
            InlineComment(file="b.py", hunk_header="@@", diff_snippet="+b", comment="B", confidence=0.8),
            InlineComment(file="c.py", hunk_header="@@", diff_snippet="+c", comment="C", confidence=0.2),
            InlineComment(file="d.py", hunk_header="@@", diff_snippet="+d", comment="D", confidence=0.7),
        ]
        review = ReviewOutput(summary="Test", inline_comments=comments)
        config = LegitConfig(review=ReviewConfig(abstention_threshold=0.5, max_comments=2))
        result = _apply_filters(review, config)
        # Threshold removes C (0.2), then cap to 2 from [A(0.9), B(0.8), D(0.7)]
        assert len(result.inline_comments) == 2
        assert result.inline_comments[0].confidence == 0.9
        assert result.inline_comments[1].confidence == 0.8

    def test_no_filters(self):
        comments = [
            InlineComment(file="a.py", hunk_header="@@", diff_snippet="+a", comment="A", confidence=0.1),
        ]
        review = ReviewOutput(summary="Test", inline_comments=comments)
        config = LegitConfig(review=ReviewConfig(abstention_threshold=0.0, max_comments=None))
        result = _apply_filters(review, config)
        assert len(result.inline_comments) == 1

    def test_preserves_summary_and_abstentions(self):
        review = ReviewOutput(
            summary="My summary",
            inline_comments=[
                InlineComment(file="a.py", hunk_header="@@", diff_snippet="+a", comment="A", confidence=0.9),
            ],
            abstained_files=["vendor/"],
            abstention_reason="vendor code",
        )
        config = LegitConfig()
        result = _apply_filters(review, config)
        assert result.summary == "My summary"
        assert result.abstained_files == ["vendor/"]
        assert result.abstention_reason == "vendor code"


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


class TestPromptConstruction:
    def test_system_prompt_includes_profile(self):
        result = _build_system_prompt("alice", "# Alice's Profile", "Example comments")
        assert "alice" in result
        assert "# Alice's Profile" in result
        assert "Example comments" in result

    def test_system_prompt_no_examples(self):
        result = _build_system_prompt("bob", "# Profile", "")
        assert "No past examples available" in result

    def test_user_prompt_includes_pr_data(self, mock_pr_data: dict):
        result = _build_user_prompt("alice", mock_pr_data)
        assert "Fix null pointer in auth handler" in result
        assert "contributor" in result
        assert "pkg/auth/handler.go" in result


# ---------------------------------------------------------------------------
# generate_review() end-to-end (fully mocked)
# ---------------------------------------------------------------------------


class TestGenerateReview:
    @patch("legit.review.retrieve")
    @patch("legit.review.GitHubClient")
    @patch("legit.review.get_token", return_value="ghp_test")
    @patch("legit.review.load_profile", return_value="# Test Profile")
    @patch("legit.review.run_inference")
    def test_dry_run(
        self,
        mock_inference: MagicMock,
        mock_load_profile: MagicMock,
        mock_get_token: MagicMock,
        mock_gh_client_cls: MagicMock,
        mock_retrieve: MagicMock,
        mock_pr_data: dict,
        mock_review_output: ReviewOutput,
    ):
        # Setup mocks
        mock_gh = MagicMock()
        mock_gh.fetch_pr_for_review.return_value = mock_pr_data
        mock_gh.__enter__ = MagicMock(return_value=mock_gh)
        mock_gh.__exit__ = MagicMock(return_value=False)
        mock_gh_client_cls.return_value = mock_gh

        mock_retrieve.return_value = []

        # First call = generate review, second call = self-critique
        critique = CritiqueOutput(
            assessments=[
                CritiqueItem(
                    comment_index=0,
                    would_reviewer_leave_this="yes",
                    phrasing_sounds_like_them="yes",
                    already_covered="no",
                ),
            ]
        )
        mock_inference.side_effect = [mock_review_output, critique]

        config = LegitConfig(
            profiles=[
                ProfileConfig(
                    name="testuser",
                    sources=[ProfileSource(repo="o/r", username="testuser")],
                )
            ],
        )

        result = generate_review(
            config=config,
            profile_name="testuser",
            pr_url="https://github.com/o/r/pull/123",
            dry_run=True,
        )

        assert isinstance(result, ReviewOutput)
        assert result.summary != ""
        mock_load_profile.assert_called_once_with("testuser")
        mock_gh.fetch_pr_for_review.assert_called_once()
