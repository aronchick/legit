"""Calibration system — measures review quality against ground truth.

Finds PRs where the target reviewer actually left comments, generates reviews
with legit, then scores how well the generated review matches the real one.
This is the core feedback loop that tells us whether the system works.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from legit.config import LegitConfig, legit_path
from legit.github_client import GitHubClient, parse_pr_url
from legit.model_runner import run_inference
from legit.models import ReviewOutput
from legit.profile import load_profile_holdout

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class HoldoutPR(BaseModel):
    """A PR where the reviewer left real comments, used as ground truth."""

    pr_url: str
    pr_number: int
    pr_title: str
    reviewer_comments: list[dict[str, Any]] = Field(default_factory=list)
    reviewer_comment_count: int = 0
    reviewed_commit: str | None = None
    reviewed_at: str | None = None
    review_id: int | None = None


class CalibrationScore(BaseModel):
    """Score for a single holdout PR comparison."""

    pr_url: str
    pr_number: int
    # Historical artifacts did not distinguish failed judging from valid zeros.
    status: Literal["unknown", "scored", "snapshot_failed", "generation_failed", "judge_failed"] = (
        "unknown"
    )
    issue_detection: float = 0.0  # 0-10: Did we catch the same problems?
    voice_fidelity: float = 0.0  # 0-10: Does it sound like the reviewer?
    appropriate_abstention: float = 0.0  # 0-10: Did we stay quiet where they would?
    false_positives: float = (
        0.0  # 0-10: Did we avoid flagging things they wouldn't? (inverted: 10 = no false positives)
    )
    overall: float = 0.0  # 0-10: Weighted average
    judge_reasoning: str = ""
    generated_comment_count: int = 0
    real_comment_count: int = 0
    reviewed_commit: str | None = None
    base_commit: str | None = None
    review_id: int | None = None
    reviewed_at: str | None = None


class CalibrationResult(BaseModel):
    """Full calibration run results for a profile."""

    profile_name: str
    timestamp: str
    context_mode: Literal["unknown", "live_pr_not_review_time", "reviewed_head_inferred_base"] = (
        "unknown"
    )
    holdout_count: int = 0
    scored_count: int = 0
    generation_failure_count: int = 0
    judge_failure_count: int = 0
    snapshot_failure_count: int = 0
    scores: list[CalibrationScore] = Field(default_factory=list)
    avg_issue_detection: float = 0.0
    avg_voice_fidelity: float = 0.0
    avg_appropriate_abstention: float = 0.0
    avg_false_positives: float = 0.0
    avg_overall: float = 0.0


# ---------------------------------------------------------------------------
# Holdout set discovery
# ---------------------------------------------------------------------------


def find_holdout_prs(
    gh: GitHubClient,
    owner: str,
    repo: str,
    username: str,
    count: int = 10,
    merged_after: str | None = None,
) -> list[HoldoutPR]:
    """Find recent PRs where this reviewer left substantive inline comments.

    These become our ground truth — we know exactly what the reviewer said,
    so we can compare against what legit generates.

    When *merged_after* is set, only PRs created AND merged on/after that date
    qualify, so nothing the profile trained on can appear in the eval set.
    """
    # Search for PRs this user reviewed with comments. Exclude PRs they
    # authored — their inline comments there are author replies ("Done in
    # next push"), not reviews, and poison the ground truth.
    q = f"repo:{owner}/{repo} is:pr is:merged reviewed-by:{username} -author:{username} comments:>3"
    if merged_after:
        q += f" created:>={merged_after} merged:>={merged_after}"
    resp = gh._transport.get(
        "/search/issues",
        params={
            "q": q,
            "per_page": min(max(count * 10, 30), 100),
            "sort": "created",
            "order": "desc",
        },
    )
    data = resp.json()
    candidates = data.get("items", [])

    holdouts: list[HoldoutPR] = []

    for item in candidates:
        if len(holdouts) >= count:
            break

        pr_num = item.get("number")
        if not pr_num:
            continue

        # Fetch this reviewer's inline comments on this PR
        try:
            all_comments, complete = gh._transport.get_paginated(
                f"/repos/{owner}/{repo}/pulls/{pr_num}/comments"
            )
            if not complete:
                continue
            if not isinstance(all_comments, list):
                continue

            reviewer_comments = [
                c
                for c in all_comments
                if (c.get("user") or {}).get("login", "").lower() == username.lower()
                and (c.get("body") or "").strip()
                and not c.get("in_reply_to_id")
            ]

            if not reviewer_comments:
                continue
            # Only the first inline review round: later rounds see intervening fixes.
            reviewer_comments.sort(key=lambda c: c.get("created_at", ""))
            first = reviewer_comments[0]
            review_id = first.get("pull_request_review_id")
            reviewer_comments = [
                c for c in reviewer_comments if c.get("pull_request_review_id") == review_id
            ]
            commits = {c.get("original_commit_id") for c in reviewer_comments}
            if not review_id or len(commits) != 1 or None in commits:
                continue

            # Only include PRs where the reviewer left 2+ substantive comments
            if len(reviewer_comments) < 2:
                continue

            holdouts.append(
                HoldoutPR(
                    pr_url=f"https://github.com/{owner}/{repo}/pull/{pr_num}",
                    pr_number=pr_num,
                    pr_title=item.get("title", ""),
                    reviewer_comments=[
                        {
                            "body": c.get("body", ""),
                            "path": c.get("path", ""),
                            "diff_hunk": c.get("diff_hunk", ""),
                        }
                        for c in reviewer_comments
                    ],
                    reviewer_comment_count=len(reviewer_comments),
                    reviewed_commit=first.get("original_commit_id"),
                    reviewed_at=first.get("created_at"),
                    review_id=review_id,
                )
            )

        except Exception:
            continue

    return holdouts


# ---------------------------------------------------------------------------
# LLM-as-judge scoring
# ---------------------------------------------------------------------------


def reconstruct_snapshot(
    gh: GitHubClient, holdout: HoldoutPR, username: str, holdout_after: str | None
) -> tuple[dict[str, Any], dict[str, str]]:
    """Use the reviewed head with an inferred base for exploratory comparisons.

    Mutable titles/descriptions and all discussions are withheld. Summary-only
    reviews, replies, later rounds, missing/binary patches and large PRs are excluded.
    The current target branch's history is queried by review time and a merge base
    inferred. Neither the historical target branch nor its exact revision is proven.
    This is not clean release evidence or a replay of the human's exact context.
    """
    if not holdout_after or not all(
        (holdout.reviewed_commit, holdout.reviewed_at, holdout.review_id)
    ):
        raise ValueError("Missing training cutoff or first-review commit provenance")
    assert holdout.reviewed_commit is not None
    owner, repo, number = parse_pr_url(holdout.pr_url)
    prefix = f"/repos/{owner}/{repo}"
    metadata = gh._transport.get(f"{prefix}/pulls/{number}").json()
    if (metadata.get("user") or {}).get("login", "").lower() == username.lower():
        raise ValueError("Reviewer authored the PR")
    if not metadata.get("created_at") or metadata["created_at"] < holdout_after:
        raise ValueError("PR predates training cutoff")
    review = gh._transport.get(f"{prefix}/pulls/{number}/reviews/{holdout.review_id}").json()
    if (
        review.get("commit_id") != holdout.reviewed_commit
        or (review.get("user") or {}).get("login", "").lower() != username.lower()
        or not review.get("submitted_at")
    ):
        raise ValueError("Review identity or commit is not verified")
    comments, complete = gh._transport.get_paginated(f"{prefix}/pulls/{number}/comments")
    first_round = sorted(
        [
            c
            for c in comments
            if not c.get("in_reply_to_id")
            and (c.get("user") or {}).get("login", "").lower() == username.lower()
            and c.get("body")
        ],
        key=lambda c: c.get("created_at", ""),
    )
    if (
        not complete
        or not first_round
        or first_round[0].get("pull_request_review_id") != holdout.review_id
    ):
        raise ValueError("First inline review round is not verified")
    first_round = [c for c in first_round if c.get("pull_request_review_id") == holdout.review_id]
    if (
        len(first_round) < 2
        or any(c.get("original_commit_id") != holdout.reviewed_commit for c in first_round)
        or first_round[0].get("created_at") != holdout.reviewed_at
        or any(c.get("updated_at") != c.get("created_at") for c in first_round)
    ):
        raise ValueError("Missing, edited or mixed-commit first-round ground truth")
    holdout.reviewer_comments = [
        {"body": c["body"], "path": c.get("path", ""), "diff_hunk": c.get("diff_hunk", "")}
        for c in first_round
    ]
    holdout.reviewer_comment_count = len(first_round)
    base_ref = (metadata.get("base") or {}).get("ref")
    if not base_ref:
        raise ValueError("Missing target branch")
    bases = gh._transport.get(
        f"{prefix}/commits",
        params={"sha": base_ref, "until": holdout.reviewed_at, "per_page": 1},
    ).json()
    if not isinstance(bases, list) or not bases or not bases[0].get("sha"):
        raise ValueError("Target branch history unavailable at review time")
    comparison = gh._transport.get(
        f"{prefix}/compare/{bases[0]['sha']}...{holdout.reviewed_commit}"
    ).json()
    if comparison.get("status") not in {"ahead", "diverged"}:
        raise ValueError("Reviewed commit has no reconstructable unmerged delta")
    files = comparison.get("files", [])
    if not files or len(files) >= 300 or any(not f.get("patch") for f in files):
        raise ValueError("Incomplete, binary, empty or oversized historical diff")
    for file in files:
        _validate_patch(file)
    diff = "\n".join(
        f"diff --git a/{f.get('previous_filename', f['filename'])} b/{f['filename']}\n"
        f"--- a/{f.get('previous_filename', f['filename'])}\n+++ b/{f['filename']}\n{f['patch']}"
        for f in files
    )
    if len(diff) > 120_000:
        raise ValueError("Historical diff exceeds prompt budget")
    paths = [f["filename"] for f in files if f.get("status") != "removed"]
    context = gh.fetch_file_contents(owner, repo, paths, ref=holdout.reviewed_commit)
    if set(context) != set(paths) or sum(map(len, context.values())) > 200_000:
        raise ValueError("Historical source context incomplete or exceeds prompt budget")
    snapshot = {
        "metadata": {
            "title": "Historical review (mutable title withheld)",
            "body": "",
            "user": metadata.get("user"),
            "head": {"sha": holdout.reviewed_commit},
            "base": comparison.get("merge_base_commit", {}),
        },
        "diff": diff,
        "files": files,
        "comments": [],
        "reviews": [],
        "linked_issues": [],
    }
    return snapshot, context


def _validate_patch(file: dict[str, Any]) -> None:
    """Reject truncated/malformed unified hunks and changed-line count mismatches."""
    patch = file.get("patch")
    if not isinstance(patch, str) or not patch:
        raise ValueError("Missing unified patch")
    expected_additions = file.get("additions")
    expected_deletions = file.get("deletions")
    if type(expected_additions) is not int or type(expected_deletions) is not int:
        raise ValueError("Missing authoritative patch change counts")
    additions = deletions = 0
    remaining_old = remaining_new = 0
    seen_hunk = False
    for line in patch.splitlines():
        header = re.fullmatch(r"@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@.*", line)
        if header:
            if remaining_old or remaining_new:
                raise ValueError("Truncated unified patch hunk")
            remaining_old = int(header.group(1)) if header.group(1) is not None else 1
            remaining_new = int(header.group(2)) if header.group(2) is not None else 1
            seen_hunk = True
            continue
        if line == "\\ No newline at end of file" and seen_hunk:
            continue
        if not seen_hunk or not line or line[0] not in {"+", "-", " "}:
            raise ValueError("Malformed unified patch line")
        if line[0] == "+":
            additions += 1
            remaining_new -= 1
        elif line[0] == "-":
            deletions += 1
            remaining_old -= 1
        else:
            remaining_old -= 1
            remaining_new -= 1
        if remaining_old < 0 or remaining_new < 0:
            raise ValueError("Unified patch exceeds declared hunk size")
    if not seen_hunk or remaining_old or remaining_new:
        raise ValueError("Truncated unified patch hunk")
    if additions != expected_additions or deletions != expected_deletions:
        raise ValueError("Unified patch does not match API change counts")


_JUDGE_SYSTEM = """\
You are an expert evaluator comparing an AI-generated code review against the \
real review left by the actual human reviewer on the same PR.

