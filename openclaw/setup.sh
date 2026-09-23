#!/bin/bash
set -euo pipefail

# Part 2 setup script for OpenClaw on Ubuntu 22.04 ARM64

echo "==> Updating system packages..."
sudo apt-get update && sudo apt-get upgrade -y
sudo apt-get install -y curl git build-essential ca-certificates gnupg certbot python3-certbot-nginx nginx

echo "==> Installing Docker..."
if ! command -v docker &> /dev/null; then
  sudo install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  sudo chmod a+r /etc/apt/keyrings/docker.gpg
  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
    $(. /etc/os-release && echo "$UBUNTU_CODENAME") stable" | \
    sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
  sudo apt-get update
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  sudo usermod -aG docker ubuntu
fi

echo "==> Installing Node.js 22 LTS..."
if ! command -v node &> /dev/null; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
  sudo apt-get install -y nodejs
fi

echo "==> Setting up OpenClaw..."
APP_DIR="/opt/openclaw"
if [ ! -d "$APP_DIR" ]; then
  sudo git clone https://github.com/openclaw/openclaw "$APP_DIR"
  sudo chown -R ubuntu:ubuntu "$APP_DIR"
fi

cd "$APP_DIR"
npm install

if [ ! -f .env ]; then
  cp .env.example .env
  echo "ANTHROPIC_API_KEY=your_key_here" >> .env
fi

echo "==> Creating systemd service..."
sudo bash -c "cat <<EOF > /etc/systemd/system/openclaw.service
[Unit]
Description=OpenClaw Service
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=$APP_DIR
ExecStart=$(which npm) start
Restart=always
RestartSec=10
Environment=NODE_ENV=production

[Install]
WantedBy=multi-user.target
EOF"

sudo systemctl daemon-reload
sudo systemctl enable openclaw

echo "==> Installation complete!"
echo "Next steps:"
echo "1. Edit /opt/openclaw/.env and set ANTHROPIC_API_KEY."
echo "2. Run certbot for domain SSL (replace domain.com):"
echo "   sudo certbot --nginx -d domain.com"
echo "3. Start OpenClaw service:"
echo "   sudo systemctl start openclaw"
echo "4. Check logs:"
echo "   journalctl -u openclaw -f"
