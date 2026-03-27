# Tasks: Build-Time Knowledge Precompute

## Phase 1: Build-Time Indexes (no new dependencies)

### Task 1: Expertise Index Builder
**File**: `src/legit/expertise.py` (new)

- [ ] Create `ExpertiseEntry` pydantic model: `dir_path, comment_count, severity_distribution, themes, example_quotes, last_activity`
- [ ] Create `ExpertiseIndex` pydantic model: mapping of directory → ExpertiseEntry
- [ ] Implement `build_expertise_index(profile_name, raw_items) → ExpertiseIndex`
  - Parse `path` field from pr_comments to extract directory
  - Group by directory, count comments, extract top quotes
  - Classify severity from comment text (keyword heuristics: "nit:", "blocking", "?", etc.)
  - Extract themes via keyword frequency per directory
- [ ] Implement `save_expertise_index(profile_name, index)` → writes to `.legit/expertise/{name}/expertise.json`
- [ ] Implement `load_expertise_index(profile_name) → ExpertiseIndex`
- [ ] Implement `lookup_expertise(index, changed_dirs) → list[ExpertiseEntry]` — returns relevant entries for a set of directories
- [ ] Add unit tests for all functions

**Verification**: Run on thockin-k8s profile, confirm expertise.json has entries for `pkg/api/`, `cmd/`, `staging/`, etc. with realistic comment counts.

### Task 2: Style Transfer Corpus Builder
**File**: `src/legit/corpus.py` (new)

- [ ] Create `StyleTriple` pydantic model: `situation, file_path, code_context, response, severity, confidence, timestamp`
- [ ] Create situation classifier: `classify_situation(comment_text, file_path, diff_hunk) → str`
  - Categories: error_handling, naming, test_coverage, api_design, performance, concurrency, documentation, backwards_compat, security, code_organization, nit, other
  - Use keyword matching + file extension heuristics (no LLM)
- [ ] Create severity classifier: `classify_severity(comment_text) → str`
  - Parse "nit:", "blocking", question marks, imperative tone, etc.
- [ ] Implement `build_corpus(profile_name, raw_items) → list[StyleTriple]`
  - Filter to items with both diff_hunk and body
  - Classify situation and severity
  - Score confidence (comment length × specificity × recency)
  - Deduplicate near-identical comments (Jaccard similarity on tokens)
  - Sort by confidence, cap at config.build.corpus.max_triples
- [ ] Implement save/load functions
- [ ] Implement `match_triples(corpus, diff_hunks, top_k) → list[StyleTriple]`
  - At review time: keyword overlap scoring (lightweight, no embeddings)
  - Returns the most relevant triples for a given PR's changes
- [ ] Add unit tests

**Verification**: Run on thockin-k8s, confirm triples.json has 200+ entries with realistic situation classifications.

### Task 3: Repo Skeleton Cache
**File**: `src/legit/repo_cache.py` (new)

- [ ] Implement `fetch_repo_tree(gh_client, owner, repo, branch) → dict`
  - Uses `GET /repos/{owner}/{repo}/git/trees/{branch}?recursive=1`
  - Returns parsed tree with paths and types
- [ ] Implement `fetch_structural_files(gh_client, owner, repo, dirs, file_names) → dict[str, str]`
  - For each directory in the expertise index, fetch key files (README.md, OWNERS, etc.)
  - Uses the existing `fetch_file_contents()` method
- [ ] Implement `save_repo_cache(owner, repo, tree, files)` → writes to `.legit/repo_cache/{owner}_{repo}/`
- [ ] Implement `load_repo_tree(owner, repo) → dict` and `load_cached_file(owner, repo, path) → str`
- [ ] Implement `get_structural_context(tree, changed_files) → str`
  - Given a set of changed files, return: sibling files in same dirs, parent directory structure, relevant cached structural files
  - Format as a concise text block for the LLM prompt
- [ ] Add unit tests

**Verification**: Run on kubernetes/kubernetes, confirm tree.json contains full file listing, structural files cached for top-level directories.

### Task 4: Integrate Into Build Pipeline
**File**: `src/legit/profile.py` (modify), `src/legit/cli.py` (modify)

