# Proposal: Build-Time Knowledge Precompute

## Why

legit currently rebuilds the reviewer's context from scratch on every PR review request. The profile document and BM25 index are pre-built, but codebase awareness, expertise mapping, coding style analysis, and example retrieval are all computed at review time. This is architecturally backwards.

**The reviewer's identity and expertise are static — only the PR being reviewed changes.**

A real reviewer like Tim Hockin doesn't re-learn the Kubernetes codebase every time they open a PR. They carry years of accumulated knowledge: which packages they know deeply, what patterns they enforce, what past decisions informed current architecture, how they write code themselves. This knowledge is stable across reviews and should be computed once.

The current approach has three concrete problems:

1. **Slow reviews**: Each review re-fetches codebase context via GitHub API (1 call per changed file), re-runs BM25 retrieval, and stuffs everything into a giant prompt. A 10-file PR takes 2-3 minutes before the LLM even starts.

2. **Shallow understanding**: BM25 lexical matching misses semantic connections. A reviewer who cares about "nil safety" won't match a query about "null pointer checks" unless those exact words appear. The reviewer's mental model of the codebase isn't captured — just keyword overlap.

3. **No coding style awareness**: We fetch the reviewer's authored PRs but only use them during the map-reduce profile build. At review time, we don't know "this reviewer writes Go error handling like X, so they'll flag code that does Y instead."

## What Changes

This change restructures legit so that `legit build` pre-computes everything needed to impersonate a reviewer, and `legit review` becomes a fast lookup + generation step.

### Build Time (once per reviewer, ~10-30 minutes)

1. **Codebase Expertise Index** — Analyze every file path the reviewer has ever commented on. Build a structured map: `package/directory → [what they care about, how many times, severity distribution, example quotes]`. Store as `.legit/expertise/{profile}/expertise.json`.

2. **Style Transfer Corpus** — Extract structured `(situation, code_context, reviewer_response)` triples from their history. Not just raw comments, but classified by: what triggered the comment, what code they were looking at, what they said, and how strongly. Store as `.legit/corpus/{profile}/triples.json`.

3. **Coding Style Fingerprint** — Analyze the reviewer's own authored PRs and commits. Extract: naming conventions, error handling patterns, test structure preferences, commit message style, code organization preferences. Store as a section in the profile document.

4. **Semantic Embeddings** — Replace BM25 with dense vector embeddings for retrieval. Embed every past comment using a sentence transformer. At review time, embed the PR's diff hunks and do cosine similarity search. Store as `.legit/embeddings/{profile}/vectors.npz`.

5. **Repo Skeleton Cache** — During build, fetch the repo's directory tree and key structural files (go.mod, OWNERS, README, package.json at each level). This gives the LLM architectural awareness without cloning the whole repo. Store as `.legit/repo_cache/{owner}_{repo}/`.

### Review Time (per PR, target: <30 seconds before LLM call)

1. Load pre-built profile + expertise index + style corpus (disk reads, instant)
2. Fetch the PR diff + changed file contents from GitHub API (~2s)
3. Semantic similarity search against pre-built embeddings (~0.1s)
4. Look up expertise index for changed packages (~instant)
5. Assemble a focused prompt with: profile, relevant expertise, matched examples, PR context
6. Generate review via LLM
7. Self-critique + filter

### What Stays the Same

- The `legit fetch` command (data ingestion)
- The CLI interface (`init`, `fetch`, `build`, `review`, `calibrate`, `serve`)
- The web UI and SSE streaming
- The self-critique and filtering pipeline
- GitHub posting (dry-run and live)

## Capabilities

### New Capabilities

- `expertise-index` — Per-reviewer map of codebase areas they know, with opinion summaries
- `style-transfer-corpus` — Structured (situation, context, response) triples for few-shot matching
- `coding-fingerprint` — Reviewer's own coding patterns extracted from authored code
- `semantic-retrieval` — Dense embedding search replacing BM25 lexical matching
- `repo-skeleton-cache` — Persistent lightweight codebase structure cache

### Modified Capabilities

- `profile-build` — Now generates expertise index, style corpus, embeddings, and repo cache alongside the profile document
- `pr-review` — Now uses pre-built indexes for fast lookup instead of re-computing context
- `retrieval` — Switches from BM25 to semantic similarity (BM25 kept as fallback)

### Removed Capabilities

- None. BM25 is kept as a fallback for profiles without embeddings.

## Risks

- **Embedding model dependency**: Semantic retrieval requires a sentence transformer model. This adds a dependency (~400MB for all-MiniLM-L6-v2). Mitigated by making it optional — BM25 remains the default, embeddings are opt-in.
- **Build time increase**: Pre-computing all indexes takes longer. For a reviewer with 3000+ comments, building embeddings and expertise maps could take 5-10 extra minutes. Acceptable since builds are infrequent.
- **Stale indexes**: If a reviewer's style evolves, pre-built indexes become stale. Mitigated by incremental updates — `legit build --incremental` only re-processes new data.
- **Storage**: Embeddings for 3000 comments ≈ 5MB. Expertise index ≈ 100KB. Style corpus ≈ 2MB. Total per-profile ≈ 10MB. Acceptable.
- **Repo skeleton staleness**: The cached repo structure becomes outdated as the target repo evolves. Mitigated by refreshing the skeleton during `legit fetch` and by always fetching live file contents for the specific PR's changed files at review time.
