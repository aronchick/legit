"""LLM abstraction layer — wraps CLI tools (claude, gemini, codex) via litellm CustomLLM."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any, Callable, Iterator, Optional, Union

import litellm
from litellm import CustomLLM, ModelResponse
from litellm.types.utils import Choices, Message
from pydantic import BaseModel, ValidationError

from legit.config import ModelConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT = 300  # 5 minutes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_cli(name: str) -> str:
    """Return the absolute path for *name* or raise."""
    path = shutil.which(name)
    if path is None:
        raise FileNotFoundError(
            f"CLI '{name}' not found on PATH. Install it or adjust your config."
        )
    return path


def _messages_to_prompt(messages: list[dict[str, str]]) -> str:
    """Flatten a list of chat messages into a single prompt string.

    System messages are prepended, user/assistant messages follow.
    """
    parts: list[str] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "system":
            parts.insert(0, content)
        else:
            parts.append(content)
    return "\n\n".join(parts)


def _build_model_response(text: str, model: str | None = None) -> ModelResponse:
    """Wrap raw text in a litellm ModelResponse."""
    msg = Message(content=text, role="assistant")
    choice = Choices(message=msg, index=0, finish_reason="stop")
    resp = ModelResponse(choices=[choice])
    if model:
        resp.model = model
    return resp


def _extract_json(text: str) -> str:
    """Try to pull a JSON object/array out of *text*.

    Looks for fenced code blocks first, then falls back to the first
    brace-delimited or bracket-delimited substring.
    """
    # Try fenced code block first
    m = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()

    # Fall back to outermost braces / brackets
    for open_ch, close_ch in [("{", "}"), ("[", "]")]:
        start = text.find(open_ch)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == open_ch:
                depth += 1
            elif text[i] == close_ch:
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
    return text.strip()


# ---------------------------------------------------------------------------
# CLI invocation backends
# ---------------------------------------------------------------------------


def _run_claude(prompt: str, model_name: str | None, timeout: int, temperature: float) -> str:
    """Run the ``claude`` CLI and return its stdout.

    Always pipes the prompt via stdin to avoid hitting the OS ``ARG_MAX``
    limit on large prompts (profiles + diffs can easily exceed 2 MB).
    """
    cli = _check_cli("claude")

    cmd = [cli, "--print", "--output-format", "text"]
    if model_name:
        cmd.extend(["--model", model_name])

    logger.debug("Running claude CLI (prompt length: %d chars)", len(prompt))

    result = subprocess.run(
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    if result.returncode != 0:
        stderr = result.stderr.strip()
        logger.error("claude CLI failed (rc=%d): %s", result.returncode, stderr)
        raise RuntimeError(f"claude CLI exited with code {result.returncode}: {stderr}")

    return result.stdout.strip()


def _run_gemini(prompt: str, model_name: str | None, timeout: int, temperature: float) -> str:
    """Run the ``gemini`` CLI and return its stdout."""
    cli = _check_cli("gemini")

    cmd = [cli]
    logger.debug("Running gemini CLI: %s", " ".join(cmd))

    result = subprocess.run(
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    if result.returncode != 0:
        stderr = result.stderr.strip()
        logger.error("gemini CLI failed (rc=%d): %s", result.returncode, stderr)
        raise RuntimeError(f"gemini CLI exited with code {result.returncode}: {stderr}")

    return result.stdout.strip()


def _run_codex(prompt: str, model_name: str | None, timeout: int, temperature: float) -> str:
    """Run the ``codex`` CLI in non-interactive mode and return its output."""
    cli = _check_cli("codex")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tmp:
        output_path = tmp.name

    cmd = [cli, "exec", "-o", output_path, prompt]
    logger.debug("Running codex CLI: %s", " ".join(cmd))

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    if result.returncode != 0:
        stderr = result.stderr.strip()
        logger.error("codex CLI failed (rc=%d): %s", result.returncode, stderr)
        raise RuntimeError(f"codex CLI exited with code {result.returncode}: {stderr}")

    try:
        from pathlib import Path

        return Path(output_path).read_text().strip()
    except FileNotFoundError:
        return result.stdout.strip()


_BACKENDS: dict[str, Callable[..., str]] = {
    "claude": _run_claude,
    "gemini": _run_gemini,
    "codex": _run_codex,
    "openai": _run_codex,
}


# ---------------------------------------------------------------------------
# litellm CustomLLM provider
# ---------------------------------------------------------------------------


class CLIBackedProvider(CustomLLM):
    """A litellm custom provider that shells out to a CLI tool for inference."""

    def completion(
        self,
        model: str,
        messages: list,
        api_base: str,
        custom_prompt_dict: dict,
        model_response: ModelResponse,
        print_verbose: Callable,
        encoding,
        api_key,
        logging_obj,
        optional_params: dict,
        acompletion=None,
        litellm_params=None,
        logger_fn=None,
        headers={},
        timeout: Optional[Union[float]] = None,
        client=None,
    ) -> ModelResponse:
        # Parse provider/model from the model string.
        # litellm passes it as "provider/model_name" for custom providers.
        provider, _, model_name = model.partition("/")
        if provider not in _BACKENDS:
            raise ValueError(f"Unknown CLI provider: {provider!r}")

        effective_timeout = int(timeout) if timeout else DEFAULT_TIMEOUT
        temperature = optional_params.get("temperature", 0.3)

        prompt = _messages_to_prompt(messages)
        backend = _BACKENDS[provider]
        raw = backend(prompt, model_name or None, effective_timeout, temperature)

        return _build_model_response(raw, model=model)


# ---------------------------------------------------------------------------
# Register providers with litellm
# ---------------------------------------------------------------------------

_provider_instance = CLIBackedProvider()

for _name in _BACKENDS:
    litellm.custom_provider_map.append(
        {"provider": _name, "custom_handler": _provider_instance}
    )


# ---------------------------------------------------------------------------
# Structured output helpers
# ---------------------------------------------------------------------------

_MAX_REPAIR_RETRIES = 2


def _try_parse(text: str, response_model: type[BaseModel]) -> BaseModel:
    """Extract JSON from *text* and validate against *response_model*."""
    raw_json = _extract_json(text)
    data = json.loads(raw_json)
    return response_model.model_validate(data)


def _repair_prompt(original_prompt: str, raw_response: str, error: str) -> str:
    """Build a repair prompt asking the LLM to fix its JSON output."""
    return (
        f"{original_prompt}\n\n"
        f"---\n"
        f"Your previous response was:\n{raw_response}\n\n"
        f"That failed validation with the following error:\n{error}\n\n"
        f"Please respond with corrected, valid JSON only."
    )


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def run_inference(
    system_prompt: str,
    user_prompt: str,
    config: ModelConfig,
    response_model: type[BaseModel] | None = None,
) -> str | BaseModel:
    """Run LLM inference via the configured CLI backend.

    Parameters
    ----------
    system_prompt:
        System-level instructions prepended to the prompt.
    user_prompt:
        The actual user query / task.
    config:
        A ``ModelConfig`` specifying provider, model name, and temperature.
    response_model:
        If provided, the response is parsed and validated as this pydantic
        model.  On parse failure the LLM gets up to 2 repair attempts.
        If repair also fails, the raw text is returned.

    Returns
    -------
    str | BaseModel
        The validated pydantic model when *response_model* is set and
        parsing succeeds, otherwise the raw response string.
    """
    provider = config.provider
    if provider not in _BACKENDS:
        raise ValueError(
            f"Provider {provider!r} is not supported. "
            f"Available: {', '.join(sorted(_BACKENDS))}"
        )

    # --- Build the full prompt ------------------------------------------------
    full_prompt_parts: list[str] = []
    if system_prompt:
        full_prompt_parts.append(system_prompt)

    user_section = user_prompt
    if response_model is not None:
        schema = json.dumps(response_model.model_json_schema(), indent=2)
        user_section += (
            f"\n\nRespond with valid JSON matching this schema:\n```json\n{schema}\n```"
        )
    full_prompt_parts.append(user_section)

    full_prompt = "\n\n".join(full_prompt_parts)

    # --- Invoke the backend ---------------------------------------------------
    timeout = DEFAULT_TIMEOUT
    backend = _BACKENDS[provider]
    model_name = config.name
    temperature = config.temperature

    raw = backend(full_prompt, model_name, timeout, temperature)

    # --- Plain text mode ------------------------------------------------------
    if response_model is None:
        return raw

    # --- Structured output with repair loop -----------------------------------
    last_error = ""
    for attempt in range(_MAX_REPAIR_RETRIES + 1):
        try:
            return _try_parse(raw, response_model)
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = str(exc)
            logger.warning(
                "Structured parse attempt %d/%d failed: %s",
                attempt + 1,
                _MAX_REPAIR_RETRIES + 1,
                last_error,
            )
            if attempt < _MAX_REPAIR_RETRIES:
                repair = _repair_prompt(full_prompt, raw, last_error)
                raw = backend(repair, model_name, timeout, temperature)

    # All retries exhausted — return raw text
    logger.error("Structured output parsing failed after retries; returning raw text.")
    return raw
