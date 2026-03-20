# Delta for PR Review

## ADDED Requirements

### Requirement: PR Data Collection
The system SHALL collect comprehensive context about a PR before generating a review.

#### Scenario: Collecting PR context
- **WHEN** `legit review --pr <url>` is run
- **THEN** the system SHALL fetch the PR diff (full patch)
- **AND** fetch the complete content of all changed files (not just the diff hunks)
- **AND** fetch the PR description/body
- **AND** fetch all existing comments and review threads on the PR
- **AND** fetch linked issues referenced in the PR description or commits

#### Scenario: Large PR handling
- **WHEN** a PR changes more than 50 files or exceeds the model's context limit
- **THEN** the system SHALL prioritize files by: files with the most changes first, then test files, then configuration files
- **AND** summarize low-priority files rather than including full content
- **AND** inform the user that the review covers a subset of changes

### Requirement: Retrieval-Augmented Review Generation
The system SHALL use retrieved past comments as few-shot examples alongside the profile.

#### Scenario: Generating a review with retrieval
- **WHEN** PR context has been collected and a profile is loaded
- **THEN** the system SHALL retrieve similar past comments from the BM25 index (per retrieval spec)
- **AND** construct the system prompt with: profile document + retrieved examples
- **AND** construct the user prompt with: PR context (diff, files, description, comments, issues)
- **AND** send to the configured LLM via litellm

#### Scenario: Review prompt structure
- **WHEN** the review prompt is assembled
- **THEN** the system prompt SHALL contain:
  1. The reviewer identity and profile document (priorities, patterns, approval bar)
  2. Retrieved examples formatted as: code context → reviewer's actual comment
  3. Behavioral instructions: write as the reviewer, match examples, abstain when unsure
- **AND** the user prompt SHALL contain:
  1. PR metadata (title, author, description)
  2. Structured diff with file paths and line numbers
  3. Full file contents for changed files
  4. Existing review comments (to avoid duplication)
  5. Linked issue summaries

#### Scenario: Review without retrieval (fallback)
- **WHEN** no retrieval index exists or retrieval returns zero results
- **THEN** the system SHALL generate the review using the profile alone (no few-shot examples)
- **AND** log a warning that review quality may be lower without retrieval

### Requirement: Structured Review Output
The system SHALL produce structured review output validated by pydantic.

#### Scenario: Review output schema
- **WHEN** a review is generated
- **THEN** the LLM response SHALL be requested as JSON matching the review schema:
  - `summary`: string (2-5 sentences, overall assessment)
  - `inline_comments`: array of objects, each with:
    - `file`: string (file path)
    - `hunk_header`: string (the `@@ ... @@` header of the diff hunk being commented on)
    - `diff_snippet`: string (exact text from the diff being commented on, minimum 3 lines for disambiguation)
    - `side`: string ("addition" or "deletion" — which side of the diff)
    - `comment`: string (the review comment)
    - `confidence`: float (0.0-1.0)
  - `abstained_files`: array of strings (files examined but not commented on)
  - `abstention_reason`: string (why no comments on abstained files)
- **AND** the response SHALL be validated with pydantic
- **AND** malformed responses SHALL trigger a repair pass (per model-abstraction spec)

#### Scenario: Review references profile priorities
- **WHEN** the loaded profile indicates the reviewer cares strongly about a pattern
- **AND** the PR exhibits that pattern
- **THEN** the generated review SHALL flag it
- **AND** the comment SHALL reflect the reviewer's typical way of raising such concerns (matching retrieved examples)

### Requirement: Self-Critique Pass
The system SHALL run a verification pass after generating the initial review.

#### Scenario: Filtering generated comments
- **WHEN** the initial review has been generated
- **THEN** the system SHALL send the review + retrieved examples to the LLM with the self-critique prompt
- **AND** for each inline comment, the LLM SHALL assess:
  1. Would this reviewer actually leave this comment? (yes/probably/no)
  2. Does the phrasing sound like them compared to the examples? (yes/close/no)
  3. Is this already covered by existing review threads? (yes/no)
- **AND** comments rated "no" on question 1 or "yes" on question 3 SHALL be dropped
- **AND** comments rated "probably" on question 1 or "close" on question 2 MAY be kept but with reduced confidence

#### Scenario: All comments filtered
- **WHEN** the self-critique pass drops all inline comments
- **THEN** the system SHALL output the review with summary only and no inline comments
- **AND** this is a valid and correct outcome