Score the AI review on four dimensions (0-10 each):

1. **issue_detection** (0-10): Did the AI catch the same problems the real \
reviewer flagged? Score 10 if it found all the same issues, 0 if it missed everything.

2. **voice_fidelity** (0-10): Does the AI review sound like the real reviewer? \
Same tone, formality level, phrasing patterns, level of detail? Score 10 if \
indistinguishable from the real reviewer.

3. **appropriate_abstention** (0-10): Did the AI stay quiet on things the real \
reviewer didn't comment on? Or did it add noise the reviewer would never have \
raised? Score 10 if it was perfectly calibrated on what to skip.

4. **false_positives** (0-10): Were there AI comments that the real reviewer \
would never have made? Score 10 if every AI comment was something the reviewer \
would plausibly say, 0 if most were off-base.

Be strict. A score of 7+ means genuinely impressive. Most AI reviews will \
score 4-6.

Respond with valid JSON matching the provided schema.\
"""

_JUDGE_USER = """\
## Real Reviewer's Comments (Ground Truth)

The real reviewer left {real_count} comments on this PR:

{real_comments}

## AI-Generated Review

Summary: {ai_summary}

The AI generated {ai_count} inline comments:

{ai_comments}

## PR Context

Title: {pr_title}
URL: {pr_url}

