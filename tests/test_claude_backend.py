"""Claude inference disables tools and verifies returned model metadata."""

import json
import logging
from subprocess import CompletedProcess
from unittest.mock import Mock

import pytest

from legit.model_runner import _run_claude


def test_claude_disables_tools_and_reports_actual_model(monkeypatch, caplog):
    monkeypatch.setattr("legit.model_runner._check_cli", lambda name: "/bin/claude")
    run = Mock(
        return_value=CompletedProcess(
            [],
            0,
            json.dumps(
                {
                    "result": " review text ",
                    "modelUsage": {"claude-actual-version": {}},
                    "is_error": False,
                }
            ),
            "",
        )
    )
    monkeypatch.setattr("legit.model_runner.subprocess.run", run)
    with caplog.at_level(logging.INFO, logger="legit.model_runner"):
        assert _run_claude("untrusted input", "sonnet", 60, 0.3) == "review text"
    args = run.call_args.args[0]
    assert args[args.index("--tools") + 1] == ""
    assert "--no-session-persistence" in args
    assert args[args.index("--output-format") + 1] == "json"
    assert args[args.index("--model") + 1] == "sonnet"
    assert run.call_args.kwargs["input"] == "untrusted input"
    assert "claude-actual-version" in caplog.text


@pytest.mark.parametrize(
    "stdout",
    [
        "plain text",
        '{"result":"hello"}',
        '{"result":"hello","modelUsage":{}}',
        '{"is_error":true,"result":"failed","modelUsage":{"model":{}}}',
        '{"modelUsage":{"model":{}}}',
        "[]",
    ],
)
def test_claude_rejects_unverified_or_failed_result(monkeypatch, stdout):
    monkeypatch.setattr("legit.model_runner._check_cli", lambda name: "/bin/claude")
    monkeypatch.setattr(
        "legit.model_runner.subprocess.run", Mock(return_value=CompletedProcess([], 0, stdout, ""))
    )
    with pytest.raises(RuntimeError):
        _run_claude("prompt", "sonnet", 60, 0.3)
