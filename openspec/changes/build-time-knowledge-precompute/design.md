# Design: Build-Time Knowledge Precompute

## Architecture Overview

```
legit fetch          legit build                    legit review
───────────          ───────────                    ────────────
GitHub API ──→ .legit/data/    ──→ Profile Doc      PR URL ──→ Fetch PR diff
               (raw JSON)     ──→ Expertise Index           ──→ Fetch changed files
                              ──→ Style Corpus              ──→ Semantic search
                              ──→ Embeddings                ──→ Expertise lookup
                              ──→ Repo Skeleton             ──→ Assemble prompt
                                                            ──→ LLM generate
                                                            ──→ Self-critique
```

### Current vs New Data Flow

**Current (review-time heavy):**
```
Build: raw data → [LLM map-reduce] → profile.md + bm25.json
Review: PR → [GitHub API: diff+files] → [GitHub API: file contents] → [BM25 search] → [build giant prompt] → [LLM] → [LLM critique]
```

**New (build-time heavy):**
```
Build: raw data → [LLM map-reduce] → profile.md
       raw data → [classify+extract] → expertise.json + triples.json
       raw data → [embed model] → vectors.npz
       authored PRs → [analyze] → coding_fingerprint (in profile.md)
       GitHub tree API → repo_skeleton/

Review: PR → [GitHub API: diff+files] → [load pre-built indexes] → [vector search] → [expertise lookup] → [assemble focused prompt] → [LLM] → [LLM critique]
```

## Directory Structure

```
.legit/
├── config.yaml
├── profiles/
│   └── {name}.md                    # Profile document (enhanced with coding style)
├── data/                            # Raw GitHub data (unchanged)
│   └── {owner}_{repo}/{username}/
├── index/                           # BM25 indexes (kept as fallback)
│   └── {name}/bm25.json
├── expertise/                       # NEW: codebase expertise maps
│   └── {name}/
│       ├── expertise.json           # Package → opinion map
│       └── file_history.json        # File → interaction history
├── corpus/                          # NEW: style transfer triples
│   └── {name}/
│       └── triples.json             # (situation, context, response) tuples
├── embeddings/                      # NEW: semantic vectors
│   └── {name}/
│       ├── vectors.npz              # Dense embeddings (numpy)
│       ├── metadata.json            # Maps vector index → document
│       └── model_info.json          # Which embedding model was used
├── repo_cache/                      # NEW: lightweight repo structure
│   └── {owner}_{repo}/
│       ├── tree.json                # Full directory tree
│       ├── go.mod                   # Key project files
│       ├── OWNERS
│       └── pkg/api/README.md        # Directory-level READMEs
└── cache/                           # Chunk cache (unchanged)
    └── chunks/{name}/
```

## Component Design

### 1. Expertise Index Builder (`legit/expertise.py`)

**Input**: Raw data from `.legit/data/` (pr_comments.json, reviews.json, issue_comments.json)

**Process**:
1. Parse every comment's `path` field to extract package/directory
2. Group comments by directory (e.g., `pkg/api/`, `pkg/scheduler/`, `test/e2e/`)
3. For each directory, compute:
   - `comment_count`: How many times the reviewer commented here
   - `severity_distribution`: {blocking: N, suggestion: N, nit: N, praise: N}
   - `themes`: Top recurring concerns (extracted via keyword clustering)
   - `example_quotes`: 3-5 most representative comments for this area
   - `last_activity`: Most recent comment timestamp
4. Also build a file-level history: which specific files have been reviewed, how many times

**Output**: `expertise.json`
```json
{
  "pkg/api/": {
    "comment_count": 47,
    "severity_distribution": {"blocking": 12, "suggestion": 20, "nit": 10, "praise": 5},
    "themes": ["API backwards compatibility", "field validation", "defaulting behavior"],
    "example_quotes": [
      {"text": "This is an API regression...", "file": "pkg/api/types.go", "date": "2024-01-15"},
    ],
    "last_activity": "2024-11-20T10:00:00Z"
  },
  "pkg/scheduler/": { ... }
}
```

**Key design decision**: This is pure data extraction, no LLM needed. Fast and deterministic.

### 2. Style Transfer Corpus Builder (`legit/corpus.py`)

**Input**: Raw PR comments with `diff_hunk`, `path`, `body` fields

**Process**:
1. For each PR comment that has both `diff_hunk` (code context) and `body` (reviewer response):
   - `situation`: Classify what triggered the comment (error handling, naming, test coverage, API design, performance, etc.) — use keyword matching + heuristics, no LLM
   - `code_context`: The diff hunk being reviewed
   - `file_path`: Which file
   - `response`: The reviewer's actual comment
   - `severity`: Inferred from language (nit:/suggestion:/blocking markers, question marks, imperative tone)
   - `confidence`: How representative this is (based on comment length, specificity)
2. Deduplicate near-identical comments
3. Sort by relevance score (severity × recency × specificity)

**Output**: `triples.json`
```json
[
  {
    "situation": "error_handling",
    "file_path": "pkg/controller/reconciler.go",
    "code_context": "@@ -45,3 +45,8 @@\n+if err != nil {\n+    return err\n+}",
    "response": "This silently swallows the error context. Wrap it: fmt.Errorf(\"reconcile failed: %w\", err)",
    "severity": "suggestion",
    "confidence": 0.9,
    "timestamp": "2024-06-15T10:00:00Z"
  },
  ...
]
```

**Key design decision**: Situation classification uses keyword heuristics at build time (fast, deterministic). The LLM is only involved at review time when selecting which triples are most relevant.

