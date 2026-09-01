#!/bin/bash
# Push built .legit artifacts (profiles, indexes, embeddings, expertise,
# config) from this machine to the hetzner deployment, then restart serve.
# Raw fetched data stays local — the server only needs the built artifacts.
set -euo pipefail
cd "$(dirname "$0")/../.."

H=hetzner
rsync -rlpt --delete \
    .legit/config.yaml .legit/profiles .legit/index .legit/embeddings .legit/expertise \
    "$H:code/legit/.legit/"
ssh "$H" 'systemctl --user restart legit-serve.service'
echo "synced; serve restarted"
