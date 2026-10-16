import threading
import time
import re
import shlex
import logging

logger = logging.getLogger("ServDock.DockerDownloader")

class DockerDownloadTask:
    def __init__(self, task_id: str, image_name: str):
        self.id = task_id
        self.image = image_name.strip()
        self.status = "queued"  # queued, downloading, completed, failed, cancelled
        self.progress = 0
        self.current_step = "در صف انتظار سیستم لینوکسی..."
        self.layers = {}  # {layer_id: status_str}
        self.total_layers = 0
        self.completed_layers = 0
        self.size_info = ""
        self.logs = []
        self.error = ""
        self.created_at = time.strftime("%H:%M:%S")
        self.started_at = ""
        self.completed_at = ""
        self.cancel_requested = False

    def to_dict(self):
        return {
            "id": self.id,
            "image": self.image,
            "status": self.status,
            "progress": self.progress,
            "current_step": self.current_step,
            "layers": self.layers,
            "total_layers": self.total_layers,
            "completed_layers": self.completed_layers,
            "size_info": self.size_info,
            "logs": self.logs[-50:],
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

class DockerDownloadManager:
    def __init__(self, ssh_client):
        self.ssh = ssh_client
        self.queue = []
        self.active_task = None
        self.lock = threading.Lock()
        self._running = True
        self._counter = 1
        self._worker_thread = threading.Thread(target=self._run_worker, daemon=True, name="ServDock-DockerDownloader")
        self._worker_thread.start()

    def queue_pull(self, image_name: str):
        if not image_name or not str(image_name).strip():
            return {"success": False, "error": "نام ایمیج مشخص نشده است"}
        clean_img = str(image_name).strip()
        if any(c in clean_img for c in " \t\r\n'\"`$<>|;&"):
            return {"success": False, "error": "نام ایمیج شامل کاراکترهای غیرمجاز است"}

        with self.lock:
            # Check if already active or queued
            for t in self.queue:
                if t.image == clean_img and t.status in ["queued", "downloading"]:
                    return {
                        "success": True,
                        "already_queued": True,
                        "task": t.to_dict(),
                        "message": f"ایمیج {clean_img} در حال حاضر در صف یا در حال دانلود است"
                    }

            self._counter += 1
            task_id = f"dl_{int(time.time())}_{self._counter}"
            task = DockerDownloadTask(task_id, clean_img)
            self.queue.append(task)

            return {
                "success": True,
                "already_queued": False,
                "task": task.to_dict(),
                "message": f"ایمیج {clean_img} به صف دانلود لینوکس اضافه شد"
            }

    def get_queue_status(self):
        with self.lock:
            tasks_list = [t.to_dict() for t in self.queue]
            active_dict = self.active_task.to_dict() if self.active_task else None
            queued_cnt = sum(1 for t in self.queue if t.status == "queued")
            downloading_cnt = 1 if self.active_task else 0
            completed_cnt = sum(1 for t in self.queue if t.status == "completed")
            failed_cnt = sum(1 for t in self.queue if t.status in ["failed", "cancelled"])

            return {
                "success": True,
                "tasks": tasks_list,
                "active_task": active_dict,
                "queued_count": queued_cnt,
                "downloading_count": downloading_cnt,
                "completed_count": completed_cnt,
                "failed_count": failed_cnt,
                "total_count": len(self.queue)
            }

    def cancel_task(self, task_id: str):
        with self.lock:
            for t in self.queue:
                if t.id == task_id:
                    if t.status == "queued":
                        t.status = "cancelled"
                        t.current_step = "توسط کاربر در صف لغو شد"
                        t.completed_at = time.strftime("%H:%M:%S")
                        return {"success": True, "message": "دانلود لغو گردید"}
                    elif t.status == "downloading":
                        t.cancel_requested = True
                        t.current_step = "در حال ارسال سیگنال توقف به لینوکس..."
                        return {"success": True, "message": "دستور لغو به فرآیند داکر ارسال شد"}
                    else:
                        return {"success": False, "error": "این دانلود در وضعیت قابل لغو نیست"}
        return {"success": False, "error": "شناسه دانلود یافت نشد"}

    def retry_task(self, task_id: str):
        with self.lock:
            for t in self.queue:
                if t.id == task_id:
                    if t.status in ["failed", "cancelled"]:
                        t.status = "queued"
                        t.progress = 0
                        t.error = ""
                        t.layers = {}
                        t.total_layers = 0
                        t.completed_layers = 0
                        t.size_info = ""
                        t.logs = []
                        t.started_at = ""
                        t.completed_at = ""
                        t.cancel_requested = False
                        t.current_step = "مجدداً در صف دانلود قرار گرفت..."
                        return {"success": True, "message": f"ایمیج {t.image} مجدداً در صف قرار گرفت"}
                    else:
                        return {"success": False, "error": "فقط دانلودهای ناموفق یا لغو شده قابل تلاش مجدد هستند"}
        return {"success": False, "error": "شناسه دانلود یافت نشد"}

    def remove_task(self, task_id: str):
        with self.lock:
            for idx, t in enumerate(self.queue):
                if t.id == task_id:
                    if t.status == "downloading":
                        return {"success": False, "error": "نمی‌توان دانلودی که هم‌اکنون در حال اجراست را حذف کرد. ابتدا آن را لغو کنید."}
                    self.queue.pop(idx)
                    return {"success": True, "message": "آیتم از صف حذف شد"}
        return {"success": False, "error": "شناسه دانلود یافت نشد"}

    def clear_completed(self):
        with self.lock:
            initial_len = len(self.queue)
            self.queue = [t for t in self.queue if t.status not in ["completed", "cancelled"]]
            removed = initial_len - len(self.queue)
            return {"success": True, "removed": removed, "message": f"{removed} مورد پایان‌یافته پاکسازی شد"}

    def _run_worker(self):
        while self._running:
            try:
                task_to_run = None
                with self.lock:
                    for t in self.queue:
                        if t.status == "queued":
                            task_to_run = t
                            break

                if not task_to_run:
                    time.sleep(1)
                    continue

                self._process_download(task_to_run)
            except Exception as e:
                logger.exception(f"Unhandled error in DockerDownloadManager worker: {e}")
                time.sleep(1)

    def _process_download(self, task: DockerDownloadTask):
        with self.lock:
            self.active_task = task
            task.status = "downloading"
            task.started_at = time.strftime("%H:%M:%S")
            task.current_step = "بررسی اتصال به سیستم لینوکسی..."
            task.progress = 5

        # Ensure SSH is ready
        if not self.ssh or not self.ssh.is_connected or not self.ssh.client:
            with self.lock:
                task.status = "failed"
                task.error = "ارتباط SSH با سیستم لینوکسی برقرار نیست"
                task.completed_at = time.strftime("%H:%M:%S")
                self.active_task = None
            return

        transport = self.ssh.client.get_transport()
        if not transport or not transport.is_active():
            with self.lock:
                task.status = "failed"
                task.error = "نشست SSH فعال نیست"
                task.completed_at = time.strftime("%H:%M:%S")
                self.active_task = None
            return

        # Build remote command that executes natively on Linux
        server_info = getattr(self.ssh, "current_server", {}) or {}
        username = server_info.get("username", "")
        password = server_info.get("password", "")

        safe_img = shlex.quote(task.image)
        if username and username != "root" and password:
            safe_pass = shlex.quote(password)
            pull_cmd = (
                f"export PATH=$PATH:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; "
                f"if docker info >/dev/null 2>&1; then "
                f"  docker pull {safe_img}; "
                f"else "
                f"  printf '%s\\n' {safe_pass} | sudo -S -p '' docker pull {safe_img}; "
                f"fi"
            )
        else:
            pull_cmd = (
                f"export PATH=$PATH:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; "
                f"docker pull {safe_img}"
            )

        channel = None
        try:
            channel = transport.open_session()
            channel.set_combine_stderr(True)
            channel.exec_command(pull_cmd)

            with self.lock:
                task.current_step = "اتصال به رجیستری و دریافت مانیفست ایمیج روی سرور..."
                task.progress = 10

            buffer = ""
            while True:
                if task.cancel_requested:
                    with self.lock:
                        task.status = "cancelled"
                        task.current_step = "فرآیند دانلود توسط کاربر متوقف گردید"
                        task.completed_at = time.strftime("%H:%M:%S")
                    try:
                        channel.close()
                    except Exception:
                        pass
                    # Terminate remote docker pull on Linux host
                    try:
                        self.ssh.execute_command(f"pkill -f 'docker pull {safe_img}' 2>/dev/null || true")
                    except Exception:
                        pass
                    break

                if channel.recv_ready():
                    try:
                        chunk = channel.recv(4096).decode("utf-8", errors="replace")
                    except Exception:
                        break
                    if not chunk:
                        break
                    buffer += chunk

                    # Split on both \n and \r since Docker uses \r for in-place terminal updates
                    while "\n" in buffer or "\r" in buffer:
                        pos_n = buffer.find("\n")
                        pos_r = buffer.find("\r")
                        if pos_n != -1 and (pos_r == -1 or pos_n < pos_r):
                            line = buffer[:pos_n]
                            buffer = buffer[pos_n + 1:]
                        else:
                            line = buffer[:pos_r]
                            buffer = buffer[pos_r + 1:]

                        line_clean = line.strip()
                        if line_clean:
                            with self.lock:
                                self._parse_line(task, line_clean)
                elif channel.exit_status_ready():
                    if buffer.strip():
                        with self.lock:
                            self._parse_line(task, buffer.strip())
                    break
                else:
                    time.sleep(0.12)

            if task.status not in ["cancelled", "failed"]:
                exit_code = channel.recv_exit_status()
                with self.lock:
                    if exit_code == 0:
                        task.status = "completed"
                        task.progress = 100
                        task.completed_layers = task.total_layers if task.total_layers > 0 else 1
                        task.current_step = "ایمیج با موفقیت روی سیستم لینوکسی ذخیره و آماده شد"
                        task.completed_at = time.strftime("%H:%M:%S")
                    else:
                        task.status = "failed"
                        task.completed_at = time.strftime("%H:%M:%S")
                        err_candidates = [
                            l for l in task.logs
                            if any(w in l.lower() for w in ["error", "denied", "not found", "timeout", "failed", "unauthorized", "refused", "no such"])
                        ]
                        if err_candidates:
                            task.error = err_candidates[-1]
                        elif task.logs:
                            task.error = task.logs[-1]
                        else:
                            task.error = f"کد خروج غیرعادی {exit_code} از داکر لینوکس"
                        task.current_step = f"خطا در دریافت: {task.error[:60]}"
        except Exception as e:
            with self.lock:
                task.status = "failed"
                task.error = str(e)
                task.current_step = f"خطای سیستمی: {str(e)[:60]}"
                task.completed_at = time.strftime("%H:%M:%S")
        finally:
            if channel:
                try:
                    channel.close()
                except Exception:
                    pass
            with self.lock:
                self.active_task = None

    def _parse_line(self, task: DockerDownloadTask, line: str):
        # Record into recent logs
        task.logs.append(line)
        if len(task.logs) > 60:
            task.logs.pop(0)

        # Match Layer activity: e.g. "a2abf6c4d29d: Pulling fs layer" or "a2abf6c4d29d: Downloading [=====> ] 3.5MB/31.4MB"
        m_layer = re.match(r"^([a-f0-9]{12}):\s*(.*)$", line, re.IGNORECASE)
        if m_layer:
            layer_id = m_layer.group(1).lower()
            action = m_layer.group(2).strip()

            if "pulling fs layer" in action.lower():
                task.layers[layer_id] = "در صف لایه‌ها"
            elif "already exists" in action.lower():
                task.layers[layer_id] = "موجود در سیستم"
            elif "downloading" in action.lower():
                task.layers[layer_id] = "در حال دانلود"
                task.current_step = "در حال دانلود لایه‌ها روی سیستم لینوکسی..."
                m_sz = re.search(r"(\d+(?:\.\d+)?(?:kB|MB|GB)/\d+(?:\.\d+)?(?:kB|MB|GB))", action, re.IGNORECASE)
                if m_sz:
                    task.size_info = m_sz.group(1)
            elif "download complete" in action.lower() or "verifying checksum" in action.lower():
                task.layers[layer_id] = "اعتبارسنجی شد"
                task.current_step = "اعتبارسنجی داده‌های لایه..."
            elif "extracting" in action.lower():
                task.layers[layer_id] = "در حال استخراج"
                task.current_step = "در حال استخراج لایه‌ها در فایل‌سیستم لینوکس..."
            elif "pull complete" in action.lower():
                task.layers[layer_id] = "تکمیل شد"

            # Re-calculate layer progress
            total = len(task.layers)
            task.total_layers = total
            completed = sum(1 for v in task.layers.values() if v in ["موجود در سیستم", "تکمیل شد"])
            task.completed_layers = completed
            extracting = sum(1 for v in task.layers.values() if v == "در حال استخراج")
            downloading = sum(1 for v in task.layers.values() if v in ["در حال دانلود", "اعتبارسنجی شد"])

            if total > 0:
                calc_pct = round((completed * 100 + extracting * 65 + downloading * 25) / total)
                task.progress = max(task.progress, min(97, calc_pct))

        # Check manifest / pulling header
        elif "pulling from" in line.lower():
            task.current_step = "دریافت مانیفست لایه‌ها از ریپازیتوری..."
            task.progress = max(task.progress, 15)

        # Check overall success indicators
        elif any(phrase in line.lower() for phrase in ["downloaded newer image", "image is up to date", "digest: sha256"]):
            task.progress = 100
            task.current_step = "تکمیل شد"
