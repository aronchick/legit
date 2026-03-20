"""Shared data models for legit."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class IndexEntry(BaseModel):
    id: int | str
    type: str
    url: str
    created_at: datetime
    updated_at: datetime | None = None
    fetched: bool = False
    pr_number: int | None = None


class CursorState(BaseModel):
    """Pagination state per activity type."""

    page: int = 1
    per_page: int = 100
    complete: bool = False
    last_timestamp: datetime | None = None


class CursorFile(BaseModel):
    cursors: dict[str, CursorState] = Field(default_factory=dict)


class InlineComment(BaseModel):
    file: str
    hunk_header: str
    diff_snippet: str
    side: str = "addition"
    comment: str
    confidence: float = 1.0


class ReviewOutput(BaseModel):
    summary: str
    inline_comments: list[InlineComment] = Field(default_factory=list)
    abstained_files: list[str] = Field(default_factory=list)
    abstention_reason: str = ""


class ChunkObservation(BaseModel):
    date_range_start: str
    date_range_end: str
    observations: list[dict[str, str | list[str]]]
    representative_quotes: list[dict[str, str]] = Field(default_factory=list)
    raw_text: str = ""


class RetrievalDocument(BaseModel):
    comment_text: str
    file_path: str = ""
    code_context: str = ""
    comment_type: str = "pr_review"
    severity: str = "unknown"
    timestamp: str = ""
    reviewer_username: str = ""
    pr_number: int | None = None
