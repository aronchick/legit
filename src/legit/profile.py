"""Map-reduce profile generation — learns a reviewer's style from GitHub history."""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from legit.config import LegitConfig, ProfileConfig, legit_path
from legit.model_runner import run_inference
from legit.models import ChunkObservation, RetrievalDocument

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def _data_dirs_for_profile(profile: ProfileConfig) -> list[Path]:
    """Return the data directories for every source in a profile config."""
    dirs: list[Path] = []
    for src in profile.sources:
        owner, repo = src.repo.split("/", 1)
        dirs.append(legit_path() / "data" / f"{owner}_{repo}" / src.username)
    return dirs


def _load_all_items(profile: ProfileConfig) -> list[dict]:
    """Load every JSON file from the profile's data directories.

    Each file is expected to contain a JSON array of comment/review objects.
    Also loads authored_prs.json (PR diffs written by the reviewer) and
    converts them into items suitable for the map phase.
    Returns a flat list of all items sorted chronologically by created_at.
    """
    items: list[dict] = []
    for data_dir in _data_dirs_for_profile(profile):
        if not data_dir.is_dir():
            logger.warning("Data directory not found: %s", data_dir)
            continue
        for json_file in sorted(data_dir.glob("*.json")):
            # Skip index and cursor metadata files
            if json_file.name in ("index.json", "cursor.json"):
                continue
            try:
                raw = json.loads(json_file.read_text())
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Skipping %s: %s", json_file, exc)
                continue

            # Special handling for authored_prs.json — convert PR diffs
            # into items the map phase can analyze for coding style
            if json_file.name == "authored_prs.json" and isinstance(raw, list):
                for pr in raw:
                    if not isinstance(pr, dict):
                        continue
                    # Convert to a format the map phase understands
                    item = {
                        "_source_file": "authored_prs.json",
                        "body": (
                            f"[AUTHORED CODE] PR #{pr.get('number', '?')}: {pr.get('title', '')}\n\n"
                            f"Files changed: {', '.join(pr.get('files', []))}\n\n"
                            f"Diff:\n{pr.get('diff', '')[:20_000]}"
                        ),
                        "created_at": pr.get("created_at", ""),
                        "html_url": f"https://github.com/pull/{pr.get('number', '')}",
                    }
                    items.append(item)
                continue

            if isinstance(raw, list):
                for item in raw:
                    if isinstance(item, dict):
                        item["_source_file"] = json_file.name
                        items.append(item)
            elif isinstance(raw, dict):
                raw["_source_file"] = json_file.name
                items.append(raw)

    # Sort chronologically — fall back to epoch for items without created_at
    def _sort_key(item: dict) -> str:
        return (
            item.get("created_at")
            or item.get("submitted_at")
            or item.get("commit", {}).get("author", {}).get("date", "")
            or "1970-01-01T00:00:00Z"
        )

    items.sort(key=_sort_key)
    return items


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def _chunk_items(items: list[dict], chunk_size: int = 150) -> list[list[dict]]:
    """Split a sorted list of items into chunks of chunk_size."""
    if not items:
        return []
    return [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)]


def _date_range(chunk: list[dict]) -> tuple[str, str]:
    """Return (earliest, latest) ISO date strings from a chunk."""
    dates: list[str] = []
    for item in chunk:
        dt = (
            item.get("created_at")
            or item.get("submitted_at")
            or item.get("commit", {}).get("author", {}).get("date", "")
        )
        if dt:
            dates.append(dt)
    if not dates:
        return ("unknown", "unknown")
    dates.sort()
    return (dates[0], dates[-1])


# ---------------------------------------------------------------------------
# Map phase
# ---------------------------------------------------------------------------

