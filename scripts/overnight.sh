#!/bin/bash
# Overnight pipeline: finish fetches (resumable), build profiles, calibrate.
# Run from repo root; safe to re-run — every stage resumes or caches.
set -uo pipefail
cd "$(dirname "$0")/.."

unfetched() {
    uv run python -c "
import json
try:
    idx = json.load(open('.legit/data/kubernetes_kubernetes/$1/index.json'))
    print(sum(1 for e in idx if not e.get('fetched')))
except Exception:
    print(999999)
"
}

for user in thockin liggitt; do
    for round in 1 2 3 4 5; do
        n=$(unfetched "$user")
        echo "=== [$user] fetch round $round: $n items pending ==="
        [ "$n" = "0" ] && break
        # --skip-reviews: the list-all-PRs review indexing path is brutally
        # slow on kubernetes-scale repos and inline review comments already
        # arrive via the search-based pr_comments fetch.
        uv run legit fetch --repo kubernetes/kubernetes --user "$user" --skip-reviews || true
    done
    n=$(unfetched "$user")
    [ "$n" != "0" ] && echo "WARNING: [$user] still $n unfetched after 5 rounds; building with partial corpus"
done

for user in thockin liggitt; do
    echo "=== building profile: $user ==="
    uv run legit build --profile "$user" || echo "ERROR: build failed for $user"
done

for user in thockin liggitt; do
    echo "=== calibrating: $user ==="
    uv run legit calibrate --profile "$user" -n 5 || echo "ERROR: calibration failed for $user"
done

echo "=== overnight pipeline done ==="
