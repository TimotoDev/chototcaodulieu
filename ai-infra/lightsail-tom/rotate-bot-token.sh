#!/usr/bin/env bash
set -euo pipefail
umask 077

if [ "$(id -u)" -ne 0 ]; then
  echo 'Run with sudo on the Tom Lightsail instance.' >&2
  exit 1
fi

runtime_file=/home/tom/.openclaw/runtime.env
if [ ! -f "$runtime_file" ] || [ "$(stat -c '%U:%a' "$runtime_file")" != 'tom:600' ]; then
  echo 'Runtime credential file is missing or has unexpected ownership/permissions.' >&2
  exit 1
fi

old_signing_secret=
gateway_token=
while IFS='=' read -r key value; do
  case "$key" in
    SLACK_SIGNING_SECRET) old_signing_secret=$value ;;
    OPENCLAW_GATEWAY_TOKEN) gateway_token=$value ;;
  esac
done < "$runtime_file"
if [ -z "$old_signing_secret" ] || [ -z "$gateway_token" ]; then
  echo 'Required existing runtime credentials are missing. Nothing was saved.' >&2
  exit 1
fi

read -r -s -p 'New Slack bot token (xoxb-...): ' new_bot_token
printf '\n'
if [[ ! "$new_bot_token" =~ ^xoxb-[A-Za-z0-9_-]+$ ]]; then
  echo 'Invalid Slack bot token format. Nothing was saved.' >&2
  exit 1
fi

tmp_env=$(mktemp /home/tom/.openclaw/runtime.env.XXXXXXXX)
tmp_curl=$(mktemp /run/tom-slack-auth.XXXXXXXX)
cleanup() {
  unset old_signing_secret gateway_token new_bot_token
  rm -f "$tmp_env" "$tmp_curl"
}
trap cleanup EXIT
chmod 600 "$tmp_curl"
printf 'header = "Authorization: Bearer %s"\n' "$new_bot_token" > "$tmp_curl"
if ! curl -fsS --config "$tmp_curl" https://slack.com/api/auth.test | jq -e '.ok == true' >/dev/null; then
  echo 'Slack rejected the new token. Nothing was saved.' >&2
  exit 1
fi

printf 'SLACK_SIGNING_SECRET=%s\nSLACK_BOT_TOKEN=%s\nOPENCLAW_GATEWAY_TOKEN=%s\n' \
  "$old_signing_secret" "$new_bot_token" "$gateway_token" > "$tmp_env"
chown tom:tom "$tmp_env"
chmod 600 "$tmp_env"
mv "$tmp_env" "$runtime_file"
echo 'New Slack bot token verified and stored on the instance only. Services remain stopped.'
