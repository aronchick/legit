# Preview design and release evidence

## Data flow

Public GitHub PR samples → cached raw comments with source URLs and cutoff → map/reduce persona → BM25, semantic and expertise indexes → public PR snapshot and retrieved examples → generated review → critique → stored review UUID → tester feedback.

The existing full-history fetcher remains available and gains explicit refresh plus correct pagination. The separate preview sampler is intentionally bounded and records its search query, total, selected PR numbers and comment counts. It is not a replacement claim for full-history ingestion. Initial sources are one repository per persona to avoid mixing unattributed cross-repository examples.

## Local layout

- `.legit/config.yaml`: configured persona sources and temporal boundary.
- `.legit/data/<owner_repo>/<username>/`: filtered sampled review and discussion comments.
- `.legit/cache/preview-corpus/<persona>/<cutoff>/`: resumable public source downloads.
- `.legit/profiles/`, `.legit/index/`, `.legit/embeddings/`, `.legit/expertise/`: generated assets.
- `.legit/preview/feedback.sqlite3`: review UUID, persona, PR, output and feedback in one database.
- `.legit/calibration/`: per-PR calibration results and explicit failure counts.
- `evidence/`: bounded sample manifests and execution evidence. Logs do not establish effective model identity unless returned provider metadata is recorded.

## Evaluation boundary

Historical judgments require a review-time commit and matching first review round. Latest/final PR state can already contain the human-requested fix. Valid zero scores belong in the denominator; generation, snapshot and judge failures must be reported separately. Unsupported historical cases fail closed instead of producing misleading scores. Current descriptions/discussion must not leak target answers into reconstructed prompts. First-review support is narrower than all historical review activity.

## Release boundary

Local tests establish workflow correctness. Live inference establishes model access and artifacts. Human examination establishes whether specific examples are useful. An authenticated deployed end-to-end run establishes release readiness. External tester feedback is the final outcome; it cannot be manufactured from local tests. Deployment and sending invitations remain separate authorized actions after the candidate is concrete.
