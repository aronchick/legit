"""Offline artifact inventory. Availability is not a persona quality claim."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from legit.config import LegitConfig, legit_path
from legit.models import RetrievalDocument


def _validate_index(index: Any) -> int:
    """Validate every field used by BM25 loading and scoring without mutating it."""
    if not isinstance(index, dict):
        raise ValueError("Expected an index object")
    documents = index["documents"]
    count = index["doc_count"]
    if type(count) is not int or count <= 0 or not isinstance(documents, list):
        raise ValueError("Invalid document count")
    if len(documents) != count:
        raise ValueError("Inconsistent document count")
    for document in documents:
        RetrievalDocument.model_validate(document, strict=True)
    for key in ("k1", "b", "avg_dl"):
        value = index[key]
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError(f"Invalid numeric parameter: {key}")
    if index["k1"] <= 0 or not 0 <= index["b"] <= 1 or index["avg_dl"] <= 0:
        raise ValueError("Invalid scoring parameters")
    lengths = index["doc_lengths"]
    if not isinstance(lengths, list) or len(lengths) != count:
        raise ValueError("Invalid document lengths")
    if any(type(length) is not int or length < 0 for length in lengths):
        raise ValueError("Invalid document length")
    if not math.isclose(sum(lengths) / count, index["avg_dl"]):
        raise ValueError("Inconsistent average document length")
    inverted = index["inverted"]
    if not isinstance(inverted, dict) or not inverted:
        raise ValueError("Missing term postings")
    for term, postings in inverted.items():
        if not isinstance(term, str) or not term or not isinstance(postings, dict) or not postings:
            raise ValueError("Invalid term postings")
        for doc_id, frequency in postings.items():
            if not isinstance(doc_id, str) or not doc_id.isascii() or not doc_id.isdecimal():
                raise ValueError("Invalid posting document ID")
            position = int(doc_id)
            if str(position) != doc_id or position >= count:
                raise ValueError("Posting document ID outside index")
            if type(frequency) is not int or not 0 < frequency <= lengths[position]:
                raise ValueError("Invalid term frequency")
    return count


def inspect_personas(config: LegitConfig, root: Path | None = None) -> list[dict[str, Any]]:
    """Report usable profile/BM25 files and corpus dates without provider calls."""
    root = root or legit_path()
    rows: list[dict[str, Any]] = []
    for profile in config.profiles:
        problems: list[str] = []
        profile_path = root / "profiles" / f"{profile.name}.md"
        try:
            profile_present = bool(profile_path.read_text().strip())
        except (OSError, UnicodeError):
            profile_present = False
        if not profile_present:
            problems.append("Profile missing or unreadable")
        count = 0
        try:
            index = json.loads((root / "index" / profile.name / "bm25.json").read_text())
            count = _validate_index(index)
        except (OSError, UnicodeError, ValueError, KeyError, TypeError):
            count = 0
            problems.append("BM25 index missing, empty or invalid")
        cutoff = None
        try:
            meta = json.loads((root / "profiles" / f"{profile.name}.meta.json").read_text())
            cutoff = meta.get("holdout_after")
        except (OSError, UnicodeError, ValueError, AttributeError):
            pass
        sources = []
        for source in profile.sources:
            data_dir = root / "data" / source.repo.replace("/", "_") / source.username
            streams = {}
            for name in ("pr_comments", "issue_comments"):
                try:
                    items = json.loads((data_dir / f"{name}.json").read_text())
                    if not isinstance(items, list):
                        raise ValueError("Expected list")
                    dates = sorted(str(i["created_at"]) for i in items if i.get("created_at"))
                    streams[name] = {
                        "count": len(items),
                        "earliest": dates[0] if dates else None,
                        "latest": dates[-1] if dates else None,
                    }
                except (OSError, UnicodeError, ValueError, TypeError, AttributeError):
                    streams[name] = {"count": None, "earliest": None, "latest": None}
            sources.append({"repo": source.repo, "username": source.username, "streams": streams})
        rows.append(
            {
                "name": profile.name,
                "profile_present": profile_present,
                "bm25_documents": count,
                "holdout_after": cutoff,
                "artifact_status": "present" if not problems else "incomplete",
                "quality_status": "unverified",
                "problems": problems,
                "sources": sources,
            }
        )
    return rows
