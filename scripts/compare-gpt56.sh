#!/bin/bash
# Hosted-lane rerun on the actual newest OpenAI model (gpt-5.6-sol,
# 2026-06-23) — waits for any in-flight comparison run to finish first so
# per-profile latest.json files don't race.
set -uo pipefail
cd "$(dirname "$0")/.."

while pgrep -f "bash scripts/compare.sh" >/dev/null 2>&1; do sleep 30; done

export LEGIT_MODEL_PROVIDER=api
export LEGIT_JUDGE_MODEL=gemini/gemini-3.1-pro-preview

for p in thockin liggitt; do
    if [ -f ".legit/calibration/$p/compare-gpt56sol.json" ]; then
        echo "=== skip $p [gpt56sol]: already done ==="
        continue
    fi
    echo "=== calibrate $p [gpt56sol] ==="
    LEGIT_MODEL_NAME=openai/gpt-5.6-sol uv run legit calibrate --profile "$p" -n 5 \
        || echo "ERROR: $p gpt56sol calibration failed"
    cp ".legit/calibration/$p/latest.json" \
       ".legit/calibration/$p/compare-gpt56sol.json" 2>/dev/null || true
done
echo "=== gpt56 lanes done ==="
