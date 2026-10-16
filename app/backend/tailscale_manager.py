import json
import subprocess
import shutil

class TailscaleManager:
    def __init__(self, ssh_client):
        self.ssh = ssh_client

    def get_local_tailscale_status(self):
        """Fetches status of the local Windows Tailscale instance."""
        if not shutil.which("tailscale"):
            return {"installed": False, "running": False, "error": "دستور tailscale در ویندوز یافت نشد"}

        try:
            res = subprocess.run(
                ["tailscale", "status", "--json"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5
            )
            if res.returncode == 0 and res.stdout.strip():
                data = json.loads(res.stdout)
                parsed = self._parse_status_data(data, is_local=True)
                parsed["installed"] = True
                return parsed
            else:
                return {"installed": True, "running": False, "error": res.stderr.strip() or "تیلویسکیل در حال اجرا نیست"}
        except Exception as e:
            return {"installed": True, "running": False, "error": str(e)}

    def get_remote_tailscale_status(self):
        """Fetches status of Tailscale running on the connected Linux server."""
        if not self.ssh or not self.ssh.is_connected:
            return {"installed": False, "running": False, "error": "SSH متصل نیست"}

        res = self.ssh.execute_command("tailscale status --json", timeout=6)
        if res["success"] and res["stdout"].strip():
            try:
                data = json.loads(res["stdout"].strip())
                parsed = self._parse_status_data(data, is_local=False)
                parsed["installed"] = True
                return parsed
            except Exception as e:
                return {"installed": True, "running": False, "error": f"خطا در پردازش JSON: {e}"}
        else:
            # Check if binary exists
            chk = self.ssh.execute_command("which tailscale", timeout=4)
            installed = chk["success"] and bool(chk["stdout"].strip())
            return {"installed": installed, "running": False, "error": "تیلویسکیل در سرور متوقف یا لاگین نشده است"}

    def _parse_status_data(self, data, is_local=True):
        self_node = data.get("Self", {})
        backend_state = data.get("BackendState", "Stopped")
        current_tailnet = data.get("CurrentTailnet", {})
        
        # Extract user info
        user_map = data.get("User", {})
        user_name = ""
        user_email = ""
        if user_map:
            for uid, uinfo in user_map.items():
                user_name = uinfo.get("DisplayName", "")
                user_email = uinfo.get("LoginName", "")
                break

        if not user_email:
            user_email = current_tailnet.get("Name", "")

        # Peers
        peers = []
        raw_peers = data.get("Peer", {}) or {}
        exit_nodes = []

        for pkey, p in raw_peers.items():
            ips = p.get("TailscaleIPs", [])
            primary_ip = ips[0] if ips else ""
            is_exit_node_opt = p.get("ExitNodeOption", False)
            is_active_exit = p.get("ExitNode", False)
            is_online = p.get("Online", False)
            host_name = p.get("HostName", "")

            peer_info = {
                "id": p.get("ID", ""),
                "hostname": host_name,
                "dns_name": p.get("DNSName", "").rstrip("."),
                "os": p.get("OS", "unknown"),
                "tailscale_ip": primary_ip,
                "all_ips": ips,
                "online": is_online,
                "relay": p.get("Relay", ""),
                "is_exit_node_option": is_exit_node_opt,
                "is_active_exit_node": is_active_exit,
                "last_seen": p.get("LastSeen", "")
            }
            peers.append(peer_info)

            if is_exit_node_opt:
                exit_nodes.append({
                    "hostname": host_name,
                    "ip": primary_ip,
                    "active": is_active_exit,
                    "online": is_online
                })

        # Self node details
        self_ips = self_node.get("TailscaleIPs", [])
        self_primary_ip = self_ips[0] if self_ips else ""
        capabilities = self_node.get("Capabilities", []) or []

        return {
            "running": backend_state.lower() == "running",
            "backend_state": backend_state,
            "version": data.get("Version", ""),
            "user": {
                "display_name": user_name or user_email.split("@")[0],
                "email": user_email
            },
            "tailnet_name": current_tailnet.get("Name", user_email),
            "magic_dns_suffix": data.get("MagicDNSSuffix", ""),
            "device": {
                "hostname": self_node.get("HostName", ""),
                "dns_name": self_node.get("DNSName", "").rstrip("."),
                "tailscale_ip": self_primary_ip,
                "all_ips": self_ips,
                "os": self_node.get("OS", ""),
                "relay": self_node.get("Relay", ""),
                "is_exit_node": self_node.get("ExitNode", False),
                "is_exit_node_option": self_node.get("ExitNodeOption", False),
                "key_expiry": self_node.get("KeyExpiry", "")
            },
            "peers": peers,
            "exit_nodes": exit_nodes,
            "preferences": {
                "shields_up": any("shields-up" in str(c) for c in capabilities),
                "ssh_enabled": any("ssh" in str(c) for c in capabilities),
                "accept_routes": True
            }
        }

    def set_preference(self, target="local", key="", value=None):
        """Changes a Tailscale preference on local (Windows) or remote (Linux)."""
        valid_keys = {
            "shields-up": "--shields-up",
            "accept-routes": "--accept-routes",
            "accept-dns": "--accept-dns",
            "ssh": "--ssh",
            "advertise-exit-node": "--advertise-exit-node",
            "auto-update": "--auto-update"
        }

        if key not in valid_keys:
            return {"success": False, "error": f"کلید تنظیم نامعتبر: {key}"}

        flag = valid_keys[key]
        val_str = "true" if value is True or value == "true" or value == 1 else "false"

        cmd_flag = f"{flag}={val_str}"

        if target == "local":
            try:
                res = subprocess.run(
                    ["tailscale", "set", cmd_flag],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=10
                )
                return {
                    "success": res.returncode == 0,
                    "message": "تنظیم با موفقیت اعمال شد" if res.returncode == 0 else res.stderr.strip()
                }
            except Exception as e:
                return {"success": False, "error": str(e)}
        else:
            if not self.ssh or not self.ssh.is_connected:
                return {"success": False, "error": "SSH متصل نیست"}
            res = self.ssh.execute_command(f"tailscale set {cmd_flag}", sudo=True, timeout=12)
            return {
                "success": res["success"],
                "message": "تنظیم روی سرور لینوکس اعمال شد" if res["success"] else (res.get("stderr") or "خطا")
            }

    def set_exit_node(self, target="local", exit_node_ip_or_name=""):
        """Sets or unsets an exit node."""
        import re
        import shlex
        node_arg = str(exit_node_ip_or_name).strip()
        if node_arg and not re.match(r"^[a-zA-Z0-9_\.\-:]+$", node_arg):
            return {"success": False, "error": "شناسه Exit Node نامعتبر است"}

        cmd_flag = f"--exit-node={node_arg}"

        if target == "local":
            try:
                res = subprocess.run(
                    ["tailscale", "set", cmd_flag],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=10
                )
                return {
                    "success": res.returncode == 0,
                    "message": f"Exit node به '{node_arg or 'غیرفعال'}' تغییر یافت" if res.returncode == 0 else res.stderr.strip()
                }
            except Exception as e:
                return {"success": False, "error": str(e)}
        else:
            if not self.ssh or not self.ssh.is_connected:
                return {"success": False, "error": "SSH متصل نیست"}
            safe_flag = shlex.quote(cmd_flag)
            res = self.ssh.execute_command(f"tailscale set {safe_flag}", sudo=True, timeout=12)
            return {
                "success": res["success"],
                "message": "Exit node در سرور تغییر یافت" if res["success"] else (res.get("stderr") or "خطا")
            }

    def ping_peer(self, peer_ip_or_name: str, count: int = 3):
        """Pings a Tailscale peer from Windows local machine using native tailscale ping."""
        import re
        if not peer_ip_or_name:
            return {"success": False, "error": "آدرس نود مشخص نشده است"}

        peer_clean = str(peer_ip_or_name).strip()
        if not re.match(r"^[a-zA-Z0-9_\.\-:]+$", peer_clean):
            return {"success": False, "error": "آدرس نود شامل کاراکترهای نامعتبر است"}

        try:
            cnt = max(1, min(10, int(count)))
        except (ValueError, TypeError):
            cnt = 3

        try:
            res = subprocess.run(
                ["tailscale", "ping", f"--c={cnt}", peer_clean],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=12
            )
            lines = res.stdout.strip().split("\n")
            pongs = [l.strip() for l in lines if "pong from" in l or "via" in l]
            return {
                "success": len(pongs) > 0,
                "raw_output": res.stdout.strip(),
                "summary": pongs[0] if pongs else "پاسخی دریافت نشد"
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def up(self, target="local", auth_key="", extra_flags=""):
        """Runs 'tailscale up' with optional authkey or flags."""
        args = ["tailscale", "up"]
        if auth_key:
            args.append(f"--authkey={auth_key.strip()}")
        if extra_flags:
            args.extend(extra_flags.strip().split())

        if target == "local":
            try:
                res = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=25)
                return {
                    "success": res.returncode == 0,
                    "message": "کلاینت محلی Tailscale متصل شد" if res.returncode == 0 else (res.stderr.strip() or res.stdout.strip() or "خطا")
                }
            except Exception as e:
                return {"success": False, "error": str(e)}
        else:
            if not self.ssh or not self.ssh.is_connected:
                return {"success": False, "error": "SSH متصل نیست"}
            cmd = "tailscale up"
            if auth_key:
                cmd += f" --authkey='{auth_key.strip()}'"
            if extra_flags:
                cmd += f" {extra_flags.strip()}"
            res = self.ssh.execute_command(cmd, sudo=True, timeout=30)
            return {
                "success": res["success"],
                "message": "Tailscale سرور متصل شد" if res["success"] else (res.get("stderr") or res.get("stdout") or "خطا")
            }

    def down(self, target="local"):
        """Runs 'tailscale down'."""
        if target == "local":
            try:
                res = subprocess.run(["tailscale", "down"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
                return {
                    "success": res.returncode == 0,
                    "message": "اتصال Tailscale محلی قطع شد" if res.returncode == 0 else res.stderr.strip()
                }
            except Exception as e:
                return {"success": False, "error": str(e)}
        else:
            if not self.ssh or not self.ssh.is_connected:
                return {"success": False, "error": "SSH متصل نیست"}
            res = self.ssh.execute_command("tailscale down", sudo=True, timeout=10)
            return {
                "success": res["success"],
                "message": "اتصال Tailscale سرور قطع شد" if res["success"] else (res.get("stderr") or "خطا")
            }

    def install_remote(self, sudo_password=None):
        """Installs Tailscale on remote Linux via official script."""
        if not self.ssh or not self.ssh.is_connected:
            return {"success": False, "error": "SSH متصل نیست"}
        cmd = "curl -fsSL https://tailscale.com/install.sh | sh && systemctl enable --now tailscaled"
        res = self.ssh.execute_command(cmd, sudo=True, sudo_password=sudo_password, timeout=120)
        chk = self.ssh.execute_command("which tailscale", timeout=5)
        if chk["success"] and chk["stdout"].strip():
            return {"success": True, "message": "نرم‌افزار Tailscale با موفقیت روی سرور لینوکس نصب شد!"}
        return {"success": False, "error": res.get("stderr") or res.get("stdout") or "خطا در نصب Tailscale"}

    # ----------------- TAILSCALE FUNNEL (PUBLIC INTERNET SHARING) -----------------
    def funnel_get_status(self, target="remote"):
        """Gets current Tailscale Funnel status and public URLs."""
        if target == "remote":
            if not self.ssh or not self.ssh.is_connected:
                return {"success": False, "installed": False, "active": False, "error": "SSH متصل نیست"}
            
            cmd_json = "export PATH=$PATH:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; tailscale funnel status --json 2>/dev/null || tailscale serve status --json 2>/dev/null"
            res_json = self.ssh.execute_command(cmd_json, sudo=True, timeout=8)
            
            cmd_txt = "export PATH=$PATH:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; tailscale funnel status 2>&1 || tailscale serve status 2>&1"
            res_txt = self.ssh.execute_command(cmd_txt, sudo=True, timeout=8)
            
            raw_txt = (res_txt.get("stdout") or res_txt.get("stderr") or "").strip()
            raw_json = (res_json.get("stdout") or "").strip()
        else:
            if not shutil.which("tailscale"):
                return {"success": False, "installed": False, "active": False, "error": "دستور tailscale در ویندوز یافت نشد"}
            
            try:
                res_j = subprocess.run(["tailscale", "funnel", "status", "--json"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=6)
                raw_json = res_j.stdout.strip() if res_j.returncode == 0 else ""
            except Exception:
                raw_json = ""
                
            try:
                res_t = subprocess.run(["tailscale", "funnel", "status"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=6)
                raw_txt = (res_t.stdout or res_t.stderr or "").strip()
            except Exception:
                raw_txt = ""

        return self._parse_funnel_output(raw_json, raw_txt)

    def _parse_funnel_output(self, raw_json: str, raw_txt: str):
        parsed = {
            "success": True,
            "installed": True,
            "active": False,
            "public_url": "",
            "public_port": 443,
            "target_port": "",
            "target_backend": "",
            "allow_funnel": False,
            "raw_text": raw_txt,
            "rules": []
        }

        if "No serve config" in raw_txt or "not running" in raw_txt.lower():
            return parsed

        if raw_json and raw_json != "{}":
            try:
                data = json.loads(raw_json)
                allow_funnel = data.get("AllowFunnel", {}) or {}
                web_configs = data.get("Web", {}) or {}
                tcp_configs = data.get("TCP", {}) or {}

                is_funnel_on = any(v is True for v in allow_funnel.values())
                parsed["allow_funnel"] = is_funnel_on

                for host_port, conf in web_configs.items():
                    handlers = conf.get("Handlers", {}) or {}
                    for path, handler in handlers.items():
                        proxy_dest = handler.get("Proxy", "")
                        is_this_funnel = allow_funnel.get(host_port, False)
                        rule = {
                            "host_port": host_port,
                            "path": path,
                            "proxy": proxy_dest,
                            "is_funnel": is_this_funnel
                        }
                        parsed["rules"].append(rule)
                        if is_this_funnel or is_funnel_on:
                            parsed["active"] = True
                            parsed["public_url"] = f"https://{host_port}{path if path != '/' else ''}"
                            parsed["target_backend"] = proxy_dest
                            if ":" in proxy_dest:
                                parsed["target_port"] = proxy_dest.split(":")[-1].strip("/")

                for p, tconf in tcp_configs.items():
                    if tconf.get("HTTPS"):
                        try:
                            parsed["public_port"] = int(p)
                        except Exception:
                            pass
            except Exception:
                pass

        if not parsed["active"] and raw_txt:
            import re
            url_match = re.search(r"https://[a-zA-Z0-9\.\-]+(?::\d+)?", raw_txt)
            if url_match and ("Funnel on" in raw_txt or "funnel" in raw_txt.lower()):
                parsed["active"] = True
                parsed["public_url"] = url_match.group(0)
                proxy_match = re.search(r"proxy\s+https?://[^\s:]+:(\d+)", raw_txt)
                if proxy_match:
                    parsed["target_port"] = proxy_match.group(1)
                    parsed["target_backend"] = f"http://127.0.0.1:{proxy_match.group(1)}"

        return parsed

    def funnel_start(self, target="remote", local_port=80, public_port=443, path="/"):
        """Enables Tailscale Funnel to route public internet traffic to local service."""
        try:
            l_port = int(local_port)
            if l_port < 1 or l_port > 65535:
                return {"success": False, "error": "شماره پورت داخلی نامعتبر است (باید بین ۱ تا ۶۵۵۳۵ باشد)"}
        except (ValueError, TypeError):
            return {"success": False, "error": "پورت داخلی باید عدد باشد"}

        try:
            p_port = int(public_port)
            if p_port not in (443, 8443, 10000):
                p_port = 443
        except (ValueError, TypeError):
            p_port = 443

        if target == "remote":
            if not self.ssh or not self.ssh.is_connected:
                return {"success": False, "error": "SSH متصل نیست"}

            port_flag = "" if p_port == 443 else f"--https={p_port}"
            cmd = f"export PATH=$PATH:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; tailscale funnel --bg --yes {port_flag} {l_port}"
            res = self.ssh.execute_command(cmd, sudo=True, timeout=25)
            
            status = self.funnel_get_status("remote")
            if status.get("active") or res.get("success"):
                pub_url = status.get("public_url") or "https://[دامنه_تایل‌اسکیل]"
                return {
                    "success": True,
                    "message": f"Tailscale Funnel با موفقیت فعال شد! سرویس پورت {l_port} بر روی اینترنت با آدرس {pub_url} منتشر گردید.",
                    "public_url": pub_url,
                    "status": status
                }
            else:
                err = res.get("stderr") or res.get("stdout") or "خطا در فعال‌سازی Funnel"
                return {"success": False, "error": err, "status": status}
        else:
            if not shutil.which("tailscale"):
                return {"success": False, "error": "دستور tailscale در ویندوز یافت نشد"}

            args = ["tailscale", "funnel", "--bg", "--yes"]
            if p_port != 443:
                args.extend(["--https", str(p_port)])
            args.append(str(l_port))

            try:
                res = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=25)
                status = self.funnel_get_status("local")
                if status.get("active") or res.returncode == 0:
                    pub_url = status.get("public_url") or "https://[دامنه_تایل‌اسکیل]"
                    return {
                        "success": True,
                        "message": f"Tailscale Funnel در ویندوز فعال شد! آدرس: {pub_url}",
                        "public_url": pub_url,
                        "status": status
                    }
                else:
                    return {"success": False, "error": res.stderr.strip() or res.stdout.strip() or "خطا", "status": status}
            except Exception as e:
                return {"success": False, "error": str(e)}

    def funnel_stop(self, target="remote"):
        """Stops and resets Tailscale Funnel / Serve configuration."""
        if target == "remote":
            if not self.ssh or not self.ssh.is_connected:
                return {"success": False, "error": "SSH متصل نیست"}

            cmd = "export PATH=$PATH:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; tailscale funnel reset 2>/dev/null; tailscale serve reset 2>/dev/null"
            res = self.ssh.execute_command(cmd, sudo=True, timeout=15)
            return {
                "success": True,
                "message": "قابلیت Tailscale Funnel با موفقیت متوقف و کلیه فورواردهای اینترنتی ریست شد."
            }
        else:
            if not shutil.which("tailscale"):
                return {"success": False, "error": "دستور tailscale در ویندوز یافت نشد"}

            try:
                subprocess.run(["tailscale", "funnel", "reset"], capture_output=True, text=True, timeout=10)
                subprocess.run(["tailscale", "serve", "reset"], capture_output=True, text=True, timeout=10)
                return {
                    "success": True,
                    "message": "قابلیت Tailscale Funnel در ویندوز متوقف و ریست شد."
                }
            except Exception as e:
                return {"success": False, "error": str(e)}
