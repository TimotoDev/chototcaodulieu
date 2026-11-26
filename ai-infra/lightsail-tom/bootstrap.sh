#!/usr/bin/env bash
if [ -z "${BASH_VERSION:-}" ]; then
  exec /usr/bin/env bash "$0"
fi
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

# The 1 GB Lightsail bundle needs temporary headroom for npm installation.
if ! swapon --show=NAME --noheadings | grep -qx /swapfile; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || printf '/swapfile none swap sw 0 0\n' >> /etc/fstab
fi

apt-get update -qq
apt-get install -y -qq ca-certificates curl gnupg git build-essential python3 >/var/log/tom-bootstrap-packages.log 2>&1
curl -fsSL https://deb.nodesource.com/setup_24.x | bash - >/var/log/tom-bootstrap-node.log 2>&1
apt-get install -y -qq nodejs >>/var/log/tom-bootstrap-node.log 2>&1

node --version >/var/log/tom-bootstrap-versions.log
npm --version >>/var/log/tom-bootstrap-versions.log

id tom >/dev/null 2>&1 || useradd --create-home --shell /bin/bash tom
install -d -m 700 -o tom -g tom /home/tom/.openclaw

npm install --global --no-audit --no-fund openclaw@2026.9.6 antigravity-claude-proxy@2.8.6 >/var/log/tom-bootstrap-npm.log 2>&1
openclaw --version >>/var/log/tom-bootstrap-versions.log
command -v antigravity-claude-proxy >>/var/log/tom-bootstrap-versions.log

touch /var/lib/tom-bootstrap-complete
