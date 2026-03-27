"""Codebase expertise index — maps which areas a reviewer knows and what they care about.

Built at `legit build` time from the reviewer's comment history. At review time,
the index is loaded and queried to enrich the LLM prompt with qualitative context
about what the reviewer focuses on in each area of the codebase.

This is NOT a retrieval system — it's prompt enrichment. BM25 (or future semantic
search) handles retrieval. The expertise index answers: "What does this reviewer
care about when reviewing code in pkg/api/?"
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from pydantic import BaseModel, Field

from legit.config import legit_path

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ExpertiseEntry(BaseModel):
    """What a reviewer knows and cares about in a specific directory."""

    repo: str
    dir_path: str
    comment_count: int = 0
    severity_distribution: dict[str, int] = Field(default_factory=dict)
    themes: list[dict[str, int | str]] = Field(default_factory=list)
    example_quotes: list[dict[str, str]] = Field(default_factory=list)
    last_activity: str = ""


class ExpertiseIndex(BaseModel):
    """Per-reviewer map of codebase areas they have opinions about."""

    profile_name: str
    entries: dict[str, ExpertiseEntry] = Field(default_factory=dict)  # keyed by "repo:dir_path"
    total_comments_analyzed: int = 0


# ---------------------------------------------------------------------------
# Severity classification (keyword heuristics)
# ---------------------------------------------------------------------------

_SEVERITY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("nit", re.compile(r"\bnit[:\s]", re.IGNORECASE)),
    ("blocking", re.compile(r"\b(block(?:ing|er)?|must|cannot|will not|do not merge)\b", re.IGNORECASE)),
    ("question", re.compile(r"\?\s*$", re.MULTILINE)),
    ("praise", re.compile(r"\b(nice|great|good|lgtm|love|clever|clean|well done)\b", re.IGNORECASE)),
    ("suggestion", re.compile(r"\b(consider|maybe|could|might|suggest|prefer|would be better)\b", re.IGNORECASE)),
]


def classify_severity(text: str) -> str:
    """Classify a comment's severity from its text. Returns the first matching category."""
    for label, pattern in _SEVERITY_PATTERNS:
        if pattern.search(text):
            return label
    return "observation"


# ---------------------------------------------------------------------------
# Theme extraction (keyword frequency)
# ---------------------------------------------------------------------------

# Common code review themes with their trigger words
_THEME_KEYWORDS: dict[str, list[str]] = {
    "error handling": ["error", "err", "panic", "recover", "nil", "null", "exception", "catch", "throw"],
    "naming": ["name", "naming", "rename", "typo", "unclear", "confusing name"],
    "testing": ["test", "coverage", "assertion", "mock", "fixture", "testcase"],
    "API design": ["api", "endpoint", "schema", "field", "backwards compat", "deprecat"],
    "performance": ["performance", "perf", "slow", "allocat", "cache", "goroutine", "concurrent"],
    "documentation": ["doc", "comment", "godoc", "readme", "document"],
    "security": ["security", "auth", "token", "secret", "credential", "permiss", "rbac"],
    "backwards compatibility": ["backward", "compat", "regression", "breaking change", "rollback"],
    "code organization": ["refactor", "extract", "helper", "duplicate", "dry", "split", "move"],
    "validation": ["valid", "check", "verify", "saniti", "bound", "range", "limit"],
}


def _extract_themes(comments: list[str], max_themes: int = 5) -> list[dict[str, int | str]]:
    """Extract top themes from a list of comments using keyword frequency."""
    theme_counts: Counter[str] = Counter()
    combined = " ".join(comments).lower()

    for theme, keywords in _THEME_KEYWORDS.items():
        count = sum(combined.count(kw) for kw in keywords)
        if count > 0:
            theme_counts[theme] = count

    return [
        {"theme": theme, "frequency": freq}
        for theme, freq in theme_counts.most_common(max_themes)
    ]


# ---------------------------------------------------------------------------
# Index building
# ---------------------------------------------------------------------------


