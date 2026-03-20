# Tasks

## 1. Project Scaffolding

- [ ] 1.1 Initialize Python project with uv (`pyproject.toml`, `uv.lock`)
- [ ] 1.2 Configure ruff for linting and formatting
- [ ] 1.3 Configure mypy or pyright for strict type checking
- [ ] 1.4 Set up pytest with basic test structure (`tests/`)
- [ ] 1.5 Create `legit` CLI entry point with click/typer (no subcommands yet, just `--version` and `--help`)
- [ ] 1.6 Implement `legit init` command that creates `.legit/` directory structure and starter config
- [ ] 1.7 Add pydantic model for `config.yaml` with validation and defaults

## 2. Configuration & Data Models

- [ ] 2.1 Define pydantic models for config schema (`ModelConfig`, `GitHubConfig`, `ProfileConfig`, `RetrievalConfig`, `ReviewConfig`, `CalibrationConfig`, `LegitConfig`)
- [ ] 2.2 Implement config loading from `.legit/config.yaml` with validation
- [ ] 2.3 Implement CLI flag override merging (flags take precedence over config)
- [ ] 2.4 Define pydantic models for index entries (`IndexEntry` with id, type, url, created_at, updated_at, fetched)
- [ ] 2.5 Define pydantic models for cursor/pagination state (`CursorState` per activity type)
- [ ] 2.6 Define pydantic models for structured LLM output (review schema, map observations, calibration scores)
- [ ] 2.7 Write unit tests for config loading, validation, and defaults

## 3. GitHub API Client

- [ ] 3.1 Implement GitHub REST API client with httpx (authenticated via PAT from env var)
- [ ] 3.2 Implement rate limit handling (read headers, exponential backoff, pause on 403)
- [ ] 3.3 Implement paginated fetching (follow `Link` headers)
- [ ] 3.4 Implement PR URL parser (extract owner, repo, pull_number from URL)
- [ ] 3.5 Implement token validation (test API call on first use, display user and rate limit)
- [ ] 3.6 Write unit tests for URL parser and integration tests for API client (with mocked responses)

## 4. Data Ingestion — Index Phase

- [ ] 4.1 Implement activity indexing for PR review comments (`GET /repos/{owner}/{repo}/pulls/comments`)
- [ ] 4.2 Implement activity indexing for issue comments (`GET /repos/{owner}/{repo}/issues/comments`)
- [ ] 4.3 Implement activity indexing for PR reviews (`GET /repos/{owner}/{repo}/pulls` + per-PR reviews)
- [ ] 4.4 Implement activity indexing for commits (`GET /repos/{owner}/{repo}/commits?author={user}`)
- [ ] 4.5 Implement activity indexing for issues authored (`GET /repos/{owner}/{repo}/issues?creator={user}`)
- [ ] 4.6 Implement index persistence to `index.json` with atomic writes
- [ ] 4.7 Implement cursor persistence to `cursor.json` for pagination resumability
- [ ] 4.8 Implement incremental indexing (only fetch items newer than latest indexed)
- [ ] 4.9 Implement progress reporting during index phase (count per type)
- [ ] 4.10 Write tests for index building with mocked GitHub responses

## 5. Data Ingestion — Download Phase

- [ ] 5.1 Implement content download loop: iterate unfetched items in index, fetch full content
- [ ] 5.2 Implement per-type JSON file storage with append semantics
- [ ] 5.3 Implement fetched-status updates in `index.json` (mark items as fetched after download)
- [ ] 5.4 Implement download resumability (skip already-fetched items on restart)
- [ ] 5.5 Implement corruption detection for per-type JSON files (validate JSON on load)
- [ ] 5.6 Implement progress reporting during download phase (downloaded/total + rate limit remaining)
- [ ] 5.7 Write tests for download loop with mocked data

## 6. Fetch Command Integration

- [ ] 6.1 Wire `legit fetch` subcommand: accept `--repo`, `--user`, `--index-only`, `--since` flags
- [ ] 6.2 Implement fetch from config (no args → fetch all configured sources)
- [ ] 6.3 Implement error handling: missing token, network errors, invalid repo/user
- [ ] 6.4 End-to-end test: `legit fetch` with a small public repo (integration test, can be skipped in CI)

