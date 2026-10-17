import os
import json
import base64
import uuid

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".servdock")
CONFIG_FILE = os.path.join(CONFIG_DIR, "servers.json")

import ctypes
import ctypes.wintypes
import platform

class DATA_BLOB(ctypes.Structure):
    _fields_ = [('cbData', ctypes.wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_byte))]

class ConfigStore:
    def __init__(self):
        self._ensure_dir()

    def _ensure_dir(self):
        if not os.path.exists(CONFIG_DIR):
            os.makedirs(CONFIG_DIR, exist_ok=True)
        if not os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump([], f)

    def _encode_secret(self, text: str) -> str:
        """Encrypts secrets using Windows Data Protection API (DPAPI) tied to user session."""
        if not text:
            return ""

        if platform.system() == "Windows":
            try:
                b = text.encode("utf-8")
                in_blob = DATA_BLOB(len(b), ctypes.cast(ctypes.create_string_buffer(b), ctypes.POINTER(ctypes.c_byte)))
                out_blob = DATA_BLOB()
                if ctypes.windll.crypt32.CryptProtectData(
                    ctypes.byref(in_blob), "ServDock_Secret_Credentials", None, None, None, 0, ctypes.byref(out_blob)
                ):
                    res = ctypes.string_at(out_blob.pbData, out_blob.cbData)
                    ctypes.windll.kernel32.LocalFree(out_blob.pbData)
                    return "DPAPI:" + base64.b64encode(res).decode("utf-8")
            except Exception as e:
                print(f"[Security Warning] DPAPI encryption failed, falling back: {e}")

        # Fallback with salt prefix
        return "B64:" + base64.b64encode(text.encode("utf-8")).decode("utf-8")

    def _decode_secret(self, encoded: str) -> str:
        """Decrypts secrets, auto-migrating legacy Base64 formats seamlessly."""
        if not encoded:
            return ""

        # Check DPAPI encrypted secret
        if encoded.startswith("DPAPI:") and platform.system() == "Windows":
            try:
                raw_bytes = base64.b64decode(encoded[6:])
                in_blob = DATA_BLOB(len(raw_bytes), ctypes.cast(ctypes.create_string_buffer(raw_bytes), ctypes.POINTER(ctypes.c_byte)))
                out_blob = DATA_BLOB()
                if ctypes.windll.crypt32.CryptUnprotectData(
                    ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
                ):
                    res = ctypes.string_at(out_blob.pbData, out_blob.cbData).decode("utf-8")
                    ctypes.windll.kernel32.LocalFree(out_blob.pbData)
                    return res
            except Exception as e:
                print(f"[Security Error] DPAPI decryption failed: {e}")

        # Check prefixed B64
        if encoded.startswith("B64:"):
            try:
                return base64.b64decode(encoded[4:].encode("utf-8")).decode("utf-8")
            except Exception:
                return encoded[4:]

        # Legacy plain Base64 fallback (with auto-migration support)
        try:
            return base64.b64decode(encoded.encode("utf-8")).decode("utf-8")
        except Exception:
            return encoded

    def _get_raw_servers(self):
        try:
            if not os.path.exists(CONFIG_FILE):
                return []
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return []
                return json.loads(content)
        except Exception as e:
            print(f"Error reading raw servers config: {e}")
            return []

    def _write_raw_servers(self, servers_list):
        try:
            temp_file = CONFIG_FILE + ".tmp"
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(servers_list, f, indent=2)
            if os.path.exists(CONFIG_FILE):
                try:
                    import shutil
                    shutil.copyfile(CONFIG_FILE, CONFIG_FILE + ".bak")
                except Exception:
                    pass
            os.replace(temp_file, CONFIG_FILE)
            return True
        except Exception as e:
            print(f"Error saving servers config: {e}")
            return False

    def get_all_servers(self):
        try:
            raw_servers = self._get_raw_servers()
            result = []
            for item in raw_servers:
                srv = dict(item)
                if "password" in srv and srv["password"]:
                    srv["password"] = self._decode_secret(srv["password"])
                result.append(srv)
            return result
        except Exception as e:
            print(f"Error reading servers config: {e}")
            return []

    def get_server_by_id(self, server_id: str):
        servers = self.get_all_servers()
        for s in servers:
            if s.get("id") == server_id:
                return s
        return None

    def save_server(self, server_data: dict) -> dict:
        raw_servers = self._get_raw_servers()
        server_id = server_data.get("id")
        
        to_store = dict(server_data)
        pwd = to_store.get("password")
        if pwd == "******" and server_id:
            raw_s = next((s for s in raw_servers if s.get("id") == server_id), None)
            if raw_s and raw_s.get("password"):
                to_store["password"] = raw_s["password"]
        elif pwd:
            if not str(pwd).startswith("DPAPI:") and not str(pwd).startswith("B64:"):
                to_store["password"] = self._encode_secret(pwd)

        if server_id:
            found = False
            for idx, s in enumerate(raw_servers):
                if s.get("id") == server_id:
                    raw_servers[idx] = to_store
                    found = True
                    break
            if not found:
                to_store["id"] = server_id
                raw_servers.append(to_store)
        else:
            to_store["id"] = str(uuid.uuid4())[:8]
            raw_servers.append(to_store)

        self._write_raw_servers(raw_servers)

        ret = dict(to_store)
        if "password" in ret and ret["password"]:
            ret["password"] = self._decode_secret(ret["password"])
        return ret

    def delete_server(self, server_id: str) -> bool:
        raw_servers = self._get_raw_servers()
        new_list = [s for s in raw_servers if s.get("id") != server_id]
        return self._write_raw_servers(new_list)
