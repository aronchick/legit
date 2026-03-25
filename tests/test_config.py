"""Tests for legit.config — configuration loading, validation, defaults, and overrides."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from legit.config import (
    CalibrationConfig,
    GitHubConfig,
    LegitConfig,
    ModelConfig,
    ProfileConfig,
    ProfileSource,
    RetrievalConfig,
    ReviewConfig,
    legit_path,
    load_config,
    write_default_config,
)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


class TestDefaults:
    def test_model_config_defaults(self):
        cfg = ModelConfig()
        assert cfg.provider == "gemini"
        assert cfg.name is None
        assert cfg.temperature == 0.3

    def test_github_config_defaults(self):
        cfg = GitHubConfig()
        assert cfg.token_env == "GITHUB_TOKEN"

    def test_retrieval_config_defaults(self):
        cfg = RetrievalConfig()
        assert cfg.top_k == 10
        assert cfg.index_type == "bm25"
        assert cfg.type_weights == {"pr_review": 2.0, "issue_comment": 1.0, "commit_comment": 0.5}

    def test_review_config_defaults(self):
        cfg = ReviewConfig()
        assert cfg.post_to_github is False
        assert cfg.review_action == "COMMENT"
        assert cfg.max_comments is None
        assert cfg.abstention_threshold == 0.5

    def test_calibration_config_defaults(self):
        cfg = CalibrationConfig()
        assert cfg.holdout_count == 15
        assert cfg.max_iterations == 5
        assert cfg.target_score == 8.0

    def test_legit_config_defaults(self):
        cfg = LegitConfig()
        assert cfg.model.provider == "gemini"
        assert cfg.profiles == []
        assert cfg.retrieval.top_k == 10
        assert cfg.review.post_to_github is False

    def test_profile_config_defaults(self):
        cfg = ProfileConfig(
            name="alice",
            sources=[ProfileSource(repo="org/repo", username="alice")],
        )
        assert cfg.chunk_size == 150
        assert cfg.temporal_half_life == 730
        assert cfg.map_concurrency == 4

    def test_profile_source_defaults(self):
        src = ProfileSource(repo="org/repo", username="alice")
        assert src.type == "primary"


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_load_valid_config(self, tmp_path: Path):
        config_data = {
            "model": {"provider": "claude", "name": "opus", "temperature": 0.1},
            "profiles": [
                {
                    "name": "alice",
                    "sources": [{"repo": "org/repo", "username": "alice"}],
                    "chunk_size": 200,
                }
            ],
            "retrieval": {"top_k": 20},
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(config_data))

        cfg = load_config(config_path)
        assert cfg.model.provider == "claude"
        assert cfg.model.name == "opus"
        assert cfg.model.temperature == 0.1
        assert len(cfg.profiles) == 1
        assert cfg.profiles[0].name == "alice"
        assert cfg.profiles[0].chunk_size == 200
        assert cfg.retrieval.top_k == 20

    def test_load_empty_yaml(self, tmp_path: Path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text("")

        cfg = load_config(config_path)
        # Should get all defaults
        assert cfg.model.provider == "gemini"
        assert cfg.profiles == []

    def test_load_missing_file_raises(self, tmp_path: Path):
        config_path = tmp_path / "nonexistent.yaml"
        with pytest.raises(FileNotFoundError, match="Config not found"):
            load_config(config_path)

    def test_load_partial_config_gets_defaults(self, tmp_path: Path):
        config_data = {"model": {"provider": "codex"}}
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(config_data))

        cfg = load_config(config_path)
        assert cfg.model.provider == "codex"
        # Everything else should be defaults
        assert cfg.github.token_env == "GITHUB_TOKEN"
        assert cfg.retrieval.top_k == 10

    def test_load_config_default_path(self, legit_dir_with_config: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.chdir(legit_dir_with_config.parent)
        cfg = load_config()
        assert cfg.profiles[0].name == "testuser"


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


class TestConfigValidation:
    def test_invalid_extra_fields_ignored(self, tmp_path: Path):
        config_data = {
            "model": {"provider": "gemini", "unknown_field": "value"},
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(config_data))
        # Pydantic by default ignores extra fields
        cfg = load_config(config_path)
        assert cfg.model.provider == "gemini"

    def test_profile_requires_name_and_sources(self):
        with pytest.raises(Exception):
            ProfileConfig(sources=[])  # type: ignore[call-arg] — missing name

    def test_profile_source_requires_repo_and_username(self):
        with pytest.raises(Exception):
            ProfileSource(repo="org/repo")  # type: ignore[call-arg] — missing username


# ---------------------------------------------------------------------------
# write_default_config
# ---------------------------------------------------------------------------


class TestWriteDefaultConfig:
    def test_writes_valid_yaml(self, tmp_path: Path):
        config_path = tmp_path / "config.yaml"
        write_default_config(config_path)

        assert config_path.exists()
        raw = yaml.safe_load(config_path.read_text())
        assert "model" in raw
        assert "profiles" in raw
        assert raw["profiles"][0]["name"] == "example"

    def test_default_config_is_loadable(self, tmp_path: Path):
        config_path = tmp_path / "config.yaml"
        write_default_config(config_path)
        cfg = load_config(config_path)
        assert isinstance(cfg, LegitConfig)


# ---------------------------------------------------------------------------
# CLI flag overrides (simulated)
# ---------------------------------------------------------------------------


class TestCLIOverrides:
    def test_override_concurrency(self):
        """Simulates the CLI overriding map_concurrency on a profile."""
        cfg = ProfileConfig(
            name="alice",
            sources=[ProfileSource(repo="org/repo", username="alice")],
            map_concurrency=4,
        )
        assert cfg.map_concurrency == 4
        cfg.map_concurrency = 8
        assert cfg.map_concurrency == 8

    def test_override_max_comments(self):
        cfg = ReviewConfig(max_comments=None)
        assert cfg.max_comments is None
        # Simulating CLI override
        cfg.max_comments = 5
        assert cfg.max_comments == 5


# ---------------------------------------------------------------------------
# legit_path
# ---------------------------------------------------------------------------


class TestLegitPath:
    def test_returns_path(self):
        p = legit_path()
        assert isinstance(p, Path)
        assert str(p) == ".legit"
