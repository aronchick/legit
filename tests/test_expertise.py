"""Tests for legit.expertise — codebase expertise index."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from legit.expertise import (
    ExpertiseEntry,
    ExpertiseIndex,
    build_expertise_index,
    classify_severity,
    format_expertise_context,
    load_expertise_index,
    lookup_expertise,
    save_expertise_index,
)


# ---------------------------------------------------------------------------
# Severity classification
# ---------------------------------------------------------------------------


class TestClassifySeverity:
    def test_nit(self):
        assert classify_severity("nit: use consistent naming") == "nit"

    def test_nit_colon(self):
        assert classify_severity("Nit: capitalize this") == "nit"

    def test_blocking(self):
        assert classify_severity("This is a blocking issue, do not merge") == "blocking"

    def test_question(self):
        assert classify_severity("Have you considered using a map here?") == "question"

    def test_praise(self):
        assert classify_severity("Nice refactor, this is much cleaner") == "praise"

    def test_suggestion(self):
        assert classify_severity("Consider adding a timeout here") == "suggestion"

    def test_observation_fallback(self):
        assert classify_severity("The return value is unused.") == "observation"

    def test_empty(self):
        assert classify_severity("") == "observation"

    def test_priority_nit_over_suggestion(self):
        # "nit:" should match first even if suggestion keywords present
        assert classify_severity("nit: maybe rename this") == "nit"


# ---------------------------------------------------------------------------
# Building the index
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_raw_items() -> list[dict]:
    """Sample PR comments with paths and bodies."""
    return [
        {"path": "pkg/api/types.go", "body": "This is an API regression, must fix before merge.", "_source_file": "pr_comments.json", "created_at": "2024-01-10T10:00:00Z"},
        {"path": "pkg/api/types.go", "body": "nit: rename this field for clarity", "_source_file": "pr_comments.json", "created_at": "2024-02-15T10:00:00Z"},
        {"path": "pkg/api/validation.go", "body": "Consider adding validation for negative values", "_source_file": "pr_comments.json", "created_at": "2024-03-20T10:00:00Z"},
        {"path": "pkg/api/defaults.go", "body": "The defaulting behavior here is inconsistent with the API spec", "_source_file": "pr_comments.json", "created_at": "2024-04-01T10:00:00Z"},
        {"path": "pkg/scheduler/queue.go", "body": "Nice optimization of the priority queue", "_source_file": "pr_comments.json", "created_at": "2024-05-10T10:00:00Z"},
        {"path": "pkg/scheduler/queue.go", "body": "Have you considered the performance impact on large clusters?", "_source_file": "pr_comments.json", "created_at": "2024-06-15T10:00:00Z"},
        {"path": "pkg/scheduler/queue.go", "body": "This needs error handling for the nil case", "_source_file": "pr_comments.json", "created_at": "2024-07-20T10:00:00Z"},
        # Below threshold (only 2 comments)
        {"path": "cmd/kube-apiserver/app.go", "body": "Fix the flag name", "_source_file": "pr_comments.json", "created_at": "2024-08-01T10:00:00Z"},
        {"path": "cmd/kube-apiserver/app.go", "body": "Add a deprecation notice", "_source_file": "pr_comments.json", "created_at": "2024-09-01T10:00:00Z"},
        # Issue (should be skipped)
        {"body": "This is a bug report", "_source_file": "issues.json", "created_at": "2024-10-01T10:00:00Z"},
        # No path (should be skipped)
        {"body": "General comment without path", "_source_file": "pr_comments.json", "created_at": "2024-11-01T10:00:00Z"},
    ]


class TestBuildExpertiseIndex:
    def test_basic_build(self, sample_raw_items: list[dict]):
        idx = build_expertise_index("test-reviewer", sample_raw_items, "org/repo", min_comments=3)

        assert idx.profile_name == "test-reviewer"
        assert idx.total_comments_analyzed > 0
        # pkg/api/ has 4 comments (above threshold of 3)
        assert "org/repo:pkg/api/" in idx.entries
        # pkg/scheduler/ has 3 comments (at threshold)
        assert "org/repo:pkg/scheduler/" in idx.entries
        # cmd/kube-apiserver/ has 2 comments (below threshold)
        assert "org/repo:cmd/kube-apiserver/" not in idx.entries

    def test_severity_distribution(self, sample_raw_items: list[dict]):
        idx = build_expertise_index("test-reviewer", sample_raw_items, "org/repo", min_comments=3)
        api_entry = idx.entries["org/repo:pkg/api/"]
        # Should have multiple severity types
        assert len(api_entry.severity_distribution) > 0
        # "must fix" should be classified as blocking
        assert api_entry.severity_distribution.get("blocking", 0) > 0

    def test_themes_extracted(self, sample_raw_items: list[dict]):
        idx = build_expertise_index("test-reviewer", sample_raw_items, "org/repo", min_comments=3)
        api_entry = idx.entries["org/repo:pkg/api/"]
        assert len(api_entry.themes) > 0
        theme_names = [t["theme"] for t in api_entry.themes]
        assert any("API" in t or "validation" in t.lower() for t in theme_names)

    def test_example_quotes(self, sample_raw_items: list[dict]):
        idx = build_expertise_index("test-reviewer", sample_raw_items, "org/repo", min_comments=3)
        api_entry = idx.entries["org/repo:pkg/api/"]
        assert len(api_entry.example_quotes) > 0
        assert all("text" in q and "file" in q for q in api_entry.example_quotes)

    def test_last_activity(self, sample_raw_items: list[dict]):
        idx = build_expertise_index("test-reviewer", sample_raw_items, "org/repo", min_comments=3)
        api_entry = idx.entries["org/repo:pkg/api/"]
        assert api_entry.last_activity != ""

    def test_empty_items(self):
        idx = build_expertise_index("empty", [], "org/repo")
        assert len(idx.entries) == 0
        assert idx.total_comments_analyzed == 0

    def test_repo_field_set(self, sample_raw_items: list[dict]):
        idx = build_expertise_index("test", sample_raw_items, "kubernetes/kubernetes", min_comments=3)
        for entry in idx.entries.values():
            assert entry.repo == "kubernetes/kubernetes"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_save_load_roundtrip(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sample_raw_items: list[dict]):
        root = tmp_path / ".legit"
        root.mkdir()
        monkeypatch.setattr("legit.expertise.legit_path", lambda: root)

        idx = build_expertise_index("test-reviewer", sample_raw_items, "org/repo", min_comments=3)
        save_expertise_index("test-reviewer", idx)

        loaded = load_expertise_index("test-reviewer")
        assert loaded is not None
        assert loaded.profile_name == idx.profile_name
        assert len(loaded.entries) == len(idx.entries)
        assert loaded.total_comments_analyzed == idx.total_comments_analyzed

    def test_load_missing_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        root = tmp_path / ".legit"
        root.mkdir()
        monkeypatch.setattr("legit.expertise.legit_path", lambda: root)

        assert load_expertise_index("nonexistent") is None


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


class TestLookupExpertise:
    def test_matches_changed_dirs(self, sample_raw_items: list[dict]):
        idx = build_expertise_index("test", sample_raw_items, "org/repo", min_comments=3)
        results = lookup_expertise(idx, ["pkg/api/types.go", "pkg/api/new_file.go"])
        assert len(results) > 0
        assert any(e.dir_path == "pkg/api/" for e in results)

    def test_matches_parent_dirs(self, sample_raw_items: list[dict]):
        idx = build_expertise_index("test", sample_raw_items, "org/repo", min_comments=3)
        # A file in a subdirectory should still match parent
        results = lookup_expertise(idx, ["pkg/api/v1/types.go"])
        assert any(e.dir_path == "pkg/api/" for e in results)

    def test_no_match(self, sample_raw_items: list[dict]):
        idx = build_expertise_index("test", sample_raw_items, "org/repo", min_comments=3)
        results = lookup_expertise(idx, ["completely/different/path.go"])
        assert len(results) == 0

    def test_empty_index(self):
        idx = ExpertiseIndex(profile_name="empty")
        results = lookup_expertise(idx, ["any/file.go"])
        assert len(results) == 0

    def test_sorted_by_comment_count(self, sample_raw_items: list[dict]):
        idx = build_expertise_index("test", sample_raw_items, "org/repo", min_comments=3)
        results = lookup_expertise(idx, ["pkg/api/types.go", "pkg/scheduler/queue.go"])
        if len(results) >= 2:
            assert results[0].comment_count >= results[1].comment_count


# ---------------------------------------------------------------------------
# Format for prompt
# ---------------------------------------------------------------------------


class TestFormatExpertiseContext:
    def test_basic_format(self, sample_raw_items: list[dict]):
        idx = build_expertise_index("test", sample_raw_items, "org/repo", min_comments=3)
        entries = lookup_expertise(idx, ["pkg/api/types.go"])
        text = format_expertise_context(entries)
        assert "Reviewer's Codebase Expertise" in text
        assert "pkg/api/" in text
        assert "past comments" in text

    def test_empty_entries(self):
        text = format_expertise_context([])
        assert text == ""

    def test_respects_budget(self, sample_raw_items: list[dict]):
        idx = build_expertise_index("test", sample_raw_items, "org/repo", min_comments=3)
        entries = lookup_expertise(idx, ["pkg/api/types.go"])
        text = format_expertise_context(entries, max_chars=100)
        assert len(text) < 500  # Some overhead from headers
