import time
import webbrowser
import os
import webview
import functools
import logging

from .config_store import ConfigStore
from .ssh_client import SSHClientWrapper
from .agent_manager import AgentManager
from .system_manager import DirectSSHMetricsCollector
from .docker_manager import DockerManager
from .file_manager import RemoteFileManager
from .task_runner import TaskRunner
from .power_manager import PowerManager
from .tailscale_manager import TailscaleManager
from .hardware_manager import HardwareControlManager
from .av_manager import AVIntercomManager
from .webserver_manager import WebServerManager

logger = logging.getLogger("ServDock.ApiBridge")

def safe_api_bridge(cls):
    """Class decorator to ensure no unhandled exceptions bubble up to pywebview."""
    for attr_name, attr_val in list(cls.__dict__.items()):
        if callable(attr_val) and not attr_name.startswith("_") and attr_name != "set_window":
            def make_wrapper(fn, name):
                @functools.wraps(fn)
                def wrapper(self, *args, **kwargs):
                    try:
                        return fn(self, *args, **kwargs)
                    except Exception as e:
                        logger.exception(f"Unhandled error in API bridge method '{name}': {e}")
                        if name == "get_servers":
                            return []
                        if name in ("is_connected", "is_agent_installed"):
                            return False
                        if name == "get_dashboard_data":
                            return {"connected": False, "error": f"خطا در دریافت وضعیت: {str(e)}"}
                        return {"success": False, "error": f"خطای سرور: {str(e)}"}
                return wrapper
            setattr(cls, attr_name, make_wrapper(attr_val, attr_name))
    return cls

