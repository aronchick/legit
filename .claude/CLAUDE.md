# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is Legit?

Legit learns a GitHub reviewer's style from their historical activity (comments, reviews, commits, issues) and generates PR reviews in their voice. It uses a map-reduce LLM pipeline to build reviewer profiles, BM25/semantic retrieval for similar past comments, and a two-pass generate-then-critique approach for high-fidelity reviews.

## Development Commands

```bash
# Install dependencies
uv sync                          # core deps
uv sync --extra embeddings       # + ONNX semantic search

# Run all tests
uv run pytest

# Run a single test
uv run pytest tests/test_review.py -k "test_name"

# Lint and format
uv run ruff check . --fix
uv run ruff format .

# Type check
uv run mypy src/ --strict

# CLI entry point (after uv sync)
uv run legit --help
```

## CLI Workflow

The CLI follows a sequential pipeline: `init` → `fetch` → `build` → `review`.

| Command | Purpose |
|---------|---------|
| `legit init` | Create `.legit/` directory and starter `config.yaml` |
| `legit fetch` | Index and download GitHub activity for configured profiles |
| `legit build` | Map-reduce profile generation + BM25 index build |
| `legit review --pr <URL>` | Generate a PR review (dry-run by default, `--post` to submit) |
| `legit calibrate` | Evaluate review quality against real reviewer comments (LLM-as-judge) |
| `legit serve` | Launch FastAPI web UI on port 8142 |

## Architecture

```
cli.py ─── entry point (Typer commands)
  ├── config.py ─── YAML → Pydantic config (LegitConfig)
  ├── github_client.py ─── GitHub REST API (pagination, rate limits, retries)
  ├── profile.py ─── Map-reduce profile builder (chunks → LLM → synthesize)
  ├── review.py ─── Main review pipeline (fetch PR → retrieve → generate → self-critique → filter)
  ├── calibrate.py ─── Quality evaluation (LLM-as-judge, 4 scoring dimensions)
  ├── model_runner.py ─── LLM abstraction (litellm + CLI backends for claude/gemini/openai)
  ├── retrieval.py ─── BM25 lexical search (pure Python)
  ├── embeddings.py ─── Semantic search (quantized ONNX sentence transformer, ~22MB)
  ├── expertise.py ─── Directory-level reviewer focus areas
  ├── models.py ─── Pydantic data models (IndexEntry, ReviewOutput, ChunkObservation, etc.)
  └── web.py ─── FastAPI + Jinja2 web UI
```

### Key Data Flow: Review Generation (`review.py:generate_review`)

1. **Load profile** — reads `.legit/profiles/{name}.md` + expertise index
2. **Fetch PR data** — metadata, diff, full source files for changed paths (budget: 200-300KB)
3. **Retrieve similar comments** — semantic search → BM25 fallback → top-k with temporal weighting
4. **Generate review** — LLM call with profile + examples + diff → structured `ReviewOutput`
5. **Self-critique** — second LLM pass filters each comment (voice match? already covered? worth saying?)
6. **Apply filters** — confidence threshold (default 0.5) + max comments cap

### Key Data Flow: Profile Building (`profile.py:build_profile`)

1. **Map** — chunk items chronologically (150/chunk), LLM extracts behavioral patterns per chunk (parallel, 4 workers)
2. **Reduce** — LLM synthesizes all chunks into a profile markdown with temporal weighting (recent > older)
3. **Index** — build BM25 index from raw comments → `.legit/index/{name}/bm25.json`

### Runtime Data Layout

```
.legit/
  config.yaml          ← project configuration
  profiles/{name}.md   ← generated reviewer profiles
  data/                ← fetched GitHub activity JSON
  index/{name}/        ← BM25 retrieval indexes
  calibration/         ← calibration run results
```

## Key Design Decisions

- **Two-pass LLM** (generate + self-critique) reduces false positives; goal is zero noise
- **Retrieval fallback chain**: semantic embeddings → BM25 → no retrieval (graceful degradation)
- **Temporal weighting**: `exp(-days / half_life)` with default 730-day half-life; 3-year-old comments get ~50% weight
- **CLI tool dispatch** in `model_runner.py`: pipes prompts via stdin to avoid OS ARG_MAX limits
- **Structured output**: LLM returns JSON validated against Pydantic schemas, with up to 2 repair retries on parse failure
- **Codebase context budget**: fetches changed files + directory context files (README, go.mod, etc.), capped at ~200-300KB total

## Configuration

All config lives in `.legit/config.yaml`. Key settings:

- `model.provider`: `gemini` (default), `claude`, or `openai`
- `profiles[].sources[].repo`: `owner/repo` format
- `retrieval.type_weights`: pr_review=2.0, issue_comment=1.0, commit_comment=0.5
- `review.abstention_threshold`: confidence cutoff (default 0.5)
- `review.post_to_github`: false by default (dry-run mode)

## Testing Conventions

- Fixtures in `tests/conftest.py`: `legit_dir` (temp `.legit/`), `mock_pr_data`, `mock_review_output`, `sample_retrieval_docs`
- Integration tests: `test_integration_pipeline.py` (end-to-end), `test_integration_web.py` (FastAPI)
- Tests monkeypatch the `.legit/` directory constant for isolation

## Tooling

- **Python 3.11+** with `uv` for package management
- **Ruff** for linting/formatting (line-length=100, rules: E, F, I, N, W, UP)
- **mypy** strict mode
- **pytest** for testing
- **Build backend**: `uv_build`
