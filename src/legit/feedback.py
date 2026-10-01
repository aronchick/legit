"""Durable private-preview reviews and feedback, stored together for attribution."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from legit.config import legit_path


class Feedback(BaseModel):
    review_id: UUID
    rating: Literal["useful", "wrong", "missed-something"]
    note: str = Field(default="", max_length=4000)


@contextmanager
def _database() -> Iterator[sqlite3.Connection]:
    directory = legit_path() / "preview"
    directory.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(directory / "feedback.sqlite3", timeout=10)
    try:
        db.execute("PRAGMA foreign_keys = ON")
        db.execute(
            "CREATE TABLE IF NOT EXISTS reviews (id TEXT PRIMARY KEY, persona TEXT, "
            "pr_url TEXT, output TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS feedback (review_id TEXT REFERENCES reviews(id), "
            "rating TEXT, note TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        with db:
            yield db
    finally:
        db.close()


def save_review(persona: str, pr_url: str, output: dict[str, Any]) -> str:
    review_id = str(uuid4())
    with _database() as db:
        db.execute(
            "INSERT INTO reviews (id, persona, pr_url, output) VALUES (?, ?, ?, ?)",
            (review_id, persona, pr_url, json.dumps(output)),
        )
    return review_id


def record_feedback(payload: Feedback) -> bool:
    with _database() as db:
        if not db.execute(
            "SELECT 1 FROM reviews WHERE id = ?", (str(payload.review_id),)
        ).fetchone():
            return False
        db.execute(
            "INSERT INTO feedback (review_id, rating, note) VALUES (?, ?, ?)",
            (str(payload.review_id), payload.rating, payload.note),
        )
    return True
