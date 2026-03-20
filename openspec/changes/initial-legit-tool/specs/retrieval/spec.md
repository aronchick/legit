# Delta for Retrieval

## ADDED Requirements

### Requirement: Retrieval Index Construction
The system SHALL build a BM25 lexical retrieval index over the reviewer's past comments during `legit build`.

#### Scenario: Building the index
- **WHEN** `legit build` completes the map-reduce profile generation
- **THEN** the system SHALL build a BM25 index over all raw comments and reviews from the profile's sources
- **AND** store the index at `.legit/index/{profile_name}/bm25.json`
- **AND** each indexed document SHALL include: comment text, file path of the code commented on, code snippet being commented on (if available), comment type (inline review, top-level review, issue comment), severity signal (nit, suggestion, blocking), timestamp, and reviewer username (for multi-primary profiles)

#### Scenario: Index includes code context
- **WHEN** a comment was made on a specific line or hunk of code
- **THEN** the indexed document SHALL include the surrounding code context (the diff hunk or file lines the comment references)
- **AND** the retrieval query can match against both the comment text and the code context

#### Scenario: Index rebuild
- **WHEN** `legit build` is run and the retrieval index already exists
- **THEN** the system SHALL rebuild the index from current data
- **AND** the previous index SHALL be replaced atomically

#### Scenario: Incremental index update
- **WHEN** new data has been fetched since the last build
- **THEN** `legit build` SHALL rebuild the index including the new data

### Requirement: Retrieval at Review Time
The system SHALL retrieve the reviewer's most similar past comments when generating a review.

#### Scenario: Constructing the retrieval query
- **WHEN** `legit review` prepares to generate a review for a PR
- **THEN** the system SHALL construct retrieval queries from each diff hunk in the PR
- **AND** each query SHALL include: the changed code, the file path, and any detected patterns (e.g., error handling, API surface, test code)

#### Scenario: Retrieving similar comments
- **WHEN** retrieval queries are executed against the BM25 index for a PR
- **THEN** each per-hunk query SHALL return up to 5 candidates
- **AND** all candidates across all hunks SHALL be pooled and globally reranked
- **AND** the system SHALL select the top-K examples from the global pool (configurable via `retrieval.top_k`, default 10)
- **AND** `retrieval.top_k` is a GLOBAL budget per review, not per hunk
- **AND** results SHALL be deduplicated (no repeated comments across queries)
- **AND** results SHALL include the original code context alongside the comment text

#### Scenario: Comment-type weighting in retrieval
- **WHEN** the global reranking selects from the candidate pool
- **THEN** PR review comments (inline and top-level) SHALL receive a 2x relevance boost over issue comments
- **AND** commit message comments SHALL receive no boost
- **AND** these weights SHALL be configurable via `retrieval.type_weights` in config

#### Scenario: Recency weighting in retrieval
- **WHEN** the global reranking selects from the candidate pool
- **THEN** the final retrieval score SHALL be `BM25_score × type_weight × recency_weight`
- **AND** `recency_weight` SHALL use the same exponential decay half-life as profile generation (`temporal_half_life` config)
- **AND** this ensures retrieved examples reflect the reviewer's CURRENT voice, not historical patterns

#### Scenario: Formatting retrieved examples for the prompt
- **WHEN** similar comments have been retrieved
- **THEN** the system SHALL format them as few-shot examples in the system prompt
- **AND** each example SHALL show: the code that was commented on, the reviewer's actual comment, the comment type, and (for multi-primary profiles) which reviewer wrote it
- **AND** the examples SHALL be ordered by relevance (most similar first)

#### Scenario: Multi-primary retrieval with reviewer attribution
- **WHEN** a profile has multiple primary reviewers
- **THEN** retrieved examples SHALL include the reviewer username for each example
- **AND** the review prompt SHALL instruct the model to match the voice of the reviewer whose examples are most relevant to each specific comment being generated

#### Scenario: No similar comments found
- **WHEN** the retrieval query returns zero results above a minimum relevance threshold
- **THEN** the system SHALL proceed with profile-only generation (no few-shot examples)
- **AND** log a warning that retrieval found no matches

#### Scenario: Retrieval index not built
- **WHEN** `legit review` is run but no retrieval index exists for the profile
- **THEN** the system SHALL proceed with profile-only generation
- **AND** display a warning suggesting to re-run `legit build` for better quality

### Requirement: Retrieval Configuration
The retrieval system SHALL be configurable via `.legit/config.yaml`.

#### Scenario: Custom top_k
- **WHEN** `retrieval.top_k` is set to 5 in config
- **THEN** the system SHALL retrieve at most 5 similar comments per review (global budget)

#### Scenario: Custom type weights
- **WHEN** `retrieval.type_weights` is configured (e.g., `{pr_review: 3.0, issue_comment: 0.5}`)
- **THEN** the system SHALL apply those weights during global reranking

#### Scenario: Retrieval disabled
- **WHEN** `retrieval.top_k` is set to 0
- **THEN** the system SHALL skip retrieval entirely and use profile-only generation
