# Private preview release tasks

Status: executing. Updated 2026-09-09. Owner: David Aronchick.

## Outcome and boundaries

An invited engineer can choose a simulated maintainer, review a public PR, inspect useful output, and record useful/wrong/missed feedback. The initial roster is mitchellh (Ghostty), thockin, lavalamp, bgrant0607, and liggitt (Kubernetes). Preserve source attribution when adding other repositories. No implied endorsement, no posting as a maintainer, no outreach sent without explicit authorization.

Implementation checkout: /Users/daaronch/code/second-brain/projects/legit/implementation (preview-release). Original checkout is preserved. The recovery branch's 26 commits are included; current origin/main was checked before implementation.

## Tasks and verification

- [x] P01 Establish clean Git baseline and isolated implementation worktrees. Proof: clean recovery-rethink at 87d7e6c; fetched origin/main is an ancestor; integration rebase is up to date.
- [x] P02 Audit all five corpora, profile files, embeddings, date boundaries and known truncation. Owner: corpus audit agent. Proof: per-persona counts and missing assets, with paths. Dependency: P01.
- [x] P03 Add Mitchell to configuration; implement repeatable readiness report so absent or incomplete personas cannot be mistaken for ready. Owner: coordinator. Proof: offline CLI report plus missing/corrupt asset tests. Dependency: P02.
- [x] P04 Fix calibration averages and failure accounting. Owner: eval agent. Proof: valid zero scores included; failures counted separately; regression tests. Dependency: P01.
- [x] P05 Diagnose historical review context and define clean reconstruction. Owner: eval agent/coordinator. Proof: specify review-time commit, exclude target answers, distinguish context retrieval from label leakage. Dependency: P04.
- [x] P06 Implement simulated labels and prevent unavailable personas from being offered as usable. Owner: preview agent. Proof: web integration tests. Dependency: P01.
- [x] P07 Persist tester feedback against server-issued review identity. Owner: preview agent. Proof: successful review → feedback → read-back; reject invalid review IDs/ratings. Dependency: P06.
- [ ] P08 Fetch complete missing corpora with verified authentication, resumability and rate-limit handling; fix truncation before claiming full coverage. Owner: coordinator. Proof: recorded source counts, dates, errors, completion state. Dependency: P02/P03.
- [x] P09 Build and manually inspect the five profiles and retrieval indexes with a fixed temporal cutoff. Owner: coordinator. Proof: artifacts, source examples, model identity and cutoff recorded. Dependency: P08.
- [ ] P10 Run bounded live evaluations on clean held-out PRs and inspect misses. Start with thockin/liggitt. Proof: per-PR generated output, actual labels withheld from generation, effective model, failures and denominators; no quality claim from mocked tests. Dependency: P04/P05/P09.
- [ ] P11 Curate 2–3 useful examples and at least one honest miss. Proof: traceable PRs and actual saved reviews; no invented findings or cherry-picked aggregate. Dependency: P10.
- [x] P12 Integrate and run pytest, lint, strict mypy, formatting, build and warning gates. Record existing debt separately; do not commit known failures or bypass checks. Dependency: P03/P04/P06/P07.
- [ ] P13 Prepare deployment candidate and verify auth, public-PR scope, model availability, review completion and feedback durability. Deployment is a separate release action requiring concrete candidate approval. Proof: deployed revision and authenticated end-to-end evidence. Dependency: P11/P12.
- [ ] P14 Prepare invitation and tester instructions; select 3–5 reviewers. Send only after explicit authorization. Proof: unsent draft with verified preview link and examples. Dependency: P11/P13.
- [ ] P15 Collect first external session and triage feedback. Proof: someone outside the implementation team completes the flow unaided; record useful findings, incorrect claims, missed issues and whether they would reuse it. Dependency: P14.

## Preview gates

Local code passing is not a release. Each persona needs a usable profile and inspected examples; readiness checks establish artifact availability, not fidelity. The private preview needs at least two credible personas, 2–3 traceable examples, an honest limitation statement, protected access, and a working feedback path. Remaining roster members can be visibly unavailable until validated. Never describe absent tests, model calls, deployment, or external feedback as complete.

The September 1 five-PR liggitt baseline reported zero issue detection on all compared models. Missing context is a hypothesis to investigate, not an established root cause. Fine-tuning, mailing lists, full council review and broad repository expansion follow this feedback loop rather than blocking the first useful preview.


## Execution ledger — 2026-09-09

- P02/P03: disk audit and offline readiness implemented. All five requested personas now have inspected profiles and matching BM25/semantic/expertise artifacts. Original unrelated wdbaruni remains configured but unavailable in this checkout.
- P04/P05: valid zeros and failures are distinct; reviewed head reconstructed with explicitly inferred base. Artifact provenance, cutoff/cache invalidation, mutable evidence filtering, and diff completeness checks added. This is not a verified historical-base benchmark.
- P06/P07: simulated labels, availability validation, public-repository enforcement, durable review UUID and feedback completed. Three live public-PR web-handler runs verified persistence and synthetic feedback read-back; synthetic feedback was removed afterward.
- P08 PARTIAL: bounded public samples fetched for all five (100 PRs Mitchell, 20 each other persona), with manifest and source dates. Full-history ingestion fixes are regression-tested but full-history corpus refresh remains outstanding.
- P09: all five live builds succeeded. Independent manual comparison softened unsupported absolutes and current-style claims, preserving quotations. Artifact hashes verified and packaged in evidence/preview-runtime.zip.
- P10 PARTIAL: live liggitt evaluation discovered PR140634, but its snapshot was rejected. No successful judged holdout or clean benchmark is claimed. See evidence/calibration-liggitt.log.
- P11 NOT MET: actual examples are preserved. Independent review found incomplete observations in Tim's output and a false caller-cap assurance in Jordan's output; these are exploratory examples and an honest miss, not demonstrated bug detections.
- P12: integration had 372 passing tests plus strict typing/lint before final critique refinement. Final gates: 388 tests passed with warnings treated as errors; lint, formatting, strict typing, package build and OpenSpec validation passed. See evidence/verification.txt.
- P13/P14: protected public endpoint responds 401, but no candidate was deployed and no invitation was sent. Deployment checklist, tester guide and invitation draft are prepared. Release quality gate remains open.

### Follow-up tasks made concrete during execution

- [x] Q01 Require supported technical evidence in critique; unsupported and uncertain claims are dropped when code context is supplied. Caller-context expansion remains future work. A live Jordan review falsely attributed a cap from another metrics path.
- [ ] Q02 Obtain a verified historical base snapshot; current base is inferred. Current reconstructed-head scores are exploratory only.
- [ ] Q03 Expand corpus representativeness, especially historical lavalamp/Brian and sparse era coverage; retain explicit source and date limitations.
- [ ] Q04 Verify at least two concrete useful findings and retain an honest miss before positioning as a useful reviewer.
- [ ] Q05 Have 3–5 engineers complete the preview unaided, after deployment and outreach are separately authorized.

Final run: fourth live web-handler review completed after the technical-evidence gate, with actual claude-fable-5-1 metadata and feedback persistence verified. The known false cap assertion was absent; remaining suggestions are not verified bug detections. Current screenshot and JSON are in evidence/. All sub-agents completed, all three agent worktrees were removed, and no preview server was left running. Integration worktree preview-release remains local and uncommitted; original recovery-rethink checkout remains clean. No push, deployment, GitHub review posting or outreach occurred.
