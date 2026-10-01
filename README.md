# legit

Learn a GitHub reviewer's style from their historical activity and generate PR reviews in their voice.

Point legit at a person (or set of people) in a repo's history — comments, reviews, commits, issues — and it builds a corpus, distills a reviewer profile, and reviews new PRs the way that maintainer would.

## Quick start

```bash
uv sync                          # core deps
uv sync --extra embeddings       # + ONNX semantic search

uv run legit init                # create .legit/ and starter config
uv run legit fetch               # index GitHub activity for configured profiles
uv run legit build               # map-reduce profile build + retrieval indexes
uv run legit review --pr <URL>   # dry-run review (add --post to submit)
uv run legit calibrate           # score review quality vs. the real reviewer
uv run legit serve               # web UI on port 8142
```

## How it works

1. **Fetch** — pull a reviewer's full activity from the GitHub API.
2. **Build** — map-reduce LLM pipeline distills behavioral patterns into a profile; builds BM25 + semantic (ONNX) retrieval indexes, a per-directory expertise map, and embeddings of every past comment.
3. **Review** — fetch the PR diff + changed files, retrieve the reviewer's most similar past comments, generate a review in their voice, then self-critique each comment (voice match? worth saying?) and filter by confidence.
4. **Calibrate** — LLM-as-judge compares generated reviews against the reviewer's real comments on held-out PRs.

Configuration lives in `.legit/config.yaml`. See `.claude/CLAUDE.md` for architecture details.

## Private preview candidate

The preview roster is `mitchellh`, `thockin`, `liggitt`, `lavalamp`, and `bgrant0607`.
These are simulations based on bounded historical samples, not maintainer endorsements
or verified current views. Review quality remains experimental.

```bash
uv sync --extra embeddings
uv run legit readiness                 # offline artifact inventory; not a quality score
uv run legit serve --host 127.0.0.1    # local preview
bash scripts/check.sh                 # tests, lint, formatting, strict typing, package build
```

Web reviews accept public repositories only and never post to GitHub. Completed
reviews and feedback are persisted in `.legit/preview/feedback.sqlite3`. Set
`LEGIT_BASIC_AUTH` before any remote exposure; deployment is a separate release step.

See [release tasks](openspec/changes/private-preview/tasks.md),
[persona inspection](evidence/persona-inspection.md), and
[live quality findings](evidence/quality-findings.md). The runtime archive includes
all five persona/index sets and their sampled source data; it excludes credentials
and feedback. `uv run scripts/package_preview.py` verifies its artifact hashes.

Use `legit fetch --refresh` to rediscover full history without discarding downloaded
content. `scripts/preview_corpus.py` is a separate bounded sampling tool; its manifest
records the selected source PRs and never certifies full-history coverage.
