"""Tests for legit.models — all pydantic data models."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from legit.models import (
    ChunkObservation,
    CursorFile,
    CursorState,
    IndexEntry,
    InlineComment,
    RetrievalDocument,
    ReviewOutput,
)


# ---------------------------------------------------------------------------
# IndexEntry
# ---------------------------------------------------------------------------


class TestIndexEntry:
    def test_create_minimal(self):
        entry = IndexEntry(
            id=123,
            type="pr_comment",
            url="https://api.github.com/repos/o/r/pulls/comments/123",
            created_at=datetime(2025, 1, 15, tzinfo=timezone.utc),
        )
        assert entry.id == 123
        assert entry.type == "pr_comment"
        assert entry.fetched is False
        assert entry.updated_at is None
        assert entry.pr_number is None

    def test_create_full(self):
        entry = IndexEntry(
            id="abc123",
            type="commit",
            url="https://api.github.com/repos/o/r/commits/abc123",
            created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
            fetched=True,
            pr_number=42,
        )
        assert entry.id == "abc123"
        assert entry.fetched is True
        assert entry.pr_number == 42

    def test_serialization_roundtrip(self):
        entry = IndexEntry(
            id=1,
            type="review",
            url="https://example.com",
            created_at=datetime(2025, 6, 1, tzinfo=timezone.utc),
            pr_number=99,
        )
        data = entry.model_dump(mode="json")
        restored = IndexEntry.model_validate(data)
        assert restored.id == entry.id
        assert restored.type == entry.type
        assert restored.pr_number == 99

    def test_id_can_be_string_or_int(self):
        e1 = IndexEntry(id=42, type="issue", url="", created_at=datetime.now(tz=timezone.utc))
        e2 = IndexEntry(id="sha123", type="commit", url="", created_at=datetime.now(tz=timezone.utc))
        assert e1.id == 42
        assert e2.id == "sha123"


# ---------------------------------------------------------------------------
# CursorState / CursorFile
# ---------------------------------------------------------------------------


class TestCursorState:
    def test_defaults(self):
        cs = CursorState()
        assert cs.page == 1
        assert cs.per_page == 100
        assert cs.complete is False
        assert cs.last_timestamp is None

    def test_serialization(self):
        cs = CursorState(page=3, complete=True, last_timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc))
        data = cs.model_dump(mode="json")
        restored = CursorState.model_validate(data)
        assert restored.page == 3
        assert restored.complete is True


class TestCursorFile:
    def test_empty(self):
        cf = CursorFile()
        assert cf.cursors == {}

    def test_with_cursors(self):
        cf = CursorFile(cursors={"pr_comments": CursorState(page=5)})
        assert cf.cursors["pr_comments"].page == 5

    def test_serialization_roundtrip(self):
        cf = CursorFile(
            cursors={
                "a": CursorState(page=2, complete=True),
                "b": CursorState(page=10),
            }
        )
        data = cf.model_dump(mode="json")
        restored = CursorFile.model_validate(data)
        assert restored.cursors["a"].complete is True
        assert restored.cursors["b"].page == 10


# ---------------------------------------------------------------------------
# InlineComment
# ---------------------------------------------------------------------------


class TestInlineComment:
    def test_defaults(self):
        c = InlineComment(
            file="main.py",
            hunk_header="@@ -1,3 +1,5 @@",
            diff_snippet="+new_line()",
            comment="Add a docstring.",
        )
        assert c.side == "addition"
        assert c.confidence == 1.0

    def test_custom_confidence(self):
        c = InlineComment(
            file="a.py",
            hunk_header="@@",
            diff_snippet="+x",
            comment="nit",
            confidence=0.3,
        )
        assert c.confidence == 0.3

    def test_serialization(self):
        c = InlineComment(
            file="a.py",
            hunk_header="@@",
            diff_snippet="+x",
            comment="nit",
            confidence=0.7,
            side="deletion",
        )
        data = c.model_dump()
        restored = InlineComment.model_validate(data)
        assert restored.side == "deletion"
        assert restored.confidence == 0.7


# ---------------------------------------------------------------------------
# ReviewOutput
# ---------------------------------------------------------------------------


class TestReviewOutput:
    def test_minimal(self):
        ro = ReviewOutput(summary="LGTM")
        assert ro.summary == "LGTM"
        assert ro.inline_comments == []
        assert ro.abstained_files == []
        assert ro.abstention_reason == ""

    def test_with_comments(self):
        comments = [
            InlineComment(file="a.py", hunk_header="@@", diff_snippet="+x", comment="fix")
        ]
        ro = ReviewOutput(summary="Needs work", inline_comments=comments)
        assert len(ro.inline_comments) == 1
        assert ro.inline_comments[0].file == "a.py"

    def test_with_abstentions(self):
        ro = ReviewOutput(
            summary="Partial review",
            abstained_files=["vendor/", "generated.go"],
            abstention_reason="Generated code",
        )
        assert len(ro.abstained_files) == 2
        assert "vendor/" in ro.abstained_files

    def test_serialization_roundtrip(self):
        ro = ReviewOutput(
            summary="Test",
            inline_comments=[
                InlineComment(file="b.py", hunk_header="@@", diff_snippet="+y", comment="ok")
            ],
            abstained_files=["c.py"],
            abstention_reason="Out of scope",
        )
        data = ro.model_dump(mode="json")
        restored = ReviewOutput.model_validate(data)
        assert restored.summary == "Test"
        assert len(restored.inline_comments) == 1
        assert restored.abstained_files == ["c.py"]


# ---------------------------------------------------------------------------
# ChunkObservation
# ---------------------------------------------------------------------------


class TestChunkObservation:
    def test_minimal(self):
        co = ChunkObservation(
            date_range_start="2025-01-01",
            date_range_end="2025-03-01",
            observations=[{"situation": "error handling", "behaviors": ["checks nil"]}],
        )
        assert co.date_range_start == "2025-01-01"
        assert len(co.observations) == 1

    def test_defaults(self):
        co = ChunkObservation(
            date_range_start="2025-01-01",
            date_range_end="2025-03-01",
            observations=[],
        )
        assert co.representative_quotes == []
        assert co.raw_text == ""

    def test_with_quotes_and_raw(self):
        co = ChunkObservation(
            date_range_start="2025-01-01",
            date_range_end="2025-03-01",
            observations=[{"situation": "test", "details": ["detail"]}],
            representative_quotes=[{"quote": "Always test", "context": "PR review"}],
            raw_text="Some raw LLM output",
        )
        assert len(co.representative_quotes) == 1
        assert co.raw_text == "Some raw LLM output"

    def test_serialization(self):
        co = ChunkObservation(
            date_range_start="2025-01-01",
            date_range_end="2025-06-01",
            observations=[{"x": "y"}],
        )
        data = co.model_dump(mode="json")
        restored = ChunkObservation.model_validate(data)
        assert restored.date_range_start == "2025-01-01"


# ---------------------------------------------------------------------------
# RetrievalDocument
# ---------------------------------------------------------------------------


class TestRetrievalDocument:
    def test_minimal(self):
        doc = RetrievalDocument(comment_text="Fix this.")
        assert doc.comment_text == "Fix this."
        assert doc.file_path == ""
        assert doc.code_context == ""
        assert doc.comment_type == "pr_review"
        assert doc.severity == "unknown"
        assert doc.timestamp == ""
        assert doc.reviewer_username == ""
        assert doc.pr_number is None

    def test_full(self):
        doc = RetrievalDocument(
            comment_text="Use constants",
            file_path="pkg/utils.go",
            code_context="timeout := 30",
            comment_type="issue_comment",
            severity="nit",
            timestamp="2025-01-15T10:00:00Z",
            reviewer_username="alice",
            pr_number=42,
        )
        assert doc.file_path == "pkg/utils.go"
        assert doc.pr_number == 42

    def test_serialization_roundtrip(self):
        doc = RetrievalDocument(
            comment_text="Check bounds",
            file_path="src/main.py",
            comment_type="commit_comment",
            timestamp="2025-06-01T00:00:00Z",
        )
        data = doc.model_dump(mode="json")
        restored = RetrievalDocument.model_validate(data)
        assert restored.comment_text == "Check bounds"
        assert restored.comment_type == "commit_comment"
