"""PR review generation pipeline.

Generates reviews that match a reviewer's authentic voice by combining their
profile document, retrieved past comments (BM25), and LLM inference with a
self-critique pass.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field
from rich.console import Console
from rich.markdown import Markdown

from legit.config import LegitConfig, legit_path
from legit.github_client import GitHubClient, get_token, parse_pr_url
from legit.model_runner import run_inference
from legit.models import InlineComment, ReviewOutput
from legit.retrieval import construct_queries, format_examples, retrieve

logger = logging.getLogger(__name__)
console = Console(stderr=True)

# ---------------------------------------------------------------------------
# Profile loading
# ---------------------------------------------------------------------------


def load_profile(profile_name: str) -> str:
    """Read the profile markdown from ``.legit/profiles/{name}.md``.

    Returns the raw text of the profile document.
    """
    profile_path = legit_path() / "profiles" / f"{profile_name}.md"
    if not profile_path.exists():
        raise FileNotFoundError(
            f"Profile not found: {profile_path}. "
            f"Run 'legit profile build {profile_name}' first."
        )
    return profile_path.read_text()


# ---------------------------------------------------------------------------
# Diff parsing helpers
# ---------------------------------------------------------------------------


def _parse_diff_hunks(diff_text: str) -> list[dict]:
    """Split a unified diff into per-file hunks for retrieval queries.

    Returns a list of dicts with ``file_path`` and ``content`` keys,
    suitable for :func:`construct_queries`.
    """
    hunks: list[dict] = []
    current_file: str | None = None
    current_lines: list[str] = []

    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            # Flush previous file
            if current_file and current_lines:
                hunks.append({"file_path": current_file, "content": "\n".join(current_lines)})
                current_lines = []
            # Extract filename from "diff --git a/path b/path"
            m = re.search(r" b/(.+)$", line)
            current_file = m.group(1) if m else None
        elif current_file is not None:
            # Collect only changed lines (+/-) and hunk headers
            if line.startswith(("+", "-", "@@")):
                current_lines.append(line)

    # Flush last file
    if current_file and current_lines:
        hunks.append({"file_path": current_file, "content": "\n".join(current_lines)})

    return hunks


def _format_existing_threads(comments: list[dict], reviews: list[dict]) -> str:
    """Summarise existing review threads so the LLM avoids duplication."""
    parts: list[str] = []

    for review in reviews:
        body = (review.get("body") or "").strip()
        user = (review.get("user") or {}).get("login", "unknown")
        if body:
            parts.append(f"- [{user}] {body[:200]}")

    for comment in comments:
        body = (comment.get("body") or "").strip()
        user = (comment.get("user") or {}).get("login", "unknown")
        path = comment.get("path", "")
        if body:
            prefix = f"  ({path})" if path else ""
            parts.append(f"- [{user}]{prefix} {body[:200]}")

    if not parts:
        return "No existing review comments."
    return "Existing review threads:\n" + "\n".join(parts)


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_SYSTEM_TEMPLATE = """\
You are impersonating {profile_name}, a code reviewer. Your goal is to write \
a pull request review that feels EXACTLY like what {profile_name} would write — \
not just the same priorities, but the same VOICE, TONE, and REASONING STYLE.

## Reviewer Profile
{profile_document}

## Examples of {profile_name}'s Actual Review Comments
The following are real comments this reviewer has left on past pull requests. \
Study these carefully — they are the ground truth for how this person communicates:

{examples}

## Critical Voice Rules
- MATCH THE REVIEWER'S REASONING STYLE. If they ask questions ("Do we really \
need this?", "What happens if...?", "Could we...?"), YOU ask questions. If they \
make declarative statements, you make declarative statements. Do NOT default to \
formal pronouncements when the reviewer is conversational and exploratory.
- MATCH THE REVIEWER'S TYPICAL DEPTH. Some reviewers leave 2-word "nit: rename" \
comments. Others write multi-paragraph analyses with concrete proposals. Match \
the length and detail level from the examples.
- NEVER rubber-stamp. If you see real issues that this reviewer would flag, flag \
them even if the PR looks mostly good. A reviewer who pushes back would not write \
"/approve /lgtm" — they would ask for changes.
- Focus on what THIS REVIEWER would focus on, not what a generic reviewer would \
say. If the examples show they care about API semantics but never comment on test \
structure, you should comment on API semantics and skip test structure.
- Do NOT leave technically-correct-but-irrelevant comments. A comment the reviewer \
would never make is worse than no comment at all.
- The diff_snippet in each inline comment should be a short extract (1-5 lines) \
from the diff that anchors the comment.

