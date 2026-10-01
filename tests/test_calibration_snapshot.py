"""Historical evaluation never substitutes a post-fix live PR for review-time code."""

from unittest.mock import MagicMock

import pytest

from legit.calibrate import HoldoutPR, _validate_patch, find_holdout_prs, reconstruct_snapshot
from legit.config import LegitConfig
from legit.models import ReviewOutput
from legit.review import generate_review


@pytest.fixture
def historical():
    when = "2026-05-02T00:00:00Z"
    holdout = HoldoutPR(
        pr_url="https://github.com/o/r/pull/1",
        pr_number=1,
        pr_title="LIVE ANSWER",
        reviewed_commit="review-sha",
        reviewed_at=when,
        review_id=11,
    )
    comments = [
        {
            "body": f"SECRET ANSWER {i}",
            "path": "a.go",
            "user": {"login": "reviewer"},
            "pull_request_review_id": 11,
            "original_commit_id": "review-sha",
            "created_at": when,
            "updated_at": when,
        }
        for i in range(2)
    ]
    metadata = {
        "title": "LIVE ANSWER",
        "body": "POST-REVIEW EXPLANATION",
        "user": {"login": "author"},
        "created_at": "2026-05-01T00:00:00Z",
        "base": {"ref": "main"},
        "head": {"sha": "post-fix-sha"},
    }
    comparison = {
        "status": "ahead",
        "merge_base_commit": {"sha": "base-sha"},
        "files": [
            {
                "filename": "a.go",
                "status": "modified",
                "additions": 1,
                "deletions": 1,
                "patch": "@@ -1 +1 @@\n-old\n+BUG BEFORE FIX",
            }
        ],
    }
    responses = {
        "/repos/o/r/pulls/1": metadata,
        "/repos/o/r/pulls/1/reviews/11": {
            "commit_id": "review-sha",
            "user": {"login": "reviewer"},
            "submitted_at": when,
        },
        "/repos/o/r/commits": [{"sha": "base-sha"}],
        "/repos/o/r/compare/base-sha...review-sha": comparison,
    }
    gh = MagicMock()
    gh._transport.get.side_effect = lambda path, **kwargs: MagicMock(json=lambda: responses[path])
    gh._transport.get_paginated.return_value = (comments, True)
    gh.fetch_file_contents.return_value = {"a.go": "BUG BEFORE FIX"}
    return gh, holdout, responses, comments


def test_snapshot_uses_reviewed_commit_and_withholds_answers_and_mutable_prose(historical):
    gh, holdout, _, _ = historical
    snapshot, context = reconstruct_snapshot(gh, holdout, "reviewer", "2026-04-01")
    assert "BUG BEFORE FIX" in snapshot["diff"]
    assert snapshot["metadata"]["head"]["sha"] == "review-sha"
    assert "SECRET ANSWER" not in str(snapshot)
    assert "LIVE ANSWER" not in str(snapshot)
    assert "POST-REVIEW" not in str(snapshot)
    assert holdout.reviewer_comment_count == 2
    gh._transport.get.assert_any_call(
        "/repos/o/r/commits", params={"sha": "main", "until": holdout.reviewed_at, "per_page": 1}
    )
    gh.fetch_file_contents.assert_called_once_with("o", "r", ["a.go"], ref="review-sha")
    assert context == {"a.go": "BUG BEFORE FIX"}


def test_discovery_keeps_first_inline_round_only_and_records_provenance(historical):
    gh, _, _, comments = historical
    comments.extend(
        [
            {**comments[0], "in_reply_to_id": 1, "body": "Reply"},
            {
                **comments[0],
                "created_at": "2026-05-03",
                "pull_request_review_id": 12,
                "body": "Later round",
                "original_commit_id": "post-fix-sha",
            },
        ]
    )
    gh._transport.get.side_effect = None
    gh._transport.get.return_value.json.return_value = {
        "items": [{"number": 1, "title": "Example"}]
    }
    found = find_holdout_prs(gh, "o", "r", "reviewer", count=1)
    assert len(found) == 1
    assert found[0].review_id == 11
    assert found[0].reviewed_commit == "review-sha"
    assert found[0].reviewed_at == "2026-05-02T00:00:00Z"
    assert found[0].reviewer_comment_count == 2
    assert [c["body"] for c in found[0].reviewer_comments] == ["SECRET ANSWER 0", "SECRET ANSWER 1"]


