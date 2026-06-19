#!/bin/bash
set -e

echo "=== AutoClip Setup ==="

# System deps
sudo apt update && sudo apt install -y ffmpeg python3-pip python3-venv git unzip wget netfilter-persistent

# Project directory
mkdir -p /home/ubuntu/autoclip/{tokens,assets/fonts}
mkdir -p /home/ubuntu/autoclip/tmp/{source,captions,clips,output}
cd /home/ubuntu/autoclip

# Python virtual env
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Download Whisper medium model (~1.5 GB, one-time)
echo "Downloading Whisper medium model..."
python3 -c "import whisper; whisper.load_model('medium')"

# Download Montserrat font
echo "Downloading Montserrat font..."
wget -q -O /tmp/Montserrat.zip "https://fonts.google.com/download?family=Montserrat"
unzip -o /tmp/Montserrat.zip -d /home/ubuntu/autoclip/assets/fonts/

# Open port 8000 (Oracle VM has both security list AND iptables to configure)
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 8000 -j ACCEPT
sudo netfilter-persistent save

# Systemd service
sudo tee /etc/systemd/system/autoclip.service > /dev/null <<EOF
[Unit]
Description=AutoClip Pipeline Daemon
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/autoclip
ExecStart=/home/ubuntu/autoclip/venv/bin/python main.py
Restart=always
RestartSec=15
EnvironmentFile=/home/ubuntu/autoclip/.env
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable autoclip
sudo systemctl start autoclip

echo ""
echo "=== Setup complete ==="
echo "Check status:  sudo journalctl -u autoclip -f"
echo "Restart:       sudo systemctl restart autoclip"
echo "Stop:          sudo systemctl stop autoclip"