- [ ] After profile map-reduce completes, run:
  1. `build_expertise_index()` using loaded raw data
  2. `build_corpus()` using loaded raw data
  3. `fetch_repo_tree()` and `fetch_structural_files()` using GitHubClient
- [ ] Add `--skip-indexes` flag to `legit build` to skip index building (for faster profile-only rebuilds)
- [ ] Add progress output for each build step
- [ ] Update `legit build` to report: "Built profile (13KB), expertise index (47 directories), style corpus (312 triples), repo skeleton (1,847 files)"
- [ ] Add unit tests for the integrated pipeline

**Verification**: `legit build --profile thockin-k8s --rebuild-map` produces all artifacts. All existing tests still pass.

### Task 5: Update Review Pipeline
**File**: `src/legit/review.py` (modify)

- [ ] At review start, load: expertise index, style corpus (fail gracefully if missing)
- [ ] After fetching PR, compute `changed_dirs` from file list
- [ ] Call `lookup_expertise()` to get relevant expertise entries
- [ ] Call `match_triples()` to get relevant style examples
- [ ] Call `get_structural_context()` to get repo structure context
- [ ] Update `_build_user_prompt()` to include:
  - Expertise context: "The reviewer has 47 comments in pkg/api/ focused on: API backwards compatibility, field validation..."
  - Matched style triples (replacing or augmenting BM25 examples)
  - Structural context (sibling files, directory overview)
- [ ] Keep BM25 retrieval as fallback when corpus/embeddings not available
- [ ] Update SSE progress panel: change "Search past review comments" to show which retrieval method is being used

**Verification**: Run a live review with thockin-k8s on a K8s PR. Confirm the prompt includes expertise context and style triples. Compare review quality before/after.

### Task 6: Update Web UI Progress
**File**: `src/legit/web.py` (modify), `src/legit/templates/index.html` (modify)

- [ ] Update `_run_review_with_progress()` to report which indexes are loaded
- [ ] Update progress step detail messages:
  - "Loaded expertise index (47 directories)"
  - "Matched 8 style triples from corpus (312 total)"
  - "Using semantic retrieval" or "Using BM25 fallback"
- [ ] No new steps — just richer detail in existing steps

**Verification**: Click a sample PR on legitimpr.dev, confirm progress messages show index usage.

### Task 7: Tests
**Files**: `tests/test_expertise.py`, `tests/test_corpus.py`, `tests/test_repo_cache.py` (new)

- [ ] Unit tests for ExpertiseIndex: build, save, load, lookup
- [ ] Unit tests for StyleCorpus: classify_situation, classify_severity, build, match
- [ ] Unit tests for RepoCache: tree parsing, structural context formatting
- [ ] Integration test: full build pipeline produces all artifacts
- [ ] Integration test: review pipeline loads and uses pre-built indexes
- [ ] All 222 existing tests still pass

**Verification**: `uv run pytest tests/ -v` — all tests pass, including new ones.

## Phase 2: Semantic Embeddings (optional, new dependency)

### Task 8: Embedding Index Builder
**File**: `src/legit/embeddings.py` (new)
**Dependency**: `sentence-transformers` (optional)

- [ ] Implement `build_embeddings(profile_name, corpus) → EmbeddingIndex`
  - Load sentence transformer model
  - Embed each triple's concatenated text
  - Store as numpy compressed array
- [ ] Implement `load_embeddings(profile_name) → EmbeddingIndex`
- [ ] Implement `semantic_search(index, query_texts, top_k) → list[StyleTriple]`
- [ ] Add config flag: `build.embeddings.enabled` (default: false)
- [ ] Add to build pipeline (after corpus, before review)
- [ ] Update review pipeline to prefer semantic search over keyword matching
- [ ] Add tests (mock the embedding model for unit tests)

**Verification**: Build embeddings for thockin-k8s. Run a review and confirm semantic search returns more relevant examples than BM25 for conceptually similar but lexically different queries.

## Phase 3: Incremental Builds (follow-up)

### Task 9: Incremental Build Support
- [ ] Track which raw data items have been processed in each index
- [ ] `legit build --incremental` only processes new items since last build
- [ ] Update expertise index, corpus, and embeddings incrementally
- [ ] Add `--force` flag to force full rebuild

**Verification**: Fetch new data, run incremental build, confirm only new items are processed.
