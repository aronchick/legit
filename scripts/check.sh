#!/usr/bin/env bash
set -euo pipefail
uv run pytest -W error
uv run ruff check .
uv run ruff format --check .
uv run mypy src/ --strict
uv build
