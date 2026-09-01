"""Tests for the temporal holdout boundary (train/eval split)."""

from pathlib import Path

from legit.calibrate import find_holdout_prs
from legit.profile import _filter_items_before, load_profile_holdout, save_profile_meta
from legit.review import _build_user_prompt


class TestFilterItemsBefore:
    def test_no_cutoff_keeps_everything(self):
        items = [{"created_at": "2026-05-01T00:00:00Z"}]
        assert _filter_items_before(items, None) == items

    def test_drops_items_on_or_after_cutoff(self):
        items = [
            {"created_at": "2026-03-15T10:00:00Z", "body": "old"},
            {"created_at": "2026-04-01T00:00:01Z", "body": "boundary"},
            {"created_at": "2026-06-20T10:00:00Z", "body": "new"},
        ]
        kept = _filter_items_before(items, "2026-04-01")
        assert [i["body"] for i in kept] == ["old"]

    def test_keeps_items_without_timestamps(self):
        items = [{"body": "undated"}, {"created_at": "2026-05-01T00:00:00Z", "body": "new"}]
        kept = _filter_items_before(items, "2026-04-01")
        assert [i["body"] for i in kept] == ["undated"]

    def test_reads_submitted_at_and_commit_dates(self):
        items = [
            {"submitted_at": "2026-05-01T00:00:00Z", "body": "review"},
            {"commit": {"author": {"date": "2026-05-01T00:00:00Z"}}, "body": "commit"},
            {"submitted_at": "2026-01-01T00:00:00Z", "body": "old-review"},
        ]
        kept = _filter_items_before(items, "2026-04-01")
        assert [i["body"] for i in kept] == ["old-review"]


class TestProfileMetaStamp:
    def test_round_trip(self, legit_dir: Path):
        save_profile_meta("alice", "2026-04-01")
        assert load_profile_holdout("alice") == "2026-04-01"

    def test_missing_stamp_returns_none(self, legit_dir: Path):
        assert load_profile_holdout("nobody") is None

    def test_null_boundary_returns_none(self, legit_dir: Path):
        save_profile_meta("bob", None)
        assert load_profile_holdout("bob") is None


class TestHoldoutSearchQuery:
    def _capture_query(self, monkeypatch, merged_after):
        captured = {}

        class FakeTransport:
            def get(self, url, params=None):
                if "search" in url:
                    captured["q"] = params["q"]

                    class R:
                        @staticmethod
                        def json():
                            return {"items": []}

                    return R()
                raise AssertionError("unexpected call")

        class FakeGH:
            _transport = FakeTransport()

        find_holdout_prs(FakeGH(), "kubernetes", "kubernetes", "thockin", merged_after=merged_after)
        return captured["q"]

    def test_boundary_added_to_query(self, monkeypatch):
        q = self._capture_query(monkeypatch, "2026-04-01")
        assert "created:>=2026-04-01" in q
        assert "merged:>=2026-04-01" in q

    def test_no_boundary_leaves_query_unchanged(self, monkeypatch):
        q = self._capture_query(monkeypatch, None)
        assert "created:" not in q
        assert "merged:" not in q


class TestJudgeModelOverride:
    def _score(self, monkeypatch):
        import legit.calibrate as cal
        from legit.calibrate import HoldoutPR, JudgeOutput, _score_review
        from legit.config import LegitConfig
        from legit.models import ReviewOutput

        captured = {}

        def fake_inference(system_prompt, user_prompt, config, response_model=None):
            captured["config"] = config
            return JudgeOutput(
                issue_detection=5, voice_fidelity=5, appropriate_abstention=5, false_positives=5
            )

        monkeypatch.setattr(cal, "run_inference", fake_inference)
        holdout = HoldoutPR(
            pr_url="https://github.com/o/r/pull/1",
            pr_number=1,
            pr_title="t",
            reviewer_comments=[],
            reviewer_comment_count=0,
        )
        _score_review(LegitConfig(), holdout, ReviewOutput(summary="s"))
        return captured["config"]

    def test_judge_env_pins_model(self, monkeypatch):
        monkeypatch.setenv("LEGIT_JUDGE_MODEL", "openai/gpt-5.3-codex")
        cfg = self._score(monkeypatch)
        assert cfg.provider == "api"
        assert cfg.name == "openai/gpt-5.3-codex"

    def test_no_env_uses_config_model(self, monkeypatch):
        monkeypatch.delenv("LEGIT_JUDGE_MODEL", raising=False)
        cfg = self._score(monkeypatch)
        assert cfg.provider == "gemini"  # LegitConfig default


class TestExistingThreadsWithheld:
    PR_DATA = {
        "metadata": {"title": "Fix thing", "user": {"login": "author"}, "body": "desc"},
        "files": [],
        "diff": "diff --git a/x b/x",
        "comments": [{"user": {"login": "thockin"}, "body": "SECRET-GROUND-TRUTH"}],
        "reviews": [],
    }

    def test_included_by_default(self):
        prompt = _build_user_prompt("thockin", self.PR_DATA)
        assert "SECRET-GROUND-TRUTH" in prompt

    def test_withheld_in_calibration_mode(self):
        prompt = _build_user_prompt("thockin", self.PR_DATA, include_existing_threads=False)
        assert "SECRET-GROUND-TRUTH" not in prompt
        assert "withheld" in prompt