## Output Format
Respond with valid JSON matching the provided schema.
"""

_USER_TEMPLATE = """\
## Pull Request to Review

**Title:** {title}
**Author:** {author}
**Description:**
{description}

## Changed Files
{file_list}

## Diff
```diff
{diff}
```
{codebase_context}
## Existing Discussion
{existing_threads}

Review this PR as {profile_name} would. You have full context of the source \
files being changed — use this to identify issues that go beyond the diff, such \
as inconsistencies with surrounding code, violated patterns, or missing \
interactions with other parts of the file. Provide a summary and inline comments \
with confidence scores (0.0-1.0). List any files you choose to abstain from \
reviewing in ``abstained_files`` with a reason in ``abstention_reason``.
"""


def _build_system_prompt(
    profile_name: str,
    profile_document: str,
    examples_text: str,
) -> str:
    return _SYSTEM_TEMPLATE.format(
        profile_name=profile_name,
        profile_document=profile_document,
        examples=examples_text if examples_text else "(No past examples available.)",
    )


def _format_codebase_context(context_files: dict[str, str], max_total_chars: int = 200_000) -> str:
    """Format fetched source files as codebase context for the LLM prompt.

    Prioritizes smaller files and truncates large ones to stay within budget.
    """
    if not context_files:
        return ""

    parts: list[str] = ["\n## Codebase Context (full source files from base branch)\n"]
    remaining = max_total_chars
    # Sort by size ascending — include smaller files first (more files > fewer complete files)
    sorted_files = sorted(context_files.items(), key=lambda kv: len(kv[1]))

    for path, content in sorted_files:
        if remaining <= 0:
            parts.append(f"\n*(additional files omitted — context budget exhausted)*\n")
            break

        # Determine language hint for code fencing
        ext = path.rsplit(".", 1)[-1] if "." in path else ""
        lang_map = {"go": "go", "py": "python", "js": "javascript", "ts": "typescript",
                     "rs": "rust", "java": "java", "rb": "ruby", "yaml": "yaml",
                     "yml": "yaml", "json": "json", "md": "markdown", "toml": "toml"}
        lang = lang_map.get(ext, "")

        if len(content) > remaining:
            content = content[:remaining] + "\n... (file truncated)"

        parts.append(f"### `{path}`")
        parts.append(f"```{lang}")
        parts.append(content)
        parts.append("```\n")

        remaining -= len(content)

    return "\n".join(parts)


def _build_user_prompt(
    profile_name: str,
    pr_data: dict,
    context_files: dict[str, str] | None = None,
    expertise_context: str = "",
) -> str:
    metadata = pr_data["metadata"]
    title = metadata.get("title", "(untitled)")
    author = (metadata.get("user") or {}).get("login", "unknown")
    description = (metadata.get("body") or "").strip() or "(no description)"

    files = pr_data.get("files", [])
    file_list = "\n".join(
        f"- `{f.get('filename', '?')}` (+{f.get('additions', 0)}, -{f.get('deletions', 0)})"
        for f in files
    ) or "(no files)"

    diff = pr_data.get("diff", "")
    # Truncate extremely large diffs to avoid exceeding context limits
    max_diff_chars = 120_000
    if len(diff) > max_diff_chars:
        diff = diff[:max_diff_chars] + "\n... (diff truncated)"

    existing_threads = _format_existing_threads(
        pr_data.get("comments", []),
        pr_data.get("reviews", []),
    )

    # Budget allocation for context sections (total prompt target: <400KB)
    # Diff: 120KB, codebase context: 200KB, expertise: 3KB, other: ~10KB
    # If diff is smaller, give the savings to codebase context
    diff_savings = max(0, 120_000 - len(diff))
    codebase_budget = min(200_000 + diff_savings, 300_000)

    codebase_context = _format_codebase_context(context_files or {}, max_total_chars=codebase_budget)

    # Combine expertise context with codebase context
    combined_context = ""
    if expertise_context:
        combined_context += expertise_context + "\n"
    if codebase_context:
        combined_context += codebase_context

    return _USER_TEMPLATE.format(
        title=title,
        author=author,
        description=description,
        file_list=file_list,
        diff=diff,
        existing_threads=existing_threads,
        profile_name=profile_name,
        codebase_context=combined_context,
    )


# ---------------------------------------------------------------------------
# Self-critique pass
# ---------------------------------------------------------------------------


class CritiqueItem(BaseModel):
    """Assessment of a single generated comment."""

    comment_index: int
    would_reviewer_leave_this: str = Field(
        description="Would this reviewer actually leave this comment? yes/probably/no"
    )
    phrasing_sounds_like_them: str = Field(
        description="Does the phrasing sound like the reviewer? yes/close/no"
    )
    already_covered: str = Field(
        description="Is this already covered by existing threads? yes/no"
    )


class CritiqueOutput(BaseModel):
    """Self-critique results for all generated comments."""

    assessments: list[CritiqueItem] = Field(default_factory=list)


_CRITIQUE_SYSTEM = """\
You are a strict quality filter for AI-generated code reviews. You are given a \
set of review comments generated to mimic a specific reviewer, along with \
examples of that reviewer's actual past comments.

