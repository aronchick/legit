# Adversarial Review: Build-Time Knowledge Precompute

## Reviewers
- **Devil's Advocate** (architecture risks, hidden assumptions, opportunity cost)
- **Codex Review** (implementability, data models, integration, test coverage)

## Consolidated Verdict: REVISE — one fatal flaw, restructure scope

---

## Fatal Flaw

**No baseline measurement of review quality exists.** Both reviewers converge on this:

- Devil's Advocate: "The fatal flaw is building Phase 1-3 without Task 0: prove the premise."
- Codex Review: "No end-to-end test for review quality regression... no metric, no dataset, no pass/fail criteria."

We're about to build 5 new data pipelines on the untested assumption that "more structured context = better reviews." The LLM call (60-120s) dominates review time. Retrieval takes 0.1s. The speedup claim is misleading.

**Resolution**: Add Task 0. Score 20 reviews against real reviewer comments before building anything. Use this as the baseline for all subsequent work.

---

## What to Drop (both reviewers agree)

### 1. Repo Skeleton Cache — DROP
- Devil's Advocate: "A flat tree.json for k8s would be 3-5MB of file paths with zero semantic value. The LLM cannot meaningfully reason over 90,000 file paths."
- We already fetch live file contents for changed files via `fetch_pr_context_files()`. The skeleton adds staleness risk for no demonstrated benefit.

### 2. Style Transfer Corpus with Keyword Matching — DROP
- Devil's Advocate: "match_triples() uses keyword overlap scoring — which is tokenize, count overlaps, rank. That's BM25 without the IDF weighting, so it's strictly worse."
- The corpus caps at 500 triples, throwing away potentially relevant examples before knowing what the PR looks like. BM25 searches all comments dynamically.
- **Exception**: The corpus becomes valuable IF semantic embeddings are added (Phase 2). Build the corpus data structure but don't build a separate keyword retrieval system for it.

### 3. Keyword-Based Situation Classification — DROP
- Devil's Advocate: "Keyword heuristics will misclassify 30-50% of comments... wrong labels will poison few-shot selection."
- Either use the LLM for classification (expensive) or skip classification and let retrieval handle relevance.

---

## What to Keep (both reviewers agree, with fixes)

### 1. Expertise Index — KEEP (simplified)
- Devil's Advocate says it "duplicates what BM25 path_boost already does." Partially true — but the expertise index adds QUALITATIVE context ("this reviewer cares about backwards compatibility in pkg/api/") that path_boost can't provide (it only adjusts a numeric weight).
- **Fix**: Don't build a parallel retrieval system. Use the expertise index only for prompt enrichment: "The reviewer has 47 comments in pkg/api/ focused on: API backwards compatibility, field validation."
- **Fix (Codex)**: Add `repo` field. Add `theme_frequency`. Define shared `Severity` enum.

### 2. Semantic Embeddings — KEEP (with corrected dependency)
- Devil's Advocate: "sentence-transformers pulls in torch (~2GB), transformers (~500MB)... the real dependency is 3-4GB."
- **Fix**: Use `onnxruntime` + pre-exported ONNX model (~80MB total) instead of the full PyTorch stack.
- This is the ONE piece that addresses a real retrieval limitation (BM25 misses semantic matches).

### 3. Coding Style Fingerprint — KEEP
- Already partially built. Authored PR diffs are fetched. Just needs better integration into the profile's reduce phase.

---

## Critical Data Model Issues (Codex Review)

| Issue | Severity | Fix |
|---|---|---|
| `ExpertiseEntry` missing `repo` field | CRITICAL | Add `repo: str` — profiles can span repos |
| `StyleTriple` missing `repo` field | CRITICAL | Same fix |
| Severity enum mismatch between expertise + corpus | HIGH | Define shared `Severity` enum |
| Missing `EmbeddingIndex` model | HIGH | Define vector shape, metadata, model ID |
| `confidence` scoring formula undefined | MEDIUM | Define normalization, decay, specificity |
| Missing `BuildMetadata` model | MEDIUM | Track build version, timestamp, data hash |
| `RetrievalDocument` overlaps with `StyleTriple` | LOW | Extend rather than duplicate |

## Critical Integration Issues (Codex Review)

| Issue | Severity | Fix |
|---|---|---|
| Prompt size will GROW, not shrink | CRITICAL | Define hard KB budgets per section |
| No `indexes_used` in ReviewOutput | HIGH | Add field so callers know what's active |
| Dual `load_profile()` functions | HIGH | Consolidate into one canonical location |
| `_load_all_items()` is private but needed everywhere | MEDIUM | Make public or extract to shared module |
| No fallback/degradation tests | HIGH | Test every missing/corrupt/empty index path |

---

## Revised Scope Recommendation

### Phase 0: Baseline (before ANY infrastructure work)
1. Run 20 reviews with current system against PRs where the target reviewer actually left comments
2. Score each: did the generated review catch the same issues? Same tone? Same abstentions?
3. Establish a quality score. This is the number we're trying to improve.

### Phase 1: High-value, low-complexity improvements
1. **Expertise Index** (simplified) — prompt enrichment only, not retrieval
2. **Coding Style Fingerprint** — already mostly built, finish integration
3. **Better prompt engineering** — the current prompt template is 140 chars of instruction. Improving how the LLM uses existing context yields more than adding more context.
4. **Calibration system** — the stubbed `legit calibrate` command. Measure quality after each change.

### Phase 2: Semantic retrieval (if Phase 1 shows retrieval is the bottleneck)
1. **Semantic Embeddings** via ONNX (~80MB, not 4GB)
2. **Style corpus as embedding source** — build the structured triples, embed them, retire BM25

### Phase 3: Incremental builds + fine-tuning (if Phase 2 proves value)
1. Incremental build support
2. Embedding model fine-tuning on reviewer corpus

---

## Opportunity Cost Warning (Devil's Advocate)

> "This is 3-4 weeks of engineering time that could go toward calibration, which is the actual bottleneck. The prompt engineering in review.py is doing the real heavy lifting, and it's not being improved."

The calibration system (`legit calibrate`) is stubbed out but never implemented. It would:
- Run reviews against known-good human reviews
- Score them with LLM-as-judge
- Identify which types of comments the system gets right vs wrong
- Direct optimization effort where it matters most

This is higher-value than any retrieval architecture change because it tells you WHAT to fix.
