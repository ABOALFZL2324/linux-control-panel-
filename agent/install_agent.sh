#!/usr/bin/env bash
set -e

echo "[ServDock] Starting Agent Installation..."

if ! command -v python3 &> /dev/null; then
    echo "[ServDock] Python 3 not found. Trying to install python3..."
    if command -v apt-get &> /dev/null; then
        sudo apt-get update -y && sudo apt-get install -y python3
    elif command -v yum &> /dev/null; then
        sudo yum install -y python3
    elif command -v dnf &> /dev/null; then
        sudo dnf install -y python3
    elif command -v pacman &> /dev/null; then
        sudo pacman -Sy --noconfirm python
    fi
fi

sudo mkdir -p /opt/servdock
sudo chmod 755 /opt/servdock

if [ -f "/tmp/servdock_agent.py" ]; then
    sudo cp /tmp/servdock_agent.py /opt/servdock/servdock_agent.py
fi
if [ -f "/tmp/servdock-agent.service" ]; then
    sudo cp /tmp/servdock-agent.service /etc/systemd/system/servdock-agent.service
fi

if [ ! -s /opt/servdock/agent.token ]; then
    python3 -c "import secrets; print(secrets.token_hex(32))" | sudo tee /opt/servdock/agent.token >/dev/null
    sudo chmod 600 /opt/servdock/agent.token
fi

sudo chmod +x /opt/servdock/servdock_agent.py

if command -v systemctl &> /dev/null; then
    sudo systemctl daemon-reload
    sudo systemctl enable servdock-agent
    sudo systemctl restart servdock-agent
    echo "[ServDock] Systemd service started successfully."
else
    sudo pkill -f servdock_agent.py || true
    sudo nohup python3 /opt/servdock/servdock_agent.py 9786 > /var/log/servdock.log 2>&1 &
    echo "[ServDock] Background process started."
fi

sleep 1
if curl -s http://127.0.0.1:9786/api/ping | grep -q "ok"; then
    echo "[ServDock] SUCCESS: Agent is running on 127.0.0.1:9786"
    exit 0
else
    echo "[ServDock] WARNING: Agent started, checking status..."
    exit 0
fi
