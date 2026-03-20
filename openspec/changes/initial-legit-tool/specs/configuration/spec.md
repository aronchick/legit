# Delta for Configuration

## ADDED Requirements

### Requirement: Config File Schema
The system SHALL use a YAML configuration file at `.legit/config.yaml` validated by pydantic.

#### Scenario: Valid minimal config
- **WHEN** `.legit/config.yaml` contains at minimum a `model.provider` and one profile with at least one source
- **THEN** the system SHALL load and validate the config successfully
- **AND** apply defaults for all unspecified fields

#### Scenario: Invalid config
- **WHEN** `.legit/config.yaml` contains invalid values (e.g., unknown provider, missing required fields)
- **THEN** the system SHALL display a specific validation error indicating which field is invalid and what values are accepted

#### Scenario: Config defaults
- **WHEN** a config field is not specified
- **THEN** the following defaults SHALL apply:
  - `model.provider`: `gemini`
  - `model.name`: `null` (use CLI default)
  - `model.temperature`: `0.3`
  - `github.token_env`: `GITHUB_TOKEN`
  - `profiles[].chunk_size`: `150`
  - `profiles[].temporal_half_life`: `730` (days, ~2 years)
  - `retrieval.top_k`: `10`
  - `retrieval.index_type`: `bm25`
  - `review.post_to_github`: `false`
  - `review.review_action`: `COMMENT`
  - `review.max_comments`: `null` (no cap)
  - `review.abstention_threshold`: `0.5`
  - `calibration.holdout_count`: `15`
  - `calibration.max_iterations`: `5`
  - `calibration.target_score`: `8.0`

### Requirement: Directory Structure
The system SHALL maintain a predictable `.legit/` directory structure.

#### Scenario: After init
- **WHEN** `legit init` completes
- **THEN** the following directory structure SHALL exist:
  ```
  .legit/
  ├── config.yaml
  ├── profiles/
  ├── data/
  ├── index/
  └── calibration/
  ```

#### Scenario: After fetch
- **WHEN** `legit fetch` completes for a user/repo
- **THEN** the following SHALL exist under `.legit/data/{owner}_{repo}/{username}/`:
  - `index.json`
  - `cursor.json`
  - Per-type content files (created as items are downloaded)

#### Scenario: After build
- **WHEN** `legit build` completes for a profile
- **THEN** `.legit/profiles/{name}.md` SHALL exist
- **AND** `.legit/cache/chunks/{name}/` SHALL contain chunk output files
- **AND** `.legit/index/{name}/bm25.json` SHALL contain the retrieval index

#### Scenario: After calibrate
- **WHEN** `legit calibrate` completes for a profile
- **THEN** `.legit/calibration/{name}/holdout.json` SHALL exist
- **AND** `.legit/calibration/{name}/scores.json` SHALL contain scoring history
- **AND** if auto-optimization was run, `.legit/calibration/{name}/iterations/` SHALL contain profile snapshots

### Requirement: Config Merging with CLI Flags
CLI flags SHALL override config file values for the duration of that command.

#### Scenario: CLI flag overrides config
- **WHEN** `legit review --pr <url> --post` is run but `review.post_to_github` is `false` in config
- **THEN** the system SHALL post the review to GitHub for this invocation only
- **AND** the config file SHALL NOT be modified

#### Scenario: Config is the source of truth
- **WHEN** no CLI flags override a value
- **THEN** the config file value SHALL be used

### Requirement: Profile Configuration
Each profile SHALL define its sources, build parameters, and retrieval settings.

#### Scenario: Single primary source
- **WHEN** a profile is configured with one source of type `primary`
- **THEN** `legit fetch` SHALL fetch data for that source
- **AND** `legit build` SHALL build the profile and retrieval index from that source's data

#### Scenario: Multiple primary sources
- **WHEN** a profile is configured with multiple sources of type `primary`
- **THEN** `legit fetch` SHALL fetch data for all sources
- **AND** `legit build` SHALL blend all sources into a single profile and combined retrieval index

#### Scenario: Custom chunk size
- **WHEN** a profile specifies `chunk_size: 200`
- **THEN** `legit build` SHALL use chunks of 200 items for the map phase

#### Scenario: Custom temporal half-life
- **WHEN** a profile specifies `temporal_half_life: 365`
- **THEN** `legit build` SHALL weight observations from 1 year ago at 50% of current observations
