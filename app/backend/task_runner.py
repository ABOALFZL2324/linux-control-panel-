import time
import json
import uuid
import shlex

class TaskRunner:
    def __init__(self, ssh_client):
        self.ssh = ssh_client

    def run_foreground(self, code_str, args=""):
        if not self.ssh.is_connected:
            return {"success": False, "error": "SSH متصل نیست"}

        # Upload script safely via SFTP
        remote_file = "/tmp/servdock_run.py"
        ok = self.ssh.upload_string_to_file(code_str, remote_file)
        if not ok:
            return {"success": False, "error": "خطا در انتقال کد به سرور"}

        # Safely quote arguments to prevent shell breakout
        safe_args = ""
        if args and args.strip():
            try:
                safe_args = " ".join(shlex.quote(a) for a in shlex.split(args.strip()))
            except Exception:
                safe_args = shlex.quote(args.strip())

        start_time = time.time()
        cmd = f"python3 -u {remote_file} {safe_args}"
        res = self.ssh.execute_command(cmd, timeout=300)
        duration = round((time.time() - start_time) * 1000)

        return {
            "success": res["success"],
            "stdout": res["stdout"],
            "stderr": res["stderr"],
            "code": res["code"],
            "duration_ms": duration
        }

    def run_background(self, code_str, task_name="پایتون تسک"):
        if not self.ssh.is_connected:
            return {"success": False, "error": "SSH متصل نیست"}

        task_id = str(uuid.uuid4())[:8]
        task_dir = f"~/.servdock/tasks/{task_id}"

        # Create dir
        self.ssh.execute_command(f"mkdir -p {task_dir}", timeout=5)

        # Upload via SFTP directly to eliminate heredoc collision/injection risks
        remote_script_path = f".servdock/tasks/{task_id}/script.py"
        up_ok = self.ssh.upload_string_to_file(code_str, remote_script_path)
        if not up_ok:
            return {"success": False, "error": "خطا در آپلود اسکریپت تسک"}

        safe_name_json = json.dumps(task_name or "Task")

        setup_script = f"""
chmod +x {task_dir}/script.py
nohup python3 -u {task_dir}/script.py > {task_dir}/output.log 2>&1 &
PID=$!

python3 -c "
import json, os, time
reg_path = os.path.expanduser('~/.servdock/tasks/registry.json')
try:
    with open(reg_path, 'r', encoding='utf-8') as f: data = json.load(f)
except Exception: data = []
data.append({{
    'id': '{task_id}',
    'name': {safe_name_json},
    'pid': $PID,
    'started_at': int(time.time()),
    'status': 'running'
}})
with open(reg_path, 'w', encoding='utf-8') as f: json.dump(data, f)
"
echo $PID
"""
        res = self.ssh.execute_command(setup_script, timeout=10)
        if res["success"]:
            pid = res["stdout"].strip().split("\n")[-1]
            return {
                "success": True,
                "task_id": task_id,
                "pid": pid,
                "message": f"پردازش در پس‌زمینه با PID {pid} آغاز شد."
            }
        else:
            return {"success": False, "error": res.get("stderr") or "خطا در شروع پس‌زمینه"}

    def list_tasks(self):
        if not self.ssh.is_connected:
            return []

        script = r"""
python3 -c "
import json, os, subprocess
reg_path = os.path.expanduser('~/.servdock/tasks/registry.json')
if not os.path.exists(reg_path):
    print('[]')
    exit(0)
try:
    with open(reg_path, 'r') as f: tasks = json.load(f)
    updated = []
    for t in tasks:
        pid = t.get('pid')
        is_running = False
        if pid:
            try:
                os.kill(int(pid), 0)
                is_running = True
            except OSError:
                is_running = False
        t['is_running'] = is_running
        t['status'] = 'running' if is_running else 'finished'
        updated.append(t)
    print(json.dumps(updated))
except Exception as e:
    print('[]')
"
"""
        res = self.ssh.execute_command(script, timeout=6)
        if res["success"] and res["stdout"].strip():
            try:
                return json.loads(res["stdout"].strip().split("\n")[-1])
            except Exception:
                return []
        return []

    def get_task_output(self, task_id):
        if not self.ssh.is_connected:
            return ""

        cmd = f"tail -n 250 ~/.servdock/tasks/{task_id}/output.log 2>/dev/null || echo '(خروجی هنوز ثبت نشده است)'"
        res = self.ssh.execute_command(cmd, timeout=5)
        return res["stdout"]

    def kill_task(self, task_id, pid):
        if not self.ssh.is_connected:
            return {"success": False, "error": "SSH متصل نیست"}

        cmd = f"kill -9 {pid} 2>/dev/null; echo 'OK'"
        res = self.ssh.execute_command(cmd, timeout=5)
        return {"success": True, "message": "پردازش متوقف شد"}
