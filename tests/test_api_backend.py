"""Tests for the litellm API backend and deployment env overrides."""

from pathlib import Path

import yaml

from legit.config import load_config
from legit.model_runner import _run_api


class _FakeMessage:
    content = "  hello from api  "


class _FakeChoice:
    message = _FakeMessage()


class _FakeResponse:
    choices = [_FakeChoice()]


def test_run_api_passes_model_and_strips(monkeypatch):
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return _FakeResponse()

    import litellm

    monkeypatch.setattr(litellm, "completion", fake_completion)
    out = _run_api("prompt text", "gemini/gemini-2.5-pro", 300, 0.3)
    assert out == "hello from api"
    assert captured["model"] == "gemini/gemini-2.5-pro"
    assert captured["messages"] == [{"role": "user", "content": "prompt text"}]
    assert captured["temperature"] == 0.3


def test_run_api_defaults_model(monkeypatch):
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return _FakeResponse()

    import litellm

    monkeypatch.setattr(litellm, "completion", fake_completion)
    _run_api("p", None, 300, 0.0)
    assert captured["model"] == "gemini/gemini-2.5-pro"


def test_env_overrides_model_provider(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.dump({"model": {"provider": "claude"}}))

    monkeypatch.setenv("LEGIT_MODEL_PROVIDER", "api")
    monkeypatch.setenv("LEGIT_MODEL_NAME", "gemini/gemini-2.5-pro")
    cfg = load_config(cfg_path)
    assert cfg.model.provider == "api"
    assert cfg.model.name == "gemini/gemini-2.5-pro"


def test_no_env_overrides_keeps_config(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.dump({"model": {"provider": "claude"}}))
    monkeypatch.delenv("LEGIT_MODEL_PROVIDER", raising=False)
    monkeypatch.delenv("LEGIT_MODEL_NAME", raising=False)
    cfg = load_config(cfg_path)
    assert cfg.model.provider == "claude"
    assert cfg.model.name is None
