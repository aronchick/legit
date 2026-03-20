# Delta for Profile Generation

## ADDED Requirements

### Requirement: Data Chunking
The system SHALL divide ingested data into fixed-size count-based chunks for independent processing.

#### Scenario: Chunking fetched data
- **WHEN** `legit build` is run for a profile
- **THEN** the system SHALL load all fetched items across all types for the configured sources
- **AND** exclude ALL items from PRs in the calibration holdout set (if one exists) — exclusion is by PR, not by individual record, to prevent leakage from sibling comments/reviews on the same PR
- **AND** sort them chronologically by `created_at`
- **AND** divide them into chunks of the configured `chunk_size` (default 150)
- **AND** the final chunk MAY be smaller than `chunk_size`

#### Scenario: Chunk stability across runs
- **WHEN** `legit build` is run multiple times without new data
- **THEN** the chunks SHALL be identical each time
- **AND** previously processed chunk outputs SHALL be reused without re-processing

### Requirement: Map Phase with Emergent Categories
The system SHALL independently analyze each chunk of reviewer activity, discovering patterns organically rather than prescribing fixed categories.

#### Scenario: Processing a single chunk
- **WHEN** the map phase processes a chunk
- **THEN** the system SHALL send the chunk content to the configured LLM with a prompt that asks it to discover and report whatever patterns it observes about the reviewer's behavior
- **AND** the prompt SHALL NOT prescribe fixed categories — it SHALL ask for observations organized by the situations in which they occur (e.g., "when reviewing API changes," "when flagging missing tests," "when leaving nits")
- **AND** the prompt SHALL ask for: behavioral observations, exact representative quotes, severity patterns, situational triggers, and any distinctive habits
- **AND** store the structured observations as `chunk_NNN.json` in `.legit/cache/chunks/{profile_name}/`
- **AND** include the chunk's date range in the output metadata

#### Scenario: Resumable map phase
- **WHEN** the map phase is interrupted
- **AND** `legit build` is run again
- **THEN** the system SHALL skip chunks that already have cached outputs
- **AND** process only chunks without cached outputs

#### Scenario: Parallel map processing
- **WHEN** the map phase has multiple chunks to process
- **THEN** the system SHOULD process chunks in parallel (up to a configurable concurrency limit)
- **AND** each chunk SHALL be processed independently with no shared state

#### Scenario: Force rebuild of map outputs
- **WHEN** `legit build --rebuild-map` is run
- **THEN** the system SHALL delete all cached chunk outputs for the profile
- **AND** re-process all chunks from scratch

### Requirement: Reduce Phase with Temporal Weighting
The system SHALL merge all chunk observations into a single distilled reviewer profile document with explicit temporal weighting.

#### Scenario: Merging observations into a profile
- **WHEN** all chunks have been processed in the map phase
- **THEN** the system SHALL send all chunk observations to the LLM with a prompt that synthesizes them into a unified profile
- **AND** the merge prompt SHALL instruct the LLM to apply exponential decay weighting based on the configured `temporal_half_life` (default 730 days / 2 years)
- **AND** the output SHALL be a structured markdown document stored at `.legit/profiles/{name}.md`

#### Scenario: Profile metadata section
- **WHEN** a profile is generated
- **THEN** the profile SHALL contain a `## Generated` metadata section with: date, source repos, usernames, data range, items processed, temporal half-life used, and calibration score (if available)

#### Scenario: Emergent profile sections
- **WHEN** the reduce phase generates a profile
- **THEN** the remaining sections (after metadata) SHALL be organized by whatever categories best describe this specific reviewer
- **AND** the reduce prompt SHALL instruct the LLM to group observations into emergent themes rather than forcing them into a prescribed template
- **AND** the reduce prompt SHALL emphasize preserving situation-specific behaviors (e.g., "terse on naming, expansive on API design")

#### Scenario: Profile with temporal evolution
- **WHEN** a reviewer's priorities have changed over time
- **THEN** the profile SHALL reflect current priorities as primary (weighted by recency)
- **AND** note the evolution where relevant
- **AND** not discard older patterns entirely — they inform the reviewer's overall philosophy

#### Scenario: Representative examples in profile
- **WHEN** a profile is generated
- **THEN** the profile SHALL include representative example quotes from the reviewer
- **AND** examples SHALL be selected for diversity (covering different situations) and recency
- **AND** each example SHALL include brief context about what was being reviewed

### Requirement: Multiple Primary Sources
The system SHALL support building a single profile from multiple primary reviewer sources.

#### Scenario: Two primary reviewers in same repo
- **WHEN** a profile is configured with two primary sources in the same repo
- **THEN** the system SHALL process each reviewer's data independently in the map phase
- **AND** merge all chunk observations from both reviewers in the reduce phase
- **AND** the reduce prompt SHALL instruct the LLM to blend shared priorities while preserving distinct contributions

#### Scenario: Primary reviewers with different emphases
- **WHEN** two primary reviewers have different but complementary priorities
- **THEN** the profile SHALL include priorities from both reviewers
- **AND** note which priorities are shared vs. distinctive to each reviewer

#### Scenario: Voice blending for multiple primaries
- **WHEN** multiple primary reviewers have different communication styles
- **THEN** the profile SHALL document each reviewer's distinctive voice patterns
- **AND** the review generation step SHALL select voice patterns based on the type of comment being made (using whichever reviewer's style best fits the situation)

### Requirement: Profile Editability
The profile SHALL be a standard markdown document that humans can manually edit and refine.

#### Scenario: Manual profile editing
- **WHEN** a user edits the generated profile markdown file
- **THEN** subsequent `legit review` commands SHALL use the edited profile
- **AND** `legit build` SHALL overwrite the profile unless `--no-overwrite` is specified

#### Scenario: Profile with manual additions preserved
- **WHEN** `legit build --no-overwrite` is run and a profile already exists
- **THEN** the system SHALL NOT overwrite the existing profile
- **AND** SHALL display a message indicating the profile was preserved
