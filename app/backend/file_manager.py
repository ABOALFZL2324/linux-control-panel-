import os
import stat
import time
import shlex
import re
import urllib.parse
import base64

class RemoteFileManager:
    def __init__(self, ssh_client):
        self.ssh = ssh_client

    def list_directory(self, remote_path=None):
        if not self.ssh.is_connected:
            return {"success": False, "error": "SSH متصل نیست", "items": []}

        sftp = self.ssh.get_sftp()
        if not sftp:
            return {"success": False, "error": "ارتباط SFTP در دسترس نیست", "items": []}

        if not remote_path:
            # Default to user home or current dir
            try:
                remote_path = sftp.normalize(".")
            except Exception:
                remote_path = "/home"

        try:
            attrs = sftp.listdir_attr(remote_path)
            items = []
            for attr in attrs:
                is_dir = stat.S_ISDIR(attr.st_mode)
                items.append({
                    "name": attr.filename,
                    "is_dir": is_dir,
                    "size": attr.st_size if not is_dir else 0,
                    "size_formatted": self._format_size(attr.st_size) if not is_dir else "-",
                    "mtime": attr.st_mtime,
                    "mtime_formatted": time.strftime("%Y-%m-%d %H:%M", time.localtime(attr.st_mtime)),
                    "permissions": oct(attr.st_mode)[-3:]
                })

            # Sort: directories first, then alphabetical
            items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
            return {
                "success": True,
                "current_path": remote_path,
                "items": items
            }
        except Exception as e:
            return {"success": False, "error": str(e), "current_path": remote_path, "items": []}

    def read_file(self, remote_path):
        if not self.ssh.is_connected:
            return {"success": False, "error": "SSH متصل نیست"}
        sftp = self.ssh.get_sftp()
        if not sftp:
            return {"success": False, "error": "ارتباط SFTP در دسترس نیست"}

        try:
            # Check size first, avoid opening huge binary files in text editor
            attr = sftp.stat(remote_path)
            if attr.st_size > 5 * 1024 * 1024:
                return {"success": False, "error": "حجم فایل بیش از ۵ مگابایت است و قابل نمایش در ادیتور نیست."}

            with sftp.open(remote_path, "r") as f:
                content = f.read().decode("utf-8", errors="replace")
            return {"success": True, "content": content, "path": remote_path, "size": attr.st_size}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def save_file(self, remote_path, content):
        if not self.ssh.is_connected:
            return {"success": False, "error": "SSH متصل نیست"}
        sftp = self.ssh.get_sftp()
        if not sftp:
            return {"success": False, "error": "ارتباط SFTP در دسترس نیست"}

        try:
            with sftp.open(remote_path, "w") as f:
                f.write(content.encode("utf-8"))
            return {"success": True, "message": "فایل با موفقیت ذخیره شد"}
        except Exception as e:
            err_str = str(e).lower()
            if "permission denied" in err_str or "[errno 13]" in err_str:
                try:
                    quoted_path = shlex.quote(remote_path)
                    b64_content = base64.b64encode(content.encode("utf-8")).decode("ascii")
                    sudo_cmd = f"echo {b64_content} | base64 -d | sudo tee {quoted_path} >/dev/null"
                    res = self.ssh.execute_sudo_command(sudo_cmd)
                    if res.get("success"):
                        return {"success": True, "message": "فایل با دسترسی مدیر (sudo) ذخیره شد"}
                except Exception as ex:
                    return {"success": False, "error": f"خطا در ذخیره سازی: {str(ex)}"}
            return {"success": False, "error": str(e)}

    def create_item(self, remote_path, item_type="file"):
        if not self.ssh.is_connected:
            return {"success": False, "error": "SSH متصل نیست"}
        sftp = self.ssh.get_sftp()
        if not sftp:
            return {"success": False, "error": "ارتباط SFTP در دسترس نیست"}

        try:
            if item_type == "folder":
                sftp.mkdir(remote_path)
            else:
                with sftp.open(remote_path, "w") as f:
                    f.write(b"")
            return {"success": True, "message": "ایجاد شد"}
        except Exception as e:
            err_str = str(e).lower()
            if "permission denied" in err_str or "[errno 13]" in err_str:
                try:
                    quoted_path = shlex.quote(remote_path)
                    sudo_cmd = f"mkdir -p {quoted_path}" if item_type == "folder" else f"touch {quoted_path}"
                    res = self.ssh.execute_sudo_command(sudo_cmd)
                    if res.get("success"):
                        return {"success": True, "message": "ایجاد شد (با دسترسی sudo)"}
                except Exception as ex:
                    return {"success": False, "error": f"خطا در ایجاد: {str(ex)}"}
            return {"success": False, "error": str(e)}

    def delete_item(self, remote_path, is_dir=False):
        if not self.ssh.is_connected:
            return {"success": False, "error": "SSH متصل نیست"}

        if not remote_path:
            return {"success": False, "error": "مسیر فایل یا پوشه مشخص نشده است"}

        # Security check: Block deletion of critical system root paths
        clean_norm = os.path.normpath(remote_path.strip()).replace("\\", "/")
        RESTRICTED_ROOTS = {"/", "/bin", "/boot", "/dev", "/etc", "/lib", "/lib64", "/proc", "/sys", "/usr", "/var", "/sbin", "/root", "/home"}
        if clean_norm in RESTRICTED_ROOTS:
            return {"success": False, "error": f"مسیر '{clean_norm}' سیستمی و محافظت‌شده است و امکان حذف آن وجود ندارد."}

        # Safe parameter quoting via shlex
        quoted_path = shlex.quote(clean_norm)
        cmd = f"rm -rf -- {quoted_path}"
        res = self.ssh.execute_command(cmd, timeout=15)
        if not res["success"] or "permission denied" in res.get("stderr", "").lower():
            # retry with sudo
            res = self.ssh.execute_sudo_command(cmd, timeout=15)
        return {"success": res["success"], "error": res.get("stderr")}

    def upload_file(self, local_path, remote_dir):
        if not self.ssh.is_connected:
            return {"success": False, "error": "SSH متصل نیست"}
        sftp = self.ssh.get_sftp()
        if not sftp:
            return {"success": False, "error": "ارتباط SFTP در دسترس نیست"}

        try:
            filename = os.path.basename(local_path)
            clean_dir = os.path.normpath(remote_dir).replace("\\", "/")
            remote_path = clean_dir.rstrip("/") + "/" + filename
            sftp.put(local_path, remote_path)
            return {"success": True, "message": f"فایل {filename} با موفقیت آپلود شد"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def download_file(self, remote_path, local_path):
        if not self.ssh.is_connected:
            return {"success": False, "error": "SSH متصل نیست"}
        sftp = self.ssh.get_sftp()
        if not sftp:
            return {"success": False, "error": "ارتباط SFTP در دسترس نیست"}

        try:
            sftp.get(remote_path, local_path)
            return {"success": True, "message": f"فایل با موفقیت روی کامپیوتر شما ذخیره شد"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def download_from_url(self, file_url: str, target_dir: str = None, filename: str = None):
        """Downloads a file from internet directly onto the Linux machine into specified or default Downloads dir."""
        if not self.ssh.is_connected:
            return {"success": False, "error": "SSH متصل نیست"}

        if not file_url:
            return {"success": False, "error": "آدرس لینک مشخص نشده است"}

        clean_url = file_url.strip()
        parsed_url = urllib.parse.urlsplit(clean_url)
        if parsed_url.scheme not in ("http", "https", "ftp") or not parsed_url.netloc:
            return {"success": False, "error": "آدرس لینک نامعتبر است. فقط پروتکل‌های http:// و https:// مجاز می‌باشند."}

        # Prevent control characters or whitespace in URL
        if any(c in clean_url for c in " \t\r\n'\"`$<>|;&"):
            return {"success": False, "error": "آدرس لینک شامل کاراکترهای غیرمجاز است."}

        # Determine target directory
        if not target_dir:
            det_cmd = 'mkdir -p ~/Downloads 2>/dev/null && cd ~/Downloads && pwd'
            res = self.ssh.execute_command(det_cmd, timeout=5)
            if res["success"] and res["stdout"].strip():
                target_dir = res["stdout"].strip()
            else:
                target_dir = "/home"

        clean_dir = os.path.normpath(target_dir.strip()).replace("\\", "/")

        # Prepare safe destination filename
        if not filename:
            base = os.path.basename(parsed_url.path)
            filename = base if base else f"download_{int(time.time())}"

        # Clean filename strictly to alphanumeric and safe symbols
        safe_filename = re.sub(r'[^a-zA-Z0-9_\.\-]', '_', filename).strip("._") or f"file_{int(time.time())}"
        target_path = f"{clean_dir.rstrip('/')}/{safe_filename}"

        q_dir = shlex.quote(clean_dir)
        q_url = shlex.quote(clean_url)
        q_target = shlex.quote(target_path)
        q_tmp = shlex.quote(f"{target_path}.tmp")

        cmd = f"""
mkdir -p {q_dir}
nohup bash -c "
if command -v wget >/dev/null 2>&1; then
    wget -c --timeout=30 --tries=3 {q_url} -O {q_tmp} && mv -f {q_tmp} {q_target}
else
    curl -L -k --connect-timeout 30 --retry 3 {q_url} -o {q_tmp} && mv -f {q_tmp} {q_target}
fi
" >/dev/null 2>&1 &
echo "started"
"""
        res = self.ssh.execute_command(cmd, timeout=8)
        if res["success"]:
            return {
                "success": True,
                "message": f"دانلود با موفقیت آغاز شد! فایل در مسیر {target_path} ذخیره خواهد شد.",
                "target_path": target_path,
                "target_dir": clean_dir,
                "filename": safe_filename
            }
        else:
            return {
                "success": False,
                "error": res.get("stderr") or "خطا در شروع دانلود"
            }

    def _format_size(self, size_bytes):
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 ** 2:
            return f"{round(size_bytes / 1024, 1)} KB"
        elif size_bytes < 1024 ** 3:
            return f"{round(size_bytes / (1024 ** 2), 1)} MB"
        else:
            return f"{round(size_bytes / (1024 ** 3), 2)} GB"
