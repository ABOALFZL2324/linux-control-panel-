import json
import re

class DirectSSHMetricsCollector:
    """Collects system and docker metrics directly via SSH commands when agent is not present."""
    def __init__(self, ssh_client):
        self.ssh = ssh_client

    def get_system_metrics(self):
        script = r"""
python3 -c "
import os, json, socket, time

def cpu():
    try:
        def r():
            with open('/proc/stat') as f:
                p = [float(x) for x in f.readline().split()[1:]]
            return p[3]+p[4], sum(p)
        i1, t1 = r(); time.sleep(0.08); i2, t2 = r()
        dt = t2 - t1
        u = 100.0 * (1.0 - (i2 - i1) / dt) if dt > 0 else 0
        m = 'Unknown'
        with open('/proc/cpuinfo') as f:
            for l in f:
                if 'model name' in l: m = l.split(':', 1)[1].strip(); break
        return {'cores': os.cpu_count() or 1, 'usage_percent': round(max(0, min(100, u)), 1), 'model': m}
    except Exception: return {'cores': 1, 'usage_percent': 0, 'model': 'N/A'}

def mem():
    try:
        mi = {}
        with open('/proc/meminfo') as f:
            for l in f:
                pts = l.split(':')
                if len(pts) == 2: mi[pts[0].strip()] = int(pts[1].split()[0]) * 1024
        tot = mi.get('MemTotal', 0)
        avail = mi.get('MemAvailable', mi.get('MemFree', 0))
        used = tot - avail
        pct = (used / tot * 100) if tot > 0 else 0
        return {'total_bytes': tot, 'used_bytes': used, 'free_bytes': avail, 'percent': round(pct, 1)}
    except Exception: return {'total_bytes': 0, 'used_bytes': 0, 'free_bytes': 0, 'percent': 0}

def disk():
    try:
        st = os.statvfs('/')
        tot = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        used = tot - free
        return [{'mount': '/', 'total_bytes': tot, 'used_bytes': used, 'free_bytes': free, 'percent': round(used/tot*100, 1)}]
    except Exception: return []

def host():
    os_name = 'Linux'
    try:
        if os.path.exists('/etc/os-release'):
            with open('/etc/os-release') as f:
                for l in f:
                    if l.startswith('PRETTY_NAME='): os_name = l.split('=', 1)[1].strip().strip('\"'); break
    except Exception: pass
    upt = 0
    try:
        with open('/proc/uptime') as f: upt = int(float(f.readline().split()[0]))
    except Exception: pass
    lavg = [0,0,0]
    try: lavg = [round(x, 2) for x in os.getloadavg()]
    except Exception: pass
    return {'hostname': socket.gethostname(), 'os_name': os_name, 'kernel': os.uname().release, 'uptime_seconds': upt, 'load_avg': lavg}

print(json.dumps({'cpu': cpu(), 'memory': mem(), 'disks': disk(), 'host': host()}))
" 2>/dev/null || (
    MEM=$(free -b 2>/dev/null | awk 'NR==2{print $2, $3, $7}')
    DISK=$(df -B1 / 2>/dev/null | awk 'NR==2{print $2, $3, $4}')
    echo "{\"shell_fallback\": true, \"mem_raw\": \"$MEM\", \"disk_raw\": \"$DISK\"}"
)
"""
        res = self.ssh.execute_command(script, timeout=6)
        parsed = None
        if res.get("success") and res.get("stdout", "").strip():
            try:
                parsed = json.loads(res["stdout"].strip().split("\n")[-1])
            except Exception:
                parsed = None

        if isinstance(parsed, dict):
            if parsed.get("shell_fallback"):
                mem_parts = str(parsed.get("mem_raw", "")).split()
                mem_total = int(mem_parts[0]) if len(mem_parts) > 0 and mem_parts[0].isdigit() else 0
                mem_used = int(mem_parts[1]) if len(mem_parts) > 1 and mem_parts[1].isdigit() else 0
                mem_avail = int(mem_parts[2]) if len(mem_parts) > 2 and mem_parts[2].isdigit() else 0
                mem_pct = round((mem_used / mem_total) * 100, 1) if mem_total > 0 else 0

                disk_parts = str(parsed.get("disk_raw", "")).split()
                d_total = int(disk_parts[0]) if len(disk_parts) > 0 and disk_parts[0].isdigit() else 0
                d_used = int(disk_parts[1]) if len(disk_parts) > 1 and disk_parts[1].isdigit() else 0
                d_avail = int(disk_parts[2]) if len(disk_parts) > 2 and disk_parts[2].isdigit() else 0
                d_pct = round((d_used / d_total) * 100, 1) if d_total > 0 else 0

                return {
                    "cpu": {"cores": 1, "usage_percent": 0, "model": "Linux System"},
                    "memory": {"percent": mem_pct, "used_bytes": mem_used, "total_bytes": mem_total, "free_bytes": mem_avail},
                    "disks": [{"mount": "/", "percent": d_pct, "used_bytes": d_used, "total_bytes": d_total, "free_bytes": d_avail}],
                    "host": {"hostname": "Linux", "os_name": "Linux", "kernel": "-", "uptime_seconds": 0, "load_avg": [0, 0, 0]}
                }
            return parsed

        return {
            "cpu": {"cores": 1, "usage_percent": 0, "model": "Linux System"},
            "memory": {"percent": 0, "used_bytes": 0, "total_bytes": 0, "free_bytes": 0},
            "disks": [{"mount": "/", "percent": 0, "used_bytes": 0, "total_bytes": 0, "free_bytes": 0}],
            "host": {"hostname": "Linux", "os_name": "Linux", "kernel": "-", "uptime_seconds": 0, "load_avg": [0, 0, 0]}
        }

    def get_docker_metrics(self):
        cmd = "docker ps -a --format '{{json .}}' 2>/dev/null; echo '---DOCKER_INFO---'; docker info --format '{{json .}}' 2>/dev/null"
        res = self.ssh.execute_command(cmd, timeout=8)
        if not res["success"]:
            return {"installed": False, "running": False, "containers": []}

        out = res["stdout"]
        if "---DOCKER_INFO---" not in out:
            return {"installed": False, "running": False, "containers": []}

        parts = out.split("---DOCKER_INFO---")
        ps_part = parts[0].strip()
        info_part = parts[1].strip()

        docker_data = {
            "installed": True,
            "running": False,
            "containers_count": 0,
            "running_count": 0,
            "stopped_count": 0,
            "images_count": 0,
            "containers": []
        }

        if info_part:
            try:
                info_json = json.loads(info_part)
                docker_data["running"] = True
                docker_data["containers_count"] = info_json.get("Containers", 0)
                docker_data["running_count"] = info_json.get("ContainersRunning", 0)
                docker_data["stopped_count"] = info_json.get("ContainersStopped", 0)
                docker_data["images_count"] = info_json.get("Images", 0)
                docker_data["server_version"] = info_json.get("ServerVersion", "")
            except Exception:
                pass

        if ps_part:
            for line in ps_part.split("\n"):
                if line.strip():
                    try:
                        c_data = json.loads(line)
                        docker_data["containers"].append({
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

        return docker_data
