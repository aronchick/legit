"""Calibration system — measures review quality against ground truth.

Finds PRs where the target reviewer actually left comments, generates reviews
with legit, then scores how well the generated review matches the real one.
This is the core feedback loop that tells us whether the system works.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from legit.config import LegitConfig, legit_path
from legit.github_client import GitHubClient, get_token
from legit.model_runner import run_inference
from legit.models import ReviewOutput

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class HoldoutPR(BaseModel):
    """A PR where the reviewer left real comments, used as ground truth."""

    pr_url: str
    pr_number: int
    pr_title: str
    reviewer_comments: list[dict] = Field(default_factory=list)
    reviewer_comment_count: int = 0


class CalibrationScore(BaseModel):
    """Score for a single holdout PR comparison."""

    pr_url: str
    pr_number: int
    issue_detection: float = 0.0      # 0-10: Did we catch the same problems?
    voice_fidelity: float = 0.0       # 0-10: Does it sound like the reviewer?
    appropriate_abstention: float = 0.0  # 0-10: Did we stay quiet where they would?
    false_positives: float = 0.0      # 0-10: Did we avoid flagging things they wouldn't? (inverted: 10 = no false positives)
    overall: float = 0.0             # 0-10: Weighted average
    judge_reasoning: str = ""
    generated_comment_count: int = 0
    real_comment_count: int = 0


class CalibrationResult(BaseModel):
    """Full calibration run results for a profile."""

    profile_name: str
    timestamp: str
    holdout_count: int = 0
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
) -> list[HoldoutPR]:
    """Find recent PRs where this reviewer left substantive inline comments.

    These become our ground truth — we know exactly what the reviewer said,
    so we can compare against what legit generates.
    """
    # Search for PRs this user reviewed with comments
    q = f"repo:{owner}/{repo} is:pr is:merged reviewed-by:{username} comments:>3"
    resp = gh._transport.get(
        "/search/issues",
        params={"q": q, "per_page": min(count * 3, 100), "sort": "created", "order": "desc"},
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
            comments_resp = gh._transport.get(
                f"/repos/{owner}/{repo}/pulls/{pr_num}/comments",
            )
            all_comments = comments_resp.json()
            if not isinstance(all_comments, list):
                continue

            reviewer_comments = [
                c for c in all_comments
                if (c.get("user") or {}).get("login", "").lower() == username.lower()
                and (c.get("body") or "").strip()
            ]

            # Only include PRs where the reviewer left 2+ substantive comments
            if len(reviewer_comments) < 2:
                continue

            holdouts.append(HoldoutPR(
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
            ))

        except Exception:
            continue

    return holdouts


# ---------------------------------------------------------------------------
# LLM-as-judge scoring
# ---------------------------------------------------------------------------

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
    for c in generated.inline_comments:
        ai_parts.append(f"**{c.file}** (confidence: {c.confidence})\n{c.comment}")
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

    result = run_inference(
        system_prompt=_JUDGE_SYSTEM,
        user_prompt=user_prompt,
        config=config.model,
        response_model=JudgeOutput,
    )

    if isinstance(result, JudgeOutput):
        overall = (
            result.issue_detection * 0.35
            + result.voice_fidelity * 0.25
            + result.appropriate_abstention * 0.20
            + result.false_positives * 0.20
        )
        return CalibrationScore(
            pr_url=holdout.pr_url,
            pr_number=holdout.pr_number,
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
    from legit.review import generate_review

    # Find the profile's source repo
    profile_cfg = None
    for p in config.profiles:
        if p.name == profile_name:
            profile_cfg = p
            break
    if not profile_cfg:
        raise ValueError(f"Profile '{profile_name}' not found")

    src = profile_cfg.sources[0]
    owner, repo = src.repo.split("/", 1)

    # Step 1: Find holdout PRs (or use provided ones)
    if not holdouts:
        with GitHubClient(config.github) as gh:
            holdouts = find_holdout_prs(gh, owner, repo, src.username, count=holdout_count)

    if not holdouts:
        raise RuntimeError(f"No holdout PRs found for {src.username} in {src.repo}")

    # Step 2: Generate reviews and score each
    scores: list[CalibrationScore] = []

    for i, holdout in enumerate(holdouts):
        logger.info(
            "Calibrating %d/%d: PR #%d (%d real comments)",
            i + 1, len(holdouts), holdout.pr_number, holdout.reviewer_comment_count,
        )

        try:
            # Generate review
            generated = generate_review(
                config=config,
                profile_name=profile_name,
                pr_url=holdout.pr_url,
                dry_run=True,
            )

            # Score against ground truth
            score = _score_review(config, holdout, generated)
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
            scores.append(CalibrationScore(
                pr_url=holdout.pr_url,
                pr_number=holdout.pr_number,
                judge_reasoning=f"Generation failed: {exc}",
                real_comment_count=holdout.reviewer_comment_count,
            ))

    # Step 3: Compute averages
    valid_scores = [s for s in scores if s.overall > 0]
    n = len(valid_scores) or 1

    result = CalibrationResult(
        profile_name=profile_name,
        timestamp=datetime.now(tz=timezone.utc).isoformat(),
        holdout_count=len(holdouts),
        scores=scores,
        avg_issue_detection=round(sum(s.issue_detection for s in valid_scores) / n, 1),
        avg_voice_fidelity=round(sum(s.voice_fidelity for s in valid_scores) / n, 1),
        avg_appropriate_abstention=round(sum(s.appropriate_abstention for s in valid_scores) / n, 1),
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
