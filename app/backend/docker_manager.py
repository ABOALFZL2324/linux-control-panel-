import re
import shlex
from .docker_downloader import DockerDownloadManager

CONTAINER_ID_REGEX = re.compile(r"^[a-zA-Z0-9_\.\-]+$")
PORT_REGEX = re.compile(r"^([0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}:)?[0-9]{1,5}(:[0-9]{1,5})?(/[a-z0-9]+)?$")

class DockerManager:
    def __init__(self, ssh_client):
        self.ssh = ssh_client
        self.downloader = DockerDownloadManager(ssh_client)

    def _execute(self, cmd, timeout=25):
        if not self.ssh or not self.ssh.is_connected:
            return {"success": False, "stdout": "", "stderr": "SSH متصل نیست", "code": -1}
        full_cmd = f"export PATH=$PATH:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; {cmd}"
        res = self.ssh.execute_command(full_cmd, sudo=False, timeout=timeout)
        err_str = (res.get("stderr") or "").lower()
        if not res["success"] and ("permission denied" in err_str or "connect to the docker daemon" in err_str or "dial unix" in err_str):
            res = self.ssh.execute_command(full_cmd, sudo=True, timeout=timeout)
        return res

    def container_action(self, container_id: str, action: str):
        valid_actions = ["start", "stop", "restart", "pause", "unpause", "rm", "rm_force", "kill"]
        if action not in valid_actions:
            return {"success": False, "error": f"عملیات نامعتبر: {action}"}

        cid = str(container_id).strip()
        if not cid or not CONTAINER_ID_REGEX.match(cid):
            return {"success": False, "error": "شناسه یا نام کانتینر نامعتبر است"}

        if action == "rm_force":
            cmd = f"docker rm -f -- {shlex.quote(cid)}"
        elif action == "rm":
            cmd = f"docker rm -- {shlex.quote(cid)} || docker rm -f -- {shlex.quote(cid)}"
        else:
            cmd = f"docker {action} -- {shlex.quote(cid)}"

        res = self._execute(cmd, timeout=25)
        return {
            "success": res["success"],
            "stdout": res["stdout"].strip(),
            "stderr": res["stderr"].strip()
        }

    def get_logs(self, container_id: str, tail: int = 150):
        cid = str(container_id).strip()
        if not cid or not CONTAINER_ID_REGEX.match(cid):
            return {"success": False, "logs": "شناسه کانتینر نامعتبر است"}

        try:
            tail_int = max(1, min(5000, int(tail)))
        except (ValueError, TypeError):
            tail_int = 150

        cmd = f"docker logs --tail {tail_int} -- {shlex.quote(cid)}"
        res = self._execute(cmd, timeout=10)
        logs = (res["stdout"] + "\n" + res["stderr"]).strip()
        return {
            "success": True,
            "logs": logs
        }

    def list_images(self):
        cmd = r"""docker images --format '{"repository":"{{.Repository}}","tag":"{{.Tag}}","id":"{{.ID}}","created":"{{.CreatedSince}}","size":"{{.Size}}"}' 2>/dev/null"""
        res = self._execute(cmd, timeout=15)
        if not res["success"]:
            return {"success": False, "images": []}
        images = []
        for line in res["stdout"].strip().split("\n"):
            line = line.strip()
            if line:
                try:
                    import json
                    img = json.loads(line)
                    repo = img.get("repository", "")
                    tag = img.get("tag", "")
                    img["full_tag"] = f"{repo}:{tag}" if tag and tag != "<none>" else repo
                    images.append(img)
                except Exception:
                    pass
        return {"success": True, "images": images}

    def pull_image(self, image_name: str):
        # Automatically route to asynchronous download queue manager
        return self.downloader.queue_pull(image_name)

    def queue_pull_image(self, image_name: str):
        return self.downloader.queue_pull(image_name)

    def get_download_queue_status(self):
        return self.downloader.get_queue_status()

    def cancel_download(self, task_id: str):
        return self.downloader.cancel_task(task_id)

    def retry_download(self, task_id: str):
        return self.downloader.retry_task(task_id)

    def remove_download(self, task_id: str):
        return self.downloader.remove_task(task_id)

    def clear_completed_downloads(self):
        return self.downloader.clear_completed()

    def run_container(self, image_name: str, container_name: str = "", port_mapping: str = "", env_vars: str = "", restart_policy: str = "unless-stopped", replace_existing: bool = False):
        if not image_name:
            return {"success": False, "error": "نام ایمیج الزامی است"}

        clean_img = image_name.strip()
        if any(c in clean_img for c in " \t\r\n'\"`$<>|;&"):
            return {"success": False, "error": "نام ایمیج نامعتبر است"}

        prefix_cmd = ""
        cname = ""
        if container_name:
            cname = container_name.strip()
            if not CONTAINER_ID_REGEX.match(cname):
                return {"success": False, "error": "نام کانتینر فقط می‌تواند شامل حروف، اعداد، خط فاصله و نقطه باشد"}
            if replace_existing:
                prefix_cmd = f"docker rm -f {shlex.quote(cname)} 2>/dev/null || true; "

        parts = ["docker run -d"]

        if cname:
            parts.append(f"--name {shlex.quote(cname)}")

        valid_restart = ["no", "on-failure", "always", "unless-stopped"]
        if restart_policy and restart_policy in valid_restart and restart_policy != "no":
            parts.append(f"--restart {restart_policy}")

        if port_mapping:
            for p in port_mapping.replace(",", " ").split():
                p_clean = p.strip()
                if p_clean and PORT_REGEX.match(p_clean):
                    parts.append(f"-p {shlex.quote(p_clean)}")

        if env_vars:
            for env in env_vars.split("\n"):
                env = env.strip()
                if env and "=" in env:
                    parts.append(f"-e {shlex.quote(env)}")

        parts.append(f"-- {shlex.quote(clean_img)}")
        cmd = prefix_cmd + " ".join(parts)
        res = self._execute(cmd, timeout=40)
        return {
            "success": res["success"],
            "container_id": res["stdout"].strip(),
            "error": res["stderr"].strip() if not res["success"] else ""
        }

    def get_container_details(self, container_id: str):
        cid = str(container_id).strip()
        if not cid or not CONTAINER_ID_REGEX.match(cid):
            return {"success": False, "error": "شناسه یا نام کانتینر نامعتبر است"}

        cmd = f"docker inspect -- {shlex.quote(cid)}"
        res = self._execute(cmd, timeout=15)
        if not res["success"] or not res["stdout"].strip():
            return {"success": False, "error": res.get("stderr") or "خطا در استعلام اطلاعات کانتینر"}

        try:
            import json
            data = json.loads(res["stdout"])
            if not data or not isinstance(data, list):
                return {"success": False, "error": "داده‌های دریافتی نامعتبر است"}
            c = data[0]

            name = c.get("Name", "").lstrip("/")
            image = c.get("Config", {}).get("Image", "")
            state = c.get("State", {}).get("Status", "unknown")
            restart_policy = c.get("HostConfig", {}).get("RestartPolicy", {}).get("Name", "unless-stopped")

            ports = []
            port_bindings = c.get("HostConfig", {}).get("PortBindings") or {}
            for cont_p, bindings in port_bindings.items():
                proto = "tcp"
                p_num = cont_p
                if "/" in cont_p:
                    p_num, proto = cont_p.split("/", 1)
                if bindings:
                    for b in bindings:
                        ports.append({
                            "host_port": b.get("HostPort", ""),
                            "container_port": p_num,
                            "protocol": proto
                        })
                else:
                    ports.append({
                        "host_port": "",
                        "container_port": p_num,
                        "protocol": proto
                    })

            if not ports:
                net_ports = c.get("NetworkSettings", {}).get("Ports") or {}
                for cont_p, bindings in net_ports.items():
                    proto = "tcp"
                    p_num = cont_p
                    if "/" in cont_p:
                        p_num, proto = cont_p.split("/", 1)
                    if bindings:
                        for b in bindings:
                            ports.append({
                                "host_port": b.get("HostPort", ""),
                                "container_port": p_num,
                                "protocol": proto
                            })

            raw_envs = c.get("Config", {}).get("Env", [])
            custom_envs = []
            for env in raw_envs:
                k = env.split("=", 1)[0]
                if k not in ["PATH", "HOSTNAME", "HOME"]:
                    custom_envs.append(env)

            return {
                "success": True,
                "container": {
                    "id": c.get("Id", "")[:12],
                    "full_id": c.get("Id", ""),
                    "name": name,
                    "image": image,
                    "state": state,
                    "restart_policy": restart_policy,
                    "ports": ports,
                    "env_vars": "\n".join(custom_envs)
                }
            }
        except Exception as e:
            return {"success": False, "error": f"خطا در پردازش اطلاعات کانتینر: {str(e)}"}

    def edit_container(self, container_id: str, new_name: str, port_mapping: str = "", env_vars: str = "", restart_policy: str = "unless-stopped"):
        cid = str(container_id).strip()
        if not cid or not CONTAINER_ID_REGEX.match(cid):
            return {"success": False, "error": "شناسه کانتینر نامعتبر است"}

        details_res = self.get_container_details(cid)
        if not details_res.get("success"):
            return {"success": False, "error": details_res.get("error") or "کانتینر اولیه یافت نشد"}

        orig = details_res["container"]
        orig_name = orig["name"]
        image = orig["image"]

        clean_new_name = str(new_name).strip() if new_name else orig_name
        if not CONTAINER_ID_REGEX.match(clean_new_name):
            return {"success": False, "error": "نام جدید کانتینر نامعتبر است"}

        # Compare existing configuration
        curr_ports_list = [f"{p['host_port']}:{p['container_port']}" if p.get('host_port') else p['container_port'] for p in orig.get("ports", [])]
        clean_curr_ports = " ".join(curr_ports_list)
        clean_req_ports = " ".join(port_mapping.replace(",", " ").split())
        clean_curr_env = "\n".join([e.strip() for e in orig.get("env_vars", "").split("\n") if e.strip()])
        clean_req_env = "\n".join([e.strip() for e in env_vars.split("\n") if e.strip()])

        only_name_changed = (
            clean_new_name != orig_name and
            clean_req_ports == clean_curr_ports and
            clean_curr_env == clean_req_env and
            restart_policy == orig.get("restart_policy")
        )

        if only_name_changed:
            res = self._execute(f"docker rename {shlex.quote(cid)} {shlex.quote(clean_new_name)}")
            if res["success"]:
                return {"success": True, "method": "rename", "message": f"نام کانتینر به {clean_new_name} تغییر یافت"}
            else:
                return {"success": False, "error": res.get("stderr") or "خطا در تغییر نام کانتینر"}

        # Stop and remove old container
        rm_res = self._execute(f"docker rm -f {shlex.quote(cid)}")
        if not rm_res["success"] and "No such container" not in rm_res.get("stderr", ""):
            return {"success": False, "error": f"خطا در توقف کانتینر قبلی: {rm_res.get('stderr')}"}

        # Re-run container with updated parameters
        run_res = self.run_container(
            image_name=image,
            container_name=clean_new_name,
            port_mapping=port_mapping,
            env_vars=env_vars,
            restart_policy=restart_policy,
            replace_existing=True
        )

        if run_res.get("success"):
            return {
                "success": True,
                "method": "recreate",
                "container_id": run_res.get("container_id", ""),
                "message": f"کانتینر {clean_new_name} با تنظیمات و پورت‌های جدید بازسازی و اجرا شد"
            }
        else:
            return {
                "success": False,
                "error": f"خطا در اجرای مجدد کانتینر: {run_res.get('error')}"
            }

    def remove_image(self, image_id_or_name: str, force: bool = False):
        clean_target = str(image_id_or_name).strip()
        if any(c in clean_target for c in " \t\r\n'\"`$<>|;&"):
            return {"success": False, "error": "شناسه ایمیج نامعتبر است"}
        flag = "-f" if force else ""
        cmd = f"docker rmi {flag} -- {shlex.quote(clean_target)}"
        res = self._execute(cmd, timeout=20)
        return {
            "success": res["success"],
            "stdout": res["stdout"].strip(),
            "stderr": res["stderr"].strip()
        }

    def prune_system(self):
        cmd = "docker system prune -f"
        res = self._execute(cmd, timeout=30)
        return {
            "success": res["success"],
            "output": res["stdout"].strip()
        }

    def load_image_from_file(self, file_path: str, tag: str = ""):
        clean_path = str(file_path).strip()
        if not clean_path:
            return {"success": False, "error": "مسیر فایل مشخص نشده است"}
        quoted_path = shlex.quote(clean_path)

        cmd_load = f"docker load -i {quoted_path}"
        res_load = self._execute(cmd_load, timeout=120)
        if res_load["success"] and res_load["stdout"] and "Loaded image" in res_load["stdout"]:
            return {
                "success": True,
                "output": res_load["stdout"].strip(),
                "method": "docker load"
            }

        tag_arg = f" {shlex.quote(tag.strip())}" if tag and tag.strip() else ""
        cmd_import = f"docker import {quoted_path}{tag_arg}"
        res_import = self._execute(cmd_import, timeout=120)
        if res_import["success"]:
            out = res_import["stdout"].strip()
            return {
                "success": True,
                "output": f"ایمیج با موفقیت از فایل ایمپورت شد: {out}",
                "method": "docker import"
            }

        if res_load["success"] and res_load["stdout"]:
            return {
                "success": True,
                "output": res_load["stdout"].strip(),
                "method": "docker load"
            }

        err = res_load["stderr"].strip() or res_import["stderr"].strip() or "خطا در پردازش و بارگذاری فایل در داکر"
        return {
            "success": False,
            "error": err
        }

    # ----------------- DOCKER STARTUP & BOOT AUTO-START -----------------
    def get_startup_status(self):
        """Gets auto-start status of docker service and all containers."""
        script = r"""
SVC_STATUS=$(systemctl is-enabled docker 2>/dev/null || systemctl is-enabled docker.service 2>/dev/null || echo "unknown")
echo "SERVICE|$SVC_STATUS"

CIDS=$(docker ps -aq 2>/dev/null | tr '\n' ' ')
if [ -n "$CIDS" ]; then
    docker inspect --format 'CONTAINER|{{.Id}}|{{.Name}}|{{.HostConfig.RestartPolicy.Name}}|{{.State.Status}}|{{.Config.Image}}' $CIDS 2>/dev/null
fi
"""
        res = self._execute(script, timeout=15)
        if not res["success"]:
            return {"success": False, "error": res.get("stderr") or "خطا در استعلام استارت‌آپ"}

        service_enabled = False
        service_state = "unknown"
        containers = []

        for line in res.get("stdout", "").strip().split("\n"):
            line = line.strip()
            if line.startswith("SERVICE|"):
                service_state = line.split("|")[1].strip()
                service_enabled = (service_state == "enabled")
            elif line.startswith("CONTAINER|"):
                parts = line.split("|")
                if len(parts) >= 6:
                    cid = parts[1][:12]
                    cname = parts[2].lstrip("/")
                    policy = parts[3] or "no"
                    status = parts[4]
                    image = parts[5]
                    is_autostart = policy in ["always", "unless-stopped"]
                    containers.append({
                        "id": cid,
                        "full_id": parts[1],
                        "name": cname,
                        "restart_policy": policy,
                        "is_autostart": is_autostart,
                        "status": status,
                        "image": image
                    })

        containers.sort(key=lambda x: (not x["is_autostart"], x["name"].lower()))

        return {
            "success": True,
            "service_enabled": service_enabled,
            "service_state": service_state,
            "containers": containers,
            "autostart_count": sum(1 for c in containers if c["is_autostart"]),
            "total_count": len(containers)
        }

    def set_container_restart_policy(self, container_id: str, policy: str):
        """Updates container restart policy (always, unless-stopped, on-failure, no)."""
        valid_policies = ["always", "unless-stopped", "on-failure", "no"]
        cid = str(container_id).strip()
        pol = str(policy).strip().lower()

        if not cid or not CONTAINER_ID_REGEX.match(cid):
            return {"success": False, "error": "شناسه کانتینر نامعتبر است"}
        if pol not in valid_policies:
            return {"success": False, "error": f"سیاست استارت‌آپ نامعتبر است: {pol}"}

        cmd = f"docker update --restart={pol} -- {shlex.quote(cid)}"
        res = self._execute(cmd, timeout=15)
        if res["success"]:
            return {
                "success": True,
                "message": f"سیاست استارت‌آپ کانتینر به '{pol}' بروزرسانی شد",
                "policy": pol,
                "container_id": cid
            }
        return {"success": False, "error": res.get("stderr") or "خطا در بروزرسانی سیاست استارت‌آپ"}

    def set_docker_service_startup(self, enable: bool):
        """Enables or disables Docker service on Linux system boot."""
        act = "enable" if enable else "disable"
        cmd = f"systemctl {act} docker"
        res = self.ssh.execute_sudo_command(cmd, timeout=10)
        if res.get("success"):
            return {
                "success": True,
                "enabled": enable,
                "message": f"راه‌اندازی خودکار داکر در هنگام بوت لینوکس {'فعال' if enable else 'غیرفعال'} شد"
            }
        return {"success": False, "error": res.get("stderr") or "خطا در تغییر وضعیت سرویس در سیستم"}

    def set_batch_containers_startup(self, policy: str):
        """Sets restart policy for all containers at once."""
        valid_policies = ["always", "unless-stopped", "no"]
        pol = str(policy).strip().lower()
        if pol not in valid_policies:
            return {"success": False, "error": "سیاست نامعتبر است"}

        cmd = f"CIDS=$(docker ps -aq 2>/dev/null | tr '\\n' ' '); [ -n \"$CIDS\" ] && docker update --restart={pol} $CIDS"
        res = self._execute(cmd, timeout=25)
        if res["success"]:
            return {
                "success": True,
                "message": f"سیاست استارت‌آپ تمام کانتینرها به '{pol}' بروزرسانی شد",
                "policy": pol
            }
        return {"success": False, "error": res.get("stderr") or "خطا در بروزرسانی دسته‌ای کانتینرها"}

    # ----------------- DOCKER HUB SEARCH -----------------
    def search_hub(self, term: str, limit: int = 25):
        """Searches Docker Hub for images matching term with fallback parser."""
        clean_term = str(term).strip()
        if not clean_term:
            return {"success": False, "error": "عبارت جستجو مشخص نشده است"}
        if any(c in clean_term for c in " \t\r\n'\"`$<>|;&"):
            return {"success": False, "error": "عبارت جستجو نامعتبر است"}

        cmd = f"docker search --limit {limit} --format '{{{{.Name}}}}\t{{{{.StarCount}}}}\t{{{{.IsOfficial}}}}\t{{{{.Description}}}}' {shlex.quote(clean_term)}"
        res = self._execute(cmd, timeout=30)
        
        results = []
        if res["success"] and res.get("stdout", "").strip():
            for line in res["stdout"].strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                parts = line.split("\t")
                if len(parts) >= 4:
                    results.append({
                        "name": parts[0],
                        "stars": parts[1],
                        "is_official": "[OK]" in parts[2] or "true" in parts[2].lower(),
                        "description": parts[3]
                    })
            if results:
                return {"success": True, "results": results, "count": len(results)}

        # Fallback to standard search if format failed or was empty
        cmd_fallback = f"docker search --limit {limit} {shlex.quote(clean_term)}"
        res_fb = self._execute(cmd_fallback, timeout=25)
        if res_fb["success"] and res_fb.get("stdout", "").strip():
            lines = res_fb["stdout"].strip().split("\n")
            if len(lines) > 1:
                for line in lines[1:]:
                    parts = line.split()
                    if len(parts) >= 3:
                        name = parts[0]
                        stars = parts[-3] if len(parts) >= 4 and parts[-3].isdigit() else (parts[-2] if parts[-2].isdigit() else "0")
                        is_off = "[OK]" in line
                        results.append({
                            "name": name,
                            "stars": stars,
                            "is_official": is_off,
                            "description": " ".join(parts[1:-3]) if len(parts) >= 5 else (parts[1] if len(parts) > 1 else "")
                        })
                return {"success": True, "results": results, "count": len(results)}

        err_msg = res.get("stderr") or res_fb.get("stderr") or "خطا در جستجو در داکر هاب (احتمال فیلترینگ یا عدم تنظیم میرور)"
        return {"success": False, "error": err_msg, "results": []}

    # ----------------- REGISTRY MIRRORS (IRAN & GLOBAL) -----------------
    def get_registry_mirrors(self):
        """Gets active Docker registry mirrors and presets."""
        cmd = "cat /etc/docker/daemon.json 2>/dev/null || sudo cat /etc/docker/daemon.json 2>/dev/null || echo '{}'"
        res = self._execute(cmd)
        raw = res.get("stdout", "").strip() or "{}"
        import json
        daemon_cfg = {}
        try:
            daemon_cfg = json.loads(raw)
        except Exception:
            daemon_cfg = {}

        mirrors = daemon_cfg.get("registry-mirrors", [])
        if not isinstance(mirrors, list):
            mirrors = []

        presets = [
            {"id": "arvan", "name": "ابر آروان (ArvanCloud)", "url": "https://docker.arvancloud.ir", "country": "IR", "desc": "میرور معتبر داکر در ایران با کش ابری و پایداری بالا"},
            {"id": "iranserver", "name": "ایران‌سرور (IranServer)", "url": "https://docker.iranserver.com", "country": "IR", "desc": "میرور تحریم‌شکن داکر با میزبانی در دیتاسنترهای داخلی"},
            {"id": "dockerir", "name": "داکر دات آی‌آر (Docker.ir)", "url": "https://registry.docker.ir", "country": "IR", "desc": "سرویس میرور آزاد داکر برای توسعه‌دهندگان"},
            {"id": "dothub", "name": "دات‌هاب (DotHub)", "url": "https://mirror.dothub.ir", "country": "IR", "desc": "میرور کامیونیتی ایرانی داکر"},
            {"id": "google", "name": "گوگل کلود (Google Mirror)", "url": "https://mirror.gcr.io", "country": "Global", "desc": "میرور عمومی بین‌المللی گوگل"}
        ]

        return {
            "success": True,
            "mirrors": mirrors,
            "presets": presets,
            "daemon_config": daemon_cfg
        }

    def set_registry_mirrors(self, mirrors: list):
        """Saves registry mirrors to /etc/docker/daemon.json and reloads docker."""
        import json
        import base64
        clean_mirrors = [str(m).strip().rstrip('/') for m in mirrors if str(m).strip()]

        read_cmd = "cat /etc/docker/daemon.json 2>/dev/null || echo '{}'"
        res_read = self._execute(read_cmd)
        raw = res_read.get("stdout", "").strip() or "{}"
        try:
            cfg = json.loads(raw)
        except Exception:
            cfg = {}

        if not isinstance(cfg, dict):
            cfg = {}

        if clean_mirrors:
            cfg["registry-mirrors"] = clean_mirrors
        else:
            cfg.pop("registry-mirrors", None)

        formatted_json = json.dumps(cfg, indent=2)
        b64_json = base64.b64encode(formatted_json.encode("utf-8")).decode("ascii")

        script = f"""
mkdir -p /etc/docker
[ -f /etc/docker/daemon.json ] && cp /etc/docker/daemon.json /etc/docker/daemon.json.bak
echo {b64_json} | base64 -d > /etc/docker/daemon.json
systemctl daemon-reload 2>/dev/null || true
systemctl restart docker 2>/dev/null || systemctl restart docker.service 2>/dev/null
"""
        res = self.ssh.execute_sudo_command(script, timeout=30)
        if res.get("success"):
            return {
                "success": True,
                "message": "میرورها با موفقیت اعمال شدند و داکر ری‌استارت گردید",
                "mirrors": clean_mirrors
            }
        else:
            return {
                "success": False,
                "error": res.get("stderr") or "خطا در اعمال میرورهای داکر"
            }

    def test_registry_mirror(self, mirror_url: str):
        clean_url = str(mirror_url).strip().rstrip('/')
        if not clean_url:
            return {"success": False, "error": "آدرس میرور مشخص نشده است"}
        target = f"{clean_url}/v2/"
        cmd = f"curl -k -s -m 6 -o /dev/null -w '%{{http_code}}:%{{time_total}}' {shlex.quote(target)}"
        res = self._execute(cmd, timeout=8)
        out = res.get("stdout", "").strip()
        if ":" in out:
            code, t = out.split(":", 1)
            try:
                ms = round(float(t) * 1000)
            except Exception:
                ms = 0
            is_valid = code in ["200", "401", "403", "301", "302"]
            return {
                "success": True,
                "reachable": is_valid,
                "status_code": code,
                "latency_ms": ms,
                "message": f"پاسخ در {ms}ms (کد {code})" if is_valid else f"کد خطا {code}"
            }
        return {"success": False, "reachable": False, "error": res.get("stderr") or "عدم پاسخگویی میرور"}