@safe_api_bridge
class ApiBridge:
    def __init__(self):
        self.config_store = ConfigStore()
        self.ssh = SSHClientWrapper()
        self.agent_mgr = AgentManager(self.ssh)
        self.system_collector = DirectSSHMetricsCollector(self.ssh)
        self.docker_mgr = DockerManager(self.ssh)
        self.file_mgr = RemoteFileManager(self.ssh)
        self.task_runner = TaskRunner(self.ssh)
        self.power_mgr = PowerManager(self.ssh)
        self.ts_mgr = TailscaleManager(self.ssh)
        self.hardware_mgr = HardwareControlManager(self.ssh)
        self.av_mgr = AVIntercomManager(self.ssh)
        self.web_mgr = WebServerManager(self.ssh)
        self.active_server_id = None
        self.window = None

    def set_window(self, window):
        self.window = window

    # ----------------- SERVERS -----------------
    def get_servers(self):
        return self.config_store.get_all_servers()

    def get_server_details(self, server_id):
        return self.config_store.get_server_by_id(server_id)

    def save_server(self, server_data):
        return self.config_store.save_server(server_data)

    def delete_server(self, server_id):
        if self.active_server_id == server_id:
            self.disconnect_server()
        return self.config_store.delete_server(server_id)

    def connect_server(self, server_id, password_override=None):
        server = self.config_store.get_server_by_id(server_id)
        if not server:
            return {"success": False, "message": "سرور مورد نظر یافت نشد"}

        host = server.get("host")
        port = int(server.get("port", 22))
        username = server.get("username", "root")
        password = password_override or server.get("password")
        key_path = server.get("key_path")

        start_time = time.time()
        res = self.ssh.connect(host=host, port=port, username=username, password=password, key_path=key_path)
        latency = round((time.time() - start_time) * 1000)

        if res["success"]:
            self.active_server_id = server_id
            agent_status = self.agent_mgr.check_status()
            res["agent"] = agent_status
            res["server_name"] = server.get("name", host)
            res["host"] = host
            res["latency_ms"] = latency
            # Auto-detect MAC if not saved
            if not server.get("mac_address"):
                mac = self.power_mgr.detect_mac_address()
                if mac:
                    server["mac_address"] = mac
                    self.config_store.save_server(server)
                    res["detected_mac"] = mac
        return res

    def test_connection_params(self, server_data):
        test_client = SSHClientWrapper()
        host = server_data.get("host")
        port = int(server_data.get("port", 22))
        username = server_data.get("username", "root")
        password = server_data.get("password")
        key_path = server_data.get("key_path")

        start_time = time.time()
        res = test_client.connect(host=host, port=port, username=username, password=password, key_path=key_path, timeout=6)
        latency = round((time.time() - start_time) * 1000)
        test_client.disconnect()
        if res["success"]:
            res["latency_ms"] = latency
        return res

    def disconnect_server(self):
        self.ssh.disconnect()
        self.active_server_id = None
        self.agent_mgr.is_agent_active = False
        return {"success": True, "message": "اتصال قطع شد"}

    def get_connection_info(self):
        if not self.ssh.is_connected:
            return {
                "connected": False,
                "server": None,
                "agent": {"active": False, "version": None}
            }
        srv = self.config_store.get_server_by_id(self.active_server_id) if self.active_server_id else None
        return {
            "connected": True,
            "server": srv,
            "agent": {
                "active": self.agent_mgr.is_agent_active,
                "version": self.agent_mgr.agent_version
            }
        }

    # ----------------- DASHBOARD -----------------
    def get_dashboard_data(self):
        if not self.ssh.is_connected:
            return {"error": "اتصال SSH برقرار نیست", "connected": False}

        start_time = time.time()
        agent_data = None
        if self.agent_mgr.is_agent_active:
            data = self.agent_mgr.fetch_agent_dashboard()
            if data and isinstance(data, dict) and "error" not in data and "system" in data:
                agent_data = data
        else:
            agent_status = self.agent_mgr.check_status()
            if agent_status.get("active"):
                data = self.agent_mgr.fetch_agent_dashboard()
                if data and isinstance(data, dict) and "error" not in data and "system" in data:
                    agent_data = data

        if agent_data:
            agent_data["source"] = "agent"
            agent_data["connected"] = True
            agent_data["roundtrip_ms"] = round((time.time() - start_time) * 1000)
            return agent_data

        sys_metrics = self.system_collector.get_system_metrics() or {}
        docker_metrics = self.system_collector.get_docker_metrics() or {}

        return {
            "connected": True,
            "source": "direct_ssh",
            "roundtrip_ms": round((time.time() - start_time) * 1000),
            "system": {
                "cpu": sys_metrics.get("cpu", {"cores": 1, "usage_percent": 0, "model": "N/A"}),
                "memory": sys_metrics.get("memory", {"percent": 0, "used_bytes": 0, "total_bytes": 0, "free_bytes": 0}),
                "disks": sys_metrics.get("disks", []),
                "host": sys_metrics.get("host", {"hostname": "-", "os_name": "Linux", "kernel": "-", "uptime_seconds": 0, "load_avg": [0,0,0]})
            },
            "docker": docker_metrics,
            "agent_version": None,
            "timestamp": int(time.time())
        }

    def install_agent(self, sudo_password=None):
        if not self.ssh.is_connected:
            return {"success": False, "message": "ابتدا باید به سرور متصل شوید"}
        return self.agent_mgr.install_agent(sudo_password=sudo_password)

    # ----------------- DOCKER -----------------
    def docker_action(self, container_id, action):
        if not self.ssh.is_connected:
            return {"success": False, "error": "عدم اتصال به سرور"}
        return self.docker_mgr.container_action(container_id, action)

    def get_container_logs(self, container_id, tail=150):
        if not self.ssh.is_connected:
            return {"success": False, "logs": "عدم اتصال به سرور"}
        return self.docker_mgr.get_logs(container_id, tail=tail)

    def docker_list_images(self):
        if not self.ssh.is_connected:
            return {"success": False, "images": []}
        return self.docker_mgr.list_images()

    def docker_pull_image(self, image_name):
        if not self.ssh.is_connected:
            return {"success": False, "error": "عدم اتصال به سرور"}
        return self.docker_mgr.pull_image(image_name)

    def docker_queue_pull(self, image_name):
        if not self.ssh.is_connected:
            return {"success": False, "error": "عدم اتصال به سرور"}
        return self.docker_mgr.queue_pull_image(image_name)

    def docker_get_download_queue(self):
        if not self.ssh.is_connected:
            return {"success": False, "error": "عدم اتصال به سرور", "tasks": []}
        return self.docker_mgr.get_download_queue_status()

    def docker_cancel_download(self, task_id):
        if not self.ssh.is_connected:
            return {"success": False, "error": "عدم اتصال به سرور"}
        return self.docker_mgr.cancel_download(task_id)

    def docker_retry_download(self, task_id):
        if not self.ssh.is_connected:
            return {"success": False, "error": "عدم اتصال به سرور"}
        return self.docker_mgr.retry_download(task_id)

    def docker_remove_download(self, task_id):
        if not self.ssh.is_connected:
            return {"success": False, "error": "عدم اتصال به سرور"}
        return self.docker_mgr.remove_download(task_id)

    def docker_clear_completed_downloads(self):
        if not self.ssh.is_connected:
            return {"success": False, "error": "عدم اتصال به سرور"}
        return self.docker_mgr.clear_completed_downloads()

    def docker_run_container(self, image_name, container_name="", port_mapping="", env_vars="", restart_policy="unless-stopped", replace_existing=False):
        if not self.ssh.is_connected:
            return {"success": False, "error": "عدم اتصال به سرور"}
        return self.docker_mgr.run_container(image_name, container_name, port_mapping, env_vars, restart_policy, replace_existing)

    def docker_get_container_details(self, container_id):
        if not self.ssh.is_connected:
            return {"success": False, "error": "عدم اتصال به سرور"}
        return self.docker_mgr.get_container_details(container_id)

    def docker_edit_container(self, container_id, new_name, port_mapping="", env_vars="", restart_policy="unless-stopped"):
        if not self.ssh.is_connected:
            return {"success": False, "error": "عدم اتصال به سرور"}
        return self.docker_mgr.edit_container(container_id, new_name, port_mapping, env_vars, restart_policy)

    def docker_remove_image(self, image_id_or_name, force=False):
        if not self.ssh.is_connected:
            return {"success": False, "error": "عدم اتصال به سرور"}
        return self.docker_mgr.remove_image(image_id_or_name, force)

    def docker_prune(self):
        if not self.ssh.is_connected:
            return {"success": False, "error": "عدم اتصال به سرور"}
        return self.docker_mgr.prune_system()

    def docker_load_file(self, file_path, tag=""):
        if not self.ssh.is_connected:
            return {"success": False, "error": "عدم اتصال به سرور"}
        return self.docker_mgr.load_image_from_file(file_path, tag)

    def docker_get_startup_status(self):
        if not self.ssh.is_connected:
            return {"success": False, "error": "عدم اتصال به سرور"}
        return self.docker_mgr.get_startup_status()

    def docker_set_container_restart_policy(self, container_id, policy):
        if not self.ssh.is_connected:
            return {"success": False, "error": "عدم اتصال به سرور"}
        return self.docker_mgr.set_container_restart_policy(container_id, policy)

    def docker_set_service_startup(self, enable):
        if not self.ssh.is_connected:
            return {"success": False, "error": "عدم اتصال به سرور"}
        return self.docker_mgr.set_docker_service_startup(enable)

    def docker_set_batch_containers_startup(self, policy):
        if not self.ssh.is_connected:
            return {"success": False, "error": "عدم اتصال به سرور"}
        return self.docker_mgr.set_batch_containers_startup(policy)

    def docker_search_hub(self, term, limit=25):
        if not self.ssh.is_connected:
            return {"success": False, "error": "عدم اتصال به سرور"}
        return self.docker_mgr.search_hub(term, limit=limit)

    def docker_get_registry_mirrors(self):
        if not self.ssh.is_connected:
            return {"success": False, "error": "عدم اتصال به سرور"}
        return self.docker_mgr.get_registry_mirrors()

    def docker_set_registry_mirrors(self, mirrors):
        if not self.ssh.is_connected:
            return {"success": False, "error": "عدم اتصال به سرور"}
        return self.docker_mgr.set_registry_mirrors(mirrors)

    def docker_test_registry_mirror(self, mirror_url):
        if not self.ssh.is_connected:
            return {"success": False, "error": "عدم اتصال به سرور"}
        return self.docker_mgr.test_registry_mirror(mirror_url)

    # ----------------- TERMINAL -----------------
    def execute_raw_command(self, cmd):
        if not self.ssh.is_connected:
            return {"success": False, "stdout": "", "stderr": "SSH متصل نیست"}
        return self.ssh.execute_command(cmd, timeout=30)

    def open_external_url(self, url):
        try:
            if not url.startswith("http://") and not url.startswith("https://"):
                url = "http://" + url
            webbrowser.open(url)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ----------------- FILE MANAGER & TRANSFERS -----------------
    def file_list(self, remote_path=None):
        return self.file_mgr.list_directory(remote_path)

    def file_read(self, remote_path):
        return self.file_mgr.read_file(remote_path)

    def file_save(self, remote_path, content):
        return self.file_mgr.save_file(remote_path, content)

    def file_create(self, remote_path, item_type="file"):
        return self.file_mgr.create_item(remote_path, item_type)

    def file_delete(self, remote_path, is_dir=False):
        return self.file_mgr.delete_item(remote_path, is_dir)

    def file_upload_interactive(self, remote_dir):
        local_file = self._pick_local_file()
        if not local_file:
            return {"cancelled": True}
        return self.file_mgr.upload_file(local_file, remote_dir)

    def file_download_interactive(self, remote_path):
        filename = os.path.basename(remote_path)
        local_path = self._pick_save_path(filename)
        if not local_path:
            return {"cancelled": True}
        return self.file_mgr.download_file(remote_path, local_path)

    def file_download_from_url(self, file_url, target_dir=None, filename=None):
        return self.file_mgr.download_from_url(file_url, target_dir, filename)

    def _pick_local_file(self):
        try:
            if self.window:
                res = self.window.create_file_dialog(webview.OPEN_DIALOG)
                if res and len(res) > 0:
                    return res[0]
        except Exception:
            pass
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            path = filedialog.askopenfilename()
            root.destroy()
            return path if path else None
        except Exception:
            return None

    def _pick_save_path(self, default_name):
        try:
            if self.window:
                res = self.window.create_file_dialog(webview.SAVE_DIALOG, save_filename=default_name)
                if res:
                    return res if isinstance(res, str) else res[0]
        except Exception:
            pass
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            path = filedialog.asksaveasfilename(initialfile=default_name)
            root.destroy()
            return path if path else None
        except Exception:
            return None

    # ----------------- PYTHON SCRIPT & TASK RUNNER -----------------
    def run_python_code(self, code_str, args="", in_background=False, task_name="اسکریپت پایتون"):
        if in_background:
            return self.task_runner.run_background(code_str, task_name=task_name)
        else:
            return self.task_runner.run_foreground(code_str, args=args)

    def get_background_tasks(self):
        return self.task_runner.list_tasks()

    def get_task_output(self, task_id):
        return self.task_runner.get_task_output(task_id)

    def kill_task(self, task_id, pid):
        return self.task_runner.kill_task(task_id, pid)

    # ----------------- POWER MANAGEMENT & WAKE-ON-LAN -----------------
    def power_reboot(self, sudo_password=None):
        return self.power_mgr.reboot(sudo_password)

    def power_shutdown(self, sudo_password=None):
        return self.power_mgr.shutdown(sudo_password)

    def power_suspend(self, sudo_password=None):
        return self.power_mgr.suspend(sudo_password)

    def power_wake_on_lan(self, mac_address, broadcast_ip=None):
        bcast = broadcast_ip if broadcast_ip else "255.255.255.255"
        return PowerManager.send_magic_packet(mac_address, broadcast_ip=bcast)

    def get_server_mac_address(self, server_id=None):
        srv_id = server_id or self.active_server_id
        if srv_id:
            srv = self.config_store.get_server_by_id(srv_id)
            if srv and srv.get("mac_address"):
                return srv.get("mac_address")
        return self.power_mgr.detect_mac_address()

    # ----------------- REMOTE DESKTOP (RDP & VNC) -----------------
    def rdp_check_status(self):
        if not self.ssh.is_connected:
            return {"connected": False, "installed": False, "running": False}

        # Check xrdp package and listening port 3389
        cmd = "which xrdp >/dev/null 2>&1 && ss -tlpn 2>/dev/null | grep -q 3389 && echo 'RUNNING' || (which xrdp >/dev/null 2>&1 && echo 'INSTALLED' || echo 'NONE')"
        res = self.ssh.execute_command(cmd, timeout=5)
        out = res.get("stdout", "").strip()

        # Check if a user is currently logged into physical local screen (:0 / tty7)
        check_local_cmd = "who 2>/dev/null | grep -E '(:0|tty7|seat0)' || true"
        res_local = self.ssh.execute_command(check_local_cmd, timeout=4)
        local_user_active = bool(res_local.get("stdout", "").strip())

        srv = self.config_store.get_server_by_id(self.active_server_id) if self.active_server_id else {}
        host = srv.get("host", "")
        username = srv.get("username", "")

        return {
            "connected": True,
            "installed": out in ["RUNNING", "INSTALLED"],
            "running": out == "RUNNING",
            "host": host,
            "port": 3389,
            "username": username,
            "local_user_active": local_user_active,
            "local_session_info": res_local.get("stdout", "").strip()
        }

    def rdp_setup_service(self, sudo_password=None):
        """Installs, repairs and configures xrdp with Cinnamon/XFCE desktop session without black screen or polkit bugs."""
        if not self.ssh.is_connected:
            return {"success": False, "error": "SSH متصل نیست"}

        setup_cmd = """
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends xrdp xorgxrdp

# Allow xrdp user to read ssl certs
adduser xrdp ssl-cert 2>/dev/null || true

# Allow any user to start X server for xrdp
if [ -f /etc/X11/Xwrapper.config ]; then
    sed -i 's/allowed_users=.*/allowed_users=anybody/' /etc/X11/Xwrapper.config
    grep -q "needs_root_rights" /etc/X11/Xwrapper.config || echo "needs_root_rights=no" >> /etc/X11/Xwrapper.config
else
    echo "allowed_users=anybody" > /etc/X11/Xwrapper.config
    echo "needs_root_rights=no" >> /etc/X11/Xwrapper.config
fi

# Fix Polkit color manager popup freeze & black screen
mkdir -p /etc/polkit-1/localauthority/50-local.d/
cat << 'EOF' > /etc/polkit-1/localauthority/50-local.d/45-allow-colord.pkla
[Allow Colord all Users]
Identity=unix-user:*
Action=org.freedesktop.color-manager.create-device;org.freedesktop.color-manager.create-profile;org.freedesktop.color-manager.delete-device;org.freedesktop.color-manager.delete-profile;org.freedesktop.color-manager.modify-device;org.freedesktop.color-manager.modify-profile
ResultAny=no
ResultInactive=no
ResultActive=yes
EOF

# CRITICAL FIX: Overwrite /etc/xrdp/startwm.sh to unset DBUS & run Xsession
cat << 'EOF' > /etc/xrdp/startwm.sh
#!/bin/sh
# xrdp session launcher - ServDock Anti-BlackScreen patch

unset DBUS_SESSION_BUS_ADDRESS
unset XDG_RUNTIME_DIR
unset XDG_DATA_DIRS

if [ -r /etc/default/locale ]; then
  . /etc/default/locale
  export LANG LANGUAGE
fi

if [ -r /etc/profile ]; then
  . /etc/profile
fi

if [ -r ~/.profile ]; then
  . ~/.profile
fi

test -x /etc/X11/Xsession && exec /etc/X11/Xsession
exec /bin/sh /etc/X11/Xsession
EOF
chmod +x /etc/xrdp/startwm.sh

# Open firewall port 3389 if UFW is active
which ufw >/dev/null 2>&1 && ufw allow 3389/tcp 2>/dev/null || true

# Clean any stale dead X11 display sockets in /tmp
rm -f /tmp/.X11-unix/X1[0-9] /tmp/.X1[0-9]-lock 2>/dev/null || true

systemctl daemon-reload 2>/dev/null || true
systemctl enable xrdp
systemctl restart xrdp xrdp-sesman 2>/dev/null || systemctl restart xrdp
"""
        res = self.ssh.execute_command(setup_cmd, sudo=True, sudo_password=sudo_password, timeout=120)

        # Configure user desktop session
        user_session_cmd = """
if which xfce4-session >/dev/null 2>&1; then
    echo "xfce4-session" > ~/.xsession
elif which cinnamon-session >/dev/null 2>&1; then
    echo "cinnamon-session" > ~/.xsession
elif which mate-session >/dev/null 2>&1; then
    echo "mate-session" > ~/.xsession
else
    echo "x-session-manager" > ~/.xsession
fi
chmod +x ~/.xsession

cat << 'EOF' > ~/.xsessionrc
export GNOME_SHELL_SESSION_MODE=cinnamon
export XDG_CURRENT_DESKTOP=X-Cinnamon
export XDG_SESSION_DESKTOP=cinnamon
export DESKTOP_SESSION=cinnamon
export CINNAMON_2D=1
unset DBUS_SESSION_BUS_ADDRESS
unset XDG_RUNTIME_DIR
EOF
chmod +x ~/.xsessionrc
"""
        self.ssh.execute_command(user_session_cmd, sudo=False, timeout=10)

        time.sleep(1.5)
        for _ in range(4):
            status = self.rdp_check_status()
            if status.get("running"):
                return {
                    "success": True, 
                    "message": "پچ رفع صفحه سیاه با موفقیت در /etc/xrdp/startwm.sh و سشن اعمال شد!",
                    "local_user_active": status.get("local_user_active", False)
                }
            time.sleep(1)

        if res.get("success"):
            return {
                "success": True, 
                "message": "پچ با موفقیت اعمال شد و سرویس xrdp فعال گردید.",
                "local_user_active": status.get("local_user_active", False)
            }
        return {"success": False, "error": res.get("stderr") or res.get("stdout") or "خطا در پیکربندی سرویس xrdp"}

    def rdp_install_xfce(self, sudo_password=None):
        """Installs XFCE lightweight desktop environment which guarantees zero black screen even with concurrent sessions."""
        if not self.ssh.is_connected:
            return {"success": False, "error": "SSH متصل نیست"}

        srv = self.config_store.get_server_by_id(self.active_server_id) if self.active_server_id else {}
        pwd_to_use = (sudo_password.strip() if sudo_password else "") or srv.get("password") or None

        cmd = """
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends xfce4 xfce4-session

# Set user .xsession to xfce4-session
cat << 'EOF' > ~/.xsession
#!/bin/sh
unset DBUS_SESSION_BUS_ADDRESS
unset XDG_RUNTIME_DIR
exec xfce4-session
EOF
chmod +x ~/.xsession

systemctl restart xrdp xrdp-sesman 2>/dev/null || systemctl restart xrdp
"""
        res = self.ssh.execute_command(cmd, sudo=True, sudo_password=pwd_to_use, timeout=180)
        time.sleep(2)
        chk = self.ssh.execute_command("which xfce4-session", timeout=5)
        if chk.get("success") and chk.get("stdout").strip():
            return {"success": True, "message": "میزکار سبک XFCE با موفقیت نصب و به عنوان سشن ریموت دسکتاپ تنظیم شد!"}
        return {"success": False, "error": res.get("stderr") or res.get("stdout") or "خطا در نصب XFCE"}

    def rdp_restart_service(self, sudo_password=None):
        """Restarts xrdp and sesman service on the remote server with reliable status checking."""
        if not self.ssh.is_connected:
            return {"success": False, "error": "SSH متصل نیست"}

        srv = self.config_store.get_server_by_id(self.active_server_id) if self.active_server_id else {}
        pwd_to_use = (sudo_password.strip() if sudo_password else "") or srv.get("password") or None

        cmd = "systemctl restart xrdp; systemctl restart xrdp-sesman 2>/dev/null || true; systemctl is-active xrdp"
        res = self.ssh.execute_command(cmd, sudo=True, sudo_password=pwd_to_use, timeout=20)

        # Wait a moment for socket to bind
        time.sleep(1.5)
        for _ in range(4):
            status = self.rdp_check_status()
            if status.get("running"):
                return {"success": True, "message": "سرویس xrdp با موفقیت راه‌اندازی مجدد شد و پورت ۳۳۸۹ فعال است."}
            time.sleep(1)

        # Check if systemctl reported active
        if "active" in res.get("stdout", "").lower():
            return {"success": True, "message": "سرویس xrdp با موفقیت راه‌اندازی شد."}

        err = res.get("stderr") or res.get("stdout") or "رمز عبور sudo اشتباه است یا سرویس اجرا نشد"
        return {"success": False, "error": err.strip()}

    def rdp_set_linux_dpi(self, dpi=125):
        """Sets High-DPI scaling on Linux for XFCE, Cinnamon, and X11 desktop apps."""
        if not self.ssh.is_connected:
            return {"success": False, "error": "SSH متصل نیست"}

        try:
            safe_dpi = int(dpi) if str(dpi).isdigit() else 125
        except Exception:
            safe_dpi = 125

        scale_factor = round(safe_dpi / 96.0, 2)
        panel_size = int(24 * scale_factor)

        cmd = f"""
# 1. Global Xresources font scaling
echo "Xft.dpi: {safe_dpi}" > ~/.Xresources
xrdb -merge ~/.Xresources 2>/dev/null || true

# 2. XFCE settings (DPI, panel size, desktop icons)
if which xfconf-query >/dev/null 2>&1; then
    xfconf-query -c xsettings -p /Xft/DPI -s {safe_dpi} --create -t int 2>/dev/null || true
    xfconf-query -c xfce4-panel -p /panels/panel-1/size -s {panel_size} 2>/dev/null || true
    xfconf-query -c xfce4-desktop -p /desktop-icons/icon-size -s 48 2>/dev/null || true
    xfconf-query -c xsettings -p /Gtk/IconSizes -s "gtk-menu=24,24:gtk-button=24,24:gtk-small-toolbar=24,24:gtk-large-toolbar=32,32:gtk-dnd=48,48:gtk-dialog=48,48" 2>/dev/null || true
fi

# 3. Cinnamon text scaling factor
if which gsettings >/dev/null 2>&1; then
    gsettings set org.cinnamon.desktop.interface text-scaling-factor {scale_factor} 2>/dev/null || true
fi
"""
        res = self.ssh.execute_command(cmd, sudo=False, timeout=10)
        return {
            "success": True, 
            "message": f"مقیاس آیکون‌ها و فونت‌های لینوکس روی {safe_dpi} DPI (بزرگ‌نمایی {int(scale_factor*100)}%) تنظیم شد!"
        }

    def rdp_launch_client(self, host=None, username=None, resolution="1920x1080", scale_factor=125, fullscreen=False):
        """Launches native Windows Remote Desktop (mstsc.exe) with custom resolution, scaling, and Smart Sizing."""
        import subprocess
        import tempfile
        srv = self.config_store.get_server_by_id(self.active_server_id) if self.active_server_id else {}
        target_host = host or srv.get("host")
        target_user = username or srv.get("username")

        if not target_host:
            return {"success": False, "error": "آدرس سرور مشخص نشده است"}

        clean_host = str(target_host).strip()
        clean_user = str(target_user).strip() if target_user else "root"

        w, h = 1920, 1080
        if resolution and "x" in resolution:
            try:
                parts = resolution.lower().split("x")
                w, h = int(parts[0]), int(parts[1])
            except Exception:
                w, h = 1920, 1080

        screen_mode = 2 if fullscreen else 1
        scale = int(scale_factor) if str(scale_factor).isdigit() else 125

        try:
            # Create optimized .rdp file for mstsc with Smart Sizing & High-DPI support
            rdp_content = f"""full address:s:{clean_host}:3389
username:s:{clean_user}
prompt for credentials:i:1
screen mode id:i:{screen_mode}
use multimon:i:0
desktopwidth:i:{w}
desktopheight:i:{h}
smart sizing:i:1
desktopscalefactor:i:{scale}
session bpp:i:24
compression:i:1
keyboardhook:i:2
audiomode:i:0
redirectclipboard:i:1
redirectprinters:i:0
redirectcomports:i:0
redirectsmartcards:i:0
displayconnectionbar:i:1
autoreconnection enabled:i:1
authentication level:i:2
negotiate security layer:i:1
enableworkspacereconnect:i:0
gatewayusagemethod:i:0
"""
            temp_dir = tempfile.gettempdir()
            rdp_path = os.path.join(temp_dir, f"ServDock_{clean_host}_{w}x{h}.rdp")
            with open(rdp_path, "w", encoding="utf-8") as f:
                f.write(rdp_content)

            subprocess.Popen(["mstsc", rdp_path])
            return {"success": True, "message": f"پنجره ریموت دسکتاپ با کیفیت Full HD ({w}×{h}) و مقیاس هوشمند باز شد."}
        except Exception:
            try:
                subprocess.Popen(["mstsc", f"/v:{clean_host}:3389", f"/w:{w}", f"/h:{h}"])
                return {"success": True, "message": f"پنجره ریموت دسکتاپ برای {clean_host} باز شد."}
            except Exception as e:
                return {"success": False, "error": str(e)}

    # ----------------- TAILSCALE HUB & SETTINGS -----------------
    def tailscale_get_status(self, scope="both"):
        """Returns Tailscale status for local (Windows) and/or remote (Linux)."""
        data = {
            "local": self.ts_mgr.get_local_tailscale_status(),
            "remote": self.ts_mgr.get_remote_tailscale_status() if self.ssh.is_connected else {"installed": False, "running": False, "error": "SSH متصل نیست"}
        }
        return data

    def tailscale_set_preference(self, target="local", key="", value=None):
        return self.ts_mgr.set_preference(target=target, key=key, value=value)

    def tailscale_set_exit_node(self, target="local", exit_node_ip_or_name=""):
        return self.ts_mgr.set_exit_node(target=target, exit_node_ip_or_name=exit_node_ip_or_name)

    def tailscale_ping_peer(self, peer_ip_or_name, count=3):
        return self.ts_mgr.ping_peer(peer_ip_or_name, count=count)

    def tailscale_up(self, target="local", auth_key="", extra_flags=""):
        return self.ts_mgr.up(target=target, auth_key=auth_key, extra_flags=extra_flags)

    def tailscale_down(self, target="local"):
        return self.ts_mgr.down(target=target)

    def tailscale_install_remote(self, sudo_password=None):
        return self.ts_mgr.install_remote(sudo_password=sudo_password)

    def tailscale_funnel_get_status(self, target="remote"):
        return self.ts_mgr.funnel_get_status(target=target)

    def tailscale_funnel_start(self, target="remote", local_port=80, public_port=443, path="/"):
        return self.ts_mgr.funnel_start(target=target, local_port=local_port, public_port=public_port, path=path)

    def tailscale_funnel_stop(self, target="remote"):
        return self.ts_mgr.funnel_stop(target=target)

    # ----------------- PHYSICAL HARDWARE LOCK & CONTROLS -----------------
    def hardware_get_status(self):
        return self.hardware_mgr.get_hardware_status()

    def hardware_set_monitor(self, power_on):
        return self.hardware_mgr.set_monitor_power(power_on)

    def hardware_set_mouse(self, enabled):
        return self.hardware_mgr.set_mouse_enabled(enabled)

    def hardware_set_keyboard(self, enabled):
        return self.hardware_mgr.set_keyboard_enabled(enabled)

    def hardware_emergency_unlock(self):
        return self.hardware_mgr.emergency_unlock_all()

    def hardware_set_safety_timer(self, minutes=30):
        return self.hardware_mgr.set_safety_timer(minutes)

    def hardware_cancel_safety_timer(self):
        return self.hardware_mgr.cancel_safety_timer()

    def hardware_get_safety_timer_status(self):
        return self.hardware_mgr.get_safety_timer_status()

    # ----------------- AV INTERCOM (VOICE & VIDEO) -----------------
    def av_get_devices(self):
        return self.av_mgr.get_hardware_av_devices()

    def av_send_audio_to_linux(self, audio_base64):
        return self.av_mgr.play_audio_chunk_on_linux(audio_base64)

    def av_record_linux_mic(self, duration_sec=3):
        return self.av_mgr.record_linux_audio_clip(duration_sec)

    def av_get_linux_cam_frame(self, device="/dev/video0"):
        return self.av_mgr.capture_linux_webcam_frame(device)

    def av_send_cam_frame_to_linux(self, image_base64):
        return self.av_mgr.display_frame_on_linux_screen(image_base64)

    def av_stop_cam_on_linux(self):
        return self.av_mgr.stop_linux_screen_display()

    # ----------------- WEB SERVER (APACHE, MYSQL, PHP) -----------------
    def web_get_status(self):
        return self.web_mgr.get_services_status()

    def web_service_action(self, service_name, action):
        return self.web_mgr.service_action(service_name, action)

    def web_test_config(self, service_name="apache2"):
        return self.web_mgr.test_config(service_name)

    def web_list_vhosts(self):
        return self.web_mgr.list_vhosts()

    def web_toggle_vhost(self, site_filename, enable=True):
        return self.web_mgr.toggle_vhost(site_filename, enable)

    def web_create_vhost(self, domain, port=80, document_root="", server_admin="webmaster@localhost"):
        return self.web_mgr.create_vhost(domain, port, document_root, server_admin)

    def web_delete_vhost(self, site_filename):
        return self.web_mgr.delete_vhost(site_filename)

    def web_get_vhost_content(self, site_filename):
        return self.web_mgr.get_vhost_content(site_filename)

    def web_save_vhost_content(self, site_filename, content):
        return self.web_mgr.save_vhost_content(site_filename, content)

    def web_mysql_list_databases(self):
        return self.web_mgr.mysql_list_databases()

    def web_mysql_create_database(self, db_name, collation="utf8mb4_unicode_ci"):
        return self.web_mgr.mysql_create_database(db_name, collation)

    def web_mysql_drop_database(self, db_name):
        return self.web_mgr.mysql_drop_database(db_name)

    def web_mysql_list_users(self):
        return self.web_mgr.mysql_list_users()

    def web_mysql_create_user(self, username, password, host="localhost", grant_db=None):
        return self.web_mgr.mysql_create_user(username, password, host, grant_db)

    def web_mysql_backup_database(self, db_name, target_dir="/var/backups"):
        return self.web_mgr.mysql_backup_database(db_name, target_dir)

    def web_mysql_restore_database(self, db_name, sql_file_path):
        return self.web_mgr.mysql_restore_database(db_name, sql_file_path)

    def web_mysql_execute_query(self, query, database=None):
        return self.web_mgr.mysql_execute_query(query, database)

    def web_get_php_details(self):
        return self.web_mgr.get_php_details()

    def web_install_lamp_stack(self):
        return self.web_mgr.install_lamp_stack()

    def web_get_logs(self, log_type="apache_error", lines=100):
        return self.web_mgr.get_logs(log_type, lines)

