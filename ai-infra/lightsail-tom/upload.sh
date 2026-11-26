#!/usr/bin/env bash
set -euo pipefail
umask 077

if [ "$#" -ne 3 ]; then
  echo 'Usage: upload.sh LOCAL_FILE REMOTE_PATH MODE' >&2
  exit 2
fi

local_file=$1
remote_path=$2
file_mode=$3
if [ ! -f "$local_file" ] || [[ ! "$remote_path" =~ ^/[A-Za-z0-9_./-]+$ ]] ||
   [[ ! "$file_mode" =~ ^0[0-7]{3}$ ]]; then
  echo 'Invalid upload arguments.' >&2
  exit 2
fi

ssh_dir=$(mktemp -d)
remote_tmp="/home/ubuntu/.tom-upload-$(openssl rand -hex 12)"
cleanup() {
  if [ -n "${ssh_ip:-}" ] && [ -s "$ssh_dir/key" ]; then
    ssh -i "$ssh_dir/key" -o CertificateFile="$ssh_dir/key-cert.pub" \
      -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
      "ubuntu@$ssh_ip" "rm -f '$remote_tmp'" >/dev/null 2>&1 || true
  fi
  for path in "$ssh_dir/access.json" "$ssh_dir/key" "$ssh_dir/key-cert.pub"; do
    if [ -e "$path" ]; then rm "$path"; fi
  done
  rmdir "$ssh_dir"
}
trap cleanup EXIT

AWS_PROFILE=harry aws lightsail get-instance-access-details \
  --region ap-southeast-1 --instance-name timoto-tom-openclaw \
  --protocol ssh --output json > "$ssh_dir/access.json"
jq -r '.accessDetails.privateKey' "$ssh_dir/access.json" > "$ssh_dir/key"
jq -r '.accessDetails.certKey' "$ssh_dir/access.json" > "$ssh_dir/key-cert.pub"
ssh_ip=$(jq -r '.accessDetails.ipAddress' "$ssh_dir/access.json")

scp -q -i "$ssh_dir/key" -o CertificateFile="$ssh_dir/key-cert.pub" \
  -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
  "$local_file" "ubuntu@$ssh_ip:$remote_tmp"
ssh -i "$ssh_dir/key" -o CertificateFile="$ssh_dir/key-cert.pub" \
  -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
  "ubuntu@$ssh_ip" "sudo install -m '$file_mode' '$remote_tmp' '$remote_path'"