MAP_SYSTEM_PROMPT = """\
You are a behavioral analyst studying a code reviewer's patterns. You will be \
given a batch of their GitHub review comments, issue comments, PR feedback, \
and potentially their own authored code changes (commits and PR diffs).

Your job: discover patterns in HOW this person reviews AND writes code. Do NOT \
use a fixed template. Instead, organize your observations by SITUATION — i.e., \
what triggers specific behaviors.

For each situation-pattern you discover, provide:
- situation: When does this behavior occur? (e.g., "when reviewing error handling", \
"when a PR lacks tests", "when commenting on API surface changes")
- behaviors: What does the reviewer actually do/say in this situation?
- severity_pattern: How critical do they treat this? (blocking, suggestion, nit, etc.)
- tone_and_style: How do they phrase things? Direct? Questioning? Diplomatic?
- example_quotes: 2-3 exact quotes from the data that best illustrate this pattern. \
Include enough context that someone could understand the quote standalone.

Also note:
- Distinctive verbal habits, recurring phrases, or signature patterns
- What they choose NOT to comment on (implicit priorities)
- How their feedback varies by file type or area of code
- If authored code is included: their coding style, naming conventions, error \
handling patterns, test coverage practices, and commit message conventions. \
Understanding how they write code reveals what they expect from others.

Respond with valid JSON only.\
"""


def _build_map_prompt(chunk: list[dict], chunk_index: int, date_start: str, date_end: str) -> str:
    """Build the user prompt for mapping a single chunk."""
    # Serialize items — keep the most useful fields, drop noise
    serialized: list[str] = []
    for item in chunk:
        body = item.get("body") or item.get("message") or item.get("commit", {}).get("message", "")
        if not body or not body.strip():
            continue
        path = item.get("path", "")
        url = item.get("html_url", "")
        created = (
            item.get("created_at")
            or item.get("submitted_at")
            or item.get("commit", {}).get("author", {}).get("date", "")
            or ""
        )
        diff_hunk = item.get("diff_hunk", "")
        source_type = item.get("_source_file", "")

        entry_parts = [f"[{created}] ({source_type})"]
        if path:
            entry_parts.append(f"File: {path}")
        if diff_hunk:
            entry_parts.append(f"Code context:\n{diff_hunk}")
        entry_parts.append(f"Comment: {body.strip()}")
        if url:
            entry_parts.append(f"URL: {url}")
        serialized.append("\n".join(entry_parts))

    items_text = "\n---\n".join(serialized)
    count = len(serialized)

    return f"""\
Chunk {chunk_index} of reviewer activity — date range: {date_start} to {date_end}
Contains {count} items.

Analyze the following reviewer comments and extract behavioral patterns:

{items_text}"""


def _run_map(
    config: LegitConfig,
    chunk: list[dict],
    chunk_index: int,
) -> ChunkObservation:
    """Run the map phase for a single chunk, returning structured observations."""
    date_start, date_end = _date_range(chunk)
    user_prompt = _build_map_prompt(chunk, chunk_index, date_start, date_end)

    result = run_inference(
        system_prompt=MAP_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        config=config.model,
        response_model=ChunkObservation,
    )

    if isinstance(result, ChunkObservation):
        result.date_range_start = date_start
        result.date_range_end = date_end
        return result

    # Fallback: LLM returned raw text instead of parsed model
    logger.warning("Map phase returned raw text for chunk %d; wrapping as raw observation", chunk_index)
    return ChunkObservation(
        date_range_start=date_start,
        date_range_end=date_end,
        observations=[{"situation": "raw_output", "details": [result]}],
        raw_text=result if isinstance(result, str) else str(result),
    )


# ---------------------------------------------------------------------------
# Reduce phase
# ---------------------------------------------------------------------------

REDUCE_SYSTEM_PROMPT = """\
You are synthesizing a reviewer profile from multiple batches of behavioral \
observations. Each batch covers a different time period of the reviewer's \
GitHub activity, including both their review comments AND their own authored code.

Your task: produce a unified reviewer profile as a Markdown document.

IMPORTANT — Temporal weighting:
- More recent batches reflect the reviewer's CURRENT style more accurately.
- Older batches show historical tendencies that may have evolved.
- When patterns conflict between old and new batches, favor the newer ones.
- Note when you observe clear evolution in the reviewer's style over time.

Structure the profile using emergent sections — do NOT use a fixed template. \
Let the actual patterns determine the section headings. Typical sections might \
include things like their review philosophy, what they focus on most, how they \
handle different situations, their communication style, etc. But only create \
sections that the data actually supports.

Requirements:
- Start with a ## Generated metadata section containing: generation date, \
source date range, number of data points analyzed.
- Preserve situation-specific behaviors — don't over-generalize.
- Include representative example quotes with enough context to be useful. \
Format quotes as blockquotes with source context.
- Include a ## Coding Style section (if authored code data is available) \
covering: naming conventions, error handling patterns, code structure preferences, \
test writing patterns, and commit message style. Understanding how they write \
code reveals what they expect from others during review.
- End with a section on distinctive traits or habits that make this \
reviewer recognizable.
- Write in a practical, concrete tone. This profile will be used by an AI \
system to emulate this reviewer's style.

Output the complete Markdown document.\
"""


