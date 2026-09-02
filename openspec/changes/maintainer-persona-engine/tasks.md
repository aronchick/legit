# Tasks: Maintainer Persona Engine

## Phase 0 — Recovery

- [x] Restore README.md (build was broken by its absence) and fix author name
- [x] Apply ruff auto-fixes and reformat (107 fixed; 248 tests pass)
- [ ] Burn down remaining 149 ruff errors (mostly E501) and 136 mypy-strict errors
- [ ] Re-fetch corpora for wdbaruni and thockin (`legit fetch`; data/ is gitignored and absent)
- [ ] Rebuild profiles and indexes (`legit build`)
- [x] Baseline calibration run for thockin and liggitt (2026-09-01, corrected scoring,
      gemini-3.1-pro judge, holdout PRs created+merged after 2026-04-01):
      liggitt n=5 — gpt-5.6-sol 1.9, gpt-5.3-codex 1.4, qwen3-30b 0.1 overall;
      issue detection 0.0 on every lane. Finding: the bottleneck is input context
      (CI results, threads, history the real reviewers reacted to), not model choice.
      thockin clean n=1 — needs a wider holdout pool (see Phase 1 full-history work).
- [ ] Baseline calibration for wdbaruni (profile exists on legitpr.dev; corpus re-fetch pending)
- [ ] Add hashimoto profile (ghostty-org/ghostty, username mitchellh) to config; fetch + build
- [ ] Have Walid eyeball the wdbaruni persona's output for fidelity (free ground truth)

## Phase 1 — Corpus engine v2

- [ ] Source adapter interface with `type` discriminator in config
- [ ] Full-pagination fallback when search-based fetch truncates (fixes bgrant0607 = 24 comments)
- [ ] Mailing-list adapter: lore.kernel.org mbox ingestion filtered to target author
- [ ] torvalds profile: fetch, build, calibrate against held-out LKML replies
- [ ] `legit corpus export --format sft-jsonl` with temporal train/eval split
- [ ] Include no-comment-needed examples from silently-approved PRs in export

## Phase 2 — Fine-tune track

- [ ] Pick hosted fine-tune provider; train LoRA on hashimoto corpus (largest clean corpus)
- [ ] `openai-compatible` provider entry in model config; wire through litellm
- [ ] Calibration comparison mode: two backends, same holdout, side-by-side scores
- [ ] Decision gate: ship fine-tune, hybrid (frontier generates, persona critiques), or stay prompt-only — per persona, by the numbers
- [ ] Repeat for wdbaruni and torvalds corpora

## Phase 3 — Persona packs and sub-agents

- [ ] `legit retrieve` CLI wrapper over retrieval module (for agent hooks)
- [ ] `legit agent export --profile X` → .claude/agents/<persona>.md
- [ ] `legit council --pr <URL>` multi-persona review with attributed merge
- [ ] Enforce simulated-output labeling and hard-refuse posting as non-self personas

## Phase 4 — OpenClaw evaluation

- [ ] Council review of legit itself (torvalds + hashimoto + wdbaruni personas)
- [ ] Council review of OpenClaw architecture
- [ ] Design doc: new OpenClaw with persona sub-agent spin-up, authored with council output
