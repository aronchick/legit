#!/bin/bash
# Stage 2 of the pipeline: build profiles via the Gemini API (decoupled from
# any claude-CLI usage limits), run held-out calibrations with a pinned
# judge, and sync artifacts to the hetzner deployment.
set -uo pipefail
cd "$(dirname "$0")/.."

export LEGIT_MODEL_PROVIDER=api
export LEGIT_JUDGE_MODEL=gemini/gemini-3.1-pro-preview

for user in thockin liggitt; do
    echo "=== building profile: $user (gemini api) ==="
    LEGIT_MODEL_NAME=gemini/gemini-3.1-pro-preview \
        uv run legit build --profile "$user" || { echo "ERROR: build failed for $user"; }
done

for user in thockin liggitt; do
    echo "=== calibrating: $user (generator gpt-5.3-codex, judge gemini-3.1-pro) ==="
    LEGIT_MODEL_NAME=openai/gpt-5.3-codex \
        uv run legit calibrate --profile "$user" -n 5 || echo "ERROR: calibration failed for $user"
done

echo "=== syncing artifacts to hetzner ==="
bash deploy/hetzner/sync-data.sh || echo "ERROR: sync failed"

echo "=== finish pipeline done ==="
