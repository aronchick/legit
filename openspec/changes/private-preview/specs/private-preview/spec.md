## ADDED Requirements

### Requirement: Honest persona availability
The preview SHALL label every review as simulated and SHALL distinguish profile availability from verified quality.

#### Scenario: Missing persona
- **WHEN** a selected persona lacks a usable profile
- **THEN** the app rejects review generation and displays its unavailability

### Requirement: Feedback attribution
The preview SHALL persist feedback against a server-created identifier for an actual completed review.

#### Scenario: Unknown review
- **WHEN** feedback names an unknown review identifier
- **THEN** the server rejects it without storing feedback

### Requirement: Public PR scope
The preview SHALL reject nonpublic repositories before fetching code or invoking inference.

#### Scenario: Private repository
- **WHEN** the host credential can read a private PR submitted by a tester
- **THEN** the preview rejects it before retrieving its diff or source

### Requirement: Honest evaluation accounting
Calibration SHALL include successful zero scores and SHALL count generation and judge failures separately.

#### Scenario: Zero score
- **WHEN** a judge successfully assigns zero to a generated review
- **THEN** that score remains in the average denominator

### Requirement: Resumable bounded corpus
A preview sample SHALL retain source PR identifiers, cutoff and sample limits and SHALL NOT claim full-history coverage.

#### Scenario: Interrupted or corrupt download
- **WHEN** a sample download fails or cached JSON cannot be decoded
- **THEN** the command fails without reporting a completed corpus; intact per-PR cache remains reusable

### Requirement: Offline availability inventory
The readiness command SHALL inspect artifacts without GitHub or model calls.

#### Scenario: Missing or corrupt index
- **WHEN** an index cannot be loaded or has inconsistent counts
- **THEN** readiness reports incomplete artifacts and returns nonzero without modifying them
