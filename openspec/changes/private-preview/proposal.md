# Private preview for maintainer personas

## Why

A working UI is not yet a useful reviewer. Local history is stale or missing for the five requested personas, ingestion can incorrectly mark incomplete data complete, and calibration uses final PR state and excludes valid zero scores. Engineers need an honest, complete preview workflow before outreach.

## What Changes

- Add Mitchell Hashimoto's Ghostty source and an offline artifact inventory.
- Repair ingestion pagination, truncation handling and refresh; build explicitly bounded public-PR samples for the preview.
- Label simulated reviews, show unavailable personas and persist feedback tied to actual review IDs.
- Enforce public repository scope before reading code through the host credential.
- Separate successful evaluations from failures and reconstruct supported historical review snapshots.
- Make inference tools unavailable to historical corpus content and record actual CLI model usage.
- Repair local lint, typing, formatting and dependency warning gates.

## Impact

Changes ingestion, CLI, evaluation, inference and the existing FastAPI UI. Runtime corpora and feedback remain local ignored data. No public deployment or outreach is implied by local acceptance. Existing optional GitHub posting remains outside this preview; browser reviews always run dry.