## 7. Model Abstraction Layer

- [ ] 7.1 Define `CLIBackedProvider(CustomLLM)` base class with litellm integration
- [ ] 7.2 Implement Gemini CLI provider (`gemini` CLI invocation)
- [ ] 7.3 Implement Claude CLI provider (`claude` CLI invocation)
- [ ] 7.4 Implement Codex CLI provider (`codex` CLI invocation)
- [ ] 7.5 Implement provider registration via `litellm.custom_provider_map`
- [ ] 7.6 Implement CLI availability check (`shutil.which`)
- [ ] 7.7 Implement structured output: JSON schema in prompt + pydantic validation
- [ ] 7.8 Implement repair pass for malformed LLM output (re-prompt with error, max 2 retries)
- [ ] 7.9 Implement configurable timeout (default 5 minutes)
- [ ] 7.10 Implement temperature passthrough from config
- [ ] 7.11 Write unit tests with mocked subprocess calls

## 8. Profile Generation — Map Phase

- [ ] 8.1 Implement data loading: read all per-type JSON files for a profile's sources, excluding holdout set
- [ ] 8.2 Implement chronological sorting across all types
- [ ] 8.3 Implement count-based chunking (configurable chunk_size, default 150)
- [ ] 8.4 Design and implement the map prompt (emergent categories, situational observations, representative quotes — NOT fixed template)
- [ ] 8.5 Implement chunk processing: send chunk to LLM via litellm, parse structured observations
- [ ] 8.6 Implement chunk output caching to `.legit/cache/chunks/{profile}/chunk_NNN.json`
- [ ] 8.7 Implement resumability: skip chunks with existing cached outputs
- [ ] 8.8 Implement `--rebuild-map` flag to force re-processing all chunks
- [ ] 8.9 Implement parallel chunk processing with configurable concurrency
- [ ] 8.10 Write tests for chunking logic and cache hit/miss behavior

## 9. Profile Generation — Reduce Phase

- [ ] 9.1 Design and implement the reduce prompt (emergent categories, temporal weighting with configurable half-life, situation-specific behaviors)
- [ ] 9.2 Implement profile markdown generation with emergent sections (not fixed template)
- [ ] 9.3 Implement temporal weighting: exponential decay based on `temporal_half_life` config
- [ ] 9.4 Implement representative example selection (diverse, recent, with context)
- [ ] 9.5 Implement profile writing to `.legit/profiles/{name}.md`
- [ ] 9.6 Implement `--no-overwrite` flag for preserving manually edited profiles
- [ ] 9.7 Implement multi-source merging (multiple primaries in reduce phase)
- [ ] 9.8 Write tests for profile structure validation

## 10. Retrieval System

- [ ] 10.1 Implement BM25 index builder: tokenize and index all raw comments with metadata
- [ ] 10.2 Implement index storage at `.legit/index/{profile_name}/bm25.json`
- [ ] 10.3 Implement retrieval query construction from PR diff hunks + file types + patterns
- [ ] 10.4 Implement BM25 scoring and top-K retrieval with deduplication
- [ ] 10.5 Implement retrieved example formatting for system prompt (code context + comment)
- [ ] 10.6 Implement fallback: proceed without retrieval if index doesn't exist
- [ ] 10.7 Wire retrieval index building into `legit build` pipeline
- [ ] 10.8 Write tests for index construction, retrieval scoring, and formatting

## 11. Build Command Integration

- [ ] 11.1 Wire `legit build` subcommand: accept `--profile`, `--rebuild-map`, `--no-overwrite` flags
- [ ] 11.2 Wire build pipeline: map phase → reduce phase → retrieval index construction
- [ ] 11.3 Implement build from config default (single profile → auto-select)
- [ ] 11.4 Implement error handling: no data fetched, missing sources, LLM failures
- [ ] 11.5 Manual verification: inspect generated profile for a real reviewer, assess quality

## 12. PR Review Generation

