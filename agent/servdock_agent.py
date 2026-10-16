#!/usr/bin/env python3
"""
ServDock Lightweight Linux Agent
Runs locally on Linux server (127.0.0.1:9786)
Provides real-time system metrics, Docker management, and web server discovery.
Zero external dependencies required (uses standard library Python 3).
"""

import os
import sys
import json
import time
import socket
import subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

AGENT_VERSION = "1.0.0"
DEFAULT_PORT = 9786

def get_cpu_info():
    cores = os.cpu_count() or 1
    model = "Unknown CPU"
    try:
        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                if "model name" in line:
                    model = line.split(":", 1)[1].strip()
                    break
    except Exception:
        pass

    def read_stat():
        with open("/proc/stat", "r") as f:
            fields = [float(column) for column in f.readline().strip().split()[1:]]
        idle = fields[3] + fields[4]
        total = sum(fields)
        return idle, total

    try:
        idle1, total1 = read_stat()
        time.sleep(0.08)
        idle2, total2 = read_stat()
        idle_delta = idle2 - idle1
        total_delta = total2 - total1
        cpu_usage = 100.0 * (1.0 - idle_delta / total_delta) if total_delta > 0 else 0.0
    except Exception:
        cpu_usage = 0.0

    return {
        "cores": cores,
        "model": model,
        "usage_percent": round(max(0.0, min(100.0, cpu_usage)), 1)
    }

def get_memory_info():
    try:
        meminfo = {}
        with open("/proc/meminfo", "r") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    key = parts[0].strip()
                    val = parts[1].strip().split()[0]
                    meminfo[key] = int(val) * 1024

        total = meminfo.get("MemTotal", 0)
        available = meminfo.get("MemAvailable", meminfo.get("MemFree", 0))
        used = total - available
        percent = (used / total * 100) if total > 0 else 0

        swap_total = meminfo.get("SwapTotal", 0)
        swap_free = meminfo.get("SwapFree", 0)
        swap_used = swap_total - swap_free
        swap_percent = (swap_used / swap_total * 100) if swap_total > 0 else 0

        return {
            "total_bytes": total,
            "used_bytes": used,
            "free_bytes": available,
            "percent": round(percent, 1),
            "swap_total": swap_total,
            "swap_used": swap_used,
            "swap_percent": round(swap_percent, 1)
        }
    except Exception as e:
        return {"error": str(e), "percent": 0, "total_bytes": 0, "used_bytes": 0}

def get_disk_info():
    disks = []
    try:
        st = os.statvfs("/")
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        used = total - free
        percent = (used / total * 100) if total > 0 else 0
        disks.append({
            "mount": "/",
            "total_bytes": total,
            "used_bytes": used,
            "free_bytes": free,
            "percent": round(percent, 1)
        })
    except Exception:
        pass
    return disks

def get_host_info():
    hostname = socket.gethostname()
    os_name = "Linux"
    try:
        if os.path.exists("/etc/os-release"):
            with open("/etc/os-release", "r") as f:
                for line in f:
                    if line.startswith("PRETTY_NAME="):
                        os_name = line.split("=", 1)[1].strip().strip('"')
                        break
    except Exception:
        pass

    kernel = "Unknown"
    try:
        kernel = os.uname().release
    except Exception:
        pass

    uptime_seconds = 0
    try:
        with open("/proc/uptime", "r") as f:
            uptime_seconds = float(f.readline().split()[0])
    except Exception:
        pass

    load_avg = [0.0, 0.0, 0.0]
    try:
        load_avg = list(os.getloadavg())
    except Exception:
        pass

    return {
        "hostname": hostname,
        "os_name": os_name,
        "kernel": kernel,
        "uptime_seconds": int(uptime_seconds),
        "load_avg": [round(x, 2) for x in load_avg]
    }

def get_docker_info():
    res = {
        "installed": False,
        "running": False,
        "containers_count": 0,
        "running_count": 0,
        "stopped_count": 0,
        "images_count": 0,
        "containers": []
    }

    try:
        p_info = subprocess.run(["docker", "info", "--format", "{{json .}}"], capture_output=True, text=True, timeout=4)
        if p_info.returncode == 0:
            res["installed"] = True
            res["running"] = True
            try:
                info_json = json.loads(p_info.stdout)
                res["containers_count"] = info_json.get("Containers", 0)
                res["running_count"] = info_json.get("ContainersRunning", 0)
                res["stopped_count"] = info_json.get("ContainersStopped", 0)
                res["images_count"] = info_json.get("Images", 0)
                res["server_version"] = info_json.get("ServerVersion", "")
            except Exception:
                pass
        else:
            which = subprocess.run(["which", "docker"], capture_output=True, text=True)
            if which.returncode == 0:
                res["installed"] = True
                res["running"] = False
                res["error"] = "Docker daemon is not running or socket permission denied"
            return res
    except FileNotFoundError:
        return res
    except Exception as e:
        res["error"] = str(e)
        return res

    try:
        p_ps = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{json .}}"],
            capture_output=True, text=True, timeout=5
        )
        if p_ps.returncode == 0:
            containers = []
            for line in p_ps.stdout.strip().split("\n"):
                if line.strip():
                    try:
                        c_data = json.loads(line)
                        containers.append({
                            "id": c_data.get("ID", ""),
                            "names": c_data.get("Names", ""),
                            "image": c_data.get("Image", ""),
                            "status": c_data.get("Status", ""),
                            "state": c_data.get("State", ""),
                            "ports": c_data.get("Ports", ""),
                            "created": c_data.get("CreatedAt", "")
                        })
                    except Exception:
                        pass
            res["containers"] = containers
    except Exception as e:
        res["container_fetch_error"] = str(e)

    return res

