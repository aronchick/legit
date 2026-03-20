# Design: Initial legit Tool

## Context

We're building a CLI tool that learns from a GitHub reviewer's history and generates PR reviews in their style. The primary inspiration is Jordan Liggett (@liggitt) on kubernetes/kubernetes, where a single reviewer may have 10+ years of activity across thousands of PRs.

Key constraints:
- Must handle massive repos (kubernetes/kubernetes has 100k+ PRs)
- Must be resumable — fetching years of data will take hours
- Must work offline after fetch — the review step shouldn't require re-fetching
- Must be model-agnostic — different teams use different LLM providers
- Profile must be human-readable and editable — not a black box
- **Reviews must feel EXACTLY like the real reviewer(s)** — not just "similar priorities" but authentic voice, phrasing, restraint, and situational behavior
- No API keys required — authenticate through existing CLI OAuth
- Nothing hard-coded — all behavior driven by configuration and learned profiles

## Goals / Non-Goals

**Goals:**
- Produce reviews that are indistinguishable from the real reviewer's output
- Handle repos at Kubernetes scale without breaking
- Be simple to set up: one directory, one config file, five commands
- Support multiple primary reviewers in a single blended profile
- Enable manual correction and tuning of the profile
- Provide measurable evaluation of review fidelity
- Automatically optimize profiles through iterative calibration

**Non-Goals (for MVP):**
- Automated triggering (webhook/GitHub Action) — manual invocation only
- Support for non-GitHub platforms — GitHub only for now
- Fine-tuning models — we use prompted inference with retrieval (hybrid prompt+RAG)
- Support for secondary/tertiary repo influences — profile merging across repos comes later

## Decisions

### Decision: Hybrid Prompt + Retrieval (Not Pure Prompting, Not Fine-Tuning)

The system uses a **two-layer approach** to achieve reviewer fidelity:

1. **Profile (priorities layer):** A human-readable markdown document describing WHAT the reviewer cares about — their priorities, patterns they flag, architectural preferences, approval bar. This is the system prompt.

2. **Retrieved examples (voice layer):** At review time, the system retrieves the reviewer's most similar past comments and includes them as few-shot examples. This shows HOW the reviewer actually writes — their phrasing, tone, humor, escalation patterns, restraint.

**Why not pure prompting (profile-only)?**
A description of someone's style ("direct, uses dry humor, prioritizes error handling") produces generic output. LLMs are dramatically better at mimicking demonstrations than following descriptions. The profile tells the model what to look for; the examples show it how to say what it finds.