def _build_reduce_prompt(
    observations: list[ChunkObservation],
    profile: ProfileConfig,
    total_items: int,
) -> str:
    """Build the user prompt for the reduce/synthesis phase."""
    parts: list[str] = []

    parts.append(f"Profile name: {profile.name}")
    parts.append(f"Total data points analyzed: {total_items}")
    sources_desc = ", ".join(f"{s.username} in {s.repo}" for s in profile.sources)
    parts.append(f"Sources: {sources_desc}")
    parts.append("")

    n_chunks = len(observations)
    for i, obs in enumerate(observations):
        recency_label = "MOST RECENT" if i == n_chunks - 1 else f"chunk {i + 1}/{n_chunks}"
        weight_hint = ""
        if n_chunks > 1:
            # Linear weighting hint: later chunks get more weight
            weight = 0.5 + 0.5 * (i / (n_chunks - 1))
            weight_hint = f" (relative weight: {weight:.2f})"

        parts.append(f"### Batch {i + 1} [{recency_label}]{weight_hint}")
        parts.append(f"Date range: {obs.date_range_start} to {obs.date_range_end}")
        parts.append("")

        if obs.observations:
            parts.append("Observations:")
            parts.append(json.dumps(obs.observations, indent=2))
            parts.append("")

        if obs.representative_quotes:
            parts.append("Representative quotes:")
            parts.append(json.dumps(obs.representative_quotes, indent=2))
            parts.append("")

        if obs.raw_text:
            parts.append("Additional notes:")
            parts.append(obs.raw_text)
            parts.append("")

    return "\n".join(parts)


def _run_reduce(
    config: LegitConfig,
    observations: list[ChunkObservation],
    profile: ProfileConfig,
    total_items: int,
) -> str:
    """Run the reduce phase, synthesizing all observations into a profile markdown doc."""
    user_prompt = _build_reduce_prompt(observations, profile, total_items)

    result = run_inference(
        system_prompt=REDUCE_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        config=config.model,
    )

    if isinstance(result, str):
        return result

    # Shouldn't happen since we didn't pass response_model, but handle it
    return str(result)


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


def _cache_dir(profile_name: str) -> Path:
    return legit_path() / "cache" / "chunks" / profile_name


def _load_cached_chunk(profile_name: str, chunk_index: int) -> ChunkObservation | None:
    """Load a cached map output for a specific chunk, or None if missing."""
    path = _cache_dir(profile_name) / f"chunk_{chunk_index:03d}.json"
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
        return ChunkObservation.model_validate(raw)
    except (json.JSONDecodeError, Exception) as exc:
        logger.warning("Corrupt cache for chunk %d: %s", chunk_index, exc)
        return None


def _save_cached_chunk(profile_name: str, chunk_index: int, observation: ChunkObservation) -> None:
    """Persist a map output to the chunk cache."""
    cache = _cache_dir(profile_name)
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"chunk_{chunk_index:03d}.json"
    path.write_text(json.dumps(observation.model_dump(mode="json"), indent=2) + "\n")


def _clear_cache(profile_name: str) -> None:
    """Remove all cached chunk files for a profile."""
    cache = _cache_dir(profile_name)
    if cache.is_dir():
        for f in cache.glob("chunk_*.json"):
            f.unlink()
        logger.info("Cleared chunk cache for profile '%s'", profile_name)


# ---------------------------------------------------------------------------
# Profile output
# ---------------------------------------------------------------------------


def _profiles_dir() -> Path:
    return legit_path() / "profiles"


