# Delta for Calibration

## ADDED Requirements

### Requirement: Holdout Set Management
The system SHALL manage TWO held-out sets: a tuning set (for auto-optimization) and a validation set (for final scoring). This prevents overfitting by never optimizing against the validation data.

#### Scenario: Creating the holdout sets
- **WHEN** `legit calibrate` is run for the first time for a profile
- **THEN** the system SHALL select the N most recent reviews from the fetched data (configurable via `calibration.holdout_count`, default 15)
- **AND** split them into a tuning set (70%, ~10 reviews) and a validation set (30%, ~5 reviews)
- **AND** the split SHALL be chronological: validation set is the most recent, tuning set is the next most recent
- **AND** store both sets at `.legit/calibration/{profile_name}/holdout.json` with their roles
- **AND** NEITHER set SHALL be used in profile generation or retrieval index construction
- **AND** exclusion SHALL be by PR (not by individual record): ALL authored comments, reviews, and review comments by the reviewer on held-out PRs SHALL be excluded from both profile generation and retrieval index — this prevents leakage from sibling records (e.g., inline comments from the same review submission)

#### Scenario: Holdout set stability
- **WHEN** `legit calibrate` is run again with the same data
- **THEN** the system SHALL reuse the existing holdout sets
- **AND** SHALL NOT re-select new reviews unless `--refresh-holdout` is specified

#### Scenario: Refreshing the holdout set
- **WHEN** `legit calibrate --refresh-holdout` is run
- **THEN** the system SHALL select new holdout sets from the most recent reviews
- **AND** rebuild the profile and retrieval index excluding all holdout data

### Requirement: Calibration Scoring
The system SHALL generate legit reviews for held-out PRs and score them against the real reviews.

#### Scenario: Running calibration
- **WHEN** `legit calibrate --profile liggitt-k8s` is run
- **THEN** for each review in the VALIDATION set, the system SHALL:
  1. Fetch the PR data for the held-out review
  2. Generate a legit review using the current profile and retrieval index
  3. Send both the real review and the generated review to the LLM as judge
- **AND** the LLM-as-judge prompt SHALL ask:
  - "Rate the stylistic similarity between these two reviews on a scale of 1-10"
  - "Which of these two reviews sounds more like the original reviewer?"
  - "What specific differences do you notice in voice, priorities, or phrasing?"
- **AND** the system SHALL report an aggregate similarity score and per-review diagnostics
- **AND** the validation set score is the REPORTED score (never optimized against)

#### Scenario: Calibration output
- **WHEN** calibration scoring completes
- **THEN** the system SHALL display:
  - Aggregate similarity score (mean across held-out reviews)
  - Per-review scores with brief diagnostics
  - Top 3 areas where the generated reviews diverge from the real ones
- **AND** store scores at `.legit/calibration/{profile_name}/scores.json`

#### Scenario: Comparing to previous calibration
- **WHEN** calibration has been run before for this profile
- **THEN** the system SHALL display the score delta from the previous run
- **AND** indicate whether the profile improved, regressed, or stayed the same

### Requirement: Auto-Optimization Loop
The system SHALL support automatic profile optimization through iterative calibration.

#### Scenario: Running auto-optimization
- **WHEN** `legit calibrate --auto --profile liggitt-k8s` is run
- **THEN** the system SHALL:
  1. Score against the TUNING set (not validation set)
  2. Send the tuning diagnostics + current profile to the LLM with the prompt: "Based on these calibration results, suggest specific edits to the profile that would improve the similarity score. Focus on the areas where the generated reviews diverge most from the real ones."
  3. Apply the suggested profile edits
  4. Save a snapshot of the profile at `.legit/calibration/{profile_name}/iterations/iter_N.md`
  5. Re-score against the TUNING set
  6. Repeat until the tuning score meets the target OR plateaus (< 0.2 improvement) OR max iterations reached
  7. After convergence, run a FINAL score against the VALIDATION set (never seen during optimization)
- **AND** display progress after each iteration: iteration number, tuning score, delta, and summary of changes
- **AND** the final output SHALL clearly distinguish: "Tuning score: X.X, Validation score: Y.Y"
- **AND** if validation score is significantly lower than tuning score (> 1.5 difference), warn about potential overfitting

#### Scenario: Auto-optimization convergence
- **WHEN** the calibration score reaches `calibration.target_score` (default 8.0)
- **THEN** the system SHALL stop iterating
- **AND** display "Target score reached" with the final score

#### Scenario: Auto-optimization plateau
- **WHEN** the score improvement between iterations is less than 0.2
- **THEN** the system SHALL stop iterating
- **AND** display "Score plateaued" with the final score and suggestion to manually review the profile

#### Scenario: Auto-optimization iteration cap
- **WHEN** `calibration.max_iterations` (default 5) iterations have been completed
- **THEN** the system SHALL stop iterating regardless of score
- **AND** display the best-scoring iteration and offer to revert to it

#### Scenario: Rollback to best iteration
- **WHEN** the final iteration's score is lower than a previous iteration
- **THEN** the system SHALL ask the user whether to keep the current profile or revert to the best-scoring iteration

### Requirement: Abstention Threshold Calibration
The system SHALL empirically calibrate the abstention confidence threshold during calibration.

#### Scenario: Calibrating the threshold
- **WHEN** calibration scoring generates reviews for held-out PRs
- **THEN** the system SHALL compare generated comments to the real reviewer's comments using the comment-matching rule
- **AND** classify each generated comment as: true positive (matched a real comment), false positive (no matching real comment), or missed (real comment with no matching generated comment)
- **AND** sweep confidence thresholds from 0.1 to 0.9 to find the threshold that maximizes F1 score (balancing false positives and missed comments)
- **AND** store the calibrated threshold at `.legit/calibration/{profile_name}/threshold.json`
- **AND** display the calibrated threshold and its precision/recall tradeoff

#### Scenario: Comment-matching rule for TP/FP classification
- **WHEN** determining whether a generated comment matches a real comment
- **THEN** a match requires ALL of:
  1. Same file path
  2. Overlapping code region (the generated comment's target lines overlap with or are within 5 lines of the real comment's target lines)
  3. Same concern type — determined by sending both comments to the LLM with the prompt: "Do these two review comments address the same underlying concern? Answer yes or no."
- **AND** this matching rule SHALL be applied consistently across all calibration runs

### Requirement: Calibration Diagnostics
The system SHALL provide actionable diagnostics about where the profile diverges from the real reviewer.

#### Scenario: Overcomment detection
- **WHEN** the generated reviews contain significantly more comments than the real reviews
- **THEN** the diagnostics SHALL flag "overcomment bias" and suggest raising the abstention threshold

#### Scenario: Voice mismatch detection
- **WHEN** the LLM-as-judge notes phrasing differences
- **THEN** the diagnostics SHALL report specific phrasing patterns that differ and suggest retrieval improvements

#### Scenario: Priority mismatch detection
- **WHEN** the generated reviews focus on different aspects than the real reviews
- **THEN** the diagnostics SHALL report which priorities are over- or under-represented in the profile