For each generated comment, assess:
1. Would this reviewer actually leave this comment? (yes/probably/no)
   - "no" if it's a generic engineering observation that doesn't match this \
reviewer's known focus areas.
   - "no" if the reviewer's examples show they never comment on this type of issue.
   - "no" if it sounds like a different reviewer or a generic AI code review.
2. Does the phrasing sound like them? (yes/close/no)
   - Compare formality level, use of questions vs statements, comment length.
   - "no" if the comment is declarative when the reviewer typically asks questions.
   - "no" if the comment is formal when the reviewer is casual.
3. Is this already covered by existing review threads? (yes/no)

Be VERY strict. The goal is zero false positives — a comment the reviewer would \
never make is actively harmful. When in doubt, drop the comment.

Respond with valid JSON matching the provided schema.
"""

_CRITIQUE_USER_TEMPLATE = """\
## Reviewer's Actual Past Comments (Ground Truth)
{examples}

## Generated Comments to Assess
{generated_comments}

## Existing Review Threads
{existing_threads}

Assess each generated comment by its index (0-based).
"""


def _run_self_critique(
    config: LegitConfig,
    review: ReviewOutput,
    examples_text: str,
    existing_threads: str,
) -> ReviewOutput:
    """Run a self-critique pass to filter low-quality comments."""
    if not review.inline_comments:
        return review

    generated = []
    for i, c in enumerate(review.inline_comments):
        generated.append(
            f"[{i}] {c.file}: {c.comment}\n  (confidence: {c.confidence})\n  > {c.diff_snippet[:200]}"
        )

    user_prompt = _CRITIQUE_USER_TEMPLATE.format(
        examples=examples_text if examples_text else "(No examples available.)",
        generated_comments="\n\n".join(generated),
        existing_threads=existing_threads,
    )

    result = run_inference(
        system_prompt=_CRITIQUE_SYSTEM,
        user_prompt=user_prompt,
        config=config.model,
        response_model=CritiqueOutput,
    )

    if not isinstance(result, CritiqueOutput):
        logger.warning("Self-critique did not return structured output; keeping all comments.")
        return review

    # Build set of indices to drop
    drop_indices: set[int] = set()
    for item in result.assessments:
        idx = item.comment_index
        if idx < 0 or idx >= len(review.inline_comments):
            continue
        # Drop if reviewer wouldn't leave it
        if item.would_reviewer_leave_this.lower() == "no":
            drop_indices.add(idx)
        # Drop if already covered
        if item.already_covered.lower() == "yes":
            drop_indices.add(idx)

    if drop_indices:
        logger.info("Self-critique dropping %d/%d comments", len(drop_indices), len(review.inline_comments))

    filtered = [c for i, c in enumerate(review.inline_comments) if i not in drop_indices]
    return ReviewOutput(
        summary=review.summary,
        inline_comments=filtered,
        abstained_files=review.abstained_files,
        abstention_reason=review.abstention_reason,
    )


# ---------------------------------------------------------------------------
# Post-processing: threshold and cap
# ---------------------------------------------------------------------------


def _apply_filters(review: ReviewOutput, config: LegitConfig) -> ReviewOutput:
    """Apply confidence threshold and max_comments cap."""
    threshold = config.review.abstention_threshold
    max_comments = config.review.max_comments

    # Filter by confidence threshold
    filtered = [c for c in review.inline_comments if c.confidence >= threshold]

    # Sort by confidence descending so the cap keeps the best
    filtered.sort(key=lambda c: c.confidence, reverse=True)

    # Apply max_comments cap
    if max_comments is not None and len(filtered) > max_comments:
        filtered = filtered[:max_comments]

    return ReviewOutput(
        summary=review.summary,
        inline_comments=filtered,
        abstained_files=review.abstained_files,
        abstention_reason=review.abstention_reason,
    )


# ---------------------------------------------------------------------------
# Dry-run output
# ---------------------------------------------------------------------------


def _format_dry_run(review: ReviewOutput, profile_name: str) -> str:
    """Format the review as readable markdown for dry-run output."""
    lines: list[str] = []

    lines.append(f"# Review by {profile_name}")
    lines.append("")
    lines.append("## Summary")
    lines.append(review.summary)
    lines.append("")

    if review.inline_comments:
        lines.append("## Inline Comments")
        for comment in review.inline_comments:
            lines.append(f"### {comment.file} (confidence: {comment.confidence:.2f})")
            # Format diff snippet as blockquote
            for snippet_line in comment.diff_snippet.splitlines():
                lines.append(f"> {snippet_line}")
            lines.append("")
            lines.append(comment.comment)
            lines.append("")
    else:
        lines.append("## Inline Comments")
        lines.append("*(no comments — reviewer would likely approve without inline feedback)*")
        lines.append("")

    if review.abstained_files:
        lines.append("## Abstained Files")
        for f in review.abstained_files:
            lines.append(f"- `{f}`")
        if review.abstention_reason:
            lines.append(f"\n*Reason:* {review.abstention_reason}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# GitHub posting
# ---------------------------------------------------------------------------


def _post_review_to_github(
    config: LegitConfig,
    pr_url: str,
    review: ReviewOutput,
) -> str:
    """Post the review to GitHub via the Pull Request Review API.

    Returns the URL of the created review.
    """
    owner, repo, pull_number = parse_pr_url(pr_url)
    token = get_token(config.github)

    import httpx

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    # Build the review payload
    review_body = review.summary

    # Build inline comments for the review
    comments_payload: list[dict] = []
    for comment in review.inline_comments:
        # The GitHub Review API expects comments with path and body.
        # We use a single-comment review body approach for simplicity.
        comments_payload.append({
            "path": comment.file,
            "body": comment.comment,
            # Use the hunk_header to place the comment; position is relative
            # to the diff. We place at position 1 as a safe fallback since
            # exact line mapping requires parsing the diff ranges.
            "position": 1,
        })

    payload: dict = {
        "body": review_body,
        "event": config.review.review_action,
    }
    if comments_payload:
        payload["comments"] = comments_payload

    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pull_number}/reviews"

    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        resp = client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        result = resp.json()

    return result.get("html_url", url)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def generate_review(
    config: LegitConfig,
    profile_name: str,
    pr_url: str,
    dry_run: bool = True,
    output_path: Path | None = None,
) -> ReviewOutput:
    """Generate a PR review in the voice of *profile_name*.

    Pipeline steps:
    1. Load profile document from ``.legit/profiles/{name}.md``
    2. Fetch PR data (diff, files, description, comments, linked issues)
    3. Retrieve similar past comments via BM25 index
    4. Construct review prompt (system = profile + examples, user = PR context)
    5. Generate structured review via LLM
    6. Run self-critique pass (second LLM call)
    7. Apply confidence threshold filtering
    8. Apply max_comments cap
    9. Output: dry-run to stdout/file, or post to GitHub
    """
    # -- Step 1: Load profile + expertise index --------------------------------
    console.print(f"[bold]Loading profile:[/] {profile_name}")
    profile_document = load_profile(profile_name)

    # Load pre-built expertise index (optional — degrades gracefully)
    from legit.expertise import format_expertise_context, load_expertise_index, lookup_expertise
    expertise_index = load_expertise_index(profile_name)
    if expertise_index:
        console.print(f"  [dim]Loaded expertise index ({len(expertise_index.entries)} directories)[/]")
    else:
        console.print(f"  [dim]No expertise index — run 'legit build' to generate[/]")

    # -- Step 2: Fetch PR data -----------------------------------------------
    console.print(f"[bold]Fetching PR:[/] {pr_url}")
    with GitHubClient(config.github) as gh:
        pr_data = gh.fetch_pr_for_review(pr_url)

        pr_title = pr_data["metadata"].get("title", "(untitled)")
        file_count = len(pr_data.get("files", []))
        console.print(f"  PR: {pr_title} ({file_count} files changed)")

        # -- Step 2b: Fetch codebase context (full source files) ----------------
        console.print("[bold]Fetching codebase context...[/]")
        context_files = gh.fetch_pr_context_files(pr_url, pr_data)
        console.print(f"  Fetched {len(context_files)} source files ({sum(len(v) for v in context_files.values()) // 1024}KB)")

    # -- Step 3: Retrieve similar past comments ------------------------------
    console.print("[bold]Retrieving similar past comments...[/]")
    diff_hunks = _parse_diff_hunks(pr_data.get("diff", ""))
    queries = construct_queries(diff_hunks)

    # Collect changed file paths for codebase-specific path boosting
    pr_changed_files = [f.get("filename", "") for f in pr_data.get("files", [])]

    # Try semantic search first, fall back to BM25
    from legit.embeddings import is_available as embeddings_available, load_embedding_index

    retrieval_method = "bm25"
    embedding_index = load_embedding_index(profile_name) if embeddings_available() else None

    if embedding_index and len(embedding_index.documents) > 0:
        # Semantic retrieval — embed diff hunks and search
        retrieval_method = "semantic"
        query_texts = [f"{h.get('file_path', '')} {h.get('content', '')}" for h in diff_hunks]
        if not query_texts:
            query_texts = queries  # fallback to BM25-style text queries
        retrieved_docs = embedding_index.search_as_retrieval_docs(
            query_texts, top_k=config.retrieval.top_k,
        )
        console.print(f"  Retrieved {len(retrieved_docs)} examples via semantic search")
    else:
        # BM25 fallback
        temporal_half_life = 730
        for pc in config.profiles:
            if pc.name == profile_name:
                temporal_half_life = pc.temporal_half_life
                break

        retrieved_docs = retrieve(
            profile_name=profile_name,
            queries=queries,
            top_k=config.retrieval.top_k,
            type_weights=config.retrieval.type_weights,
            temporal_half_life=temporal_half_life,
            pr_changed_files=pr_changed_files,
        )
        console.print(f"  Retrieved {len(retrieved_docs)} examples via BM25")

    examples_text = format_examples(retrieved_docs)

    # -- Step 3b: Expertise lookup (pre-built, instant) -----------------------
    expertise_context = ""
    if expertise_index:
        expertise_entries = lookup_expertise(expertise_index, pr_changed_files)
        expertise_context = format_expertise_context(expertise_entries)
        if expertise_entries:
            console.print(f"  Matched {len(expertise_entries)} areas of expertise")

    # -- Step 4: Construct prompt --------------------------------------------
    system_prompt = _build_system_prompt(profile_name, profile_document, examples_text)
    user_prompt = _build_user_prompt(
        profile_name, pr_data,
        context_files=context_files,
        expertise_context=expertise_context,
    )

    # -- Step 5: Generate review via LLM -------------------------------------
    console.print("[bold]Generating review...[/]")
    result = run_inference(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        config=config.model,
        response_model=ReviewOutput,
    )

    if isinstance(result, ReviewOutput):
        review = result
    else:
        # Fallback: LLM returned raw text instead of structured output
        logger.warning("LLM returned raw text instead of ReviewOutput; wrapping as summary.")
        review = ReviewOutput(summary=str(result))

    console.print(
        f"  Generated {len(review.inline_comments)} inline comments, "
        f"{len(review.abstained_files)} abstained files"
    )

    # -- Step 6: Self-critique pass ------------------------------------------
    console.print("[bold]Running self-critique...[/]")
    existing_threads = _format_existing_threads(
        pr_data.get("comments", []),
        pr_data.get("reviews", []),
    )
    review = _run_self_critique(config, review, examples_text, existing_threads)
    console.print(f"  After critique: {len(review.inline_comments)} comments remain")

    # -- Steps 7 & 8: Threshold filtering and max_comments cap ---------------
    review = _apply_filters(review, config)
    console.print(f"  After filtering: {len(review.inline_comments)} comments (threshold={config.review.abstention_threshold})")

    # -- Step 9: Output ------------------------------------------------------
    if dry_run:
        md_text = _format_dry_run(review, profile_name)

        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(md_text)
            console.print(f"[green]Review written to {output_path}[/]")
        else:
            # Print to stdout (not stderr) for piping
            out = Console()
            out.print(Markdown(md_text))
    else:
        console.print("[bold]Posting review to GitHub...[/]")
        review_url = _post_review_to_github(config, pr_url, review)
        console.print(f"[green]Review posted:[/] {review_url}")

    return review