def _profile_path(profile_name: str) -> Path:
    return _profiles_dir() / f"{profile_name}.md"


# ---------------------------------------------------------------------------
# Raw data -> RetrievalDocument conversion
# ---------------------------------------------------------------------------


def _infer_comment_type(item: dict) -> str:
    """Infer the comment type from an item's source file or structure."""
    source = item.get("_source_file", "")
    if "pr_comment" in source:
        return "pr_review"
    if "review" in source:
        return "pr_review"
    if "issue_comment" in source:
        return "issue_comment"
    if "commit" in source:
        return "commit_comment"
    if "issue" in source:
        return "issue_comment"
    # Fallback: check for structural hints
    if item.get("diff_hunk"):
        return "pr_review"
    if item.get("pull_request_url"):
        return "pr_review"
    return "pr_review"


def _extract_timestamp(item: dict) -> str:
    """Extract the best timestamp from an item."""
    return (
        item.get("created_at")
        or item.get("submitted_at")
        or item.get("commit", {}).get("author", {}).get("date", "")
        or ""
    )


def _extract_username(item: dict) -> str:
    """Extract the reviewer's username from an item."""
    user = item.get("user")
    if isinstance(user, dict):
        return user.get("login", "")
    # Commit author
    author = item.get("commit", {}).get("author", {})
    if isinstance(author, dict):
        return author.get("name", "")
    return ""


def _extract_pr_number(item: dict) -> int | None:
    """Try to extract a PR number from the item."""
    # Direct field
    pr_num = item.get("pull_request_number")
    if pr_num is not None:
        return int(pr_num)

    # From pull_request_url
    pr_url = item.get("pull_request_url", "")
    if pr_url:
        import re

        m = re.search(r"/pulls?/(\d+)", pr_url)
        if m:
            return int(m.group(1))

    # From html_url
    html_url = item.get("html_url", "")
    if html_url:
        import re

        m = re.search(r"/pull/(\d+)", html_url)
        if m:
            return int(m.group(1))

    return None


def load_raw_data_as_retrieval_docs(
    config: LegitConfig,
    profile_name: str,
) -> list[RetrievalDocument]:
    """Load all raw GitHub data for a profile and convert to RetrievalDocument objects.

    This bridges the raw fetched data and the retrieval index, converting
    PR comments, reviews, issue comments, etc. into a uniform document
    format suitable for BM25 indexing and few-shot retrieval.
    """
    profile = _find_profile(config, profile_name)
    items = _load_all_items(profile)
    docs: list[RetrievalDocument] = []

    for item in items:
        body = item.get("body") or item.get("message") or item.get("commit", {}).get("message", "")
        if not body or not body.strip():
            continue

        comment_type = _infer_comment_type(item)
        file_path = item.get("path", "")
        code_context = item.get("diff_hunk", "")
        timestamp = _extract_timestamp(item)
        username = _extract_username(item)
        pr_number = _extract_pr_number(item)

        doc = RetrievalDocument(
            comment_text=body.strip(),
            file_path=file_path,
            code_context=code_context,
            comment_type=comment_type,
            timestamp=timestamp,
            reviewer_username=username,
            pr_number=pr_number,
        )
        docs.append(doc)

    return docs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_profile(config: LegitConfig, profile_name: str) -> ProfileConfig:
    """Look up a profile by name in the config, or raise."""
    for p in config.profiles:
        if p.name == profile_name:
            return p
    available = [p.name for p in config.profiles]
    raise ValueError(
        f"Profile '{profile_name}' not found in config. Available: {available}"
    )


# ---------------------------------------------------------------------------
# Main entry points
# ---------------------------------------------------------------------------


