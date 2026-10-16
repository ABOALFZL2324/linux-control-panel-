import os
import json
import time

AGENT_PORT = 9786

class AgentManager:
    def __init__(self, ssh_client):
        self.ssh = ssh_client
        self.is_agent_active = False
        self.agent_version = None
        self.agent_token = None

    def _get_token(self):
        if self.agent_token:
            return self.agent_token
        if not self.ssh or not self.ssh.is_connected:
            return ""
        # Try reading token directly, then via sudo if password available
        cmd = "cat /opt/servdock/agent.token 2>/dev/null || cat ~/.servdock/agent.token 2>/dev/null || true"
        res = self.ssh.execute_command(cmd, timeout=3)
        tok = res.get("stdout", "").strip() if res.get("success") else ""
        if not tok:
            # Try with sudo
            res_sudo = self.ssh.execute_command("cat /opt/servdock/agent.token 2>/dev/null || true", sudo=True, timeout=3)
            tok = res_sudo.get("stdout", "").strip() if res_sudo.get("success") else ""

        if tok and len(tok) >= 16:
            self.agent_token = tok
            return self.agent_token
        return ""

    def check_status(self):
        if not self.ssh or not self.ssh.is_connected:
            self.is_agent_active = False
            return {"active": False, "version": None}

        cmd = f"curl -s --connect-timeout 2 http://127.0.0.1:{AGENT_PORT}/api/ping"
        res = self.ssh.execute_command(cmd, timeout=4)
        if res.get("success") and "ok" in res.get("stdout", ""):
            try:
                data = json.loads(res["stdout"])
                self.is_agent_active = True
                self.agent_version = data.get("version", "1.1.0-hardened")
                return {"active": True, "version": self.agent_version}
            except Exception:
                self.is_agent_active = True
                return {"active": True, "version": "1.1.0-hardened"}
        
        self.is_agent_active = False
        return {"active": False, "version": None}

    def fetch_agent_dashboard(self):
        tok = self._get_token()
        hdr = f"-H 'X-ServDock-Token: {tok}'" if tok else ""
        cmd = f"curl -s --connect-timeout 3 {hdr} http://127.0.0.1:{AGENT_PORT}/api/dashboard"
        res = self.ssh.execute_command(cmd, timeout=5)
        if res.get("success") and res.get("stdout", "").strip():
            try:
                data = json.loads(res["stdout"].strip())
                if isinstance(data, dict) and "error" not in data:
                    return data
            except Exception:
                pass
        return None

    def install_agent(self, sudo_password=None):
        if not self.ssh.is_connected:
            return {"success": False, "message": "اتصال SSH برقرار نیست"}

        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        agent_script_path = os.path.join(base_dir, "agent", "servdock_agent.py")
        service_file_path = os.path.join(base_dir, "agent", "servdock-agent.service")

        if not os.path.exists(agent_script_path):
            return {"success": False, "message": "فایل اسکریپت Agent در ویندوز یافت نشد"}

        try:
            with open(agent_script_path, "r", encoding="utf-8") as f:
                agent_content = f.read()
            with open(service_file_path, "r", encoding="utf-8") as f:
                service_content = f.read()
        except Exception as e:
            return {"success": False, "message": f"خطا در خواندن فایل‌های محلی: {str(e)}"}

        installer_script = r"""#!/usr/bin/env bash
set -e
mkdir -p /opt/servdock
cp -f /tmp/servdock_agent.py /opt/servdock/servdock_agent.py
chmod 755 /opt/servdock/servdock_agent.py

if [ ! -s /opt/servdock/agent.token ]; then
    python3 -c "import secrets; print(secrets.token_hex(32))" > /opt/servdock/agent.token
fi
chmod 644 /opt/servdock/agent.token

# Sync token to active user home for fallback
USER_HOME=$(eval echo "~$SUDO_USER")
if [ -n "$USER_HOME" ] && [ -d "$USER_HOME" ]; then
    mkdir -p "$USER_HOME/.servdock"
    cp -f /opt/servdock/agent.token "$USER_HOME/.servdock/agent.token" 2>/dev/null || true
    chown -R "$SUDO_USER:" "$USER_HOME/.servdock" 2>/dev/null || true
fi

if [ -f /tmp/servdock-agent.service ]; then
    cp -f /tmp/servdock-agent.service /etc/systemd/system/servdock-agent.service
fi

if command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload
    systemctl enable servdock-agent
    systemctl restart servdock-agent
else
    pkill -f servdock_agent.py || true
    nohup python3 /opt/servdock/servdock_agent.py 9786 >/var/log/servdock.log 2>&1 &
fi
rm -f /tmp/servdock_agent.py /tmp/servdock-agent.service /tmp/servdock_install.sh
"""

        # Upload files via SFTP
        up1 = self.ssh.upload_string_to_file(agent_content, "/tmp/servdock_agent.py")
        up2 = self.ssh.upload_string_to_file(service_content, "/tmp/servdock-agent.service")
        up3 = self.ssh.upload_string_to_file(installer_script, "/tmp/servdock_install.sh")

        if not up1 or not up3:
            return {"success": False, "message": "آپلود فایل‌های اسکریپت از طریق SFTP ناموفق بود"}

        # Execute installer script with sudo
        exec_res = self.ssh.execute_command(
            "bash /tmp/servdock_install.sh",
            sudo=True,
            sudo_password=sudo_password,
            timeout=25
        )

        time.sleep(1.5)

        # Check if running
        status = self.check_status()
        if status["active"]:
            return {
                "success": True,
                "message": f"ایجنت ServDock (نسخه {status['version']}) با موفقیت نصب و فعال شد!"
            }

        # Fallback: if systemd/root failed, try user-level background daemon
        user_fallback_cmd = r"""
mkdir -p ~/.servdock
cp -f /tmp/servdock_agent.py ~/.servdock/servdock_agent.py 2>/dev/null || true
pkill -f servdock_agent.py || true
nohup python3 ~/.servdock/servdock_agent.py 9786 >~/.servdock/agent.log 2>&1 &
sleep 1
"""
        self.ssh.execute_command(user_fallback_cmd, sudo=False, timeout=10)
        time.sleep(1.2)
        status = self.check_status()
        if status["active"]:
            return {
                "success": True,
                "message": f"ایجنت ServDock در حالت کاربر لینوکس (User Mode) با موفقیت فعال شد!"
            }

        err_detail = exec_res.get("stderr") or exec_res.get("stdout") or "ایجنت پاسخی ارسال نکرد"
        return {
            "success": False,
            "message": f"خطا در نصب: {err_detail.strip()}"
        }
