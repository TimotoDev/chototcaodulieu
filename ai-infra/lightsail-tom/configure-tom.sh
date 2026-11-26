#!/usr/bin/env bash
set -euo pipefail
umask 077

if [ "$(id -u)" -ne 0 ]; then
  echo 'Run with sudo on the Tom Lightsail instance.' >&2
  exit 1
fi

read -r -s -p 'Slack signing secret: ' slack_signing_secret
printf '\n'
read -r -s -p 'Slack bot OAuth token (xoxb-...): ' slack_bot_token
printf '\n'

if [[ ! "$slack_signing_secret" =~ ^[A-Fa-f0-9]{32}$ ]] ||
   [[ ! "$slack_bot_token" =~ ^xoxb-[A-Za-z0-9_-]+$ ]]; then
  echo 'Invalid Slack token format. Nothing was saved.' >&2
  exit 1
fi

runtime_dir=/home/tom/.openclaw
install -d -o tom -g tom -m 700 "$runtime_dir"
runtime_tmp=$(mktemp "$runtime_dir/runtime.env.XXXXXXXX")
cleanup() {
  unset slack_signing_secret slack_bot_token gateway_token
  if [ -e "$runtime_tmp" ]; then rm "$runtime_tmp"; fi
}
trap cleanup EXIT

gateway_token=$(openssl rand -hex 32)
printf 'SLACK_SIGNING_SECRET=%s\nSLACK_BOT_TOKEN=%s\nOPENCLAW_GATEWAY_TOKEN=%s\n' \
  "$slack_signing_secret" "$slack_bot_token" "$gateway_token" > "$runtime_tmp"
chown tom:tom "$runtime_tmp"
chmod 600 "$runtime_tmp"
mv "$runtime_tmp" "$runtime_dir/runtime.env"
echo 'Slack credentials saved on this instance only. Tom remains stopped.'
