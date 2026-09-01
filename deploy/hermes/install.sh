#!/bin/bash
# Install legit serve + Cloudflare tunnel as LaunchAgents on Hermes.
# Run ON Hermes from the repo root: bash deploy/hermes/install.sh
# Prereq: the tunnel credential JSON must already exist at
#   ~/.config/cloudflared/83df6b9c-f6cd-4472-add8-b6b9f85fb553.json
set -euo pipefail

cd "$(dirname "$0")"

CRED=~/.config/cloudflared/83df6b9c-f6cd-4472-add8-b6b9f85fb553.json
if [ ! -f "$CRED" ]; then
    echo "ERROR: tunnel credential missing at $CRED" >&2
    echo "Copy it from the machine that ran 'cloudflared tunnel create legit'." >&2
    exit 1
fi
chmod 600 "$CRED"

mkdir -p ~/.config/cloudflared ~/Library/LaunchAgents ~/Library/Logs
cp legit-config.yml ~/.config/cloudflared/legit-config.yml
cp com.legit.tunnel.plist com.legit.serve.plist ~/Library/LaunchAgents/

UID_N=$(id -u)
for label in com.legit.tunnel com.legit.serve; do
    launchctl bootout "gui/$UID_N/$label" 2>/dev/null || true
    launchctl bootstrap "gui/$UID_N" ~/Library/LaunchAgents/$label.plist
done

sleep 3
launchctl list | grep legit || true
curl -s -o /dev/null -w "local UI: HTTP %{http_code}\n" --max-time 5 http://127.0.0.1:8142/