def build_profile(
    config: LegitConfig,
    profile_name: str,
    rebuild_map: bool = False,
    max_chunks: int | None = None,
) -> Path:
    """Build a reviewer profile using map-reduce over their GitHub history.

    Steps:
    1. Load all raw data for the profile's sources.
    2. Chunk items chronologically.
    3. Map: send each chunk to the LLM for pattern extraction (with caching).
    4. Reduce: synthesize all chunk observations into a unified profile.
    5. Write the profile markdown to .legit/profiles/{name}.md.

    Parameters
    ----------
    config:
        The full legit configuration.
    profile_name:
        Name of the profile to build (must match a profile in config.profiles).
    rebuild_map:
        If True, ignore cached map outputs and re-process all chunks.

    Returns
    -------
    Path
        The path to the generated profile markdown file.
    """
    profile = _find_profile(config, profile_name)

    # 1. Load all data
    logger.info("Loading data for profile '%s'…", profile_name)
    items = _load_all_items(profile)
    if not items:
        raise RuntimeError(
            f"No data found for profile '{profile_name}'. "
            "Run 'legit fetch' to download reviewer activity first."
        )

    total_items = len(items)
    logger.info("Loaded %d items for profile '%s'", total_items, profile_name)

    # 2. Chunk
    chunks = _chunk_items(items, profile.chunk_size)
    if max_chunks is not None and max_chunks < len(chunks):
        logger.info("Limiting to first %d of %d chunks (--max-chunks)", max_chunks, len(chunks))
        chunks = chunks[:max_chunks]
    logger.info("Processing %d chunks of up to %d items", len(chunks), profile.chunk_size)

    # 3. Map phase — with caching and parallel processing
    if rebuild_map:
        logger.info("--rebuild-map: clearing chunk cache")
        _clear_cache(profile_name)

    observations: list[ChunkObservation | None] = [None] * len(chunks)

    # First pass: load cached results
    pending: list[tuple[int, list[dict]]] = []
    for i, chunk in enumerate(chunks):
        cached = None if rebuild_map else _load_cached_chunk(profile_name, i)
        if cached is not None:
            logger.info("Chunk %d/%d: cached", i + 1, len(chunks))
            observations[i] = cached
        else:
            pending.append((i, chunk))

    if pending:
        concurrency = min(profile.map_concurrency, len(pending))
        logger.info(
            "Processing %d uncached chunks (%d workers, %d already cached)",
            len(pending), concurrency, len(chunks) - len(pending),
        )

        def _process_chunk(idx_chunk: tuple[int, list[dict]]) -> tuple[int, ChunkObservation]:
            idx, chunk = idx_chunk
            logger.info("Chunk %d/%d: sending to LLM (%d items)…", idx + 1, len(chunks), len(chunk))
            obs = _run_map(config, chunk, idx)
            _save_cached_chunk(profile_name, idx, obs)
            return idx, obs

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(_process_chunk, item): item[0] for item in pending}
            completed = 0
            for future in as_completed(futures):
                idx, obs = future.result()
                observations[idx] = obs
                completed += 1
                logger.info("  Completed %d/%d chunks", completed, len(pending))

    # Type-narrow: all slots should be filled now
    final_observations: list[ChunkObservation] = []
    for obs in observations:
        assert obs is not None, "Bug: missing chunk observation after map phase"
        final_observations.append(obs)

    # 4. Reduce phase
    logger.info("Running reduce phase — synthesizing %d chunk observations…", len(final_observations))
    profile_markdown = _run_reduce(config, final_observations, profile, total_items)

    # 5. Write output
    output_path = _profile_path(profile_name)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(profile_markdown + "\n")
    logger.info("Profile written to %s", output_path)

    # 6. Build expertise index (no LLM, pure data extraction)
    from legit.expertise import build_expertise_index, save_expertise_index

    logger.info("Building expertise index…")
    for src in profile.sources:
        expertise_idx = build_expertise_index(profile_name, items, src.repo)
        idx_path = save_expertise_index(profile_name, expertise_idx)
        logger.info(
            "Expertise index: %d directories, %d total comments → %s",
            len(expertise_idx.entries),
            expertise_idx.total_comments_analyzed,
            idx_path,
        )

    return output_path


def load_profile(profile_name: str) -> str:
    """Load a profile markdown document from disk.

    Returns the full text content of the profile.

    Raises
    ------
    FileNotFoundError
        If the profile has not been generated yet.
    """
    path = _profile_path(profile_name)
    if not path.exists():
        raise FileNotFoundError(
            f"Profile '{profile_name}' not found at {path}. "
            "Run 'legit build-profile' to generate it."
        )
    return path.read_text()
