# Delta for Data Ingestion

## ADDED Requirements

### Requirement: Activity Indexing
The system SHALL build a complete index of all activity by a specified GitHub user in a specified repository, including PR reviews, PR comments, issue comments, commits, and issues authored.

#### Scenario: First-time index for a user/repo pair
- **WHEN** `legit fetch --repo kubernetes/kubernetes --user liggitt` is run for the first time
- **THEN** the system SHALL query the GitHub API for all activity types
- **AND** store an `index.json` containing item ID, type, URL, timestamp, and fetch status for each item
- **AND** store a `cursor.json` tracking pagination state per activity type

#### Scenario: Incremental index update
- **WHEN** `legit fetch` is run for a user/repo pair that already has an index
- **THEN** the system SHALL query only for items newer than the most recent indexed timestamp per type
- **AND** append new items to the existing `index.json`
- **AND** preserve all existing index entries unchanged

#### Scenario: Index includes all activity types
- **WHEN** indexing completes for a user/repo pair
- **THEN** `index.json` SHALL contain entries of types: `pr_review_comment`, `issue_comment`, `pull_request_review`, `commit`, `issue`, `pull_request`
- **AND** each entry SHALL have fields: `id`, `type`, `url`, `created_at`, `updated_at`, `fetched`

### Requirement: Content Download
The system SHALL download the full content for each indexed item, storing results in per-type JSON files.

#### Scenario: Download all unfetched items
- **WHEN** the download phase runs
- **THEN** the system SHALL iterate over all items in `index.json` where `fetched` is `false`
- **AND** fetch full content from the GitHub API
- **AND** append content to the appropriate per-type JSON file (`pr_comments.json`, `issue_comments.json`, `reviews.json`, `commits.json`, `issues.json`)
- **AND** mark the item's `fetched` field as `true` in `index.json`

#### Scenario: Resumable download after interruption
- **WHEN** a download is interrupted (crash, Ctrl+C, network failure)
- **AND** `legit fetch` is run again
- **THEN** the system SHALL resume from the first unfetched item
- **AND** SHALL NOT re-download items already marked as `fetched`
- **AND** previously downloaded content SHALL remain intact

#### Scenario: Corrupted per-type file recovery
- **WHEN** a per-type JSON file is malformed (e.g., truncated write)
- **THEN** the system SHALL detect the corruption on next run
- **AND** offer to re-download all items of that type by resetting their `fetched` status

### Requirement: Rate Limit Handling
The system SHALL respect GitHub API rate limits and recover gracefully from rate limit responses.

#### Scenario: Approaching rate limit
- **WHEN** the GitHub API response includes `X-RateLimit-Remaining` below a threshold (e.g., 100)
- **THEN** the system SHALL reduce request frequency using exponential backoff

#### Scenario: Rate limit exceeded
- **WHEN** the GitHub API returns HTTP 403 with a rate limit error
- **THEN** the system SHALL pause until the time specified in `X-RateLimit-Reset`
- **AND** display a message showing the wait time
- **AND** resume automatically when the limit resets

#### Scenario: Secondary rate limit (abuse detection)
- **WHEN** the GitHub API returns HTTP 403 with a secondary rate limit error
- **THEN** the system SHALL pause for an increasing backoff period
- **AND** retry the request

### Requirement: Data Storage Layout
The system SHALL store all fetched data in a predictable directory structure under `.legit/data/`.

#### Scenario: Directory structure for a user/repo pair
- **WHEN** data is fetched for user `liggitt` in repo `kubernetes/kubernetes`
- **THEN** all data SHALL be stored under `.legit/data/kubernetes_kubernetes/liggitt/`
- **AND** the directory SHALL contain: `index.json`, `cursor.json`, and per-type content files

#### Scenario: Multiple users in same repo
- **WHEN** data is fetched for multiple users in the same repo
- **THEN** each user's data SHALL be in a separate subdirectory
- **AND** there SHALL be no shared state between user directories

### Requirement: Progress Reporting
The system SHALL report progress during both index and download phases.

#### Scenario: Index progress
- **WHEN** the index phase is running
- **THEN** the system SHALL display the count of items discovered per type as they are found

#### Scenario: Download progress
- **WHEN** the download phase is running
- **THEN** the system SHALL display a progress indicator showing items downloaded vs total items to fetch
- **AND** display the current rate limit remaining
