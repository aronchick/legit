#!/bin/bash
# Push built .legit artifacts (profiles, indexes, embeddings, expertise,
# config) from this machine to the Hermes deployment, then restart serve.
# Raw fetched data stays local — the server only needs the built artifacts.
set -euo pipefail
cd "$(dirname "$0")/../.."

H=daaronch@feralhogpen.tail8e9db.ts.net
ssh "$H" 'mkdir -p ~/code/legit/.legit'
rsync -rlpt --delete \
    .legit/config.yaml .legit/profiles .legit/index .legit/embeddings .legit/expertise \
    "$H:code/legit/.legit/"
ssh "$H" 'launchctl kickstart -k "gui/$(id -u)/com.legit.serve"' || true
echo "synced; serve restarted"