### Requirement: Abstention Policy
The system SHALL model reviewer restraint — "say nothing" is a valid outcome.

#### Scenario: Confidence-based filtering
- **WHEN** an inline comment has a confidence score below the effective abstention threshold
- **THEN** the comment SHALL be dropped from the review
- **AND** the effective threshold SHALL be `review.abstention_threshold` from config (default 0.5) unless overridden by calibration

#### Scenario: Calibrated abstention threshold
- **WHEN** `legit calibrate` has been run and produced a calibrated threshold
- **THEN** the calibrated threshold SHALL override the config default
- **AND** the calibrated value SHALL be stored in `.legit/calibration/{profile_name}/threshold.json`
- **AND** the threshold is determined empirically by maximizing F1 score (per calibration spec)

#### Scenario: No-comment review
- **WHEN** zero inline comments survive confidence filtering and self-critique
- **THEN** the review output SHALL explicitly state "No issues matching reviewer priorities"
- **AND** this SHALL be the output in both dry-run and post modes

#### Scenario: Max comments cap
- **WHEN** `review.max_comments` is configured and the review exceeds it
- **THEN** the system SHALL keep only the highest-confidence comments up to the cap

### Requirement: Anchor Resolution
The system SHALL deterministically map review comments to GitHub diff positions using multi-key disambiguation.

#### Scenario: Mapping comments to positions (primary path)
- **WHEN** an inline comment includes `file`, `hunk_header`, `diff_snippet`, and `side`
- **THEN** the system SHALL:
  1. Locate the file in the diff
  2. Find the hunk matching `hunk_header`
  3. Search within that hunk for `diff_snippet` on the correct side (addition/deletion)
  4. Map to the GitHub diff position
- **AND** this mapping SHALL be deterministic code, not LLM-generated

#### Scenario: Disambiguating repeated snippets
- **WHEN** the same `diff_snippet` appears multiple times within the same hunk on the same side
- **THEN** the system SHALL NOT guess which occurrence is intended
- **AND** SHALL include the comment in the review body as a fallback with the file path and snippet for human reference
- **AND** log the ambiguity for debugging

#### Scenario: Hunk header not found
- **WHEN** the `hunk_header` doesn't match any hunk in the file
- **THEN** the system SHALL fall back to searching all hunks for `diff_snippet`
- **AND** if exactly one match is found, use it
- **AND** if multiple matches or zero matches, include the comment in the review body as a fallback

#### Scenario: Snippet not found in diff
- **WHEN** a `diff_snippet` cannot be located in any hunk of the specified file
- **THEN** the system SHALL attempt fuzzy matching (longest common substring, > 80% overlap)
- **AND** if fuzzy matching succeeds with a unique result, use that position
- **AND** if fuzzy matching finds multiple candidates or fails, include the comment in the review body as a fallback
- **AND** log the unresolved anchor for debugging

### Requirement: Dry-Run Mode
The system SHALL support outputting a review as markdown without posting to GitHub.

#### Scenario: Dry-run to stdout
- **WHEN** `legit review --pr <url> --dry-run` is run
- **THEN** the system SHALL output the review as formatted markdown to stdout
- **AND** the output SHALL include the summary section
- **AND** each inline comment SHALL be formatted with file path, line reference, confidence score, and comment text

#### Scenario: Dry-run to file
- **WHEN** `legit review --pr <url> --dry-run --output review.md` is run
- **THEN** the system SHALL write the review markdown to the specified file

#### Scenario: Default mode is dry-run
- **WHEN** `legit review --pr <url>` is run without explicit mode flags
- **AND** `review.post_to_github` is `false` in config (the default)
- **THEN** the system SHALL operate in dry-run mode

### Requirement: Review Posting
The system SHALL post reviews to GitHub when configured to do so.

#### Scenario: Posting a review to GitHub
- **WHEN** `legit review --pr <url> --post` is run (or `review.post_to_github` is `true`)
- **THEN** the system SHALL create a GitHub pull request review via the API
- **AND** the review SHALL be posted with action `COMMENT` (never `APPROVE` or `REQUEST_CHANGES`)
- **AND** the summary SHALL be the review body
- **AND** each inline comment SHALL be attached to the correct file and line via anchor resolution

#### Scenario: Review attribution
- **WHEN** a review is posted to GitHub
- **THEN** the review body SHALL include a footer indicating it was generated by `legit` with the profile name used
- **AND** the footer SHALL include a disclaimer that this is automated initial feedback, not a human review
