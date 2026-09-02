#!/bin/bash
# Three-way backend comparison on identical held-out PRs, one pinned judge.
# Lane 1: hosted gpt-5.3-codex. Lane 2: self-hosted qwen3-coder:30b on the
# hetzner GPU, reached through an ssh tunnel so the laptop's judge key can
# score every lane identically.
set -uo pipefail
cd "$(dirname "$0")/.."

export LEGIT_MODEL_PROVIDER=api
export LEGIT_JUDGE_MODEL=gemini/gemini-3.1-pro-preview

run_cal() {
    local label=$1 model=$2 profile=$3
    if [ -f ".legit/calibration/$profile/compare-$label.json" ]; then
        echo "=== skip $profile [$label]: already done ==="
        return 0
    fi
    echo "=== calibrate $profile [$label] ==="
    LEGIT_MODEL_NAME=$model uv run legit calibrate --profile "$profile" -n 5 \
        || echo "ERROR: $profile $label calibration failed"
    cp ".legit/calibration/$profile/latest.json" \
       ".legit/calibration/$profile/compare-$label.json" 2>/dev/null || true
}

for p in thockin liggitt; do run_cal gpt53codex openai/gpt-5.3-codex "$p"; done

ssh -f -N -L 11435:localhost:11434 hetzner
export LEGIT_MODEL_API_BASE=http://localhost:11435
export LEGIT_MODEL_TIMEOUT=1500
export LEGIT_MODEL_MAX_TOKENS=32768
for p in thockin liggitt; do run_cal qwen30b ollama_chat/qwen3-coder:30b "$p"; done
pkill -f "11435:localhost:11434" || true

echo "=== compare done ==="
