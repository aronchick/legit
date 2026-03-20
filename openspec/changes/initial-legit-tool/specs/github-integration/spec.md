# Delta for GitHub Integration

## ADDED Requirements

### Requirement: Authentication
The system SHALL authenticate to the GitHub API using a Personal Access Token (PAT) sourced from an environment variable.

#### Scenario: Token from configured env var
- **WHEN** the system needs to make a GitHub API call
- **THEN** it SHALL read the token from the environment variable named in `github.token_env` config (default: `GITHUB_TOKEN`)

#### Scenario: Token validation
- **WHEN** the system first uses the token in a session
- **THEN** it SHALL make a test API call to verify the token is valid
- **AND** display the authenticated user and rate limit status

### Requirement: API Client
The system SHALL use the GitHub REST API for all data operations.

#### Scenario: Fetching paginated data
- **WHEN** the system fetches a paginated GitHub API endpoint
- **THEN** it SHALL follow `Link` headers to retrieve all pages
- **AND** respect rate limit headers on each response

#### Scenario: Fetching PR review data
- **WHEN** the system fetches PR reviews for a user
- **THEN** it SHALL retrieve: review body, review state, all review comments with diff positions, file paths, and line numbers

#### Scenario: Fetching PR diff
- **WHEN** the system needs the diff for a PR
- **THEN** it SHALL fetch the diff via the GitHub API (Accept: application/vnd.github.diff)
- **AND** parse it into a structured representation with file paths, hunks, and line numbers

#### Scenario: Fetching full file content
- **WHEN** the system needs the full content of a changed file in a PR
- **THEN** it SHALL fetch the file at the PR's head commit SHA via the contents API

### Requirement: Review Posting
The system SHALL post reviews using the GitHub Pull Request Reviews API.

#### Scenario: Creating a review with inline comments
- **WHEN** the system posts a review
- **THEN** it SHALL use `POST /repos/{owner}/{repo}/pulls/{pull_number}/reviews`
- **AND** the body SHALL contain the review summary
- **AND** the `comments` array SHALL contain inline comments with `path`, `position` (diff position), and `body`
- **AND** the `event` SHALL be `COMMENT`

#### Scenario: Review posting failure
- **WHEN** posting a review fails (e.g., 422 due to invalid diff position)
- **THEN** the system SHALL log the specific comments that failed
- **AND** retry posting the review without the failed comments
- **AND** include the failed comments in the review body as a fallback

### Requirement: PR URL Parsing
The system SHALL parse GitHub PR URLs to extract owner, repo, and PR number.

#### Scenario: Standard PR URL
- **WHEN** given `https://github.com/kubernetes/kubernetes/pull/12345`
- **THEN** the system SHALL extract owner=`kubernetes`, repo=`kubernetes`, pull_number=`12345`

#### Scenario: Invalid PR URL
- **WHEN** given a URL that doesn't match the GitHub PR pattern
- **THEN** the system SHALL display a clear error with the expected format
