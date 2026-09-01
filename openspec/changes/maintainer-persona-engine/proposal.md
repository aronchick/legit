# Proposal: Maintainer Persona Engine

## Why

legit was built (March 2026) to learn a GitHub reviewer's style and generate PR reviews in
their voice. It went dormant for five months. The goal has now widened: point at a person or
set of people in a project's history and say **"from now on, evaluate PRs, issues, APIs, and
design choices as though you were that person"** — an overt, labeled impersonation of a
maintainer, usable interactively and by sub-agents.

Target personas (three separate projects):

1. **Linus Torvalds** — the Linux kernel
2. **Bacalhau core maintainers** — bacalhau-project/bacalhau (wdbaruni already built: 3,126
   comments embedded)
3. **Mitchell Hashimoto** — ghostty-org/ghostty (and historical hashicorp/*)

### State assessment (2026-08-31)

The project is in far better shape than "dormant" suggests. After restoring a missing
README.md that broke the build entirely, **all 248 tests pass**. What exists and works:

- **Corpus ingestion** — `github_client.py` (950 lines): pagination, rate limiting, retries,
  search-based fetch for large repos. Five reviewer corpora were already fetched and embedded
  (wdbaruni, thockin, liggitt, bgrant0607, lavalamp).
- **Profile builder** — map-reduce LLM pipeline with temporal weighting.
- **Retrieval** — semantic ONNX embeddings with BM25 fallback, per-directory expertise index.
- **Review pipeline** — generate + self-critique two-pass, confidence filtering.
- **Calibration harness** — LLM-as-judge on holdout PRs, scoring issue detection, voice
  fidelity, appropriate abstention, and false positives. This is the eval loop every later
  phase depends on.
- **LLM abstraction** — litellm + CLI backends. A fine-tuned model served on any
  OpenAI-compatible endpoint is a config entry, not an architecture change.

Known rot: 149 ruff + 136 mypy-strict pre-existing errors (version drift); raw fetched data
and profiles are gitignored and absent locally (re-fetch needed); the
`build-time-knowledge-precompute` change half-landed (embeddings and expertise shipped; style
corpus, coding fingerprint, and repo skeleton cache did not); sparse-history reviewers fetch
badly (bgrant0607: 24 comments — search API caps need a full-pagination fallback).

**Verdict: recover, don't rewrite.** Roughly 90% of the plumbing the corpus phase needs
already exists and passes tests.

### The Linus problem

Kernel development does not happen in GitHub PRs. torvalds/linux has essentially no review
activity on GitHub; the real corpus is the kernel mailing lists (lore.kernel.org,
public-inbox/mbox format). A GitHub-only fetcher cannot build a Linus persona. Corpus sources
must become pluggable, with a mailing-list adapter as the second source type. Mitchell
Hashimoto (ghostty is GitHub-native and highly active) and Bacalhau are fully served by the
existing fetcher, so the mailing-list adapter is not on the critical path for the first two
personas.

## What Changes

Four phases. Each phase ends with a calibration run so quality claims are measured, not
asserted.

### Phase 0 — Recovery (this change's immediate tasks)

Restore the build (done), burn down lint/type debt, re-fetch corpora, rebuild profiles, run a
baseline calibration for wdbaruni and thockin, and reconfigure profiles around the three
target personas (hashimoto on ghostty first; wdbaruni exists; torvalds blocked on Phase 1's
mailing-list source).

### Phase 1 — Corpus engine v2

- **Pluggable sources**: `type: github` (today) plus `type: mailing-list` (lore.kernel.org
  mbox ingestion) mapped into the same `IndexEntry` model.
- **Full-history fetch**: pagination fallback when search caps truncate (fixes the
  24-comment bgrant0607 failure mode).
- **Corpus export**: `legit corpus export --profile X --format sft-jsonl` producing
  supervised fine-tuning pairs — (persona card + PR context + diff hunk) → (the reviewer's
  actual comment) — with a **temporal split**: hold out the most recent months for eval so
  training never sees eval data.

### Phase 2 — Fine-tune track

- LoRA fine-tune of an open-weights code model (e.g. Qwen2.5-Coder class) on the exported
  corpus, hosted first (Together/Fireworks-style API), local later if warranted.
- Serve on an OpenAI-compatible endpoint; wire into `model_runner` via litellm config.
- **Decision gate**: calibrate fine-tuned vs. prompt+retrieval on the same holdout set.
  Hypothesis to test honestly: fine-tuning wins voice fidelity, frontier prompt+retrieval
  wins issue detection. If that holds, the endgame is a hybrid — frontier model finds the
  issues, persona model voices and filters them.

### Phase 3 — Persona packs and sub-agents

- `legit agent export --profile X` generates a Claude Code subagent definition (persona
  profile + expertise map + a retrieval hook) so "spin up a sub-agent that reviews as
  Mitchell" works with zero training — and improves transparently when Phase 2 ships.
- `legit council --pr <URL>` runs multiple personas over one PR/design and merges their
  reviews with attribution.

### Phase 4 — OpenClaw evaluation (first real dogfood)

Run the maintainer council over everything built so far — legit itself, then OpenClaw's
architecture — and produce the design for a new OpenClaw that can spin up persona sub-agents
to re-implement components in an end-user-friendly way. This phase is a *use* of the tool,
not a feature of it; its deliverable is a design document authored with the council.

## Capabilities

### New
- `corpus-sources` — pluggable ingestion (GitHub + mailing-list archives)
- `corpus-export` — SFT JSONL export with temporal train/eval split
- `fine-tune-track` — training config, model registry, fine-tuned-endpoint serving
- `agent-export` — persona pack → Claude Code subagent definition
- `persona-council` — multi-persona review with merged, attributed output

### Modified
- `fetch` — full-pagination fallback; source adapters
- `calibration` — comparison mode (two backends, same holdout, side-by-side scores)

### Removed
- None.

## Risks

- **Impersonation ethics**: personas are overt simulations for private evaluation. Output is
  always labeled as simulated. `post_to_github` remains false for personas of people other
  than the operator; posting under a real person's name is never supported.
- **Fine-tune underperformance**: a small fine-tuned model may lose substance vs. frontier
  prompting. Mitigated by the Phase 2 decision gate — calibration decides, and the
  prompt+retrieval path remains the default until beaten.
- **Corpus contamination**: frontier models have already read Linus and Mitchell. Voice
  fidelity scores can be inflated by prior knowledge. Mitigated by judging against held-out
  *specific* comments, not general style impressions.
- **Mailing-list ingestion scope**: lore.kernel.org archives are enormous. Scope to
  Linus-authored messages in selected windows first; never bulk-mirror.