### 3. Semantic Embedding Index (`legit/embeddings.py`)

**Input**: Style transfer corpus triples + raw comments

**Process**:
1. Load a sentence transformer model (default: `all-MiniLM-L6-v2`, ~80MB)
2. For each document, concatenate: `file_path + " " + code_context + " " + response`
3. Generate 384-dim embedding vectors
4. Store as numpy compressed archive with metadata mapping

**At review time**:
1. Embed each diff hunk from the PR
2. Compute cosine similarity against stored vectors
3. Return top-K most similar past comments

**Output**: `vectors.npz` + `metadata.json`

**Key design decision**: Embedding model is optional. If `sentence-transformers` is not installed, fall back to BM25. This keeps the core tool lightweight.

### 4. Repo Skeleton Cache (`legit/repo_cache.py`)

**Input**: GitHub Tree API for the target repository

**Process**:
1. Fetch the full tree: `GET /repos/{owner}/{repo}/git/trees/{branch}?recursive=1`
   - This returns every file path in a single API call (~1-5MB for large repos)
2. Store the tree as `tree.json`
3. Fetch key structural files:
   - Root: `go.mod`, `Cargo.toml`, `package.json`, `pyproject.toml`, `README.md`, `OWNERS`
   - Per unique directory in the reviewer's expertise index: `README.md`, `OWNERS`, `doc.go`
4. Cache these files locally

**At review time**:
1. Load the tree to understand project structure around changed files
2. Include relevant cached structural files in the prompt
3. Still fetch the actual changed file contents live (they may have changed since the cache was built)

**Output**: `tree.json` + cached structural files

**Key design decision**: The tree API returns the full repo structure in ONE call. This is much cheaper than cloning. We cache structural files but always fetch PR-specific content live.

### 5. Enhanced Profile Builder

The existing map-reduce profile builder gets two additions:

**Coding Style Fingerprint**: Analyze `authored_prs.json` data (already fetched) to extract:
- Naming conventions (camelCase vs snake_case, abbreviation habits)
- Error handling patterns (wrap vs return, error types used)
- Test structure (table-driven tests, subtests, test naming)
- Code organization (file sizes, function lengths, package structure preferences)
- Commit message style (imperative vs descriptive, scope prefixes)

This becomes a `## Coding Style` section in the profile markdown.

**Expertise Summary**: Include a high-level summary of the expertise index in the profile:
- "This reviewer has deep expertise in: pkg/api (47 comments), pkg/scheduler (32 comments)..."
- "They rarely comment on: test/integration/, docs/, vendor/"

### 6. Review Pipeline Changes

The `generate_review()` function changes from:

```python
# Old
profile = load_profile(name)              # 0s
pr_data = gh.fetch_pr_for_review(url)     # 2s
context = gh.fetch_pr_context_files(...)  # 3-10s (N API calls)
examples = bm25_retrieve(queries)          # 0.1s
prompt = build_prompt(profile, examples, pr_data, context)
review = llm(prompt)                       # 60-120s
```

To:

```python
# New
profile = load_profile(name)              # 0s
expertise = load_expertise(name)          # 0s
corpus = load_corpus(name)                # 0s
embeddings = load_embeddings(name)        # 0.1s (load vectors into memory)
repo_tree = load_repo_skeleton(repo)      # 0s

pr_data = gh.fetch_pr_for_review(url)     # 2s
context = gh.fetch_pr_context_files(...)  # 2-5s (still live for accuracy)

# Fast retrieval against pre-built indexes
relevant_expertise = lookup_expertise(expertise, pr_changed_dirs)  # 0s
matched_examples = semantic_search(embeddings, diff_hunks, top_k=10)  # 0.1s
structural_context = get_structural_context(repo_tree, pr_changed_files)  # 0s

# Focused, smaller prompt
prompt = build_prompt(
    profile,
    relevant_expertise,   # Only expertise for the areas being changed
    matched_examples,     # Semantically matched, not keyword matched
    structural_context,   # Repo structure around changed files
    pr_data,
    context,
)
review = llm(prompt)                      # 30-60s (smaller, more focused prompt)
```

## Config Changes

```yaml
# .legit/config.yaml additions
build:
  embeddings:
    enabled: true                    # Set false to use BM25 only
    model: "all-MiniLM-L6-v2"       # Sentence transformer model
  expertise:
    min_comments: 3                  # Minimum comments to include a directory
    max_quotes_per_dir: 5            # Cap example quotes per directory
  corpus:
    max_triples: 500                 # Cap style transfer corpus size
  repo_cache:
    enabled: true
    structural_files:                # Which files to cache per directory
      - README.md
      - OWNERS
      - go.mod
      - doc.go
```

## Migration Path

1. **Phase 1** (this change): Add expertise index, style corpus, and repo skeleton cache. These are pure Python, no new dependencies. Review pipeline uses them alongside existing BM25.

2. **Phase 2** (follow-up): Add semantic embeddings as optional enhancement. Requires `sentence-transformers` dependency. BM25 remains the default.

3. **Phase 3** (follow-up): Add incremental build support (`--incremental` flag) and embedding model fine-tuning on the reviewer's corpus.

## Error Handling

- If expertise index doesn't exist at review time: skip expertise lookup, review still works
- If embeddings don't exist: fall back to BM25
- If repo skeleton is stale: still fetch live file contents for the PR
- If corpus is empty: fall back to raw BM25 examples
- All new indexes are optional enhancements, not hard requirements
