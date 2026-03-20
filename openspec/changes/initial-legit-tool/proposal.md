# Proposal: Initial legit Tool

## Why

Expert code reviewers develop distinctive styles over years — specific priorities, patterns they flag, architectural taste, tone. Their feedback is invaluable but doesn't scale: they can only review so many PRs. When they're unavailable, PRs either wait or get lower-quality initial feedback.

`legit` solves this by learning a reviewer's style from their GitHub history and generating initial PR feedback in their voice. It's not a replacement — it's a first pass that catches what they'd catch, so their time is spent on the nuanced judgment calls only a human can make.

The reviews must feel EXACTLY like the real reviewer — not just similar priorities, but authentic voice, phrasing, and situational behavior. This requires more than a description of the reviewer's style; it requires demonstrating that style through retrieved examples of their actual writing.

Named as a nod to [@liggitt](https://github.com/liggitt) (Jordan Liggett), a prolific Kubernetes reviewer whose thoroughness and consistency inspired this project.

## What Changes

This is a greenfield project. We are building:

- **Data fetcher**: Two-phase GitHub ingestion (index then download) for all activity a user performed in a repo. Resumable, append-only.
- **Profile builder**: Map-reduce pipeline that processes fetched data chronologically in count-based chunks, producing a distilled reviewer profile with emergent categories and temporal weighting.
- **Retrieval index**: BM25 lexical index over raw comments for retrieving similar past reviews at generation time.
- **PR reviewer**: Analyzes a PR against the profile with retrieved few-shot examples, generates structured output with self-critique and abstention support, and posts via GitHub API.
- **Calibration system**: Measures profile fidelity against held-out reviews using LLM-as-judge scoring, with an auto-optimization loop that iteratively improves the profile.
- **CLI interface**: Five commands — `legit init`, `legit fetch`, `legit build`, `legit review`, `legit calibrate` — with config in `.legit/` working directory.
- **Model abstraction**: litellm with CustomLLM wrapping authenticated CLIs (gemini, claude, codex). No API keys required.
- **GitHub integration**: Posts reviews via PAT. Dry-run mode outputs equivalent markdown.

## Capabilities

### New Capabilities

- `data-ingestion` — Two-phase GitHub data fetching (index + download) with resumability
- `profile-generation` — Map-reduce profile building with emergent categories and temporal weighting
- `retrieval` — BM25 lexical index for retrieving similar past comments at review time
- `pr-review` — Retrieval-augmented review generation with self-critique, structured output, and abstention
- `calibration` — LLM-as-judge scoring with automated profile optimization loop
- `cli-interface` — Command-line interface (`fetch`, `build`, `review`, `calibrate`) and project structure
- `github-integration` — GitHub API interactions (read data, post reviews) with PAT auth
- `model-abstraction` — litellm CustomLLM wrapper for CLI-based inference without API keys
- `configuration` — `.legit/` directory structure, config schema, profile and index storage

### Modified Capabilities

(none — greenfield project)

## Impact

- New repository: `legit/`
- New Python package managed by uv
- Runtime dependencies: pydantic, httpx, litellm, click/typer
- Dev dependencies: ruff, pytest, mypy
- External: requires GitHub PAT, requires at least one LLM CLI installed and authenticated (gemini, claude, or codex)
- No infrastructure required — runs locally as a CLI tool
- No API keys required — authenticates through CLI OAuth
