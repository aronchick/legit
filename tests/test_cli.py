"""Tests for legit.cli — CLI commands via typer's CliRunner."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from legit.cli import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# legit --help
# ---------------------------------------------------------------------------


class TestHelp:
    def test_help_shows_all_commands(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "init" in result.output
        assert "fetch" in result.output
        assert "build" in result.output
        assert "review" in result.output
        assert "calibrate" in result.output
        assert "serve" in result.output


# ---------------------------------------------------------------------------
# legit init
# ---------------------------------------------------------------------------


class TestInit:
    def test_creates_directory_structure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("legit.config.LEGIT_DIR", str(tmp_path / ".legit"))

        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0

        root = tmp_path / ".legit"
        assert (root / "config.yaml").exists()
        assert (root / "profiles").is_dir()
        assert (root / "data").is_dir()
        assert (root / "index").is_dir()
        assert (root / "calibration").is_dir()

    def test_does_not_overwrite_existing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.chdir(tmp_path)
        root = tmp_path / ".legit"
        root.mkdir()
        config_path = root / "config.yaml"
        config_path.write_text("existing: true\n")
        monkeypatch.setattr("legit.config.LEGIT_DIR", str(root))

        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        # Should not have overwritten
        assert "existing: true" in config_path.read_text()


# ---------------------------------------------------------------------------
# legit fetch — error cases
# ---------------------------------------------------------------------------


class TestFetch:
    def test_missing_config_shows_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("legit.config.LEGIT_DIR", str(tmp_path / ".legit"))

        result = runner.invoke(app, ["fetch"])
        assert result.exit_code == 1
        assert "config.yaml" in result.output.lower() or "init" in result.output.lower()

    @patch("legit.config.load_config")
    def test_missing_token_shows_error(self, mock_load: MagicMock, monkeypatch: pytest.MonkeyPatch):
        from legit.config import LegitConfig, ProfileConfig, ProfileSource

        mock_load.return_value = LegitConfig(
            profiles=[
                ProfileConfig(
                    name="test",
                    sources=[ProfileSource(repo="o/r", username="u")],
                )
            ]
        )
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

        # validate_token will fail because of missing token
        result = runner.invoke(app, ["fetch"])
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# legit build — error cases
# ---------------------------------------------------------------------------


class TestBuild:
    def test_missing_config_shows_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("legit.config.LEGIT_DIR", str(tmp_path / ".legit"))

        result = runner.invoke(app, ["build"])
        assert result.exit_code == 1

    @patch("legit.config.load_config")
    def test_no_profiles_shows_error(self, mock_load: MagicMock):
        from legit.config import LegitConfig

        mock_load.return_value = LegitConfig(profiles=[])

        result = runner.invoke(app, ["build"])
        assert result.exit_code == 1
        assert "no profiles" in result.output.lower() or "configured" in result.output.lower()

    @patch("legit.config.load_config")
    def test_unknown_profile_shows_error(self, mock_load: MagicMock):
        from legit.config import LegitConfig, ProfileConfig, ProfileSource

        mock_load.return_value = LegitConfig(
            profiles=[
                ProfileConfig(name="alice", sources=[ProfileSource(repo="o/r", username="a")])
            ]
        )

        result = runner.invoke(app, ["build", "--profile", "nonexistent"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()


# ---------------------------------------------------------------------------
# legit review — error cases
# ---------------------------------------------------------------------------


class TestReview:
    def test_missing_config_shows_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("legit.config.LEGIT_DIR", str(tmp_path / ".legit"))

        result = runner.invoke(app, ["review", "--pr", "https://github.com/o/r/pull/1"])
        assert result.exit_code == 1

    @patch("legit.config.load_config")
    def test_no_profiles_shows_error(self, mock_load: MagicMock):
        from legit.config import LegitConfig

        mock_load.return_value = LegitConfig(profiles=[])

        result = runner.invoke(app, ["review", "--pr", "https://github.com/o/r/pull/1"])
        assert result.exit_code == 1

    def test_missing_pr_flag(self):
        """--pr is required; omitting it should fail."""
        result = runner.invoke(app, ["review"])
        assert result.exit_code != 0
