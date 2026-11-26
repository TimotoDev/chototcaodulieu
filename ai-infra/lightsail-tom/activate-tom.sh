#!/usr/bin/env bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo 'Run with sudo on the Tom Lightsail instance.' >&2
  exit 1
fi

if [ ! -s /home/tom/.openclaw/runtime.env ]; then
  echo 'Slack tokens are missing. Run configure-tom.sh first.' >&2
  exit 1
fi

systemctl start tom-proxy.service
sleep 2
if ! curl -fsS http://127.0.0.1:8080/health | jq -e '.counts.available > 0' >/dev/null; then
  systemctl stop tom-proxy.service
  echo 'No available Antigravity account. Complete proxy OAuth first.' >&2
  exit 1
fi

if ! sudo -u tom -H bash -c '
  cd /home/tom
  set -a
  source /home/tom/.openclaw/runtime.env
  export OPENCLAW_CONFIG_PATH=/home/tom/.openclaw/openclaw.json
  openclaw config validate --json | jq -e .valid >/dev/null
  /usr/bin/node /home/tom/kb-mcp/server.mjs --smoke | jq -e ".pageCount >= 100 and (.topPaths | length > 0)" >/dev/null
'; then
  systemctl stop tom-proxy.service
  echo 'The read-only TiMoto knowledge source failed its local smoke check.' >&2
  exit 1
fi

systemctl enable tom-proxy.service
systemctl enable --now tom-openclaw.service
sleep 3
systemctl is-active --quiet tom-openclaw.service
echo 'Tom service is running. Test a paired Slack DM and verify a cited answer.'
