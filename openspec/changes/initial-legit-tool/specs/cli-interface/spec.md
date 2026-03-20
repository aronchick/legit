# Delta for CLI Interface

## ADDED Requirements

### Requirement: CLI Entry Point
The system SHALL provide a `legit` CLI with five subcommands: `init`, `fetch`, `build`, `review`, and `calibrate`.

#### Scenario: Running legit without arguments
- **WHEN** `legit` is run without subcommands
- **THEN** the system SHALL display usage help showing available subcommands and a brief description of each

#### Scenario: Running legit with --version
- **WHEN** `legit --version` is run
- **THEN** the system SHALL display the installed version

### Requirement: Fetch Command
The system SHALL provide a `legit fetch` command that indexes and downloads GitHub activity data.

#### Scenario: Fetch with explicit arguments
- **WHEN** `legit fetch --repo kubernetes/kubernetes --user liggitt` is run
- **THEN** the system SHALL run the index phase followed by the download phase for the specified user/repo

#### Scenario: Fetch from config
- **WHEN** `legit fetch` is run without arguments and a `.legit/config.yaml` exists with profile sources
- **THEN** the system SHALL fetch data for all configured sources

#### Scenario: Fetch index only
- **WHEN** `legit fetch --index-only` is run
- **THEN** the system SHALL run only the index phase, skipping content download

#### Scenario: Fetch with since filter
- **WHEN** `legit fetch --since 2023-01-01` is run
- **THEN** the system SHALL only index/download items created after the specified date

### Requirement: Build Command
The system SHALL provide a `legit build` command that generates a reviewer profile and retrieval index from fetched data.

#### Scenario: Build a named profile
- **WHEN** `legit build --profile liggitt-k8s` is run
- **THEN** the system SHALL process all fetched data for the configured sources of that profile
- **AND** generate the profile at `.legit/profiles/liggitt-k8s.md`
- **AND** build the BM25 retrieval index at `.legit/index/liggitt-k8s/`

#### Scenario: Build from config default
- **WHEN** `legit build` is run without arguments and only one profile is configured
- **THEN** the system SHALL build that profile

#### Scenario: Build with rebuild flag
- **WHEN** `legit build --rebuild-map` is run
- **THEN** the system SHALL re-process all chunks even if cached outputs exist

#### Scenario: Build with no-overwrite flag
- **WHEN** `legit build --no-overwrite` is run and the profile already exists
- **THEN** the system SHALL skip profile generation and inform the user

### Requirement: Review Command
The system SHALL provide a `legit review` command that reviews a PR using a generated profile and retrieval index.

#### Scenario: Review a PR
- **WHEN** `legit review --pr https://github.com/kubernetes/kubernetes/pull/12345` is run
- **THEN** the system SHALL load the profile, retrieve similar past comments, generate a review with self-critique, and output or post it

#### Scenario: Review with explicit profile
- **WHEN** `legit review --pr <url> --profile liggitt-k8s` is run
- **THEN** the system SHALL use the specified profile regardless of config defaults

#### Scenario: Review with dry-run
- **WHEN** `legit review --pr <url> --dry-run` is run
- **THEN** the system SHALL output the review to stdout without posting to GitHub

#### Scenario: Review with post
- **WHEN** `legit review --pr <url> --post` is run
- **THEN** the system SHALL post the review to GitHub via the API

#### Scenario: Review with output file
- **WHEN** `legit review --pr <url> --dry-run --output review.md` is run
- **THEN** the system SHALL write the review to the specified file

### Requirement: Calibrate Command
The system SHALL provide a `legit calibrate` command that measures and optimizes profile fidelity.

#### Scenario: Run calibration scoring
- **WHEN** `legit calibrate --profile liggitt-k8s` is run
- **THEN** the system SHALL score the profile against held-out reviews and display results

#### Scenario: Run auto-optimization
- **WHEN** `legit calibrate --auto --profile liggitt-k8s` is run
- **THEN** the system SHALL run the iterative calibration loop, optimizing the profile until convergence

#### Scenario: Refresh holdout set
- **WHEN** `legit calibrate --refresh-holdout --profile liggitt-k8s` is run
- **THEN** the system SHALL select a new holdout set and rebuild the profile excluding holdout data

#### Scenario: Show calibration history
- **WHEN** `legit calibrate --history --profile liggitt-k8s` is run
- **THEN** the system SHALL display the score history for that profile across all calibration runs

### Requirement: Init Command
The system SHALL provide a `legit init` command that creates the `.legit/` directory structure and a starter config.

#### Scenario: Initialize a new project
- **WHEN** `legit init` is run in a directory without `.legit/`
- **THEN** the system SHALL create `.legit/config.yaml` with sensible defaults
- **AND** create `.legit/profiles/`, `.legit/data/`, `.legit/index/`, and `.legit/calibration/` directories
- **AND** display next steps for the user

#### Scenario: Init in existing project
- **WHEN** `legit init` is run in a directory that already has `.legit/`
- **THEN** the system SHALL NOT overwrite existing config
- **AND** display a message that the project is already initialized

### Requirement: Error Messaging
The system SHALL provide clear, actionable error messages.

#### Scenario: Missing GitHub token
- **WHEN** a command requiring GitHub API access is run without a token configured
- **THEN** the system SHALL display an error explaining how to set the token

#### Scenario: Missing LLM CLI
- **WHEN** a command requiring LLM inference is run but the configured CLI is not installed
- **THEN** the system SHALL display an error naming the missing CLI and how to install it

#### Scenario: No profile exists for review
- **WHEN** `legit review` is run but no profile has been generated yet
- **THEN** the system SHALL display an error suggesting to run `legit build` first

#### Scenario: No data exists for build
- **WHEN** `legit build` is run but no data has been fetched yet
- **THEN** the system SHALL display an error suggesting to run `legit fetch` first

#### Scenario: No retrieval index for review
- **WHEN** `legit review` is run but no retrieval index exists
- **THEN** the system SHALL proceed with a warning and suggest re-running `legit build`
