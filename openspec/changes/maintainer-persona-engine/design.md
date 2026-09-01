# Design: Maintainer Persona Engine

## D1. Source adapters share one data model

`IndexEntry` (models.py) stays the canonical unit. A mailing-list adapter maps an email to
the same shape: message body → `body`, subject → `context`, patch hunks quoted in the mail →
`code_context`, Date header → `created_at`, thread position → reply metadata. Fetch config
grows a `type` discriminator per source:

```yaml
profiles:
  - name: torvalds
    sources:
      - type: mailing-list
        archive: https://lore.kernel.org/lkml/
        author: torvalds@linux-foundation.org
        since: 2015-01-01
  - name: hashimoto
    sources:
      - type: github
        repo: ghostty-org/ghostty
        username: mitchellh
```

lore.kernel.org serves per-message and per-thread mbox over HTTPS with stable Message-ID
URLs; ingestion filters to the target author before storing anything.

## D2. SFT pair construction

One training example per review comment, built from what the reviewer actually saw:

- **system**: persona card (condensed profile — voice, priorities, expertise summary)
- **user**: PR title/description + the diff hunk the comment attaches to + surrounding file
  context (same budget logic review.py already uses)
- **assistant**: the reviewer's verbatim comment

Thread replies become multi-turn examples. Approvals/silence matter too: a sample of PRs the
reviewer approved without comment becomes explicit "no comment needed" examples, because
appropriate abstention is a scored calibration dimension and fine-tunes that always find
something are the classic failure.

**Split**: temporal, not random — last 6 months of activity is eval-only. Random splits leak
style evolution and near-duplicate threads across the boundary.

## D3. Model and serving

Start with a hosted LoRA fine-tune of an open-weights code model (Qwen2.5-Coder-14B class;
32B if 14B voice quality disappoints). Rationale: frontier-lab models can't be fine-tuned on
personal corpora; open-weights + LoRA is cheap enough to iterate per persona.

Serving goes through any OpenAI-compatible endpoint. `model_runner.py` already routes via
litellm, so a persona model is:

```yaml
model:
  provider: openai-compatible
  base_url: https://.../v1
  model_name: legit-hashimoto-v1
```

No new abstraction. The CLI-backend path (claude/gemini/codex) is untouched and remains the
default for the prompt+retrieval mode.

## D4. Calibration as the decision gate

`calibrate.py` already scores issue detection, voice fidelity, appropriate abstention, and
false positives against real held-out comments. Phase 2 adds a comparison mode: run two
backends (prompt+retrieval frontier vs. fine-tuned persona) over the identical holdout set
and emit a side-by-side table. The fine-tune ships only where it wins; the expected hybrid
is frontier-generates → persona-model critiques/rewrites, which slots into the existing
two-pass pipeline by swapping the self-critique model.

## D5. Agent export

`legit agent export` writes a `.claude/agents/<persona>.md` subagent definition: persona
card in the system prompt, plus instructions to call `legit retrieve` (thin CLI wrapper over
the existing retrieval module) for similar-past-comment lookup during review. This makes
personas usable from any Claude Code session immediately, keeps legit as the single source
of persona truth, and gives Phase 4's council a spawn mechanism that OpenClaw (or its
successor) can reuse: a council is N persona sub-agents plus one merge step.

## D6. Identity guardrail

Persona output is labeled simulated at generation time (not as an afterthought in the UI).
`post_to_github` is refused, not just defaulted off, for any profile whose `username` is not
the authenticated token's user.