Score the AI review against the real reviewer's comments.\
"""


class JudgeOutput(BaseModel):
    issue_detection: float = Field(ge=0, le=10)
    voice_fidelity: float = Field(ge=0, le=10)
    appropriate_abstention: float = Field(ge=0, le=10)
    false_positives: float = Field(ge=0, le=10)
    reasoning: str = ""


def _score_review(
    config: LegitConfig,
    holdout: HoldoutPR,
    generated: ReviewOutput,
) -> CalibrationScore:
    """Use LLM-as-judge to score a generated review against ground truth."""

    # Format real comments
    real_parts = []
    for c in holdout.reviewer_comments:
        real_parts.append(f"**{c.get('path', '(no file)')}**\n{c.get('body', '')}")
    real_text = "\n\n---\n\n".join(real_parts)

    # Format AI comments
    ai_parts = []
    for comment in generated.inline_comments:
        ai_parts.append(f"**{comment.file}** (confidence: {comment.confidence})\n{comment.comment}")
    ai_text = "\n\n---\n\n".join(ai_parts) if ai_parts else "(no inline comments)"

    user_prompt = _JUDGE_USER.format(
        real_count=holdout.reviewer_comment_count,
        real_comments=real_text,
        ai_summary=generated.summary,
        ai_count=len(generated.inline_comments),
        ai_comments=ai_text,
        pr_title=holdout.pr_title,
        pr_url=holdout.pr_url,
    )

    # LEGIT_JUDGE_MODEL pins the judge to one model regardless of which
    # backend generated the review. Without it, each backend judges its own
    # output and cross-model comparisons are self-preference noise.
    import os

    judge_config = config.model
    judge_model = os.environ.get("LEGIT_JUDGE_MODEL")
    if judge_model:
        from legit.config import ModelConfig

        judge_config = ModelConfig(provider="api", name=judge_model, temperature=0.0)

    result = run_inference(
        system_prompt=_JUDGE_SYSTEM,
        user_prompt=user_prompt,
        config=judge_config,
        response_model=JudgeOutput,
    )

    if isinstance(result, JudgeOutput):
        # Total silence where the real reviewer spoke is not "perfect
        # abstention" — without this, a model that generates nothing outscores
        # one that honestly tries and misses.
        if not generated.inline_comments and holdout.reviewer_comment_count > 0:
            result.appropriate_abstention = 0.0
            result.false_positives = 0.0

        overall = (
            result.issue_detection * 0.35
            + result.voice_fidelity * 0.25
            + result.appropriate_abstention * 0.20
            + result.false_positives * 0.20
        )
        return CalibrationScore(
            pr_url=holdout.pr_url,
            pr_number=holdout.pr_number,
            status="scored",
            issue_detection=result.issue_detection,
            voice_fidelity=result.voice_fidelity,
            appropriate_abstention=result.appropriate_abstention,
            false_positives=result.false_positives,
            overall=round(overall, 1),
            judge_reasoning=result.reasoning,
            generated_comment_count=len(generated.inline_comments),
            real_comment_count=holdout.reviewer_comment_count,
        )

    # Fallback if judge failed
    return CalibrationScore(
        pr_url=holdout.pr_url,
        pr_number=holdout.pr_number,
        status="judge_failed",
        judge_reasoning=f"Judge failed: {result}",
        generated_comment_count=len(generated.inline_comments),
        real_comment_count=holdout.reviewer_comment_count,
    )


