import json
import re
import time

class HardwareControlManager:
    """Controls physical mouse, keyboard, and monitor on Linux Mint,
    and maintains persistence so settings survive system reboot."""
    def __init__(self, ssh_client):
        self.ssh = ssh_client

    def _get_display_env(self):
        # Auto-detect active user XAUTHORITY and DISPLAY
        script = r"""
USER_NAME=$(w -h | awk '{print $1}' | head -n 1)
USER_HOME=$(eval echo "~$USER_NAME")
if [ -z "$USER_NAME" ]; then
    USER_NAME="abolfazl"
    USER_HOME="/home/abolfazl"
fi
DISP=$(ls -1 /tmp/.X11-unix/ 2>/dev/null | grep -E '^X[0-9]+' | sed 's/^X/:/' | head -n 1)
if [ -z "$DISP" ]; then DISP=":0"; fi
XAUTH=$(ls -1 "$USER_HOME/.Xauthority" /home/*/.Xauthority 2>/dev/null | head -n 1)
if [ -z "$XAUTH" ]; then XAUTH="$USER_HOME/.Xauthority"; fi
echo "USER=$USER_NAME"
echo "HOME=$USER_HOME"
echo "DISPLAY=$DISP"
echo "XAUTHORITY=$XAUTH"
"""
        res = self.ssh.execute_command(script, timeout=4)
        env = {
            "USER": "abolfazl",
            "HOME": "/home/abolfazl",
            "DISPLAY": ":0",
            "XAUTHORITY": "/home/abolfazl/.Xauthority"
        }
        if res["success"]:
            for line in res["stdout"].strip().split("\n"):
                if "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
        return env

    def get_hardware_status(self):
        """Returns the current state of physical mouse, keyboard, and monitor."""
        if not self.ssh or not self.ssh.is_connected:
            return {"connected": False, "error": "SSH متصل نیست"}

        env = self._get_display_env()
        cmd = f"""
export DISPLAY={env['DISPLAY']}
export XAUTHORITY={env['XAUTHORITY']}

echo "===INPUTS==="
xinput list --name-only 2>/dev/null | while read name; do
    id=$(xinput list --id-only "$name" 2>/dev/null | head -n 1)
    if [ -n "$id" ]; then
        props=$(xinput list-props "$id" 2>/dev/null | grep 'Device Enabled' | head -n 1)
        enabled="1"
        if echo "$props" | grep -q ":.*0"; then enabled="0"; fi
        echo "$id;;;$name;;;$enabled"
    fi
done

echo "===MONITOR==="
xset q 2>/dev/null | grep 'Monitor is' | head -n 1
"""
        res = self.ssh.execute_command(cmd, timeout=8)
        if not res["success"]:
            return {"connected": True, "error": "خطا در خواندن دستگاه‌های ورودی و مانیتور", "devices": []}

        out = res["stdout"]
        parts = out.split("===MONITOR===")
        inputs_part = parts[0].replace("===INPUTS===", "").strip()
        monitor_part = parts[1].strip() if len(parts) > 1 else ""

        monitor_on = True
        if "Monitor is Off" in monitor_part:
            monitor_on = False

        devices = []
        mice_disabled = False
        kb_disabled = False
        has_mouse = False
        has_kb = False

        for line in inputs_part.split("\n"):
            line = line.strip()
            if not line or ";;;" not in line:
                continue
            bits = line.split(";;;")
            if len(bits) < 3:
                continue
            dev_id, dev_name, dev_enabled = bits[0].strip(), bits[1].strip(), bits[2].strip() == "1"

            # Filter out virtual XTEST devices and power buttons
            lower_name = dev_name.lower()
            if "xtest" in lower_name or "power button" in lower_name or "video bus" in lower_name:
                continue

            dev_type = "other"
            if any(k in lower_name for k in ["mouse", "pointer", "touchpad", "trackball"]):
                dev_type = "mouse"
                has_mouse = True
                if not dev_enabled:
                    mice_disabled = True
            elif any(k in lower_name for k in ["keyboard", "key"]):
                dev_type = "keyboard"
                has_kb = True
                if not dev_enabled:
                    kb_disabled = True

            devices.append({
                "id": dev_id,
                "name": dev_name,
                "enabled": dev_enabled,
                "type": dev_type
            })

        # Load saved persistence state
        saved_state = self._read_saved_persistence_state()

        return {
            "connected": True,
            "monitor_on": monitor_on,
            "mouse_enabled": not mice_disabled if has_mouse else True,
            "keyboard_enabled": not kb_disabled if has_kb else True,
            "devices": devices,
            "persistence": saved_state
        }

    def set_monitor_power(self, power_on: bool):
        """Turns physical monitor display on or off instantly."""
        if not self.ssh or not self.ssh.is_connected:
            return {"success": False, "error": "SSH متصل نیست"}

        env = self._get_display_env()
        arg = "on" if power_on else "off"
        cmd = f"""
export DISPLAY={env['DISPLAY']}
export XAUTHORITY={env['XAUTHORITY']}
xset dpms force {arg}
"""
        res = self.ssh.execute_command(cmd, timeout=5)
        # Update persistence state
        self._update_saved_state("monitor", power_on)

        status_msg = "مانیتور فیزیکی روشن شد" if power_on else "مانیتور فیزیکی خاموش شد (Standby)"
        return {"success": res["success"], "message": status_msg, "power_on": power_on}

    def set_mouse_enabled(self, enable: bool):
        """Enables or disables physical mice connected to the Linux PC."""
        if not self.ssh or not self.ssh.is_connected:
            return {"success": False, "error": "SSH متصل نیست"}

        val = "1" if enable else "0"
        env = self._get_display_env()

        cmd = f"""
export DISPLAY={env['DISPLAY']}
export XAUTHORITY={env['XAUTHORITY']}
xinput list --name-only 2>/dev/null | grep -iE 'mouse|pointer' | grep -iv 'xtest' | while read name; do
    xinput set-prop "$name" "Device Enabled" {val} 2>/dev/null || true
done
"""
        res = self.ssh.execute_command(cmd, timeout=6)
        self._update_saved_state("mouse", enable)
        msg = "موس فیزیکی متصل و فعال شد" if enable else "موس فیزیکی غیرفعال (قطع) شد"
        return {"success": res["success"], "message": msg, "enabled": enable}

    def set_keyboard_enabled(self, enable: bool):
        """Enables or disables physical keyboards connected to the Linux PC."""
        if not self.ssh or not self.ssh.is_connected:
            return {"success": False, "error": "SSH متصل نیست"}

        val = "1" if enable else "0"
        env = self._get_display_env()

        cmd = f"""
export DISPLAY={env['DISPLAY']}
export XAUTHORITY={env['XAUTHORITY']}
xinput list --name-only 2>/dev/null | grep -i 'keyboard' | grep -ivE 'xtest|video|power' | while read name; do
    xinput set-prop "$name" "Device Enabled" {val} 2>/dev/null || true
done
"""
        res = self.ssh.execute_command(cmd, timeout=6)
        self._update_saved_state("keyboard", enable)
        msg = "کیبورد فیزیکی متصل و فعال شد" if enable else "کیبورد فیزیکی غیرفعال (قطع) شد"
        return {"success": res["success"], "message": msg, "enabled": enable}

    def _update_saved_state(self, component: str, enabled: bool):
        """Saves state and ensures autostart script is installed in ~/.config/autostart."""
        state = self._read_saved_persistence_state()
        state[component] = enabled

        state_json = json.dumps(state)
        # Write to ~/.servdock/hardware_state.json
        save_script = f"""
mkdir -p ~/.servdock
cat << 'EOF' > ~/.servdock/hardware_state.json
{state_json}
EOF

mkdir -p ~/.config/autostart
cat << 'EOF' > ~/.servdock/apply_hardware_state.sh
#!/usr/bin/env bash
sleep 3
STATE_FILE="$HOME/.servdock/hardware_state.json"
if [ ! -f "$STATE_FILE" ]; then exit 0; fi

export DISPLAY=:0
export XAUTHORITY="$HOME/.Xauthority"

python3 -c "
import json, subprocess, os
try:
    with open(os.path.expanduser('~/.servdock/hardware_state.json')) as f:
        s = json.load(f)
    if s.get('mouse') is False:
        subprocess.run(['bash', '-c', 'xinput list --name-only | grep -iE \"mouse|pointer\" | grep -iv xtest | while read n; do xinput set-prop \"$n\" \"Device Enabled\" 0 2>/dev/null; done'])
    if s.get('keyboard') is False:
        subprocess.run(['bash', '-c', 'xinput list --name-only | grep -i keyboard | grep -ivE \"xtest|video|power\" | while read n; do xinput set-prop \"$n\" \"Device Enabled\" 0 2>/dev/null; done'])
    if s.get('monitor') is False:
        subprocess.run(['xset', 'dpms', 'force', 'off'])
except Exception: pass
" 2>/dev/null
EOF
chmod +x ~/.servdock/apply_hardware_state.sh

cat << 'EOF' > ~/.config/autostart/servdock-hardware.desktop
[Desktop Entry]
Type=Application
Name=ServDock Hardware Lock
Exec=/bin/bash /home/abolfazl/.servdock/apply_hardware_state.sh
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
EOF
"""
        self.ssh.execute_command(save_script, timeout=6)

    def _read_saved_persistence_state(self):
        cmd = "cat ~/.servdock/hardware_state.json 2>/dev/null || echo '{}'"
        res = self.ssh.execute_command(cmd, timeout=4)
        if res["success"] and res["stdout"].strip():
            try:
                return json.loads(res["stdout"].strip())
            except Exception:
                return {"mouse": True, "keyboard": True, "monitor": True}
        return {"mouse": True, "keyboard": True, "monitor": True}

    def emergency_unlock_all(self):
        """Immediately and unconditionally enables physical mouse, keyboard, monitor,
        and removes all lock autostart files and scheduled safety timers."""
        if not self.ssh or not self.ssh.is_connected:
            return {"success": False, "error": "SSH متصل نیست"}

        env = self._get_display_env()
        script = f"""
export DISPLAY={env['DISPLAY']}
export XAUTHORITY={env['XAUTHORITY']}

# 1. Enable all physical mice
xinput list --name-only 2>/dev/null | grep -iE 'mouse|pointer' | grep -iv 'xtest' | while read name; do
    xinput set-prop "$name" "Device Enabled" 1 2>/dev/null || true
done

# 2. Enable all physical keyboards
xinput list --name-only 2>/dev/null | grep -i 'keyboard' | grep -ivE 'xtest|video|power' | while read name; do
    xinput set-prop "$name" "Device Enabled" 1 2>/dev/null || true
done

# 3. Turn monitor display on
xset dpms force on 2>/dev/null || true

# 4. Remove autostart desktop file & reset state file
rm -f ~/.config/autostart/servdock-hardware.desktop 2>/dev/null || true
mkdir -p ~/.servdock
cat << 'EOF' > ~/.servdock/hardware_state.json
{{"mouse": true, "keyboard": true, "monitor": true}}
EOF

# 5. Cancel any running failsafe dead-man timer
if [ -f /tmp/servdock_timer.pid ]; then
    kill -9 $(cat /tmp/servdock_timer.pid 2>/dev/null) 2>/dev/null || true
    rm -f /tmp/servdock_timer.pid 2>/dev/null || true
fi
rm -f /tmp/servdock_failsafe.timer 2>/dev/null || true

# 6. Ensure standalone emergency script is available on Linux host
cat << 'EOF' > ~/.servdock/emergency_unlock.sh
#!/usr/bin/env bash
export DISPLAY=:0
export XAUTHORITY=$(ls -1 /home/*/.Xauthority 2>/dev/null | head -n 1 || echo "$HOME/.Xauthority")
xinput list --name-only 2>/dev/null | while read name; do
    xinput set-prop "$name" "Device Enabled" 1 2>/dev/null || true
done
xset dpms force on 2>/dev/null || true
rm -f ~/.config/autostart/servdock-hardware.desktop 2>/dev/null || true
echo '{{"mouse": true, "keyboard": true, "monitor": true}}' > ~/.servdock/hardware_state.json
if [ -f /tmp/servdock_timer.pid ]; then
    kill -9 $(cat /tmp/servdock_timer.pid 2>/dev/null) 2>/dev/null || true
    rm -f /tmp/servdock_timer.pid 2>/dev/null || true
fi
rm -f /tmp/servdock_failsafe.timer 2>/dev/null || true
echo "ServDock: All hardware devices have been unlocked successfully."
EOF
chmod +x ~/.servdock/emergency_unlock.sh || true
echo "UNLOCK_DONE"
"""
        res = self.ssh.execute_command(script, timeout=15)
        success = res["success"] or ("UNLOCK_DONE" in res.get("stdout", ""))
        return {
            "success": success,
            "message": "کلیه قطعات سخت‌افزاری (موس، کیبورد و مانیتور) با موفقیت بازگشایی و متصل شدند و قفل ریبوت برداشته شد."
        }

    def set_safety_timer(self, minutes: int = 30):
        """Schedules an automatic background emergency unlock timer on the Linux system.
        If user loses access or does not cancel, Linux will auto-unlock itself after N minutes."""
        if not self.ssh or not self.ssh.is_connected:
            return {"success": False, "error": "SSH متصل نیست"}

        try:
            mins = max(1, min(1440, int(minutes)))
        except (ValueError, TypeError):
            mins = 30

        secs = mins * 60
        target_ts = time.time() + secs
        script = f"""
if [ -f /tmp/servdock_timer.pid ]; then
    kill -9 $(cat /tmp/servdock_timer.pid 2>/dev/null) 2>/dev/null || true
    rm -f /tmp/servdock_timer.pid 2>/dev/null || true
fi
echo "{target_ts}:{mins}" > /tmp/servdock_failsafe.timer

(sleep {secs} && if [ -f /tmp/servdock_failsafe.timer ]; then bash ~/.servdock/emergency_unlock.sh 2>/dev/null || true; rm -f /tmp/servdock_failsafe.timer; fi) </dev/null >/dev/null 2>&1 &
echo $! > /tmp/servdock_timer.pid
echo TIMER_STARTED
"""
        res = self.ssh.execute_command(script, timeout=10)
        success = res["success"] or ("TIMER_STARTED" in res.get("stdout", ""))
        return {
            "success": success,
            "message": f"تایمر ایمنی بازگشت خودکار فعال شد: در صورت عدم لغو، سیستم پس از {mins} دقیقه به طور خودکار موس و کیبورد را بازگشایی خواهد کرد.",
            "minutes": mins
        }

    def cancel_safety_timer(self):
        if not self.ssh or not self.ssh.is_connected:
            return {"success": False, "error": "SSH متصل نیست"}

        script = """
if [ -f /tmp/servdock_timer.pid ]; then
    kill -9 $(cat /tmp/servdock_timer.pid 2>/dev/null) 2>/dev/null || true
    rm -f /tmp/servdock_timer.pid 2>/dev/null || true
fi
rm -f /tmp/servdock_failsafe.timer 2>/dev/null || true
"""
        res = self.ssh.execute_command(script, timeout=4)
        return {"success": True, "message": "تایمر ایمنی بازگشت خودکار لغو شد."}

    def get_safety_timer_status(self):
        if not self.ssh or not self.ssh.is_connected:
            return {"active": False}

        cmd = "cat /tmp/servdock_failsafe.timer 2>/dev/null || true"
        res = self.ssh.execute_command(cmd, timeout=4)
        if res["success"] and res["stdout"].strip() and ":" in res["stdout"].strip():
            try:
                target_ts_str, mins_str = res["stdout"].strip().split(":", 1)
                rem_secs = max(0, int(float(target_ts_str) - time.time()))
                if rem_secs > 0:
                    return {
                        "active": True,
                        "remaining_seconds": rem_secs,
                        "total_minutes": int(mins_str)
                    }
            except Exception:
                pass
        return {"active": False}