@pytest.mark.parametrize(
    "defect",
    [
        "no_cutoff",
        "no_commit",
        "author",
        "old_pr",
        "wrong_commit",
        "later_round",
        "edited_answer",
        "mixed_commits",
        "incomplete_comments",
        "already_merged",
        "missing_patch",
        "missing_source",
        "large_diff",
    ],
)
def test_snapshot_fails_closed(historical, defect):
    gh, holdout, responses, comments = historical
    cutoff = "2026-04-01"
    metadata = responses["/repos/o/r/pulls/1"]
    comparison = responses["/repos/o/r/compare/base-sha...review-sha"]
    if defect == "no_cutoff":
        cutoff = None
    elif defect == "no_commit":
        holdout.reviewed_commit = None
    elif defect == "author":
        metadata["user"]["login"] = "reviewer"
    elif defect == "old_pr":
        metadata["created_at"] = "2026-03-01"
    elif defect == "wrong_commit":
        responses["/repos/o/r/pulls/1/reviews/11"]["commit_id"] = "post-fix-sha"
    elif defect == "later_round":
        comments[0]["pull_request_review_id"] = 10
    elif defect == "edited_answer":
        comments[0]["updated_at"] = "2026-06-01"
    elif defect == "mixed_commits":
        comments[0]["original_commit_id"] = "other-sha"
    elif defect == "incomplete_comments":
        gh._transport.get_paginated.return_value = (comments, False)
    elif defect == "already_merged":
        comparison["status"] = "identical"
    elif defect == "missing_patch":
        comparison["files"][0].pop("patch")
    elif defect == "missing_source":
        gh.fetch_file_contents.return_value = {}
    elif defect == "large_diff":
        comparison["files"][0]["patch"] = "x" * 120_001
    with pytest.raises(ValueError):
        reconstruct_snapshot(gh, holdout, "reviewer", cutoff)


def test_snapshot_generation_never_fetches_live_pr(monkeypatch):
    import legit.review as review

    gh = MagicMock(side_effect=AssertionError("Live GitHub fetch forbidden"))
    monkeypatch.setattr(review, "GitHubClient", gh)
    monkeypatch.setattr(review, "load_profile", lambda _: "Reviewer profile")
    monkeypatch.setattr("legit.expertise.load_expertise_index", lambda _: None)
    monkeypatch.setattr("legit.embeddings.is_available", lambda: False)
    monkeypatch.setattr(review, "retrieve", lambda **kwargs: [])
    prompts = []

    def infer(**kwargs):
        prompts.append(kwargs["user_prompt"])
        return ReviewOutput(summary="Review")

    monkeypatch.setattr(review, "run_inference", infer)
    generate_review(
        LegitConfig(),
        "reviewer",
        "https://github.com/o/r/pull/1",
        include_existing_threads=False,
        pr_snapshot={"metadata": {"title": "Historical"}, "diff": "PRE-FIX DIFF", "files": []},
        context_snapshot={"a.go": "PRE-FIX SOURCE"},
    )
    gh.assert_not_called()
    assert "PRE-FIX DIFF" in prompts[0]
    assert "PRE-FIX SOURCE" in prompts[0]


def test_partial_or_posted_snapshot_is_rejected_before_work():
    with pytest.raises(ValueError, match="together"):
        generate_review(LegitConfig(), "reviewer", "url", pr_snapshot={})
    with pytest.raises(ValueError, match="dry-run"):
        generate_review(
            LegitConfig(), "reviewer", "url", dry_run=False, pr_snapshot={}, context_snapshot={}
        )


@pytest.mark.parametrize(
    "patch,additions,deletions",
    [
        ("@@ -1,2 +1,2 @@\n-old\n+new", 1, 1),
        ("@@ -1 +1 @@\n-old\n+new", 2, 1),
        ("@@ -1 +1 @@\n-old\n+new\n+extra", 2, 1),
        ("@@ -1 +1 @@\n-old\n+new\n@@ -9,2 +9,2 @@\n only one", 1, 1),
        ("@@ -1 +1 @@\n-old\n+new", None, None),
        ("@@ -1 +1 @@\n-old\n+new\n...truncated", 1, 1),
    ],
)
def test_nonempty_incomplete_patch_is_rejected(patch, additions, deletions):
    with pytest.raises(ValueError):
        _validate_patch({"patch": patch, "additions": additions, "deletions": deletions})


def test_complete_multi_hunk_patch_accepts_zero_ranges_and_no_newline_marker():
    _validate_patch(
        {
            "patch": "@@ -0,0 +1,1 @@\n+added\n@@ -9,1 +10,0 @@\n-deleted\n"
            "\\ No newline at end of file",
            "additions": 1,
            "deletions": 1,
        }
    )
