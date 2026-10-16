import socket
import re

class PowerManager:
    def __init__(self, ssh_client):
        self.ssh = ssh_client

    def reboot(self, sudo_password=None):
        if not self.ssh.is_connected:
            return {"success": False, "error": "SSH متصل نیست"}
        cmd = "systemctl reboot || reboot"
        # Since server will reboot and close connection, don't wait long
        try:
            self.ssh.execute_command(cmd, sudo=True, sudo_password=sudo_password, timeout=3)
        except Exception:
            pass
        self.ssh.disconnect()
        return {"success": True, "message": "دستور ریبوت با موفقیت ارسال شد. سرور در حال راه‌اندازی مجدد است."}

    def shutdown(self, sudo_password=None):
        if not self.ssh.is_connected:
            return {"success": False, "error": "SSH متصل نیست"}
        cmd = "systemctl poweroff || poweroff"
        try:
            self.ssh.execute_command(cmd, sudo=True, sudo_password=sudo_password, timeout=3)
        except Exception:
            pass
        self.ssh.disconnect()
        return {"success": True, "message": "دستور خاموش شدن ارسال شد. سرور در حال خاموش شدن است."}

    def suspend(self, sudo_password=None):
        if not self.ssh.is_connected:
            return {"success": False, "error": "SSH متصل نیست"}
        cmd = "systemctl suspend"
        try:
            self.ssh.execute_command(cmd, sudo=True, sudo_password=sudo_password, timeout=3)
        except Exception:
            pass
        self.ssh.disconnect()
        return {"success": True, "message": "سیستم به حالت تعلیق (Suspend) رفت."}

    def detect_mac_address(self):
        """Detects physical MAC address of primary interface while connected."""
        if not self.ssh.is_connected:
            return None

        cmd = r"""
python3 -c "
import os, glob
found = []
for p in glob.glob('/sys/class/net/*/address'):
    iface = p.split('/')[4]
    if iface != 'lo' and not iface.startswith('docker') and not iface.startswith('veth') and not iface.startswith('tailscale'):
        with open(p) as f:
            addr = f.read().strip()
            if addr and len(addr) == 17:
                found.append(addr)
if found:
    print(found[0])
" 2>/dev/null
"""
        res = self.ssh.execute_command(cmd, timeout=4)
        if res["success"] and res["stdout"].strip():
            mac = res["stdout"].strip().split("\n")[-1].strip()
            if len(mac) == 17:
                return mac
        return None

    @staticmethod
    def send_magic_packet(mac_address, broadcast_ip="255.255.255.255", port=9):
        """Sends Wake-on-LAN Magic Packet via UDP."""
        try:
            # Clean MAC address
            mac_clean = re.sub(r'[^a-fA-F0-9]', '', mac_address)
            if len(mac_clean) != 12:
                return {"success": False, "error": "فرمت مک آدرس (MAC) نامعتبر است. نمونه صحیح: AA:BB:CC:DD:EE:FF"}

            # Build magic packet: 6 bytes of 0xFF followed by 16 repetitions of MAC
            mac_bytes = bytes.fromhex(mac_clean)
            magic_packet = b'\xff' * 6 + mac_bytes * 16

            # Send via broadcast socket
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.sendto(magic_packet, (broadcast_ip, port))

            return {
                "success": True,
                "message": f"سیگنال روشن شدن (Magic Packet) به مک آدرس {mac_address} ارسال شد."
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
