import os
import subprocess
import threading
import time
import re

BASE64_MEDIA_REGEX = re.compile(r"^[A-Za-z0-9+/=\s]+$")
DEV_VIDEO_REGEX = re.compile(r"^/dev/video[0-9]{1,3}$")

class AVIntercomManager:
    """Manages audio & video intercom between Windows workstation and Linux Mint."""
    def __init__(self, ssh_client):
        self.ssh = ssh_client
        self.active_streams = {}

    def get_hardware_av_devices(self):
        """Discovers capture (microphone), playback (speaker), and video (webcam) devices on Linux."""
        if not self.ssh or not self.ssh.is_connected:
            return {"connected": False, "error": "SSH متصل نیست"}

        cmd = r"""
echo "===RECORDING==="
arecord -l 2>/dev/null | grep -E '^card ' || true
echo "===PLAYBACK==="
aplay -l 2>/dev/null | grep -E '^card ' || true
echo "===CAMERAS==="
ls -1 /dev/video* 2>/dev/null || true
echo "===PULSE_SINKS==="
pactl list short sinks 2>/dev/null || true
echo "===PULSE_SOURCES==="
pactl list short sources 2>/dev/null || true
"""
        res = self.ssh.execute_command(cmd, timeout=6)
        if not res["success"]:
            return {"connected": True, "error": "خطا در خواندن تجهیزات صوتی و تصویری"}

        out = res["stdout"]
        rec_part = out.split("===RECORDING===")[1].split("===PLAYBACK===")[0].strip()
        play_part = out.split("===PLAYBACK===")[1].split("===CAMERAS===")[0].strip()
        cam_part = out.split("===CAMERAS===")[1].split("===PULSE_SINKS===")[0].strip()
        sinks_part = out.split("===PULSE_SINKS===")[1].split("===PULSE_SOURCES===")[0].strip()
        sources_part = out.split("===PULSE_SOURCES===")[1].strip()

        # Parse microphones
        mics = []
        for line in rec_part.split("\n"):
            line = line.strip()
            if line.startswith("card "):
                mics.append(line)

        # Parse speakers
        speakers = []
        for line in play_part.split("\n"):
            line = line.strip()
            if line.startswith("card "):
                speakers.append(line)

        # Parse cameras
        cameras = [c.strip() for c in cam_part.split("\n") if c.strip().startswith("/dev/video")]

        return {
            "connected": True,
            "microphones": mics,
            "speakers": speakers,
            "cameras": cameras,
            "has_mic": len(mics) > 0,
            "has_speaker": len(speakers) > 0,
            "has_camera": len(cameras) > 0
        }

    def play_audio_chunk_on_linux(self, base64_audio_data):
        """Receives audio chunk from Windows mic and streams to Linux speaker."""
        if not self.ssh or not self.ssh.is_connected:
            return {"success": False, "error": "SSH متصل نیست"}

        if not base64_audio_data:
            return {"success": False, "error": "داده صوتی خالی است"}

        # Security check: Ensure strictly valid Base64 to prevent Heredoc breakout / command injection
        clean_b64 = str(base64_audio_data).strip()
        if not BASE64_MEDIA_REGEX.match(clean_b64):
            return {"success": False, "error": "داده صوتی ساختار Base64 معتبر ندارد"}

        # Write to temp file and play with ffplay / paplay / aplay
        script = f"""
export XDG_RUNTIME_DIR=/run/user/1000
export PULSE_SERVER=unix:/run/user/1000/pulse/native
cat << 'EOF' | base64 -d > /tmp/servdock_voice.bin
{clean_b64}
EOF
ffplay -nodisp -autoexit -loglevel quiet /tmp/servdock_voice.bin 2>/dev/null || \
paplay /tmp/servdock_voice.bin 2>/dev/null || \
aplay /tmp/servdock_voice.bin 2>/dev/null || true
rm -f /tmp/servdock_voice.bin
"""
        res = self.ssh.execute_command(script, timeout=8)
        return {"success": res["success"], "message": "صدا در بلندگوی لینوکس پخش شد"}

    def record_linux_audio_clip(self, duration_sec=3):
        """Records a clip from Linux microphone and returns base64 wav to play in Windows."""
        if not self.ssh or not self.ssh.is_connected:
            return {"success": False, "error": "SSH متصل نیست"}

        dur = max(1, min(10, int(duration_sec)))
        script = f"""
export XDG_RUNTIME_DIR=/run/user/1000
export PULSE_SERVER=unix:/run/user/1000/pulse/native
rm -f /tmp/servdock_rec.wav

# Use ffmpeg with pulse or parecord or arecord
ffmpeg -y -f pulse -i default -t {dur} -ar 24000 -ac 1 -c:a pcm_s16le /tmp/servdock_rec.wav 2>/dev/null || \
arecord -q -d {dur} -f cd -t wav /tmp/servdock_rec.wav 2>/dev/null || true

if [ -s /tmp/servdock_rec.wav ]; then
    base64 -w 0 /tmp/servdock_rec.wav
    rm -f /tmp/servdock_rec.wav
fi
"""
        res = self.ssh.execute_command(script, timeout=dur + 6)
        if res["success"] and res["stdout"].strip():
            b64_audio = res["stdout"].strip().split("\n")[-1]
            return {
                "success": True,
                "audio_base64": b64_audio,
                "duration": dur
            }
        return {"success": False, "error": "صدایی از میکروفن لینوکس ضبط نشد یا میکروفن در دسترس نیست"}

    def capture_linux_webcam_frame(self, device="/dev/video0"):
        """Captures a snapshot frame from Linux webcam and returns base64 jpeg image."""
        if not self.ssh or not self.ssh.is_connected:
            return {"success": False, "error": "SSH متصل نیست"}

        # Validate video device string to prevent injection
        dev = str(device).strip() if DEV_VIDEO_REGEX.match(str(device).strip()) else "/dev/video0"
        script = f"""
if [ ! -e "{dev}" ]; then
    echo "NO_DEVICE"
    exit 0
fi
rm -f /tmp/servdock_cam.jpg
ffmpeg -y -f v4l2 -i {dev} -frames:v 1 -q:v 3 /tmp/servdock_cam.jpg 2>/dev/null || true
if [ -f /tmp/servdock_cam.jpg ]; then
    base64 -w 0 /tmp/servdock_cam.jpg
    rm -f /tmp/servdock_cam.jpg
else
    echo "CAPTURE_FAILED"
fi
"""
        res = self.ssh.execute_command(script, timeout=8)
        out = res.get("stdout", "").strip()
        if "NO_DEVICE" in out:
            return {"success": False, "no_device": True, "error": f"وبکم فیزیکی در مسیر {dev} یافت نشد"}
        if "CAPTURE_FAILED" in out or not out:
            return {"success": False, "error": "خطا در عکس‌برداری از وبکم لینوکس"}

        return {
            "success": True,
            "image_base64": out.split("\n")[-1]
        }

    def display_frame_on_linux_screen(self, base64_image_data):
        """Displays Windows webcam image on Linux physical desktop screen in a window."""
        if not self.ssh or not self.ssh.is_connected:
            return {"success": False, "error": "SSH متصل نیست"}

        clean_img_b64 = str(base64_image_data).strip()
        if not clean_img_b64 or not BASE64_MEDIA_REGEX.match(clean_img_b64):
            return {"success": False, "error": "داده تصویر ساختار Base64 معتبر ندارد"}

        script = f"""
export DISPLAY=:0
export XAUTHORITY=$(ls -1 /home/*/.Xauthority 2>/dev/null | head -n 1 || echo "/home/abolfazl/.Xauthority")

cat << 'EOF' | base64 -d > /tmp/servdock_win_cam.jpg
{clean_img_b64}
EOF

# Update display or launch viewer if not running
pkill -f servdock_win_cam 2>/dev/null || true
ffplay -window_title "ServDock Windows Webcam" -x 480 -y 360 -loop 0 -loglevel quiet /tmp/servdock_win_cam.jpg &
disown
"""
        res = self.ssh.execute_command(script, timeout=6)
        return {"success": res["success"], "message": "تصویر روی نمایشگر لینوکس نمایش داده شد"}

    def stop_linux_screen_display(self):
        """Closes webcam display window on Linux screen."""
        if not self.ssh or not self.ssh.is_connected:
            return {"success": False}
        self.ssh.execute_command("pkill -f 'ServDock Windows Webcam' 2>/dev/null; pkill -f servdock_win_cam 2>/dev/null; rm -f /tmp/servdock_win_cam.jpg", timeout=4)
        return {"success": True}