def build_expertise_index(
    profile_name: str,
    raw_items: list[dict],
    repo: str,
    min_comments: int = 3,
    max_quotes_per_dir: int = 5,
) -> ExpertiseIndex:
    """Build an expertise index from raw GitHub activity data.

    Groups comments by directory, extracts severity distributions, themes,
    and representative quotes. Only includes directories with at least
    *min_comments* comments.
    """
    # Group comments by directory
    dir_comments: dict[str, list[dict]] = defaultdict(list)
    total = 0

    for item in raw_items:
        path = item.get("path", "")
        body = item.get("body") or item.get("message") or ""
        if not path or not body.strip():
            continue

        # Skip non-review items (issues, commits without file paths)
        source = item.get("_source_file", "")
        if source in ("issues.json", "authored_prs.json"):
            continue

        # Extract directory from file path
        parts = path.rsplit("/", 1)
        dir_path = parts[0] + "/" if len(parts) == 2 else "/"
        dir_comments[dir_path].append(item)
        total += 1

    # Build entries for directories meeting the threshold
    entries: dict[str, ExpertiseEntry] = {}

    for dir_path, items in dir_comments.items():
        if len(items) < min_comments:
            continue

        # Severity distribution
        severity_dist: Counter[str] = Counter()
        comment_texts: list[str] = []
        for item in items:
            body = (item.get("body") or "").strip()
            if body:
                severity_dist[classify_severity(body)] += 1
                comment_texts.append(body)

        # Themes
        themes = _extract_themes(comment_texts)

        # Representative quotes (longest, most specific comments)
        scored_quotes = []
        for item in items:
            body = (item.get("body") or "").strip()
            if body and len(body) > 30:
                scored_quotes.append({
                    "text": body[:300],
                    "file": item.get("path", ""),
                    "date": (
                        item.get("created_at")
                        or item.get("submitted_at")
                        or ""
                    ),
                })

        # Sort by length descending (longer = more detailed/specific)
        scored_quotes.sort(key=lambda q: len(q["text"]), reverse=True)
        example_quotes = scored_quotes[:max_quotes_per_dir]

        # Last activity
        dates = [
            item.get("created_at") or item.get("submitted_at") or ""
            for item in items
        ]
        dates = [d for d in dates if d]
        last_activity = max(dates) if dates else ""

        key = f"{repo}:{dir_path}"
        entries[key] = ExpertiseEntry(
            repo=repo,
            dir_path=dir_path,
            comment_count=len(items),
            severity_distribution=dict(severity_dist),
            themes=themes,
            example_quotes=example_quotes,
            last_activity=last_activity,
        )

    return ExpertiseIndex(
        profile_name=profile_name,
        entries=entries,
        total_comments_analyzed=total,
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _expertise_dir(profile_name: str) -> Path:
    return legit_path() / "expertise" / profile_name


def save_expertise_index(profile_name: str, index: ExpertiseIndex) -> Path:
    """Save the expertise index to disk."""
    path = _expertise_dir(profile_name)
    path.mkdir(parents=True, exist_ok=True)
    out = path / "expertise.json"
    out.write_text(json.dumps(index.model_dump(mode="json"), indent=2) + "\n")
    return out


def load_expertise_index(profile_name: str) -> ExpertiseIndex | None:
    """Load a pre-built expertise index, or None if it doesn't exist."""
    path = _expertise_dir(profile_name) / "expertise.json"
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
        return ExpertiseIndex.model_validate(raw)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Lookup (used at review time)
# ---------------------------------------------------------------------------


def lookup_expertise(
    index: ExpertiseIndex,
    changed_files: list[str],
    max_entries: int = 10,
) -> list[ExpertiseEntry]:
    """Find relevant expertise entries for a set of changed files.

    Matches changed file directories against the expertise index.
    Returns entries sorted by comment_count (most expertise first).
    """
    if not index or not index.entries:
        return []

    # Extract unique directories from changed files
    changed_dirs: set[str] = set()
    for fpath in changed_files:
        parts = fpath.rsplit("/", 1)
        if len(parts) == 2:
            changed_dirs.add(parts[0] + "/")
            # Also add parent directories
            segments = parts[0].split("/")
            for i in range(len(segments)):
                changed_dirs.add("/".join(segments[: i + 1]) + "/")

    matched: list[ExpertiseEntry] = []
    for key, entry in index.entries.items():
        if entry.dir_path in changed_dirs:
            matched.append(entry)

    # Sort by comment count (most expertise first), cap
    matched.sort(key=lambda e: e.comment_count, reverse=True)
    return matched[:max_entries]


def format_expertise_context(entries: list[ExpertiseEntry], max_chars: int = 3000) -> str:
    """Format expertise entries as a prompt section.

    Returns a concise text block summarizing what the reviewer cares about
    in the areas being changed. Stays within *max_chars* budget.
    """
    if not entries:
        return ""

    parts: list[str] = ["\n## Reviewer's Codebase Expertise\n"]
    parts.append("The reviewer has historical opinions about these areas of the codebase:\n")
    remaining = max_chars

    for entry in entries:
        if remaining <= 0:
            break

        theme_str = ", ".join(t["theme"] for t in entry.themes[:3]) if entry.themes else "general"
        severity_str = ", ".join(
            f"{k}: {v}" for k, v in sorted(entry.severity_distribution.items(), key=lambda x: -x[1])[:3]
        )

        section = f"**`{entry.dir_path}`** ({entry.comment_count} past comments)\n"
        section += f"  Focus areas: {theme_str}\n"
        section += f"  Severity pattern: {severity_str}\n"

        # Add one representative quote if space allows
        if entry.example_quotes and remaining > 200:
            quote = entry.example_quotes[0]
            section += f"  Example: \"{quote['text'][:150]}...\"\n"

        section += "\n"

        if len(section) > remaining:
            break

        parts.append(section)
        remaining -= len(section)

    return "".join(parts)