**Why not fine-tuning?**
- Model-specific (retrain for every model upgrade)
- Expensive (compute, data preparation)
- Non-portable (can't switch providers)
- Black box (can't inspect or edit what the model learned)
- Requires thousands of examples in a specific format

**Why not pure RAG (no profile)?**
Retrieved examples alone don't capture long-term priorities, evolution of focus, or the reviewer's overall philosophy. The profile provides the strategic "worldview" that individual examples can't.

**The hybrid is the Pareto-optimal point:** profile for strategy, retrieval for voice, at minimum complexity.

### Decision: BM25 Lexical Retrieval for MVP

At review time, retrieve similar past comments using BM25 (lexical similarity) rather than embedding-based semantic search.

**Rationale:**
- Zero ML dependencies — pure text matching
- Works fully offline
- Reviewer comments are heavily word-overlap-friendly (they critique specific patterns using consistent vocabulary)
- Fast enough for interactive use
- Upgrade path to embeddings exists without changing the interface

**Retrieval index:** Built during `legit build` alongside the profile. Stored at `.legit/index/{profile_name}/`. Indexes all raw comments with metadata (file type, comment type, code pattern, severity).

**Retrieval query:** Constructed from PR diff hunks + file types + detected patterns. Per-hunk queries return up to 5 candidates; all candidates are pooled and globally reranked by `BM25_score × type_weight × recency_weight`. Top-K (configurable, default 10) are selected as few-shot examples. This is a GLOBAL budget per review, not per hunk, preventing prompt explosion on large PRs.

**Comment-type weighting:** PR review comments receive a 2x relevance boost over issue comments by default (configurable via `retrieval.type_weights`). This ensures the voice layer learns from the reviewer's PR review voice, not their issue discussion voice.

**Recency weighting:** Retrieval uses the same exponential decay half-life as profile generation, ensuring retrieved examples reflect the reviewer's CURRENT voice.

### Decision: Two-Phase Fetch (Index → Download)

Separate indexing from content download.

**Phase 1 — Index:** Query the GitHub API for all activity by a user in a repo. Store only IDs, URLs, timestamps, and types. This is fast (metadata only) and creates the manifest that drives everything else.

**Phase 2 — Download:** Walk the index, fetch full content for each item. Checkpoint progress so interruptions don't lose work.

**Rationale:**
- Indexing is fast and gives us a complete picture of scope upfront
- Download can be parallelized and resumed independently
- GitHub data is append-only — items fetched once never need re-fetching
- The index doubles as a progress tracker

**Alternatives considered:**
- Single-pass fetch: simpler but can't show progress or resume cleanly
- GraphQL bulk export: GitHub's GraphQL has stricter rate limits for large queries

### Decision: Count-Based Chunking for Profile Build

Process ingested data in fixed-size chunks (configurable, default 150 items) rather than time-based or context-window-based chunks.

**Rationale:**
- Predictable processing time per chunk
- Comment volume is "bursty" — time-based chunks would be wildly uneven
- Easy to parallelize — chunks are independent in the map phase
- Simple to implement and reason about

### Decision: Map-Reduce Profile Generation with Emergent Categories

**Map phase:** Each chunk of comments is independently analyzed by the LLM, producing observations about the reviewer's style, priorities, and patterns. The map prompt does NOT prescribe fixed categories — it asks the LLM to discover and report whatever patterns it observes, organized by the situations in which they occur.

**Reduce phase:** All observations are merged into a single profile document. The merge step:
- Groups similar observations into emergent sections
- Applies temporal weighting with configurable exponential decay (default half-life: 2 years)
- Preserves situation-specific behaviors (e.g., "terse on naming nits, expansive on API surface changes")
- Extracts representative example comments as first-class records with metadata

**Output:** A structured markdown document (the "profile") stored at `.legit/profiles/{name}.md`.

**Profile structure (emergent, not fixed):**
The profile MUST contain these metadata sections:
```markdown
# Reviewer Profile: {name}

## Generated
- Date: {date}
- Source: {repo} by {github_user}
- Data range: {earliest_date} to {latest_date}
- Items processed: {count}
- Temporal half-life: {half_life}
```

The remaining sections are DISCOVERED by the map-reduce process, not prescribed. The reduce prompt organizes observations into whatever categories best describe this specific reviewer. Common emergent sections include (but are not limited to): voice patterns, technical priorities, situational behaviors, approval bar, evolution notes.

**Rationale:**
- Map-reduce is naturally parallelizable
- Each chunk produces independent observations — no state threading
- The reduce step can be re-run with different weighting without re-processing chunks
- Emergent categories capture distinctive qualities that fixed templates miss
- The profile is human-readable, editable, and version-controllable

**Alternatives considered:**
- RAG over raw comments only: too variable, hard to introspect, costs scale with review volume
- Rolling refinement (process chunk → update profile → repeat): creates ordering dependency, harder to parallelize
- Fine-tuning: expensive, non-portable, model-specific
- Fixed profile template: imposes an ontology that may not fit the reviewer

### Decision: Automated Calibration Loop

The system includes a `legit calibrate` command that measures and optimizes profile fidelity.

**Calibration process:**
1. Hold out the N most recent reviews (configurable, default 15)
2. Split into tuning set (70%, ~10 reviews) and validation set (30%, ~5 reviews) — chronological split with validation being the most recent
3. Score against the validation set using LLM-as-judge: "Rate stylistic similarity 1-10"
4. Report aggregate score and per-review diagnostics

**Auto-optimization mode (`legit calibrate --auto`):**
1. Score against the TUNING set (never the validation set)
2. Send the diagnostic results + current profile to the LLM
3. LLM analyzes WHERE the score is weak and suggests profile edits
4. Apply the profile edits
5. Re-score against the TUNING set
6. Repeat until score plateaus or hits target (configurable, default 8.0/10)
7. Cap at configurable max iterations (default 5)
8. After convergence, run a FINAL score against the VALIDATION set (never seen during optimization)
9. If validation score is significantly lower than tuning score (> 1.5 gap), warn about overfitting

**Abstention threshold calibration:**
During calibration, the system also calibrates the confidence threshold empirically by sweeping thresholds and finding the value that maximizes F1 score (balancing false positives vs. missed comments). The calibrated threshold overrides the config default.

**Rationale:**
- Transforms "hope it works" into "measured and improved"
- Tuning/validation split prevents overfitting to a small benchmark
- Each iteration is an inspectable profile edit — no black box
- LLM-as-judge is well-calibrated for stylistic comparison
- Empirical threshold calibration adapts to each model's confidence scale
- The loop converges because the profile is finite and the scoring function is consistent

### Decision: litellm with CustomLLM Wrapping CLIs

Invoke LLM models through litellm's `CustomLLM` interface, with implementations that shell out to authenticated CLIs (`gemini`, `claude`, `codex`).

**Why litellm:**
- Unified interface for all providers
- Built-in retry logic, timeout handling
- Clean abstraction for structured output requests
- Provider-agnostic code throughout the application

**Why CustomLLM wrapping CLIs:**
- No API keys required — CLIs handle their own OAuth authentication
- Users already have their preferred CLI installed and authenticated
- Anthropic explicitly blocks OAuth tokens from direct API use
- OpenAI ChatGPT OAuth tokens have different scopes than platform API keys
- The CustomLLM class gives us litellm's interface while executing through OAuth-authenticated CLIs

**Structured output handling:**
Since CLIs don't natively support JSON mode, we:
1. Instruct the model via prompt to output JSON with a specified schema
2. Validate the output with pydantic
3. If validation fails, send the output back for a repair pass (max 2 retries)

**Interface:**
```python
class CLIBackedProvider(CustomLLM):
    """litellm interface, CLI execution underneath."""
    cli_command: str  # e.g., "gemini", "claude", "codex"
    model: str | None  # optional model override

    def completion(self, model, messages, **kwargs) -> ModelResponse:
        # Construct CLI invocation from messages
        # Shell out to authenticated CLI
        # Parse response into litellm ModelResponse
        ...
```

**Alternatives considered:**
- Direct SDK usage: requires API keys, which we prohibit
- Raw CLI subprocess without litellm: loses unified interface, retry logic, and future upgrade path
- LiteLLM with API keys: violates the no-API-keys constraint

### Decision: Review Prompt Architecture

The review prompt is the most critical prompt in the system. It has three layers:

**System prompt (identity):**
```
You are reviewing this PR as {reviewer_name}. Your priorities, style, and
judgment are defined by the following profile:

{profile_document}

Your voice and phrasing should match these examples of how you actually write:

{retrieved_examples — 5-10 most similar past comments with context}

Rules:
- Write in first person as the reviewer
- Match the tone, length, and phrasing patterns from the examples
- Only comment on things this reviewer would actually comment on
- If unsure whether this reviewer would flag something, don't flag it
- "Say nothing" is a valid and often correct choice for a file or issue
```

**User prompt (context):**
```
PR: {title} by {author}
Description: {pr_body}

Diff:
{structured_diff_with_file_paths_and_line_numbers}

Full file contents for changed files:
{file_contents}

Existing review comments:
{existing_comments — avoid duplicating feedback already given}

Linked issues:
{linked_issue_summaries}
```

**Output schema:**
```json
{
  "summary": "Top-level review summary (2-5 sentences)",
  "inline_comments": [
    {
      "file": "path/to/file.go",
      "hunk_header": "@@ -10,5 +10,7 @@ func example()",
      "diff_snippet": "the exact diff text (min 3 lines for disambiguation)",
      "side": "addition",
      "comment": "the review comment text",
      "confidence": 0.0-1.0
    }
  ],
  "abstained_files": ["path/to/file_not_commented_on.go"],
  "abstention_reason": "No issues matching reviewer priorities"
}
```

The multi-key anchor (`file` + `hunk_header` + `diff_snippet` + `side`) enables deterministic disambiguation even when identical code appears in multiple hunks.

**Self-critique pass (cheap second call):**
After generating the review, run a verification pass:
```
Here is a generated review and the reviewer's actual past comments.
For each inline comment, answer:
1. Would this reviewer actually leave this comment? (yes/probably/no)
2. Does the phrasing sound like them? (yes/close/no)
3. Is this already covered by existing review threads? (yes/no)
Drop any comment rated "no" on question 1 or "yes" on question 3.
```

**Anchor resolution (separate from issue detection):**
The model cites `diff_snippet` (exact text from the diff). A deterministic code layer maps that snippet to the correct GitHub diff position. The model never invents line numbers or diff positions directly.

### Decision: Abstention as First-Class Behavior

"Say nothing" is a valid and correct review outcome. The system must model restraint, not just detection.

- Each inline comment carries a confidence score (0.0-1.0)
- Comments below the configured abstention threshold (default 0.5) are dropped
- If zero comments pass the threshold, the review output is explicitly "no comments" — not silence
- The calibration loop measures overcomment rate alongside similarity score

### Decision: GitHub Review API for Output

Post reviews using the GitHub Pull Request Review API. The bot always posts as `COMMENT` — never approves or requests changes. It's advisory only.

**Anchor resolution:** The model outputs multi-key anchors (`file` + `hunk_header` + `diff_snippet` + `side`). Application code uses these keys to deterministically locate the correct position in the diff, disambiguating repeated snippets via hunk header and side. If mapping fails for a comment, the comment is included in the review body as a fallback rather than dropped.

**Dry-run mode:** Outputs the same structured review as markdown to stdout or a file.

### Decision: Single .legit/ Directory

All state lives in `.legit/` in the working directory:

```
.legit/
├── config.yaml              # Tool configuration
├── profiles/
│   └── {name}.md            # Generated reviewer profiles
├── data/
│   └── {owner}_{repo}/
│       └── {username}/
│           ├── index.json         # Activity index
│           ├── cursor.json        # Pagination/download progress
│           ├── pr_comments.json   # Downloaded PR review comments
│           ├── issue_comments.json # Downloaded issue comments
│           ├── reviews.json       # Downloaded PR reviews
│           ├── commits.json       # Downloaded commit data
│           └── issues.json        # Downloaded issue data
├── index/
│   └── {profile_name}/
│       └── bm25.json             # BM25 retrieval index
├── cache/
│   └── chunks/
│       └── {profile_name}/
│           ├── chunk_001.json    # Map phase output for chunk 1
│           └── ...
└── calibration/
    └── {profile_name}/
        ├── holdout.json          # Held-out review IDs
        ├── scores.json           # Calibration scores history
        └── iterations/           # Profile snapshots per iteration
```

### Decision: Config Schema

```yaml
# .legit/config.yaml
model:
  provider: gemini          # gemini | claude | openai
  name: null                # optional model name override
  temperature: 0.3          # lower = more consistent style mimicry

github:
  token_env: GITHUB_TOKEN   # env var name containing PAT

profiles:
  - name: liggitt-k8s
    sources:
      - type: primary
        repo: kubernetes/kubernetes
        username: liggitt
    chunk_size: 150          # items per chunk for map phase
    temporal_half_life: 730  # days — observations from this long ago contribute 50%

retrieval:
  top_k: 10                 # GLOBAL budget: total examples per review
  index_type: bm25           # bm25 for MVP
  type_weights:              # relevance multipliers by comment type
    pr_review: 2.0
    issue_comment: 1.0
    commit_comment: 0.5

review:
  post_to_github: false      # default to dry-run
  review_action: COMMENT     # always COMMENT
  max_comments: null         # optional cap on inline comments
  abstention_threshold: 0.5  # below this confidence, don't comment

calibration:
  holdout_count: 15          # total reviews to hold out (split 70/30 tuning/validation)
  max_iterations: 5          # max auto-optimization rounds
  target_score: 8.0          # target similarity score (1-10)
```

### Decision: Python + uv + Strict Typing

- **Python 3.11+** for modern typing features and performance
- **uv** for dependency management and script portability
- **pydantic** for all data models — config, index entries, profile schema, API responses, structured LLM output
- **litellm** for model-agnostic LLM invocation via CustomLLM
- **ruff** for linting and formatting
- **mypy** or pyright for static type checking
- **pytest** for testing with high coverage targets

## Risks / Trade-offs

- **[GitHub rate limits]** → Implement exponential backoff, conditional requests, and respect `X-RateLimit-*` headers.
- **[LLM CLI availability]** → Fail fast with a clear error message if the configured CLI isn't installed or authenticated.
- **[Profile quality]** → Mitigated by: emergent categories (not forced template), retrieval for voice (not just profile), calibration loop for measurement, human editability as escape hatch.
- **[Large repos overwhelming context]** → Prioritize diff and changed files, summarize unchanged context, chunk if needed.
- **[Reviewer style drift]** → Temporal weighting with configurable decay, incremental fetch + rebuild flow.
- **[Structured output reliability]** → Pydantic validation + repair pass. If repair fails twice, fall back to best-effort parsing with degraded formatting.
- **[Retrieval quality with BM25]** → Lexical matching is strong for code review vocabulary. Upgrade path to embeddings exists. Calibration loop will surface retrieval gaps.
- **[Overcomment bias]** → Abstention policy with confidence thresholds. Self-critique pass filters "would they actually say this?" Calibration measures overcomment rate.

## Data Flow

```
                    GitHub API
                        │
                        ▼
              ┌─────────────────┐
              │   legit fetch   │
              │                 │
              │ Phase 1: Index  │ ─── index.json + cursor.json
              │ Phase 2: Download│ ─── pr_comments.json, reviews.json, ...
              └────────┬────────┘
                       │
                       ▼
              ┌─────────────────┐
              │   legit build   │
              │                 │
              │ Map: chunk →    │ ─── chunks/chunk_NNN.json
              │   observations  │     (emergent categories)
              │                 │
              │ Reduce: merge → │ ─── profiles/{name}.md
              │   profile       │
              │                 │
              │ Index: build    │ ─── index/{name}/bm25.json
              │   retrieval idx │
              └────────┬────────┘
                       │
                       ▼
              ┌─────────────────┐
              │ legit calibrate │  (optional but recommended)
              │                 │
              │ Hold out reviews│
              │ Generate legit  │
              │ Score with LLM  │
              │ Auto-optimize   │ ─── improved profiles/{name}.md
              │   profile       │
              └────────┬────────┘
                       │
                       ▼
              ┌─────────────────┐
              │  legit review   │
              │                 │
              │ Load profile    │
              │ Fetch PR data   │ ◄── GitHub API
              │ Retrieve similar│ ◄── BM25 index
              │   past comments │
              │ Generate review │ ◄── LLM (profile + examples + PR)
              │ Self-critique   │ ◄── LLM (filter pass)
              │ Validate output │ ◄── pydantic
              │ Resolve anchors │ ◄── deterministic diff mapping
              │ Post or print   │ ──► GitHub Review API or stdout
              └─────────────────┘
```

## Open Questions

1. **CLI framework**: click vs typer? Leaning typer for type-hint-driven interface.
2. **Retrieval granularity**: Should BM25 index individual comments, or comment+context pairs (the code being commented on)? Likely comment+context for better matching.
3. **Calibration convergence**: How many iterations does the auto-optimization loop typically need? Will need empirical testing.
4. **Multi-reviewer blending in retrieval**: When a profile has multiple primaries, should retrieval pull from both reviewers' comment pools? Likely yes, weighted by recency.