- [ ] 12.1 Implement PR context fetching: diff, full files, description, comments, linked issues
- [ ] 12.2 Implement large PR handling: prioritize files, summarize low-priority ones
- [ ] 12.3 Implement retrieval step: query BM25 index with PR diff, get similar past comments
- [ ] 12.4 Design and implement the review prompt: system prompt = profile + retrieved examples, user prompt = PR context
- [ ] 12.5 Implement structured review output: JSON schema with pydantic validation
- [ ] 12.6 Implement self-critique pass: LLM verifies each comment against reviewer style
- [ ] 12.7 Implement confidence-based filtering (abstention threshold from config)
- [ ] 12.8 Implement max_comments cap (keep highest confidence if over limit)
- [ ] 12.9 Write tests for review response parsing and filtering

## 13. Anchor Resolution

- [ ] 13.1 Implement diff parser: structured representation with file paths, hunks, line numbers
- [ ] 13.2 Implement exact snippet matching: map `diff_snippet` to GitHub diff position
- [ ] 13.3 Implement fuzzy matching fallback (longest common substring, >80% threshold)
- [ ] 13.4 Implement body-fallback: unresolvable comments included in review body
- [ ] 13.5 Write tests for anchor resolution with various diff formats

## 14. Review Output

- [ ] 14.1 Implement dry-run markdown output to stdout (with confidence scores)
- [ ] 14.2 Implement dry-run output to file (`--output` flag)
- [ ] 14.3 Implement GitHub review posting via Pull Request Reviews API
- [ ] 14.4 Implement review attribution footer (generated by legit, profile name, disclaimer)
- [ ] 14.5 Implement posting error handling: invalid diff positions, retry without failed comments
- [ ] 14.6 Write tests for markdown formatting and review payload construction

## 15. Review Command Integration

- [ ] 15.1 Wire `legit review` subcommand: accept `--pr`, `--profile`, `--dry-run`, `--post`, `--output` flags
- [ ] 15.2 Wire review pipeline: load profile → fetch PR → retrieve examples → generate → self-critique → filter → resolve anchors → output
- [ ] 15.3 Implement default mode selection (dry-run unless config says otherwise)
- [ ] 15.4 Implement error handling: no profile, invalid PR URL, missing token for posting
- [ ] 15.5 End-to-end test: `legit review --dry-run` on a real PR (integration test)

## 16. Calibration System

- [ ] 16.1 Implement holdout set selection: pick N most recent reviews from fetched data
- [ ] 16.2 Implement holdout persistence at `.legit/calibration/{name}/holdout.json`
- [ ] 16.3 Implement holdout exclusion from profile generation and retrieval index
- [ ] 16.4 Implement calibration scoring: generate legit reviews for held-out PRs
- [ ] 16.5 Design and implement LLM-as-judge prompt (similarity rating, identification, diagnostics)
- [ ] 16.6 Implement score aggregation and reporting (mean, per-review, top divergences)
- [ ] 16.7 Implement score history storage at `.legit/calibration/{name}/scores.json`
- [ ] 16.8 Implement auto-optimization loop: diagnose → suggest edits → apply → re-score → repeat
- [ ] 16.9 Implement iteration snapshots at `.legit/calibration/{name}/iterations/`
- [ ] 16.10 Implement convergence detection: target score, plateau detection, iteration cap
- [ ] 16.11 Implement rollback: offer to revert to best-scoring iteration
- [ ] 16.12 Implement `--refresh-holdout` flag
- [ ] 16.13 Implement `--history` flag for viewing past calibration scores
- [ ] 16.14 Write tests for holdout selection, scoring pipeline, convergence logic

## 17. Calibrate Command Integration

- [ ] 17.1 Wire `legit calibrate` subcommand: accept `--profile`, `--auto`, `--refresh-holdout`, `--history` flags
- [ ] 17.2 Implement error handling: no data, no profile, insufficient reviews for holdout
- [ ] 17.3 End-to-end test: `legit calibrate` with mocked reviews

## 18. Polish & Documentation

- [ ] 18.1 Add `--verbose` / `--quiet` flags for controlling output verbosity across all commands
- [ ] 18.2 Write README with quickstart, examples for each command, and config reference
- [ ] 18.3 Add inline `--help` text for all commands and flags
- [ ] 18.4 Ensure all pydantic models have clear field descriptions for error messages
- [ ] 18.5 Final ruff + mypy pass to ensure clean lint and type checking
