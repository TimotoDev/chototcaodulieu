#!/usr/bin/env bash
set -euo pipefail
umask 077

ssh_dir=$(mktemp -d)
cleanup() {
  for path in "$ssh_dir/access.json" "$ssh_dir/key" "$ssh_dir/key-cert.pub"; do
    if [ -e "$path" ]; then rm "$path"; fi
  done
  rmdir "$ssh_dir"
}
trap cleanup EXIT

ssh_tty=false
if [ "${1:-}" = '--tty' ]; then
  ssh_tty=true
  shift
fi
if [ "$#" -eq 0 ]; then
  echo 'Usage: ssh.sh [--tty] REMOTE_COMMAND' >&2
  exit 2
fi

AWS_PROFILE=harry aws lightsail get-instance-access-details \
  --region ap-southeast-1 --instance-name timoto-tom-openclaw \
  --protocol ssh --output json > "$ssh_dir/access.json"
jq -r '.accessDetails.privateKey' "$ssh_dir/access.json" > "$ssh_dir/key"
jq -r '.accessDetails.certKey' "$ssh_dir/access.json" > "$ssh_dir/key-cert.pub"
ssh_ip=$(jq -r '.accessDetails.ipAddress' "$ssh_dir/access.json")

if [ "$ssh_tty" = true ]; then
  ssh -i "$ssh_dir/key" -o CertificateFile="$ssh_dir/key-cert.pub" \
    -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
    -o ConnectTimeout=12 -tt "ubuntu@$ssh_ip" "$@"
else
  ssh -i "$ssh_dir/key" -o CertificateFile="$ssh_dir/key-cert.pub" \
    -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
    -o ConnectTimeout=12 "ubuntu@$ssh_ip" "$@"
fi
