# hetzner deployment (legitpr.dev)

The public endpoint is **https://legitpr.dev** — Caddy (`/etc/caddy/sites.d`, existing
vhost) reverse-proxies to a systemd *user* service on `127.0.0.1:8142`.

## Layout on the server

- Checkout: `~/code/legit` (branch `recovery-rethink`)
- Runtime data: `~/code/legit/.legit/` (profiles, indexes, embeddings, expertise)
- Unit: `~/.config/systemd/user/legit-serve.service` (copy of the file here)
- Auth: HTTP basic auth enforced by the app (`LEGIT_BASIC_AUTH=user:password`), set in
  `~/.config/systemd/user/legit-serve.service.d/auth.conf` (chmod 600, NOT in git)

## Install / update

```bash
cd ~/code/legit && git pull && uv sync --extra embeddings
cp deploy/hetzner/legit-serve.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user restart legit-serve
```

Auth drop-in (once):

```bash
mkdir -p ~/.config/systemd/user/legit-serve.service.d
printf '[Service]\nEnvironment=LEGIT_BASIC_AUTH=team:<password>\n' \
  > ~/.config/systemd/user/legit-serve.service.d/auth.conf
chmod 600 ~/.config/systemd/user/legit-serve.service.d/auth.conf
```

## Pushing freshly built profiles from a workstation

```bash
deploy/hetzner/sync-data.sh
```

LLM backend: the box has the claude CLI authenticated (`~/.claude`); GitHub API uses
`gh auth token` fallback. The historical `~/src/legit` checkout holds the March-era
runtime data and 500+ local autoresearch commits — leave it alone.
