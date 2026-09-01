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
