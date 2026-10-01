"""Calibration accounting must distinguish bad reviews from failed evaluations."""

import pytest

import legit.calibrate as cal
from legit.config import LegitConfig, ProfileConfig, ProfileSource
from legit.models import InlineComment, ReviewOutput


def holdout(number):
    return cal.HoldoutPR(
        pr_url=f"https://github.com/o/r/pull/{number}",
        pr_number=number,
        pr_title="Example",
        reviewer_comment_count=2,
    )


@pytest.fixture
def config(monkeypatch):
    monkeypatch.setattr("legit.profile.verify_build_provenance", lambda _: None, raising=False)
    monkeypatch.setattr(cal, "load_profile_holdout", lambda _: "2026-04-01")
    from contextlib import nullcontext

    monkeypatch.setattr(cal, "GitHubClient", lambda _: nullcontext(None))
    monkeypatch.setattr(
        cal, "reconstruct_snapshot", lambda *args: ({"metadata": {"title": "Historical"}}, {})
    )
    return LegitConfig(
        profiles=[
            ProfileConfig(name="reviewer", sources=[ProfileSource(repo="o/r", username="reviewer")])
        ]
    )


def test_valid_zero_counts_in_average_and_failures_are_separate(config, monkeypatch, caplog):
    def generate(**kwargs):
        assert kwargs["dry_run"] is True
        assert kwargs["include_existing_threads"] is False
        if kwargs["pr_url"].endswith("/3"):
            raise RuntimeError("generation unavailable")
        return ReviewOutput(summary="Review")

    judgments = iter(
        [
            cal.JudgeOutput(
                issue_detection=0, voice_fidelity=0, appropriate_abstention=0, false_positives=0
            ),
            cal.JudgeOutput(
                issue_detection=10, voice_fidelity=10, appropriate_abstention=10, false_positives=10
            ),
            "invalid judge response",
            RuntimeError("judge unavailable"),
        ]
    )

    def judge(**kwargs):
        result = next(judgments)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr("legit.review.generate_review", generate)
    monkeypatch.setattr(cal, "run_inference", judge)
    result = cal.run_calibration(config, "reviewer", holdouts=[holdout(i) for i in range(1, 6)])
    assert [s.status for s in result.scores] == [
        "scored",
        "scored",
        "generation_failed",
        "judge_failed",
        "judge_failed",
    ]
    assert result.holdout_count == 5
    assert result.context_mode == "reviewed_head_inferred_base"
    assert "results are not clean release evidence" in caplog.text
    assert result.scored_count == 2
    assert result.generation_failure_count == 1
    assert result.judge_failure_count == 2
    assert result.avg_issue_detection == 5
    assert result.avg_voice_fidelity == 5
    # Silence is penalized, so the second overall score is 6 rather than 10.
    assert result.avg_overall == 3
    assert result.scores[-1].judge_reasoning == "Judge failed: judge unavailable"


def test_all_failed_is_explicitly_unscored(config, monkeypatch):
    def generate(**kwargs):
        raise RuntimeError("unavailable")

    monkeypatch.setattr("legit.review.generate_review", generate)
    result = cal.run_calibration(config, "reviewer", holdouts=[holdout(1)])
    assert result.scored_count == 0
    assert result.generation_failure_count == result.holdout_count == 1


def test_snapshot_failure_never_generates_or_judges(config, monkeypatch):
    def snapshot(*args):
        raise ValueError("Reviewed commit missing")

    def forbidden(**kwargs):
        pytest.fail("Cannot infer against an unverified snapshot")

    monkeypatch.setattr(cal, "reconstruct_snapshot", snapshot)
    monkeypatch.setattr("legit.review.generate_review", forbidden)
    monkeypatch.setattr(cal, "run_inference", forbidden)
    result = cal.run_calibration(config, "reviewer", holdouts=[holdout(1)])
    assert result.scored_count == 0
    assert result.snapshot_failure_count == 1
    assert result.scores[0].status == "snapshot_failed"


def test_explicit_empty_holdouts_do_not_discover_or_call_provider(config, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Explicit holdout list must not trigger discovery")

    monkeypatch.setattr(cal, "GitHubClient", forbidden)
    with pytest.raises(RuntimeError, match="No holdout PRs"):
        cal.run_calibration(config, "reviewer", holdouts=[])


def test_judge_failure_preserves_generated_comment_count(config, monkeypatch):
    review = ReviewOutput(
        summary="Review",
        inline_comments=[
            InlineComment(
                file="a.go",
                comment="Check this",
                confidence=0.8,
                hunk_header="@@ -1 +1 @@",
                diff_snippet="+code",
            )
        ],
    )
    monkeypatch.setattr("legit.review.generate_review", lambda **_: review)

    def judge(**kwargs):
        raise RuntimeError("unavailable")

    monkeypatch.setattr(cal, "run_inference", judge)
    result = cal.run_calibration(config, "reviewer", holdouts=[holdout(1)])
    assert result.scores[0].status == "judge_failed"
    assert result.scores[0].generated_comment_count == 1
    assert result.scores[0].real_comment_count == 2


def test_legacy_score_is_not_silently_declared_successful():
    score = cal.CalibrationScore(pr_url="https://github.com/o/r/pull/1", pr_number=1)
    assert score.status == "unknown"


@pytest.mark.parametrize("reason", ["Missing provenance", "Artifact hash mismatch"])
def test_provenance_failure_prevents_discovery_and_inference(config, monkeypatch, reason):
    def invalid(_):
        raise ValueError(reason)

    def forbidden(*args, **kwargs):
        pytest.fail("Unverified training artifacts cannot reach discovery or inference")

    monkeypatch.setattr("legit.profile.verify_build_provenance", invalid)
    monkeypatch.setattr(cal, "load_profile_holdout", forbidden)
    monkeypatch.setattr(cal, "GitHubClient", forbidden)
    monkeypatch.setattr(cal, "run_inference", forbidden)
    with pytest.raises(ValueError, match=reason):
        cal.run_calibration(config, "reviewer")
