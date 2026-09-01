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

*(Revised 2026-09-01 after live verification of available models and hardware.)*

**Generation/judge models (hosted, verified live against each API):** `openai/gpt-5.3-codex`
(newest code model on the existing OpenAI key), `gemini/gemini-3.1-pro-preview` (current
Gemini Pro; 2.5-pro is retired for new API users), and `anthropic/claude-sonnet-5` /
`claude-opus-5` once an ANTHROPIC_API_KEY is provisioned. The `api` provider in
`model_runner.py` reaches all of these through litellm; `LEGIT_MODEL_PROVIDER` /
`LEGIT_MODEL_NAME` / `LEGIT_MODEL_API_BASE` select the backend per deployment. Hardcoded
model names rot in months — verify against the provider's live model list before changing
defaults.

**Self-hosting (real option, not hypothetical):** the hetzner box has an RTX 4000 SFF Ada
(20GB VRAM, CUDA 12.8), 20 cores, 62GB RAM, and ollama installed. A Qwen3-Coder-30B-class
MoE runs there today via `ollama_chat/qwen3-coder:30b` with zero API keys — useful as a
free/fallback backend and as the serving substrate for Phase 2 persona LoRAs. Caveat:
review prompts run 100-400KB, so long-context KV cache spills past 20GB into RAM; fine for
experiments and smaller PRs, but frontier hosted models remain the quality bar for the
generation step.

**Fine-tune target:** LoRA on the current open-weights coder family (Qwen3-Coder class at
time of writing — re-verify what's current when Phase 2 starts, not from memory). Train
hosted or on rented GPU; serve on the hetzner GPU via ollama/vLLM on an OpenAI-compatible
endpoint, which the `api` provider already speaks.

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
