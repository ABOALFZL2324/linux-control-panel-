import paramiko
import os
import io
import time
import socket
import shlex

class SSHClientWrapper:
    def __init__(self):
        self.client = None
        self.sftp = None
        self.current_server = None
        self.is_connected = False

    def connect(self, host: str, port: int = 22, username: str = "root", password: str = None, key_path: str = None, timeout: int = 15):
        self.disconnect()
        
        self.client = paramiko.SSHClient()
        known_hosts = os.path.join(os.path.expanduser("~"), ".servdock", "known_hosts")
        try:
            self.client.load_system_host_keys()
            if os.path.exists(known_hosts):
                self.client.load_host_keys(known_hosts)
        except Exception:
            pass
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        connect_kwargs = {
            "hostname": host,
            "port": int(port),
            "username": username,
            "timeout": max(timeout, 15),
            "banner_timeout": 35,
            "auth_timeout": 30,
            "look_for_keys": False,
            "allow_agent": False
        }

        if key_path and os.path.exists(key_path):
            try:
                connect_kwargs["key_filename"] = key_path
            except Exception as e:
                print(f"Error loading key: {e}")

        if password:
            connect_kwargs["password"] = password

        try:
            self.client.connect(**connect_kwargs)
            try:
                os.makedirs(os.path.dirname(known_hosts), exist_ok=True)
                self.client.save_host_keys(known_hosts)
            except Exception:
                pass
            try:
                transport = self.client.get_transport()
                if transport:
                    transport.set_keepalive(15)
            except Exception:
                pass
            self.is_connected = True
            self.current_server = {
                "host": host,
                "port": port,
                "username": username,
                "password": password
            }
            return {"success": True, "message": "اتصال موفقیت‌آمیز بود"}
        except paramiko.AuthenticationException:
            self.disconnect()
            return {"success": False, "message": "خطای احراز هویت: نام کاربری یا کلمه عبور نادرست است"}
        except (socket.timeout, TimeoutError):
            self.disconnect()
            return {"success": False, "message": f"اتصال به {host}:{port} به دلیل اتمام زمان تایم‌اوت ناموفق بود"}
        except Exception as e:
            self.disconnect()
            return {"success": False, "message": f"خطا در برقراری اتصال SSH: {str(e)}"}

    def disconnect(self):
        if self.sftp:
            try:
                self.sftp.close()
            except Exception:
                pass
            self.sftp = None

        if self.client:
            try:
                self.client.close()
            except Exception:
                pass
            self.client = None

        self.is_connected = False
        self.current_server = None

    def execute_command(self, command: str, sudo: bool = False, sudo_password: str = None, timeout: int = 25):
        if not self.is_connected or not self.client:
            return {"success": False, "stdout": "", "stderr": "SSH متصل نیست", "code": -1}

        try:
            transport = self.client.get_transport()
            if not transport or not transport.is_active():
                self.disconnect()
                return {"success": False, "stdout": "", "stderr": "ارتباط SSH قطع شده است (Connection Lost)", "code": -1}

            if sudo and self.current_server and self.current_server.get("username") != "root":
                pass_to_use = sudo_password or self.current_server.get("password") or ""
                safe_pass_arg = shlex.quote(pass_to_use)
                command = f"printf '%s\\n' {safe_pass_arg} | sudo -S -p '' bash -c {shlex.quote(command)}"

            stdin, stdout, stderr = self.client.exec_command(command, timeout=timeout)
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
            exit_code = stdout.channel.recv_exit_status()

            return {
                "success": exit_code == 0,
                "stdout": out,
                "stderr": err,
                "code": exit_code
            }
        except (socket.timeout, TimeoutError):
            return {"success": False, "stdout": "", "stderr": "پاسخ دستور در زمان مشخص شده دریافت نشد (Timeout)", "code": -1}
        except (paramiko.SSHException, EOFError, ConnectionResetError, BrokenPipeError) as e:
            self.disconnect()
            return {"success": False, "stdout": "", "stderr": f"ارتباط با سرور حین اجرای دستور قطع شد: {e}", "code": -1}
        except Exception as e:
            return {"success": False, "stdout": "", "stderr": str(e), "code": -1}

    def get_sftp(self):
        if not self.is_connected or not self.client:
            return None
        transport = self.client.get_transport()
        if not transport or not transport.is_active():
            self.disconnect()
            return None

        if not self.sftp:
            try:
                self.sftp = self.client.open_sftp()
            except Exception as e:
                print(f"Error opening SFTP: {e}")
                self.sftp = None
                return None
        return self.sftp

    def upload_string_to_file(self, content: str, remote_path: str):
        sftp = self.get_sftp()
        if not sftp:
            return False
        try:
            fl = io.BytesIO(content.encode("utf-8"))
            sftp.putfo(fl, remote_path)
            return True
        except Exception as e:
            print(f"Error uploading content: {e}")
            return False

    def upload_file(self, local_path: str, remote_path: str):
        sftp = self.get_sftp()
        if not sftp:
            return False
        try:
            sftp.put(local_path, remote_path)
            return True
        except Exception as e:
            print(f"Error uploading file: {e}")
            return False