# ---------------------------------------------------------------------------
# Full calibration run
# ---------------------------------------------------------------------------


def run_calibration(
    config: LegitConfig,
    profile_name: str,
    holdout_count: int = 10,
    holdouts: list[HoldoutPR] | None = None,
) -> CalibrationResult:
    """Run a full calibration: find holdouts, generate reviews, score them.

    Returns a CalibrationResult with per-PR scores and averages.
    """
    from legit.profile import verify_build_provenance
    from legit.review import generate_review

    # Find the profile's source repo
    profile_cfg = None
    for p in config.profiles:
        if p.name == profile_name:
            profile_cfg = p
            break
    if not profile_cfg:
        raise ValueError(f"Profile '{profile_name}' not found")

    verify_build_provenance(profile_name)

    src = profile_cfg.sources[0]
    owner, repo = src.repo.split("/", 1)

    # Only a cutoff stamped at build time attests to a training boundary;
    # changing config after building must not make an unfiltered profile eligible.
    holdout_after = load_profile_holdout(profile_name)
    if holdout_after:
        logger.info("Holdout boundary: only evaluating PRs created+merged >= %s", holdout_after)
    else:
        logger.warning(
            "No holdout_after boundary set — eval PRs may overlap the training corpus. "
            "Rebuild with 'legit build --holdout-after YYYY-MM-DD'."
        )

    # Step 1: Find holdout PRs (or use provided ones)
    if holdouts is None:
        with GitHubClient(config.github) as gh:
            holdouts = find_holdout_prs(
                gh, owner, repo, src.username, count=holdout_count, merged_after=holdout_after
            )

    if not holdouts:
        raise RuntimeError(f"No holdout PRs found for {src.username} in {src.repo}")

    logger.warning(
        "Exploratory calibration only: the reviewed head is verified but the base is "
        "inferred from the current target branch's history. The historical target branch "
        "and exact base are unverified; results are not clean release evidence. "
        "Mutable prose, summary-only reviews and later rounds are excluded."
    )

    # Step 2: Generate reviews and score each
    scores: list[CalibrationScore] = []

    for i, holdout in enumerate(holdouts):
        logger.info(
            "Calibrating %d/%d: PR #%d (%d real comments)",
            i + 1,
            len(holdouts),
            holdout.pr_number,
            holdout.reviewer_comment_count,
        )

        phase = "snapshot"
        generated = None
        try:
            with GitHubClient(config.github) as gh:
                pr_snapshot, context_snapshot = reconstruct_snapshot(
                    gh, holdout, src.username, holdout_after
                )
            phase = "generation"
            # Generate review. Existing threads are withheld — the ground-truth
            # comments live on these PRs and must not reach the prompt.
            generated = generate_review(
                config=config,
                profile_name=profile_name,
                pr_url=holdout.pr_url,
                dry_run=True,
                include_existing_threads=False,
                pr_snapshot=pr_snapshot,
                context_snapshot=context_snapshot,
            )

            # Score against ground truth
            phase = "judge"
            score = _score_review(
                config,
                holdout.model_copy(update={"pr_title": pr_snapshot["metadata"]["title"]}),
                generated,
            )
            score.reviewed_commit = holdout.reviewed_commit
            score.reviewed_at = holdout.reviewed_at
            score.review_id = holdout.review_id
            score.base_commit = pr_snapshot["metadata"].get("base", {}).get("sha")
            scores.append(score)

            logger.info(
                "  PR #%d: issue=%.1f voice=%.1f abstention=%.1f false_pos=%.1f overall=%.1f",
                holdout.pr_number,
                score.issue_detection,
                score.voice_fidelity,
                score.appropriate_abstention,
                score.false_positives,
                score.overall,
            )

        except Exception as exc:
            logger.error("  PR #%d failed: %s", holdout.pr_number, exc)
            scores.append(
                CalibrationScore(
                    pr_url=holdout.pr_url,
                    pr_number=holdout.pr_number,
                    status=(
                        "snapshot_failed"
                        if phase == "snapshot"
                        else "generation_failed"
                        if phase == "generation"
                        else "judge_failed"
                    ),
                    judge_reasoning=f"{phase.capitalize()} failed: {exc}",
                    generated_comment_count=len(generated.inline_comments) if generated else 0,
                    real_comment_count=holdout.reviewer_comment_count,
                )
            )

    # Step 3: Compute averages
    valid_scores = [s for s in scores if s.status == "scored"]
    n = len(valid_scores) or 1

    result = CalibrationResult(
        profile_name=profile_name,
        timestamp=datetime.now(tz=UTC).isoformat(),
        context_mode="reviewed_head_inferred_base",
        holdout_count=len(holdouts),
        scored_count=len(valid_scores),
        generation_failure_count=sum(s.status == "generation_failed" for s in scores),
        judge_failure_count=sum(s.status == "judge_failed" for s in scores),
        snapshot_failure_count=sum(s.status == "snapshot_failed" for s in scores),
        scores=scores,
        avg_issue_detection=round(sum(s.issue_detection for s in valid_scores) / n, 1),
        avg_voice_fidelity=round(sum(s.voice_fidelity for s in valid_scores) / n, 1),
        avg_appropriate_abstention=round(
            sum(s.appropriate_abstention for s in valid_scores) / n, 1
        ),
        avg_false_positives=round(sum(s.false_positives for s in valid_scores) / n, 1),
        avg_overall=round(sum(s.overall for s in valid_scores) / n, 1),
    )

    return result


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def save_calibration(result: CalibrationResult) -> Path:
    """Save calibration results to disk."""
    cal_dir = legit_path() / "calibration" / result.profile_name
    cal_dir.mkdir(parents=True, exist_ok=True)

    # Save with timestamp for history
    ts = result.timestamp.replace(":", "-").replace("+", "").split(".")[0]
    path = cal_dir / f"calibration_{ts}.json"
    path.write_text(json.dumps(result.model_dump(mode="json"), indent=2) + "\n")

    # Also save as latest
    latest = cal_dir / "latest.json"
    latest.write_text(json.dumps(result.model_dump(mode="json"), indent=2) + "\n")

    return path


def load_latest_calibration(profile_name: str) -> CalibrationResult | None:
    """Load the most recent calibration result for a profile."""
    path = legit_path() / "calibration" / profile_name / "latest.json"
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
        return CalibrationResult.model_validate(raw)
    except Exception:
        return None


def list_calibration_history(profile_name: str) -> list[Path]:
    """List all calibration result files for a profile."""
    cal_dir = legit_path() / "calibration" / profile_name
    if not cal_dir.exists():
        return []
    return sorted(cal_dir.glob("calibration_*.json"))
