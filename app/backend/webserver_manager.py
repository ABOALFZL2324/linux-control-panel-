import re
import shlex
import json

SAFE_NAME_REGEX = re.compile(r"^[a-zA-Z0-9_\.\-]+$")
DB_NAME_REGEX = re.compile(r"^[a-zA-Z0-9_]+$")
SYSTEM_DBS = {"information_schema", "mysql", "performance_schema", "sys"}

class WebServerManager:
    def __init__(self, ssh_client):
        self.ssh = ssh_client

    def _execute(self, cmd, sudo=True, timeout=20):
        if not self.ssh or not self.ssh.is_connected:
            return {"success": False, "error": "SSH متصل نیست", "stdout": "", "stderr": "SSH متصل نیست"}
        full_cmd = f"export PATH=$PATH:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; {cmd}"
        return self.ssh.execute_command(full_cmd, sudo=sudo, timeout=timeout)

    # ----------------- OVERVIEW & STATUS -----------------
    def get_services_status(self):
        """Fetches status of Apache, Nginx, MySQL/MariaDB, and PHP in one single SSH call."""
        if not self.ssh or not self.ssh.is_connected:
            return {"connected": False, "error": "SSH متصل نیست"}

        probe_script = r"""
export PATH=$PATH:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# 1. Apache
APACHE_INSTALLED=false
APACHE_RUNNING=false
APACHE_VER=""
if which apache2 >/dev/null 2>&1; then
    APACHE_INSTALLED=true
    systemctl is-active --quiet apache2 2>/dev/null && APACHE_RUNNING=true
    APACHE_VER=$(apache2 -v 2>/dev/null | head -n1 | grep -oE 'Apache/[0-9\.]+' | cut -d/ -f2)
elif which httpd >/dev/null 2>&1; then
    APACHE_INSTALLED=true
    systemctl is-active --quiet httpd 2>/dev/null && APACHE_RUNNING=true
    APACHE_VER=$(httpd -v 2>/dev/null | head -n1 | grep -oE 'Apache/[0-9\.]+' | cut -d/ -f2)
fi

# 2. Nginx
NGINX_INSTALLED=false
NGINX_RUNNING=false
NGINX_VER=""
if which nginx >/dev/null 2>&1; then
    NGINX_INSTALLED=true
    systemctl is-active --quiet nginx 2>/dev/null && NGINX_RUNNING=true
    NGINX_VER=$(nginx -v 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1)
fi

# 3. MySQL / MariaDB
MYSQL_INSTALLED=false
MYSQL_RUNNING=false
MYSQL_TYPE="mysql"
MYSQL_VER=""
if which mysql >/dev/null 2>&1 || which mariadb >/dev/null 2>&1; then
    MYSQL_INSTALLED=true
    (systemctl is-active --quiet mysql 2>/dev/null || systemctl is-active --quiet mariadb 2>/dev/null) && MYSQL_RUNNING=true
    MYSQL_OUT=$(mysql --version 2>/dev/null || mariadb --version 2>/dev/null || true)
    if echo "$MYSQL_OUT" | grep -qi "mariadb"; then
        MYSQL_TYPE="mariadb"
    fi
    MYSQL_VER=$(echo "$MYSQL_OUT" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1)
fi

# 4. PHP
PHP_INSTALLED=false
PHP_VER=""
PHP_FPM_RUNNING=false
if which php >/dev/null 2>&1; then
    PHP_INSTALLED=true
    PHP_VER=$(php -v 2>/dev/null | head -n1 | grep -oE 'PHP [0-9\.]+' | awk '{print $2}')
    systemctl list-units --type=service --state=running 2>/dev/null | grep -q "php.*fpm" && PHP_FPM_RUNNING=true
fi

# 5. Listening Ports
PORTS=$(ss -tlpn 2>/dev/null | grep -E ':(80|443|3306|8080)\b' | awk '{print $4}' | awk -F: '{print $NF}' | sort -un | tr '\n' ',' | sed 's/,$//')

# 6. Document Root Stats
DOC_ROOT="/var/www/html"
[ ! -d "$DOC_ROOT" ] && DOC_ROOT="/var/www"
DOC_EXISTS=false
DOC_SIZE="0"
if [ -d "$DOC_ROOT" ]; then
    DOC_EXISTS=true
    DOC_SIZE=$(du -sh "$DOC_ROOT" 2>/dev/null | awk '{print $1}')
fi

echo "APACHE|$APACHE_INSTALLED|$APACHE_RUNNING|$APACHE_VER"
echo "NGINX|$NGINX_INSTALLED|$NGINX_RUNNING|$NGINX_VER"
echo "MYSQL|$MYSQL_INSTALLED|$MYSQL_RUNNING|$MYSQL_TYPE|$MYSQL_VER"
echo "PHP|$PHP_INSTALLED|$PHP_VER|$PHP_FPM_RUNNING"
echo "PORTS|$PORTS"
echo "DOCROOT|$DOC_ROOT|$DOC_EXISTS|$DOC_SIZE"
"""
        res = self._execute(probe_script, sudo=False, timeout=12)
        if not res["success"] or not res["stdout"].strip():
            return {
                "success": False,
                "connected": True,
                "error": res.get("stderr") or "خطا در بررسی وضعیت سرویس‌ها",
                "apache": {"installed": False, "running": False, "version": ""},
                "mysql": {"installed": False, "running": False, "type": "mysql", "version": ""},
                "php": {"installed": False, "version": "", "fpm_running": False},
                "ports": {"port_80": False, "port_443": False, "port_3306": False, "raw": ""}
            }

        data = {
            "success": True,
            "connected": True,
            "apache": {"installed": False, "running": False, "version": "", "service": "apache2"},
            "nginx": {"installed": False, "running": False, "version": "", "service": "nginx"},
            "mysql": {"installed": False, "running": False, "type": "mysql", "version": "", "service": "mysql"},
            "php": {"installed": False, "version": "", "fpm_running": False},
            "ports": {
                "port_80": False,
                "port_443": False,
                "port_3306": False,
                "port_8080": False,
                "raw": ""
            },
            "document_root": {"path": "/var/www/html", "exists": False, "size": "0"}
        }

        for line in res["stdout"].strip().split("\n"):
            line = line.strip()
            parts = line.split("|")
            key = parts[0]
            if key == "APACHE" and len(parts) >= 4:
                data["apache"] = {
                    "installed": parts[1].lower() == "true",
                    "running": parts[2].lower() == "true",
                    "version": parts[3],
                    "service": "apache2"
                }
            elif key == "NGINX" and len(parts) >= 4:
                data["nginx"] = {
                    "installed": parts[1].lower() == "true",
                    "running": parts[2].lower() == "true",
                    "version": parts[3],
                    "service": "nginx"
                }
            elif key == "MYSQL" and len(parts) >= 5:
                data["mysql"] = {
                    "installed": parts[1].lower() == "true",
                    "running": parts[2].lower() == "true",
                    "type": parts[3],
                    "version": parts[4],
                    "service": parts[3]
                }
            elif key == "PHP" and len(parts) >= 4:
                data["php"] = {
                    "installed": parts[1].lower() == "true",
                    "version": parts[2],
                    "fpm_running": parts[3].lower() == "true"
                }
            elif key == "PORTS" and len(parts) >= 2:
                raw_ports = parts[1]
                plist = [p.strip() for p in raw_ports.split(",") if p.strip()]
                data["ports"] = {
                    "port_80": "80" in plist,
                    "port_443": "443" in plist,
                    "port_3306": "3306" in plist,
                    "port_8080": "8080" in plist,
                    "raw": raw_ports
                }
            elif key == "DOCROOT" and len(parts) >= 4:
                data["document_root"] = {
                    "path": parts[1],
                    "exists": parts[2].lower() == "true",
                    "size": parts[3]
                }

        # If Nginx is active instead of Apache, prioritize Nginx for main card
        if data["nginx"]["running"] and not data["apache"]["running"]:
            data["apache"]["service"] = "nginx"
            data["apache"]["running"] = True
            data["apache"]["version"] = data["nginx"]["version"]

        return data

    def service_action(self, service_name: str, action: str):
        """Controls service: start, stop, restart, reload."""
        valid_actions = ["start", "stop", "restart", "reload", "enable", "disable"]
        valid_services = ["apache2", "httpd", "nginx", "mysql", "mariadb"]
        
        srv = str(service_name).strip().lower()
        act = str(action).strip().lower()

        if act not in valid_actions:
            return {"success": False, "error": f"عملیات نامعتبر: {act}"}

        # Check if php-fpm
        if srv.startswith("php") and "fpm" in srv:
            target_service = srv
        elif srv in valid_services:
            target_service = srv
        else:
            return {"success": False, "error": f"سرویس نامعتبر: {srv}"}

        cmd = f"systemctl {act} {shlex.quote(target_service)}"
        res = self._execute(cmd, sudo=True, timeout=25)
        
        status_res = self._execute(f"systemctl is-active {shlex.quote(target_service)}", sudo=False, timeout=5)
        is_active = status_res.get("stdout", "").strip() == "active"

        return {
            "success": res["success"] or (act == "start" and is_active) or (act == "stop" and not is_active),
            "service": target_service,
            "action": act,
            "is_active": is_active,
            "message": f"عملیات {act} بر روی سرویس {target_service} با موفقیت انجام شد." if res["success"] else (res.get("stderr") or res.get("stdout") or "خطا در اجرای عملیات")
        }

    def test_config(self, service_name="apache2"):
        """Checks configuration syntax of Apache or Nginx."""
        if not self.ssh or not self.ssh.is_connected:
            return {
                "success": False,
                "valid": False,
                "error": "ارتباط با سرور لینوکس قطع است. لطفاً ابتدا به سرور SSH متصل شوید.",
                "output": ""
            }

        srv = str(service_name).strip().lower()
        if "apache" in srv or srv == "httpd":
            chk = self._execute("which apache2 >/dev/null 2>&1 || which httpd >/dev/null 2>&1 || [ -x /usr/sbin/apache2 ]", sudo=False, timeout=5)
            if not chk.get("success"):
                return {
                    "success": False,
                    "valid": False,
                    "error": "وب‌سرور آپاچی (apache2) روی این سرور لینوکس نصب نیست.\n\nجهت نصب خودکار، روی دکمه «⚡ نصب ۱ کلیک پکیج LAMP» در بالای صفحه کلیک کنید.",
                    "output": "apache2: command not found"
                }
            cmd = "apache2ctl configtest 2>&1 || /usr/sbin/apache2ctl configtest 2>&1 || apachectl configtest 2>&1 || apache2 -t 2>&1"
        elif "nginx" in srv:
            chk = self._execute("which nginx >/dev/null 2>&1 || [ -x /usr/sbin/nginx ]", sudo=False, timeout=5)
            if not chk.get("success"):
                return {
                    "success": False,
                    "valid": False,
                    "error": "وب‌سرور Nginx روی این سرور لینوکس نصب نیست.",
                    "output": "nginx: command not found"
                }
            cmd = "nginx -t 2>&1 || /usr/sbin/nginx -t 2>&1"
        else:
            return {"success": False, "valid": False, "error": f"سرویس {service_name} پشتیبانی نمی‌شود", "output": ""}

        res = self._execute(cmd, sudo=True, timeout=12)
        output = (res.get("stdout") or res.get("stderr") or "").strip()
        is_ok = "syntax ok" in output.lower() or "syntax is ok" in output.lower() or "successful" in output.lower()
        
        err_msg = ""
        if not is_ok:
            err_msg = output if output else "دستور تست کانفیگ پاسخی برنگرداند یا خطایی در اجرای آن رخ داد."

        return {
            "success": is_ok,
            "valid": is_ok,
            "output": output,
            "error": err_msg
        }

    # ----------------- VIRTUAL HOSTS / APACHE SITES -----------------
    def list_vhosts(self):
        """Lists available and enabled VirtualHosts in Apache / Nginx."""
        probe_script = r"""
if [ -d /etc/apache2/sites-available ]; then
    for f in /etc/apache2/sites-available/*.conf; do
        [ ! -f "$f" ] && continue
        fname=$(basename "$f")
        enabled=false
        [ -f "/etc/apache2/sites-enabled/$fname" ] && enabled=true
        sname=$(grep -E '^\s*ServerName' "$f" | head -n1 | awk '{print $2}')
        salias=$(grep -E '^\s*ServerAlias' "$f" | head -n1 | awk '{$1=""; print $0}' | sed 's/^ //')
        docroot=$(grep -E '^\s*DocumentRoot' "$f" | head -n1 | awk '{print $2}')
        port=$(grep -E '^\s*<VirtualHost' "$f" | head -n1 | grep -oE ':[0-9]+' | tr -d ':')
        [ -z "$port" ] && port="80"
        [ "$fname" = "default-ssl.conf" ] && port="443"
        echo "SITE|$fname|$enabled|$sname|$salias|$docroot|$port|apache"
    done
elif [ -d /etc/nginx/sites-available ]; then
    for f in /etc/nginx/sites-available/*; do
        [ ! -f "$f" ] && continue
        fname=$(basename "$f")
        enabled=false
        [ -f "/etc/nginx/sites-enabled/$fname" ] && enabled=true
        sname=$(grep -E '^\s*server_name' "$f" | head -n1 | sed 's/server_name//;s/;//' | awk '{print $1}')
        docroot=$(grep -E '^\s*root' "$f" | head -n1 | sed 's/root//;s/;//' | awk '{print $1}')
        port=$(grep -E '^\s*listen' "$f" | head -n1 | grep -oE '[0-9]+' | head -n1)
        [ -z "$port" ] && port="80"
        echo "SITE|$fname|$enabled|$sname||$docroot|$port|nginx"
    done
fi
"""
        res = self._execute(probe_script, sudo=True, timeout=12)
        if not res["success"]:
            return {"success": False, "sites": []}

        sites = []
        for line in res["stdout"].strip().split("\n"):
            line = line.strip()
            if line.startswith("SITE|"):
                parts = line.split("|")
                if len(parts) >= 8:
                    sites.append({
                        "filename": parts[1],
                        "enabled": parts[2].lower() == "true",
                        "server_name": parts[3] or parts[1].replace(".conf", ""),
                        "server_alias": parts[4] or "",
                        "document_root": parts[5] or "/var/www/html",
                        "port": parts[6] or "80",
                        "type": parts[7]
                    })
        return {"success": True, "sites": sites, "vhosts": sites}

    def toggle_vhost(self, site_filename: str, enable: bool):
        """Enables or disables a virtual host site."""
        sf = str(site_filename).strip()
        if not sf or not SAFE_NAME_REGEX.match(sf):
            return {"success": False, "error": "نام سایت نامعتبر است"}

        if enable:
            cmd = f"a2ensite {shlex.quote(sf)} && (systemctl reload apache2 2>/dev/null || systemctl restart apache2)"
        else:
            cmd = f"a2dissite {shlex.quote(sf)} && (systemctl reload apache2 2>/dev/null || systemctl restart apache2)"

        res = self._execute(cmd, sudo=True, timeout=15)
        return {
            "success": res["success"],
            "message": f"سایت {sf} با موفقیت {'فعال' if enable else 'غیرفعال'} شد." if res["success"] else (res.get("stderr") or "خطا در تغییر وضعیت سایت")
        }

    def create_vhost(self, domain: str, port=80, document_root="", server_admin="webmaster@localhost"):
        """Creates a new VirtualHost config file and document root."""
        dom = str(domain).strip().lower()
        if not dom or not SAFE_NAME_REGEX.match(dom):
            return {"success": False, "error": "نام دامنه یا شناسه سایت نامعتبر است"}

        doc_root = document_root.strip() or f"/var/www/{dom}"
        try:
            port_num = int(port)
        except Exception:
            port_num = 80

        conf_content = f"""<VirtualHost *:{port_num}>
    ServerAdmin {server_admin}
    ServerName {dom}
    ServerAlias www.{dom}
    DocumentRoot {doc_root}

    <Directory {doc_root}>
        Options Indexes FollowSymLinks
        AllowOverride All
        Require all granted
    </Directory>

    ErrorLog ${{APACHE_LOG_DIR}}/{dom}_error.log
    CustomLog ${{APACHE_LOG_DIR}}/{dom}_access.log combined
</VirtualHost>
"""
        site_filename = f"{dom}.conf"
        target_path = f"/etc/apache2/sites-available/{site_filename}"

        # Write config, mkdir doc_root, enable site and reload
        cmd = f"""
mkdir -p {shlex.quote(doc_root)}
chown -R www-data:www-data {shlex.quote(doc_root)} 2>/dev/null || true
chmod -R 755 {shlex.quote(doc_root)} 2>/dev/null || true

cat << 'EOF' > {shlex.quote(target_path)}
{conf_content}
EOF

# Create a sample index.html if empty
if [ ! -f "{doc_root}/index.html" ] && [ ! -f "{doc_root}/index.php" ]; then
    cat << 'EOF' > "{doc_root}/index.html"
<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
    <meta charset="UTF-8">
    <title>{dom} - به وب‌سایت خوش آمدید</title>
    <style>body{{font-family:sans-serif;text-align:center;padding:50px;background:#0d1117;color:#c9d1d9;}}h1{{color:#58a6ff;}}</style>
</head>
<body>
    <h1>وب‌سایت {dom} فعال شد! 🚀</h1>
    <p>میزبانی شده توسط سرور لینوکس مدیریت‌شده با ServDock</p>
</body>
</html>
EOF
    chown www-data:www-data "{doc_root}/index.html" 2>/dev/null || true
fi

a2ensite {shlex.quote(site_filename)}
systemctl reload apache2 2>/dev/null || systemctl restart apache2
"""
        res = self._execute(cmd, sudo=True, timeout=20)
        return {
            "success": res["success"],
            "site_filename": site_filename,
            "document_root": doc_root,
            "message": f"هاست مجازی {dom} با موفقیت ایجاد و فعال شد!" if res["success"] else (res.get("stderr") or "خطا در ایجاد هاست مجازی")
        }

    def delete_vhost(self, site_filename: str):
        """Disables and removes a VirtualHost config."""
        sf = str(site_filename).strip()
        if not sf or not SAFE_NAME_REGEX.match(sf):
            return {"success": False, "error": "نام سایت نامعتبر است"}

        cmd = f"""
a2dissite {shlex.quote(sf)} 2>/dev/null || true
rm -f /etc/apache2/sites-available/{shlex.quote(sf)}
systemctl reload apache2 2>/dev/null || systemctl restart apache2
"""
        res = self._execute(cmd, sudo=True, timeout=15)
        return {
            "success": res["success"],
            "message": f"هاست {sf} با موفقیت حذف شد." if res["success"] else (res.get("stderr") or "خطا در حذف هاست")
        }

    def get_vhost_content(self, site_filename: str):
        """Reads raw config of a site."""
        sf = str(site_filename).strip()
        if not sf or not SAFE_NAME_REGEX.match(sf):
            return {"success": False, "error": "نام سایت نامعتبر است"}
        cmd = f"cat /etc/apache2/sites-available/{shlex.quote(sf)} 2>/dev/null || cat /etc/nginx/sites-available/{shlex.quote(sf)}"
        res = self._execute(cmd, sudo=True, timeout=10)
        return {"success": res["success"], "content": res.get("stdout", "")}

    def save_vhost_content(self, site_filename: str, content: str):
        """Overwrites site configuration and reloads."""
        sf = str(site_filename).strip()
        if not sf or not SAFE_NAME_REGEX.match(sf):
            return {"success": False, "error": "نام سایت نامعتبر است"}

        safe_path = f"/etc/apache2/sites-available/{sf}"
        script = f"""
cat << 'EOF' > {shlex.quote(safe_path)}
{content}
EOF
apache2ctl configtest >/dev/null 2>&1 && systemctl reload apache2
"""
        res = self._execute(script, sudo=True, timeout=15)
        test_res = self.test_config("apache2")
        return {
            "success": res["success"] and test_res["success"],
            "config_valid": test_res["success"],
            "test_output": test_res.get("output", ""),
            "message": "فایل کانفیگ با موفقیت ذخیره و اعمال شد." if test_res["success"] else f"فایل ذخیره شد اما کانفیگ خطای سینتکس دارد: {test_res.get('output')}"
        }

    # ----------------- MYSQL / MARIADB DATABASES -----------------
    def mysql_list_databases(self, password=None):
        """Lists databases and sizes."""
        cmd = """mysql -N -e "SELECT schema_name, ROUND(SUM(data_length + index_length) / 1024 / 1024, 2) AS size_mb, COUNT(table_name) AS tables_count FROM information_schema.schemata LEFT JOIN information_schema.tables ON tables.table_schema = schemata.schema_name GROUP BY schema_name;" 2>/dev/null"""
        res = self._execute(cmd, sudo=True, timeout=15)
        if not res["success"] or not res["stdout"].strip():
            # Fallback simpler query
            res = self._execute('mysql -N -e "SHOW DATABASES;" 2>/dev/null', sudo=True, timeout=10)
            if not res["success"]:
                return {"success": False, "databases": [], "error": res.get("stderr") or "عدم دسترسی به MySQL"}
            databases = []
            for db in res["stdout"].strip().split("\n"):
                db = db.strip()
                if db:
                    databases.append({
                        "name": db,
                        "size_mb": "0",
                        "tables_count": 0,
                        "is_system": db in SYSTEM_DBS
                    })
            return {"success": True, "databases": databases}

        databases = []
        for line in res["stdout"].strip().split("\n"):
            line = line.strip()
            if line:
                parts = line.split("\t")
                dbname = parts[0]
                size = parts[1] if len(parts) > 1 and parts[1] != "NULL" else "0"
                t_count = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
                databases.append({
                    "name": dbname,
                    "size_mb": size,
                    "tables_count": t_count,
                    "is_system": dbname in SYSTEM_DBS
                })
        return {"success": True, "databases": databases}

    def mysql_create_database(self, db_name: str, collation="utf8mb4_unicode_ci"):
        """Creates a new database."""
        name = str(db_name).strip()
        if not name or not DB_NAME_REGEX.match(name):
            return {"success": False, "error": "نام دیتابیس فقط می‌تواند شامل حروف انگلیسی، اعداد و خط زیر (_) باشد"}

        cmd = f"mysql -e \"CREATE DATABASE IF NOT EXISTS \\`{name}\\` CHARACTER SET utf8mb4 COLLATE {collation};\""
        res = self._execute(cmd, sudo=True, timeout=10)
        return {
            "success": res["success"],
            "message": f"دیتابیس {name} با موفقیت ساخته شد." if res["success"] else (res.get("stderr") or "خطا در ساخت دیتابیس")
        }

    def mysql_drop_database(self, db_name: str):
        """Drops a database."""
        name = str(db_name).strip()
        if name in SYSTEM_DBS:
            return {"success": False, "error": "حذف دیتابیس‌های سیستمی MySQL مجاز نیست"}
        if not name or not DB_NAME_REGEX.match(name):
            return {"success": False, "error": "نام دیتابیس نامعتبر است"}

        cmd = f"mysql -e \"DROP DATABASE IF EXISTS \\`{name}\\`;\""
        res = self._execute(cmd, sudo=True, timeout=10)
        return {
            "success": res["success"],
            "message": f"دیتابیس {name} با موفقیت حذف شد." if res["success"] else (res.get("stderr") or "خطا در حذف دیتابیس")
        }

    def mysql_list_users(self):
        """Lists MySQL users and hosts."""
        cmd = 'mysql -N -e "SELECT User, Host FROM mysql.user;" 2>/dev/null'
        res = self._execute(cmd, sudo=True, timeout=10)
        if not res["success"]:
            return {"success": False, "users": []}
        users = []
        for line in res["stdout"].strip().split("\n"):
            line = line.strip()
            if line:
                parts = line.split("\t")
                if len(parts) >= 2:
                    users.append({"user": parts[0], "host": parts[1]})
        return {"success": True, "users": users}

    def mysql_create_user(self, username: str, password: str, host="localhost", grant_db=None):
        """Creates a MySQL user and optionally grants privileges to a database."""
        user = str(username).strip()
        if not user or not DB_NAME_REGEX.match(user):
            return {"success": False, "error": "نام کاربری نامعتبر است"}
        if not password:
            return {"success": False, "error": "رمز عبور نمی‌تواند خالی باشد"}

        safe_pass = password.replace("'", "\\'")
        h = host.strip() or "localhost"

        script = f"""
mysql -e "CREATE USER IF NOT EXISTS '{user}'@'{h}' IDENTIFIED BY '{safe_pass}';"
"""
        if grant_db:
            gdb = str(grant_db).strip()
            if DB_NAME_REGEX.match(gdb):
                script += f"""
mysql -e "GRANT ALL PRIVILEGES ON \\`{gdb}\\`.* TO '{user}'@'{h}'; FLUSH PRIVILEGES;"
"""
        res = self._execute(script, sudo=True, timeout=15)
        return {
            "success": res["success"],
            "message": f"کاربر {user}@{h} با موفقیت ایجاد شد." if res["success"] else (res.get("stderr") or "خطا در ایجاد کاربر")
        }

    def mysql_backup_database(self, db_name: str, target_dir="/var/backups"):
        """Creates a .sql dump backup of a database."""
        name = str(db_name).strip()
        if not name or not DB_NAME_REGEX.match(name):
            return {"success": False, "error": "نام دیتابیس نامعتبر است"}

        cmd = f"""
mkdir -p {shlex.quote(target_dir)}
BACKUP_FILE="{target_dir}/{name}_$(date +%Y%m%d_%H%M%S).sql"
mysqldump {shlex.quote(name)} > "$BACKUP_FILE"
echo "$BACKUP_FILE"
"""
        res = self._execute(cmd, sudo=True, timeout=60)
        out = res.get("stdout", "").strip().split("\n")[-1]
        return {
            "success": res["success"],
            "backup_file": out,
            "message": f"پشتیبان‌گیری از دیتابیس با موفقیت در {out} ذخیره شد." if res["success"] else (res.get("stderr") or "خطا در بکاپ")
        }

    def mysql_restore_database(self, db_name: str, sql_file_path: str):
        """Restores a database from .sql file."""
        name = str(db_name).strip()
        sql_path = str(sql_file_path).strip()
        if not name or not DB_NAME_REGEX.match(name):
            return {"success": False, "error": "نام دیتابیس نامعتبر است"}
        if not sql_path:
            return {"success": False, "error": "مسیر فایل SQL مشخص نشده است"}

        cmd = f"""
mysql -e "CREATE DATABASE IF NOT EXISTS \\`{name}\\`;"
mysql {shlex.quote(name)} < {shlex.quote(sql_path)}
"""
        res = self._execute(cmd, sudo=True, timeout=90)
        return {
            "success": res["success"],
            "message": f"دیتابیس {name} با موفقیت از فایل بازیابی شد." if res["success"] else (res.get("stderr") or "خطا در بازیابی دیتابیس")
        }

    def mysql_execute_query(self, query: str, database=None):
        """Executes a custom SQL query and returns structured results."""
        q = str(query).strip()
        if not q:
            return {"success": False, "error": "دستور SQL خالی است", "data": [], "rows_count": 0, "query_type": "UNKNOWN"}

        first_word = q.split()[0].upper() if q.split() else "SQL"

        # Add limit if select without limit
        if first_word == "SELECT" and "limit" not in q.lower():
            q += " LIMIT 100"

        db_flag = f"--database={shlex.quote(database)}" if database else ""
        # -B gives tab-delimited output with headers in row 0
        cmd = f"mysql -B {db_flag} -e {shlex.quote(q)}"
        res = self._execute(cmd, sudo=True, timeout=25)

        if not res["success"]:
            err_msg = res.get("stderr") or res.get("stdout") or "خطای اجرای کوئری در MySQL"
            return {
                "success": False,
                "error": err_msg.strip(),
                "query_type": first_word,
                "rows_count": 0,
                "data": []
            }

        stdout = res.get("stdout", "").strip()
        data = []
        if stdout:
            lines = stdout.split("\n")
            if lines:
                headers = [h.strip() for h in lines[0].split("\t")]
                for line in lines[1:]:
                    if not line.strip():
                        continue
                    vals = line.split("\t")
                    while len(vals) < len(headers):
                        vals.append("")
                    row_dict = {}
                    for idx, h in enumerate(headers):
                        row_dict[h] = vals[idx] if idx < len(vals) else ""
                    data.append(row_dict)

        return {
            "success": True,
            "query_type": first_word,
            "rows_count": len(data),
            "data": data,
            "output": stdout or "عملیات با موفقیت انجام شد."
        }

    # ----------------- PHP INFORMATION & INI -----------------
    def get_php_details(self):
        """Gets PHP modules, version, and key php.ini directives."""
        script = r"""
if which php >/dev/null 2>&1; then
    VER=$(php -v | head -n1 | awk '{print $2}')
    EXTS=$(php -m | grep -v '^\[' | tr '\n' ',' | sed 's/,$//')
    UPLOAD=$(php -r "echo ini_get('upload_max_filesize');")
    POST=$(php -r "echo ini_get('post_max_size');")
    MEM=$(php -r "echo ini_get('memory_limit');")
    TIME=$(php -r "echo ini_get('max_execution_time');")
    INI_PATH=$(php --ini 2>/dev/null | grep 'Loaded Configuration File' | awk '{print $NF}')
    echo "PHP|$VER|$UPLOAD|$POST|$MEM|$TIME|$INI_PATH|$EXTS"
else
    echo "NONE"
fi
"""
        res = self._execute(script, sudo=False, timeout=10)
        if not res["success"] or "NONE" in res.get("stdout", ""):
            return {"installed": False}

        line = res["stdout"].strip().split("\n")[-1]
        parts = line.split("|")
        if len(parts) >= 8 and parts[0] == "PHP":
            mem = parts[4] or "128M"
            up = parts[2] or "2M"
            post = parts[3] or "8M"
            t_max = parts[5] or "30"
            return {
                "success": True,
                "installed": True,
                "version": parts[1],
                "upload_max_filesize": up,
                "post_max_size": post,
                "memory_limit": mem,
                "max_execution_time": t_max,
                "ini_path": parts[6],
                "extensions": parts[7].split(",") if parts[7] else [],
                "ini_settings": {
                    "upload_max_filesize": up,
                    "post_max_size": post,
                    "memory_limit": mem,
                    "max_execution_time": t_max
                }
            }
        return {"success": False, "installed": False, "ini_settings": {}}

    # ----------------- 1-CLICK LAMP INSTALLER -----------------
    def install_lamp_stack(self):
        """Installs Apache2, MySQL-Server, PHP and essential modules in 1 click."""
        install_script = """
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y apache2 mysql-server php libapache2-mod-php php-mysql php-curl php-gd php-mbstring php-xml php-zip php-json

systemctl enable apache2
systemctl restart apache2
systemctl enable mysql 2>/dev/null || systemctl enable mariadb
systemctl restart mysql 2>/dev/null || systemctl restart mariadb

which ufw >/dev/null 2>&1 && ufw allow 'Apache Full' 2>/dev/null || true
which ufw >/dev/null 2>&1 && ufw allow 80/tcp 2>/dev/null && ufw allow 443/tcp 2>/dev/null || true
"""
        res = self._execute(install_script, sudo=True, timeout=240)
        status = self.get_services_status()
        return {
            "success": status.get("apache", {}).get("running", False),
            "status": status,
            "message": "پکیج‌های وب‌سرور آپاچی، دیتابیس MySQL و زبان PHP با موفقیت نصب و راه‌اندازی شدند!" if status.get("apache", {}).get("running") else (res.get("stderr") or "خطا در نصب پکیج‌ها")
        }

    # ----------------- LOGS VIEWER -----------------
    def get_logs(self, log_type="apache_error", lines=100):
        """Reads recent logs of web server or mysql."""
        log_files = {
            "apache_error": "/var/log/apache2/error.log",
            "apache_access": "/var/log/apache2/access.log",
            "nginx_error": "/var/log/nginx/error.log",
            "nginx_access": "/var/log/nginx/access.log",
            "mysql_error": "/var/log/mysql/error.log"
        }
        target = log_files.get(log_type, "/var/log/apache2/error.log")
        try:
            l_num = max(10, min(1000, int(lines)))
        except Exception:
            l_num = 100

        cmd = f"tail -n {l_num} {shlex.quote(target)} 2>/dev/null || echo 'فایل لاگ خالی است یا در دسترس نیست.'"
        res = self._execute(cmd, sudo=True, timeout=10)
        return {
            "success": True,
            "log_type": log_type,
            "file": target,
            "logs": res.get("stdout") or res.get("stderr") or ""
        }