def get_webserver_info():
    servers = {}
    for srv in ["nginx", "apache2", "httpd", "caddy"]:
        try:
            p = subprocess.run(["systemctl", "is-active", srv], capture_output=True, text=True)
            status = p.stdout.strip()
            if status in ["active", "inactive", "failed"]:
                servers[srv] = {
                    "installed": True,
                    "status": status,
                    "active": (status == "active")
                }
        except Exception:
            pass
    return servers

import secrets
import re

AGENT_VERSION = "1.1.0-hardened"
DEFAULT_PORT = 9786
CONTAINER_REGEX = re.compile(r"^[a-zA-Z0-9_\.\-]+$")

def get_or_create_agent_token():
    """Retrieves or creates a cryptographically secure token for agent authentication."""
    paths = [
        "/opt/servdock/agent.token",
        os.path.expanduser("~/.servdock/agent.token")
    ]
    for p in paths:
        try:
            if os.path.isfile(p):
                with open(p, "r", encoding="utf-8") as f:
                    tok = f.read().strip()
                    if tok and len(tok) >= 16:
                        return tok
        except Exception:
            pass

    # Generate new cryptographically secure token
    new_token = secrets.token_hex(32)
    for p in paths:
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(new_token)
            os.chmod(p, 0o644)
            return new_token
        except Exception:
            pass
    return new_token

AUTH_TOKEN = get_or_create_agent_token()

class ServDockHandler(BaseHTTPRequestHandler):
    def _send_json(self, data, status_code=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))

        # Strict security headers & restricted loopback origin
        origin = self.headers.get("Origin", "")
        if origin and ("127.0.0.1" in origin or "localhost" in origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-ServDock-Token, Authorization")

        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def _is_authenticated(self):
        """Timing-safe authentication verification."""
        if not AUTH_TOKEN:
            return True
        token_hdr = self.headers.get("X-ServDock-Token", "")
        auth_hdr = self.headers.get("Authorization", "")
        bearer_token = auth_hdr.split("Bearer ", 1)[1].strip() if "Bearer " in auth_hdr else ""
        provided = token_hdr or bearer_token
        return secrets.compare_digest(provided, AUTH_TOKEN)

    def do_OPTIONS(self):
        self._send_json({"status": "ok"})

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        # Health ping does not reveal metrics
        if path == "/api/ping":
            self._send_json({
                "status": "ok",
                "version": AGENT_VERSION,
                "name": "ServDock Linux Agent (Hardened)",
                "auth_required": True
            })
            return

        # Authenticate all other endpoints
        if not self._is_authenticated():
            self._send_json({"error": "Unauthorized: invalid or missing X-ServDock-Token"}, 401)
            return

        if path == "/api/dashboard" or path == "/api/metrics":
            data = {
                "system": {
                    "cpu": get_cpu_info(),
                    "memory": get_memory_info(),
                    "disks": get_disk_info(),
                    "host": get_host_info()
                },
                "docker": get_docker_info(),
                "webservers": get_webserver_info(),
                "agent_version": AGENT_VERSION,
                "timestamp": int(time.time())
            }
            self._send_json(data)
        elif path == "/api/docker/logs":
            container_id = query.get("container", [""])[0].strip()
            if not container_id or not CONTAINER_REGEX.match(container_id):
                self._send_json({"error": "Invalid or missing container identifier"}, 400)
                return

            try:
                tail = int(query.get("tail", ["150"])[0])
                tail = max(1, min(5000, tail))
            except (ValueError, TypeError):
                tail = 150

            try:
                p = subprocess.run(["docker", "logs", "--tail", str(tail), container_id],
                                   capture_output=True, text=True, timeout=5)
                self._send_json({
                    "stdout": p.stdout,
                    "stderr": p.stderr,
                    "code": p.returncode
                })
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if not self._is_authenticated():
            self._send_json({"error": "Unauthorized: invalid or missing X-ServDock-Token"}, 401)
            return

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > 1024 * 1024:  # 1MB max body limit to avoid memory exhaustion
            self._send_json({"error": "Payload too large"}, 413)
            return

        post_data = self.rfile.read(content_length)
        payload = {}
        if post_data:
            try:
                payload = json.loads(post_data.decode("utf-8"))
            except Exception:
                pass

        if path == "/api/docker/action":
            container = str(payload.get("container", "")).strip()
            action = str(payload.get("action", "")).strip()
            if not container or not CONTAINER_REGEX.match(container):
                self._send_json({"error": "Invalid or missing container identifier"}, 400)
                return

            if action not in ["start", "stop", "restart", "pause", "unpause"]:
                self._send_json({"error": f"Invalid action: {action}"}, 400)
                return

            try:
                p = subprocess.run(["docker", action, container], capture_output=True, text=True, timeout=20)
                self._send_json({
                    "success": p.returncode == 0,
                    "stdout": p.stdout.strip(),
                    "stderr": p.stderr.strip()
                })
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
        else:
            self._send_json({"error": "Not found"}, 404)

    def log_message(self, format, *args):
        return

def run_server(port=DEFAULT_PORT):
    server_address = ("127.0.0.1", port)
    httpd = HTTPServer(server_address, ServDockHandler)
    print(f"ServDock Agent v{AGENT_VERSION} (Security Hardened) listening on http://127.0.0.1:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    httpd.server_close()

if __name__ == "__main__":
    port = DEFAULT_PORT
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass
    run_server(port)

