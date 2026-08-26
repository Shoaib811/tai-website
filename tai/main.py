import glob
import json
import msvcrt
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import taicloud
import taicrypt

CLOUD_DIR = os.path.join(os.environ.get("OneDrive", r"C:\Users\cccsh\OneDrive"), "TAI Cloud")
MAPPING_FILE = os.path.join(CLOUD_DIR, "mapping.json")
STORAGE_DIR = os.path.join(CLOUD_DIR, "Storage")
CHUNK_SIZE = 100 * 1024
OVERHEAD = taicrypt.NONCE_SIZE + taicrypt.TAG_SIZE
MAX_PLAIN = CHUNK_SIZE - OVERHEAD
MAIN_PY = os.path.abspath(__file__)
STAGING_DIR = os.path.join(tempfile.gettempdir(), "TAI", "staging")

BG = "#0f1318"
PANEL = "#1a1f27"
CARD = "#1e2430"
BORDER = "#2a3040"
FG = "#e8ecf1"
MUTED = "#7a8394"
ACCENT = "#34d058"
ACCENT2 = "#1f6feb"
DANGER = "#f85149"
BADGE_TRIAL = "#8b5cf6"
BADGE_BASIC = "#1f6feb"
BADGE_PRO = "#34d058"
FONT_UI = "Segoe UI"
FONT_MONO = "Consolas"


def unique_path(folder: str, name: str) -> str:
    base, ext = os.path.splitext(name)
    candidate = os.path.join(folder, name)
    counter = 1
    while os.path.exists(candidate):
        candidate = os.path.join(folder, f"{base} ({counter}){ext}")
        counter += 1
    return candidate


def random_cloud_name(original_name: str) -> str:
    ext = os.path.splitext(original_name)[1]
    return f"TAI-{secrets.token_hex(4)}{ext}"


MAP_PART_PREFIX = "TAIMAP-"
MAP_CAP = CHUNK_SIZE - 4
LOCK_FILE = os.path.join(STORAGE_DIR, "~$tai.lock")


@contextmanager
def map_lock(timeout: float = 120.0):
    os.makedirs(STORAGE_DIR, exist_ok=True)
    fh = open(LOCK_FILE, "a+b")
    deadline = time.time() + timeout
    locked = False
    while time.time() < deadline:
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            locked = True
            break
        except OSError:
            time.sleep(0.25)
    if not locked:
        fh.close()
        raise TimeoutError("TAI lock timeout — doosra process busy hai")
    try:
        yield
    finally:
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        fh.close()


def _map_search_paths() -> list[str]:
    return [STORAGE_DIR] + [os.path.join(STORAGE_DIR, d) for d in PART_DIRS]


def load_mapping() -> dict:
    if os.path.exists(MAPPING_FILE):
        try:
            with open(MAPPING_FILE, "r", encoding="utf-8-sig") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            pass
    paths = []
    for d in _map_search_paths():
        paths.extend(glob.glob(os.path.join(d, f"{MAP_PART_PREFIX}*.tpart")))
    paths = sorted(paths)
    if not paths:
        return {}
    key = taicrypt.master_key()
    chunks = []
    for part_path in paths:
        with open(part_path, "rb") as fh:
            raw = fh.read(CHUNK_SIZE)
        real_len = int.from_bytes(raw[:4], "little")
        chunks.append(raw[4 : 4 + real_len])
    blob = taicrypt.unseal(key, b"".join(chunks), "TAI-MAP")
    return json.loads(blob.decode("utf-8"))


def save_mapping(mapping: dict) -> None:
    key = taicrypt.master_key()
    os.makedirs(STORAGE_DIR, exist_ok=True)
    for d in _map_search_paths():
        for old in glob.glob(os.path.join(d, f"{MAP_PART_PREFIX}*.tpart")):
            os.remove(old)
    data = taicrypt.seal(
        key,
        json.dumps(mapping, indent=2, ensure_ascii=False).encode("utf-8"),
        "TAI-MAP",
    )
    dest_dir = os.path.join(STORAGE_DIR, secrets.choice(PART_DIRS))
    serial = 1
    pos = 0
    while True:
        chunk = data[pos : pos + MAP_CAP]
        blob = len(chunk).to_bytes(4, "little") + chunk
        blob = blob + os.urandom(CHUNK_SIZE - len(blob))
        part_path = os.path.join(dest_dir, f"{MAP_PART_PREFIX}{serial:04d}.tpart")
        with open(part_path, "wb") as fh:
            fh.write(blob)
        serial += 1
        pos += MAP_CAP
        if pos >= len(data):
            break
    if os.path.exists(MAPPING_FILE):
        os.remove(MAPPING_FILE)
    balance_ghost_files()


def add_mapping(
    original_name: str,
    cloud_name: str,
    shortcut_path: str,
    size: int | None = None,
    parts: list[dict] | None = None,
) -> None:
    mapping = cloud_load_mapping() if cloud_active() else load_mapping()
    entry = {
        "original": original_name,
        "shortcut": shortcut_path,
    }
    if size is not None:
        entry["size"] = size
    if parts:
        entry["parts"] = parts
    mapping[cloud_name] = entry
    if cloud_active():
        cloud_save_mapping(mapping)
    else:
        save_mapping(mapping)


PART_DIRS = ["A", "B", "C"]


def find_part(name: str) -> str:
    base = os.path.basename(name)
    for d in [STORAGE_DIR] + [os.path.join(STORAGE_DIR, x) for x in PART_DIRS]:
        candidate = os.path.join(d, base)
        if os.path.exists(candidate):
            return candidate
    raise FileNotFoundError(f"Part nahi mila: {base}")


def split_into_parts(file_path: str, cloud_name: str, progress_cb=None) -> list[dict]:
    key = taicrypt.master_key()
    cid = secrets.token_hex(4)
    parts: list[dict] = []
    serial = 1
    os.makedirs(STORAGE_DIR, exist_ok=True)
    total = max(1, (os.path.getsize(file_path) + MAX_PLAIN - 1) // MAX_PLAIN)
    with open(file_path, "rb") as fh:
        while True:
            data = fh.read(MAX_PLAIN)
            if not data:
                break
            dir_name = PART_DIRS[(serial - 1) % len(PART_DIRS)]
            target_dir = os.path.join(STORAGE_DIR, dir_name)
            os.makedirs(target_dir, exist_ok=True)
            part_path = unique_path(target_dir, f"TAIP-{cid}-{serial:04d}.tpart")
            payload = data + os.urandom(MAX_PLAIN - len(data))
            with open(part_path, "wb") as pf:
                pf.write(taicrypt.seal(key, payload, cloud_name))
            parts.append({"name": f"{dir_name}/{os.path.basename(part_path)}", "len": len(data)})
            if progress_cb:
                progress_cb(serial, total)
            serial += 1
    if not parts:
        target_dir = os.path.join(STORAGE_DIR, PART_DIRS[0])
        os.makedirs(target_dir, exist_ok=True)
        part_path = unique_path(target_dir, f"TAIP-{cid}-0001.tpart")
        with open(part_path, "wb") as pf:
            pf.write(taicrypt.seal(key, os.urandom(MAX_PLAIN), cloud_name))
        parts.append({"name": f"A/{os.path.basename(part_path)}", "len": 0})
    return parts


def join_parts(parts: list[dict], out_path: str, cloud_name: str, progress_cb=None) -> None:
    key = taicrypt.master_key()
    total = max(1, len(parts))
    with open(out_path, "wb") as out:
        for i, part in enumerate(parts):
            part_path = find_part(part["name"])
            with open(part_path, "rb") as fh:
                raw = fh.read(CHUNK_SIZE)
            payload = taicrypt.unseal(key, raw, cloud_name)
            out.write(payload[: part["len"]])
            if progress_cb:
                progress_cb(i + 1, total)


def cloud_active() -> bool:
    return taicloud.has_session()


def cloud_upload_single(
    file_path: str,
    cloud_name: str,
    progress_cb=None,
    uploaded_ids: list[str] | None = None,
) -> list[dict]:
    key = taicrypt.master_key()
    with open(file_path, "rb") as fh:
        data = fh.read()
    blob = taicrypt.seal(key, data, cloud_name)
    res = taicloud.api_upload_part(blob)
    if uploaded_ids is not None:
        uploaded_ids.append(res["id"])
    if progress_cb:
        progress_cb(1, 1)
    return [{"id": res["id"], "len": len(data)}]


def cloud_split_into_parts(
    file_path: str,
    cloud_name: str,
    progress_cb=None,
    uploaded_ids: list[str] | None = None,
) -> list[dict]:
    key = taicrypt.master_key()
    total = max(1, (os.path.getsize(file_path) + MAX_PLAIN - 1) // MAX_PLAIN)
    parts: list[dict] = []
    serial = 0
    with open(file_path, "rb") as fh:
        while True:
            data = fh.read(MAX_PLAIN)
            if not data:
                break
            serial += 1
            payload = data + os.urandom(MAX_PLAIN - len(data))
            blob = taicrypt.seal(key, payload, cloud_name)
            res = taicloud.api_upload_part(blob)
            if uploaded_ids is not None:
                uploaded_ids.append(res["id"])
            parts.append({"id": res["id"], "len": len(data)})
            if progress_cb:
                progress_cb(serial, total)
    if not parts:
        blob = taicrypt.seal(key, os.urandom(MAX_PLAIN), cloud_name)
        res = taicloud.api_upload_part(blob)
        if uploaded_ids is not None:
            uploaded_ids.append(res["id"])
        parts.append({"id": res["id"], "len": 0})
    return parts


def cloud_join_parts(parts: list[dict], out_path: str, cloud_name: str, progress_cb=None) -> None:
    key = taicrypt.master_key()
    total = max(1, len(parts))
    with open(out_path, "wb") as out:
        for i, part in enumerate(parts):
            raw = taicloud.api_download_part(part["id"])
            payload = taicrypt.unseal(key, raw, cloud_name)
            out.write(payload[: part["len"]])
            if progress_cb:
                progress_cb(i + 1, total)


def cloud_load_mapping() -> dict:
    try:
        blob = taicloud.api_get_map()
    except taicloud.TaiCloudError as exc:
        if exc.code == "no_map":
            return {}
        raise
    if not blob:
        return {}
    key = taicrypt.master_key()
    return json.loads(taicrypt.unseal(key, blob, "TAI-MAP").decode("utf-8"))


def cloud_save_mapping(mapping: dict) -> None:
    key = taicrypt.master_key()
    blob = taicrypt.seal(
        key,
        json.dumps(mapping, indent=2, ensure_ascii=False).encode("utf-8"),
        "TAI-MAP",
    )
    taicloud.api_put_map(blob)


def cloud_quota() -> dict:
    return taicloud.api_me()


def _ghost_name() -> str:
    return f"TAIP-{secrets.token_hex(4)}-{secrets.randbelow(10000):04d}.tpart"


def dehydrate_parts() -> int:
    count = 0
    for d in [STORAGE_DIR] + [os.path.join(STORAGE_DIR, x) for x in PART_DIRS]:
        if not os.path.isdir(d):
            continue
        result = subprocess.run(
            ["attrib", "+U", "-P", os.path.join(d, "*.tpart")],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            count += 1
    return count


def balance_ghost_files() -> int:
    counts = {}
    for d in PART_DIRS:
        folder = os.path.join(STORAGE_DIR, d)
        os.makedirs(folder, exist_ok=True)
        counts[d] = len([f for f in os.listdir(folder) if f.endswith(".tpart")])
    target = max(counts.values())
    created = 0
    for d in PART_DIRS:
        folder = os.path.join(STORAGE_DIR, d)
        while counts[d] < target:
            path = os.path.join(folder, _ghost_name())
            if os.path.exists(path):
                continue
            with open(path, "wb") as fh:
                fh.write(os.urandom(CHUNK_SIZE))
            counts[d] += 1
            created += 1
    return created


def shuffle_part_names() -> int:
    with map_lock():
        mapping = load_mapping()
        used = set()
        count = 0
        for entry in mapping.values():
            for i, part in enumerate(entry.get("parts", [])):
                base = os.path.basename(part["name"])
                old_path = find_part(base)
                new_name = f"TAIP-{secrets.token_hex(4)}-{i + 1:04d}.tpart"
                new_path = os.path.join(os.path.dirname(old_path), new_name)
                os.rename(old_path, new_path)
                part["name"] = f"{os.path.basename(os.path.dirname(old_path))}/{new_name}"
                used.add(new_path.lower())
                count += 1
        for d in PART_DIRS:
            folder = os.path.join(STORAGE_DIR, d)
            for f in list(os.listdir(folder)):
                if not f.endswith(".tpart"):
                    continue
                full = os.path.join(folder, f)
                if full.lower() in used:
                    continue
                new_path = os.path.join(folder, _ghost_name())
                if not os.path.exists(new_path):
                    os.rename(full, new_path)
                    used.add(new_path.lower())
                    count += 1
        save_mapping(mapping)
        dehydrate_parts()
        return count


def restore_cloud(cloud_name: str, progress_cb=None) -> str:
    if cloud_active():
        mapping = cloud_load_mapping()
        entry = mapping.get(cloud_name)
        if not entry or "parts" not in entry:
            raise FileNotFoundError(f"Mapping/parts nahi mile: {cloud_name}")
        original_name = entry.get("original", cloud_name)
        out_dir = os.path.join(tempfile.gettempdir(), "TAI")
        os.makedirs(out_dir, exist_ok=True)
        out_path = unique_path(out_dir, original_name)
        cloud_join_parts(entry["parts"], out_path, cloud_name, progress_cb)
        return out_path
    with map_lock():
        mapping = load_mapping()
        entry = mapping.get(cloud_name)
        if not entry or "parts" not in entry:
            raise FileNotFoundError(f"Mapping/parts nahi mile: {cloud_name}")
        original_name = entry.get("original", cloud_name)
        out_dir = os.path.join(tempfile.gettempdir(), "TAI")
        os.makedirs(out_dir, exist_ok=True)
        out_path = unique_path(out_dir, original_name)
        join_parts(entry["parts"], out_path, cloud_name, progress_cb)
        return out_path


SHORTCUTS_DIR = r"C:\TAI\Shortcuts"


def save_shortcut_copy(shortcut_path: str) -> str | None:
    try:
        os.makedirs(SHORTCUTS_DIR, exist_ok=True)
        readme = os.path.join(SHORTCUTS_DIR, "README.txt")
        if not os.path.exists(readme):
            with open(readme, "w", encoding="utf-8") as fh:
                fh.write(
                    "TAI Shortcuts Backup\n"
                    "====================\n\n"
                    "- Ye folder OneDrive mein hai: agar PC kho jaye to yahan se\n"
                    "  shortcuts milenge (usi PC pe kaam karenge jispe TAI installed hai).\n"
                    "- Naye PC se data chahiye? To naye PC pe Python + TAI code lao,\n"
                    "  master password use karo, aur parts OneDrive se download karke\n"
                    "  TAI-Crypt format se decrypt karoge.\n"
                    "- Ye shortcuts kabhi bhi delete kar sakte ho - TAI ko farq nahi padta.\n"
                )
        dest = os.path.join(SHORTCUTS_DIR, os.path.basename(shortcut_path))
        shutil.copy2(shortcut_path, dest)
        return dest
    except OSError:
        return None


def create_restore_shortcut(shortcut_path: str, cloud_name: str) -> None:
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    python_exe = pythonw if os.path.exists(pythonw) else sys.executable
    wd = os.path.dirname(MAIN_PY)
    ps_script = os.path.join(tempfile.gettempdir(), "tai_shortcut.ps1")
    with open(ps_script, "w", encoding="utf-8") as fh:
        fh.write(
            "$ws = New-Object -ComObject WScript.Shell\n"
            f"$s = $ws.CreateShortcut('{shortcut_path}')\n"
            f"$s.TargetPath = '{python_exe}'\n"
            f"$s.Arguments = '\"{MAIN_PY}\" --restore \"{cloud_name}\"'\n"
            f"$s.WorkingDirectory = '{wd}'\n"
            "$s.Save()\n"
        )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps_script],
        capture_output=True, text=True,
    )
    try:
        os.remove(ps_script)
    except OSError:
        pass
    if result.returncode != 0 or not os.path.exists(shortcut_path):
        raise RuntimeError(result.stderr.strip() or "Shortcut create nahi hui")


def create_shortcut(shortcut_path: str, target_path: str) -> None:
    sp = shortcut_path.replace("'", "''")
    tp = target_path.replace("'", "''")
    script = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{sp}'); "
        f"$s.TargetPath = '{tp}'; "
        "$s.Save()"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not os.path.exists(shortcut_path):
        raise RuntimeError(result.stderr.strip() or "Shortcut create nahi hui")


def unblock_file(path: str) -> None:
    pp = path.replace("'", "''")
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", f"Unblock-File -LiteralPath '{pp}'"],
        capture_output=True,
        text=True,
    )


def offload_core(
    file_path: str,
    make_shortcut: bool = True,
    log=None,
    progress_cb=None,
) -> dict:
    if not os.path.isfile(file_path):
        raise FileNotFoundError("File exist nahi karti")
    if taicrypt.master_key() is None:
        raise RuntimeError("Key config nahi mila — pehle TAI app kholo")
    if cloud_active():
        return _cloud_offload_core(file_path, make_shortcut, log, progress_cb)
    os.makedirs(CLOUD_DIR, exist_ok=True)

    original_name = os.path.basename(file_path)
    size = os.path.getsize(file_path)
    cloud_target = unique_path(CLOUD_DIR, random_cloud_name(original_name))
    if log:
        log(f"[..] Moving: {original_name}")
    with map_lock():
        shutil.move(file_path, cloud_target)
        cloud_name = os.path.basename(cloud_target)

        shortcut_path = None
        try:
            unblock_file(cloud_target)
            parts = split_into_parts(cloud_target, cloud_name, progress_cb)
            if make_shortcut:
                shortcut_path = file_path + ".lnk"
                create_restore_shortcut(shortcut_path, cloud_name)
                unblock_file(shortcut_path)
                save_shortcut_copy(shortcut_path)
            add_mapping(original_name, cloud_name, shortcut_path or "", size=size, parts=parts)
        except Exception:
            shutil.move(cloud_target, file_path)
            raise
        os.remove(cloud_target)
    result = {
        "cloud_name": cloud_name,
        "original": original_name,
        "size": size,
        "parts": len(parts),
        "shortcut": shortcut_path,
    }
    return result


def find_existing_cloud_file(original_name: str) -> str | None:
    try:
        mapping = cloud_load_mapping()
    except Exception:
        return None
    for cloud_name, entry in mapping.items():
        if entry.get("original") == original_name:
            return cloud_name
    return None


def delete_cloud_file(cloud_name: str) -> None:
    try:
        mapping = cloud_load_mapping()
    except Exception:
        return
    entry = mapping.pop(cloud_name, None)
    if not entry:
        return
    for part in entry.get("parts", []):
        part_id = part.get("id") or part.get("name") if isinstance(part, dict) else part
        if part_id:
            try:
                taicloud.api_delete_part(part_id)
            except taicloud.TaiCloudError:
                pass
    cloud_save_mapping(mapping)
    shortcut = entry.get("shortcut")
    if shortcut and os.path.exists(shortcut):
        try:
            os.remove(shortcut)
        except OSError:
            pass


def _cloud_offload_core(
    file_path: str,
    make_shortcut: bool,
    log=None,
    progress_cb=None,
) -> dict:
    original_name = os.path.basename(file_path)
    size = os.path.getsize(file_path)
    try:
        quota = taicloud.api_me()
    except taicloud.TaiCloudError as exc:
        raise RuntimeError(f"Cloud account check fail: {exc}") from exc

    plan = quota.get("plan", "trial")
    is_pro = plan == "pro"

    existing_cn = find_existing_cloud_file(original_name)
    if existing_cn:
        delete_cloud_file(existing_cn)

    if is_pro:
        est_parts = max(1, (size + MAX_PLAIN - 1) // MAX_PLAIN)
        est_bytes = est_parts * CHUNK_SIZE
    else:
        est_bytes = size + OVERHEAD + 1024
    if quota["used_bytes"] + est_bytes > quota["quota_bytes"]:
        raise RuntimeError(
            f"Quota kam hai! Chahiye ~{est_bytes / (1024**2):.1f} MB, "
            f"bacha hai {(quota['quota_bytes'] - quota['used_bytes']) / (1024**2):.1f} MB"
        )
    os.makedirs(STAGING_DIR, exist_ok=True)
    staged_target = unique_path(STAGING_DIR, random_cloud_name(original_name))
    if log:
        mode_label = "Pro (split)" if is_pro else "Basic (single)"
        log(f"[..] Uploading to TAI Cloud: {original_name} [{mode_label}]")
    shutil.copy2(file_path, staged_target)
    cloud_name = os.path.basename(staged_target)
    uploaded_ids: list[str] = []
    shortcut_path = None
    try:
        if is_pro:
            parts = cloud_split_into_parts(staged_target, cloud_name, progress_cb, uploaded_ids)
        else:
            parts = cloud_upload_single(staged_target, cloud_name, progress_cb, uploaded_ids)
        if make_shortcut:
            tai_shortcuts = r"C:\TAI\Shortcuts"
            os.makedirs(tai_shortcuts, exist_ok=True)
            shortcut_path = os.path.join(tai_shortcuts, original_name + ".lnk")
            create_restore_shortcut(shortcut_path, cloud_name)
            unblock_file(shortcut_path)
            save_shortcut_copy(shortcut_path)
        mapping = cloud_load_mapping()
        mapping[cloud_name] = {
            "original": original_name,
            "shortcut": shortcut_path or "",
            "size": size,
            "parts": parts,
        }
        cloud_save_mapping(mapping)
    except Exception:
        for pid in uploaded_ids:
            try:
                taicloud.api_delete_part(pid)
            except taicloud.TaiCloudError:
                pass
        shutil.move(staged_target, file_path)
        raise
    os.remove(staged_target)
    if os.path.exists(file_path):
        try:
            subprocess.run(
                ["cmd", "/c", "del", "/f", "/q", file_path],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass
    if os.path.exists(file_path):
        try:
            subprocess.run(
                ["cmd", "/c", "attrib", "+h", "+s", file_path],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass
    return {
        "cloud_name": cloud_name,
        "original": original_name,
        "size": size,
        "parts": len(parts),
        "shortcut": shortcut_path,
        "mode": "cloud",
    }


class TaiApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("TAI  |  Tricrypt AI")
        root.geometry("960x600")
        root.minsize(800, 520)
        root.configure(bg=BG)
        os.makedirs(r"C:\TAI\Shortcuts", exist_ok=True)
        os.makedirs(r"C:\TAI\Downloads", exist_ok=True)

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        try:
            style.configure("TProgressbar", background=ACCENT, troughcolor=PANEL)
        except Exception:
            pass
        try:
            style.configure("Treeview", background=PANEL, foreground=FG,
                             fieldbackground=PANEL, font=(FONT_UI, 10), rowheight=28)
            style.configure("Treeview.Heading", background=CARD, foreground=FG,
                             font=(FONT_UI, 10, "bold"), relief="flat")
            style.map("Treeview", background=[("selected", ACCENT2)],
                       foreground=[("selected", "#ffffff")])
        except Exception:
            pass

        self._activity_items: list[tk.Frame] = []

        header = tk.Frame(root, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        header.pack(fill="x")

        left_hdr = tk.Frame(header, bg=CARD)
        left_hdr.pack(side="left", padx=20, pady=12)

        tk.Label(left_hdr, text="TAI", font=(FONT_UI, 24, "bold"),
                 fg=ACCENT, bg=CARD).pack(side="left")
        tk.Label(left_hdr, text="  Tricrypt AI", font=(FONT_UI, 11),
                 fg=MUTED, bg=CARD).pack(side="left", padx=(2, 0), pady=(6, 0))
        tk.Label(left_hdr, text="Cloud Offloader", font=(FONT_UI, 9),
                 fg=MUTED, bg=CARD).pack(side="left", padx=(8, 0), pady=(8, 0))

        right_hdr = tk.Frame(header, bg=CARD)
        right_hdr.pack(side="right", padx=20, pady=12)

        self.email_lbl = tk.Label(right_hdr, text="", font=(FONT_UI, 10),
                                   fg=FG, bg=CARD)
        self.email_lbl.pack(side="left", padx=(0, 10))

        self.plan_badge = tk.Label(right_hdr, text="", font=(FONT_UI, 9, "bold"),
                                    fg="#ffffff", bg=BADGE_TRIAL, padx=8, pady=2)
        self.plan_badge.pack(side="left", padx=(0, 12))

        stor_frame = tk.Frame(right_hdr, bg=CARD)
        stor_frame.pack(side="left", padx=(0, 14))
        tk.Label(stor_frame, text="Storage", font=(FONT_UI, 8),
                 fg=MUTED, bg=CARD).pack(anchor="w")
        self.storage_bar = ttk.Progressbar(stor_frame, maximum=100, length=110,
                                            style="TProgressbar")
        self.storage_bar.pack(anchor="w")
        self.quota_lbl = tk.Label(stor_frame, text="", font=(FONT_UI, 9),
                                   fg=MUTED, bg=CARD)
        self.quota_lbl.pack(anchor="w")

        self.files_btn = tk.Button(
            right_hdr, text="MY FILES", font=(FONT_UI, 10, "bold"),
            fg="#ffffff", bg=ACCENT2, activebackground="#388bfd",
            activeforeground="#ffffff", relief="flat", padx=12, pady=4,
            cursor="hand2", state="disabled", command=self._open_files_dialog,
        )
        self.files_btn.pack(side="left", padx=(0, 6))

        self.login_btn = tk.Button(
            right_hdr, text="LOGIN", font=(FONT_UI, 10, "bold"),
            fg="#ffffff", bg=ACCENT2, activebackground="#388bfd",
            activeforeground="#ffffff", relief="flat", padx=12, pady=4,
            cursor="hand2", command=self._open_login_dialog,
        )
        self.login_btn.pack(side="left", padx=(0, 6))

        self.signup_btn = tk.Button(
            right_hdr, text="SIGNUP", font=(FONT_UI, 10, "bold"),
            fg="#ffffff", bg=ACCENT, activebackground="#2ea043",
            activeforeground="#ffffff", relief="flat", padx=12, pady=4,
            cursor="hand2", command=self._open_signup_dialog,
        )
        self.signup_btn.pack(side="left")

        body = tk.Frame(root, bg=BG)
        body.pack(fill="both", expand=True, padx=0, pady=0)

        welcome_card = tk.Frame(body, bg=CARD, highlightbackground=BORDER,
                                 highlightthickness=1)
        welcome_card.pack(fill="x", padx=20, pady=(16, 8))
        tk.Label(welcome_card, text="Your Cloud is Ready",
                 font=(FONT_UI, 15, "bold"), fg=FG, bg=CARD).pack(
            anchor="w", padx=18, pady=(12, 2))
        tk.Label(welcome_card,
                 text="Select files to securely encrypt and upload to TAI Cloud.",
                 font=(FONT_UI, 10), fg=MUTED, bg=CARD).pack(
            anchor="w", padx=18, pady=(0, 12))

        upload_card = tk.Frame(body, bg=CARD, highlightbackground=BORDER,
                                highlightthickness=1)
        upload_card.pack(fill="x", padx=20, pady=(0, 8))

        up_left = tk.Frame(upload_card, bg=CARD)
        up_left.pack(side="left", padx=18, pady=14)
        ico_lbl = tk.Label(up_left, text="\u2601", font=(FONT_UI, 28),
                            fg=ACCENT, bg=CARD)
        ico_lbl.pack(side="left", padx=(0, 12))
        txt_frame = tk.Frame(up_left, bg=CARD)
        txt_frame.pack(side="left")
        tk.Label(txt_frame, text="Upload Files",
                 font=(FONT_UI, 12, "bold"), fg=FG, bg=CARD).pack(anchor="w")
        tk.Label(txt_frame,
                 text="Choose files from your PC to encrypt and store in the cloud",
                 font=(FONT_UI, 9), fg=MUTED, bg=CARD).pack(anchor="w")

        self.load_btn = tk.Button(
            upload_card, text="Browse Files", font=(FONT_UI, 10, "bold"),
            fg="#ffffff", bg=ACCENT, activebackground="#2ea043",
            activeforeground="#ffffff", relief="flat", padx=18, pady=6,
            cursor="hand2", command=self.pick_file,
        )
        self.load_btn.pack(side="right", padx=18, pady=14)

        act_frame = tk.Frame(body, bg=CARD, highlightbackground=BORDER,
                              highlightthickness=1)
        act_frame.pack(fill="both", expand=True, padx=20, pady=(0, 8))

        act_header = tk.Frame(act_frame, bg=CARD)
        act_header.pack(fill="x", padx=14, pady=(10, 0))
        tk.Label(act_header, text="Recent Activity",
                 font=(FONT_UI, 12, "bold"), fg=FG, bg=CARD).pack(side="left")
        self._act_count_lbl = tk.Label(act_header, text="",
                                        font=(FONT_UI, 9), fg=MUTED, bg=CARD)
        self._act_count_lbl.pack(side="right")

        sep = tk.Frame(act_frame, bg=BORDER, height=1)
        sep.pack(fill="x", padx=14, pady=(6, 0))

        self._act_canvas = tk.Canvas(act_frame, bg=CARD, highlightthickness=0)
        act_scrollbar = tk.Scrollbar(act_frame, orient="vertical",
                                      command=self._act_canvas.yview)
        self._act_inner = tk.Frame(self._act_canvas, bg=CARD)
        self._act_inner.bind("<Configure>",
                              lambda e: self._act_canvas.configure(
                                  scrollregion=self._act_canvas.bbox("all")))
        self._act_canvas.create_window((0, 0), window=self._act_inner, anchor="nw")
        self._act_canvas.configure(yscrollcommand=act_scrollbar.set)
        self._act_canvas.pack(side="left", fill="both", expand=True, padx=(14, 0), pady=4)
        act_scrollbar.pack(side="right", fill="y", padx=(0, 14), pady=4)

        self._act_canvas.bind("<Enter>",
                               lambda e: self._act_canvas.bind_all(
                                   "<MouseWheel>", self._on_act_scroll))
        self._act_canvas.bind("<Leave>",
                               lambda e: self._act_canvas.unbind_all("<MouseWheel>"))

        self.status = tk.Label(
            root, text="Ready. Files C:\\TAI folder se upload karo (OneDrive se bahar).",
            font=(FONT_UI, 9), fg=MUTED, bg=BG, anchor="w",
        )
        self.status.pack(fill="x", padx=20, pady=(0, 6))

        self.log("TAI started.")
        self.log(f"Cloud location: {CLOUD_DIR}")
        self.refresh_account_ui()

    def _on_act_scroll(self, event):
        self._act_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def log(self, msg: str) -> None:
        if "[OK]" in msg and "Cloud pe gayi" in msg:
            name = msg.split(":", 1)[-1].strip() if ":" in msg else msg
            self._add_activity("\u2713", "Upload completed", name, ACCENT)
        elif "[OK]" in msg and "Shortcut" in msg:
            path = msg.split(":", 1)[-1].strip() if ":" in msg else msg
            self._add_activity("\u2713", "Shortcut created", path, ACCENT)
        elif "[ERROR]" in msg:
            detail = msg.replace("[ERROR]", "").strip()
            self._add_activity("\u2717", "Error", detail, DANGER)
        elif "[MAP]" in msg:
            detail = msg.replace("[MAP]", "").strip()
            self._add_activity("\u2192", "File mapped", detail, ACCENT2)
        elif "[CUT]" in msg:
            detail = msg.replace("[CUT]", "").strip()
            self._add_activity("\u2699", "Processing", detail, MUTED)
        elif "[CLOUD]" in msg:
            detail = msg.replace("[CLOUD]", "").strip()
            self._add_activity("\u2601", "Cloud", detail, ACCENT2)
        else:
            self._add_activity("\u2022", "Info", msg, MUTED)

    def _add_activity(self, icon: str, title: str, subtitle: str, color: str) -> None:
        item = tk.Frame(self._act_inner, bg=CARD)
        item.pack(fill="x", padx=10, pady=3)

        ico = tk.Label(item, text=icon, font=(FONT_UI, 12), fg=color, bg=CARD, width=3)
        ico.pack(side="left", padx=(0, 6))

        txt_frame = tk.Frame(item, bg=CARD)
        txt_frame.pack(side="left", fill="x", expand=True)
        tk.Label(txt_frame, text=title, font=(FONT_UI, 10, "bold"),
                 fg=FG, bg=CARD).pack(anchor="w")
        tk.Label(txt_frame, text=subtitle, font=(FONT_MONO, 9),
                 fg=MUTED, bg=CARD, anchor="w").pack(anchor="w")

        self._activity_items.append(item)
        self._act_count_lbl.configure(text=f"{len(self._activity_items)} events")
        self._act_canvas.update_idletasks()
        self._act_canvas.yview_moveto(1.0)

    def _show_progress(self, title: str):
        win = tk.Toplevel(self.root)
        win.title(title)
        win.geometry("420x130")
        win.resizable(False, False)
        win.configure(bg=CARD)

        inner = tk.Frame(win, bg=CARD)
        inner.pack(fill="both", expand=True, padx=16, pady=12)

        tk.Label(inner, text=title, font=(FONT_UI, 11, "bold"),
                 fg=FG, bg=CARD).pack(anchor="w")
        lbl = tk.Label(inner, text="0 / 0 parts", font=(FONT_UI, 9),
                       fg=MUTED, bg=CARD)
        lbl.pack(anchor="w", pady=(4, 6))
        bar = ttk.Progressbar(inner, maximum=100, length=370,
                               style="TProgressbar")
        bar.pack(fill="x")
        pct = tk.Label(inner, text="0%", font=(FONT_UI, 10, "bold"),
                       fg=ACCENT, bg=CARD)
        pct.pack(anchor="e", pady=(4, 0))

        def cb(done: int, total: int) -> None:
            val = int(done * 100 / max(total, 1))
            bar["value"] = val
            lbl.config(text=f"{done} / {total} parts processed")
            pct.config(text=f"{val}%")
            self.root.update_idletasks()
            self.root.update()

        return win, cb

    def refresh_account_ui(self) -> None:
        if cloud_active():
            email = taicloud.account_email() or "cloud"
            self.email_lbl.configure(text=email)
            self.login_btn.configure(text="LOGOUT", command=self._do_logout)
            self.signup_btn.configure(state="disabled")
            self.files_btn.configure(state="normal")
            try:
                q = taicloud.api_me()
                used_gb = q["used_bytes"] / (1024**3)
                quota_gb = q["quota_bytes"] / (1024**3)
                pct_val = min(100, (q["used_bytes"] / max(q["quota_bytes"], 1)) * 100)
                self.storage_bar["value"] = pct_val
                bar_color = DANGER if pct_val > 90 else ACCENT
                self.storage_bar.configure(style="TProgressbar")
                self.quota_lbl.configure(
                    text=f"{used_gb:.2f} / {quota_gb:.0f} GB", fg=MUTED)
                plan = q.get("plan", "trial")
                plan_labels = {"trial": "TRIAL", "basic": "BASIC", "pro": "PRO"}
                plan_colors = {"trial": BADGE_TRIAL, "basic": BADGE_BASIC, "pro": BADGE_PRO}
                self.plan_badge.configure(
                    text=plan_labels.get(plan, plan).upper(),
                    bg=plan_colors.get(plan, BADGE_TRIAL),
                )
            except taicloud.TaiCloudError:
                self.quota_lbl.configure(text="offline", fg=DANGER)
                self.plan_badge.configure(text="", bg=CARD)
                self.storage_bar["value"] = 0
        else:
            self.email_lbl.configure(text="")
            self.login_btn.configure(text="LOGIN", command=self._open_login_dialog)
            self.signup_btn.configure(text="SIGNUP", state="normal",
                                      command=self._open_signup_dialog)
            self.files_btn.configure(state="disabled")
            self.quota_lbl.configure(text="")
            self.plan_badge.configure(text="", bg=CARD)
            self.storage_bar["value"] = 0

    def _open_files_dialog(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("TAI Cloud — Your Encrypted Files")
        win.geometry("760x480")
        win.resizable(False, False)
        win.configure(bg=CARD)

        tk.Label(
            win, text="Your Encrypted Files",
            font=(FONT_UI, 14, "bold"), fg=FG, bg=CARD,
        ).pack(pady=(16, 4))

        tk.Label(
            win,
            text="These are random encrypted fragments stored on the server.\n"
                 "Only your password can make them readable.",
            font=(FONT_UI, 10), fg=MUTED, bg=CARD, justify="center",
        ).pack(pady=(0, 12))

        summary_lbl = tk.Label(win, text="Loading...", font=(FONT_UI, 10),
                                fg=MUTED, bg=CARD)
        summary_lbl.pack(pady=(0, 8))

        tree_frame = tk.Frame(win, bg=BORDER)
        tree_frame.pack(padx=20, fill="both", expand=True)

        cols = ("part_id", "size_kb", "date")
        tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=12)
        tree.heading("part_id", text="Part ID (random)")
        tree.heading("size_kb", text="Size")
        tree.heading("date", text="Uploaded")
        tree.column("part_id", width=360, anchor="w")
        tree.column("size_kb", width=100, anchor="e")
        tree.column("date", width=180, anchor="center")
        tree.pack(fill="both", expand=True)

        tk.Button(
            win, text="Close", font=(FONT_UI, 10, "bold"), fg="#ffffff",
            bg=ACCENT2, activebackground="#388bfd", relief="flat",
            padx=20, pady=5, cursor="hand2", command=win.destroy,
        ).pack(pady=12)

        def _load() -> None:
            try:
                data = taicloud.api_my_files()
            except taicloud.TaiCloudError as exc:
                messagebox.showerror("TAI Cloud", f"Files load nahi ho saki:\n{exc}")
                win.destroy()
                return
            total_mb = data["used_bytes"] / (1024**2)
            quota_gb = data["quota_bytes"] / (1024**3)
            n = len(data["parts"])
            plan = data["plan"].upper()
            exp = data.get("plan_expires_at")
            exp_txt = f"  |  expires: {str(exp)[:10]}" if exp else ""
            summary_lbl.configure(
                text=f"{data['email']}  |  {plan}{exp_txt}  |  {n} parts  |  {total_mb:.1f} MB of {quota_gb:.0f} GB",
                fg=FG,
            )
            for p in data["parts"]:
                kb = p["size"] / 1024
                size_txt = f"{kb:.0f} KB"
                tree.insert("", "end", values=(p["id"], size_txt, str(p["created_at"])[:16]))
            if n == 0:
                tree.insert("", "end", values=("No files stored yet", "-", "-"))

        win.after(100, _load)

    def _do_logout(self) -> None:
        if messagebox.askyesno(
            "TAI Cloud", f"Logout from {taicloud.account_email()}?"
        ):
            taicloud.clear_session()
            self.refresh_account_ui()
            self.log("[CLOUD] Logout done.")

    def _open_login_dialog(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("TAI Cloud — Login")
        win.geometry("420x310")
        win.resizable(False, False)
        win.configure(bg=CARD)
        win.grab_set()

        tk.Label(
            win, text="Login to TAI Cloud", font=(FONT_UI, 14, "bold"),
            fg=FG, bg=CARD,
        ).pack(pady=(18, 10))

        form = tk.Frame(win, bg=CARD)
        form.pack(fill="x", padx=30)

        tk.Label(form, text="Email", font=(FONT_UI, 10),
                 fg=MUTED, bg=CARD).pack(anchor="w")
        email_e = tk.Entry(form, font=(FONT_UI, 11), bg=BG, fg=FG,
                           insertbackground=FG, relief="flat")
        email_e.pack(fill="x", ipady=4, pady=(2, 6))

        tk.Label(form, text="Password", font=(FONT_UI, 10),
                 fg=MUTED, bg=CARD).pack(anchor="w")
        pass_frame = tk.Frame(form, bg=BG)
        pass_frame.pack(fill="x", pady=(2, 4))
        pass_e = tk.Entry(pass_frame, font=(FONT_UI, 11), bg=BG, fg=FG,
                          show="\u2022", insertbackground=FG, relief="flat")
        pass_e.pack(side="left", fill="x", expand=True, ipady=4, padx=(8, 0))
        pass_eye = tk.Label(pass_frame, text="\U0001F441", font=(FONT_UI, 12),
                            fg=MUTED, bg=BG, cursor="hand2")
        pass_eye.pack(side="right", padx=(0, 6))
        pass_visible = [False]
        def _toggle_pass(_e=None):
            pass_visible[0] = not pass_visible[0]
            pass_e.configure(show="" if pass_visible[0] else "\u2022")
            pass_eye.configure(fg=FG if pass_visible[0] else MUTED)
        pass_eye.bind("<Button-1>", _toggle_pass)

        forgot_btn = tk.Label(
            win, text="Forgot Password?", font=(FONT_UI, 9),
            fg=ACCENT2, bg=CARD, cursor="hand2",
        )
        forgot_btn.pack(anchor="e", padx=30)
        forgot_btn.bind("<Button-1>", lambda e: (win.destroy(), self._open_forgot_dialog()))

        msg = tk.Label(win, text="", font=(FONT_UI, 9), fg=DANGER, bg=CARD)
        msg.pack(pady=(6, 0))

        def _err(text: str) -> None:
            msg.configure(text=text, fg=DANGER)

        def do_login():
            email = email_e.get().strip()
            pw = pass_e.get()
            if not email or not pw:
                _err("Email and password are required")
                return
            try:
                taicloud.api_login(email, pw)
            except taicloud.TaiCloudError as exc:
                hint = "Invalid credentials" if exc.code == "bad_credentials" else exc.code
                _err(f"Login failed: {hint}")
                return
            win.destroy()
            self.log(f"[CLOUD] Login: {email}")
            self.refresh_account_ui()

        tk.Button(
            win, text="LOGIN", font=(FONT_UI, 10, "bold"), fg="#ffffff",
            bg=ACCENT2, activebackground="#388bfd", relief="flat",
            padx=20, pady=6, cursor="hand2", command=do_login,
        ).pack(pady=12)
        pass_e.bind("<Return>", lambda e: do_login())

    def _open_forgot_dialog(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("TAI — Forgot Password")
        win.geometry("420x280")
        win.resizable(False, False)
        win.configure(bg=CARD)
        win.grab_set()

        tk.Label(
            win, text="Forgot Password", font=(FONT_UI, 14, "bold"),
            fg=FG, bg=CARD,
        ).pack(pady=(18, 6))
        tk.Label(
            win, text="Enter your email, we'll send a reset code",
            font=(FONT_UI, 10), fg=MUTED, bg=CARD,
        ).pack(pady=(0, 10))

        form = tk.Frame(win, bg=CARD)
        form.pack(fill="x", padx=30)
        tk.Label(form, text="Email", font=(FONT_UI, 10),
                 fg=MUTED, bg=CARD).pack(anchor="w")
        email_e = tk.Entry(form, font=(FONT_UI, 11), bg=BG, fg=FG,
                           insertbackground=FG, relief="flat")
        email_e.pack(fill="x", ipady=4, pady=(2, 6))

        msg = tk.Label(win, text="", font=(FONT_UI, 9), fg=DANGER, bg=CARD)
        msg.pack(pady=(4, 0))

        def _send_code():
            email = email_e.get().strip()
            if not email:
                msg.configure(text="Enter your email", fg=DANGER)
                return
            try:
                taicloud.api_forgot_send(email)
            except taicloud.TaiCloudError as exc:
                msg.configure(text=f"Error: {exc.code}", fg=DANGER)
                return
            win.destroy()
            self._open_forgot_verify(email)

        tk.Button(
            win, text="SEND RESET CODE", font=(FONT_UI, 10, "bold"),
            fg="#ffffff", bg=ACCENT2, activebackground="#388bfd",
            relief="flat", padx=20, pady=6, cursor="hand2", command=_send_code,
        ).pack(pady=12)

    def _open_forgot_verify(self, email: str) -> None:
        win = tk.Toplevel(self.root)
        win.title("TAI — Reset Password")
        win.geometry("420x340")
        win.resizable(False, False)
        win.configure(bg=CARD)
        win.grab_set()

        tk.Label(
            win, text="Reset Password", font=(FONT_UI, 14, "bold"),
            fg=FG, bg=CARD,
        ).pack(pady=(18, 4))
        tk.Label(
            win, text=f"Code sent to: {email}", font=(FONT_UI, 10),
            fg=MUTED, bg=CARD,
        ).pack(pady=(0, 8))

        form = tk.Frame(win, bg=CARD)
        form.pack(fill="x", padx=30)

        tk.Label(form, text="Verification Code", font=(FONT_UI, 10),
                 fg=MUTED, bg=CARD).pack(anchor="w")
        code_e = tk.Entry(form, font=(FONT_MONO, 12), bg=BG, fg=FG,
                          insertbackground=FG, relief="flat", justify="center")
        code_e.pack(fill="x", ipady=4, pady=(2, 8))

        tk.Label(form, text="New Password", font=(FONT_UI, 10),
                 fg=MUTED, bg=CARD).pack(anchor="w")
        np_frame = tk.Frame(form, bg=BG)
        np_frame.pack(fill="x", pady=(2, 2))
        np_e = tk.Entry(np_frame, font=(FONT_UI, 11), bg=BG, fg=FG,
                        show="\u2022", insertbackground=FG, relief="flat")
        np_e.pack(side="left", fill="x", expand=True, ipady=4, padx=(8, 0))
        np_eye = tk.Label(np_frame, text="\U0001F441", font=(FONT_UI, 12),
                          fg=MUTED, bg=BG, cursor="hand2")
        np_eye.pack(side="right", padx=(0, 6))
        np_visible = [False]
        def _toggle_np(_e=None):
            np_visible[0] = not np_visible[0]
            np_e.configure(show="" if np_visible[0] else "\u2022")
            np_eye.configure(fg=FG if np_visible[0] else MUTED)
        np_eye.bind("<Button-1>", _toggle_np)

        strength_frame = tk.Frame(form, bg=CARD)
        strength_frame.pack(fill="x", pady=(0, 4))
        checks = [("len", "8+ chars"), ("upper", "Uppercase"),
                  ("digit", "Digit"), ("symbol", "Symbol (!@#)")]
        check_labels = {}
        for key, text in checks:
            row = tk.Frame(strength_frame, bg=CARD)
            row.pack(fill="x", pady=1)
            dot = tk.Label(row, text="\u25cf", font=(FONT_UI, 7),
                           fg=DANGER, bg=CARD, width=2)
            dot.pack(side="left")
            lbl = tk.Label(row, text=text, font=(FONT_UI, 8),
                           fg=MUTED, bg=CARD)
            lbl.pack(side="left")
            check_labels[key] = (dot, lbl)

        def _check_strength(*_a):
            pw = np_e.get()
            res = {"len": len(pw) >= 8, "upper": any(c.isupper() for c in pw),
                   "digit": any(c.isdigit() for c in pw),
                   "symbol": any(not c.isalnum() and not c.isspace() for c in pw)}
            for k, (d, l) in check_labels.items():
                d.configure(fg=ACCENT if res[k] else DANGER)
                l.configure(fg=ACCENT if res[k] else MUTED)
        np_e.bind("<KeyRelease>", _check_strength)

        msg = tk.Label(win, text="", font=(FONT_UI, 9), fg=DANGER, bg=CARD)
        msg.pack(pady=(4, 0))

        def _reset():
            code = code_e.get().strip()
            pw = np_e.get()
            if not code:
                msg.configure(text="Enter the code", fg=DANGER)
                return
            if len(pw) < 8 or not any(c.isupper() for c in pw) or \
               not any(c.isdigit() for c in pw) or \
               not any(not c.isalnum() and not c.isspace() for c in pw):
                msg.configure(text="Needs: 8+ chars, uppercase, digit, symbol", fg=DANGER)
                return
            try:
                taicloud.api_forgot_reset(email, code, pw)
            except taicloud.TaiCloudError as exc:
                hint = {"invalid_code": "Wrong code", "code_expired": "Code expired"}.get(exc.code, exc.code)
                msg.configure(text=f"Failed: {hint}", fg=DANGER)
                return
            win.destroy()
            self.log(f"[CLOUD] Password reset: {email}")
            self.refresh_account_ui()

        tk.Button(
            win, text="RESET PASSWORD", font=(FONT_UI, 10, "bold"),
            fg="#ffffff", bg=ACCENT, activebackground="#2ea043",
            relief="flat", padx=20, pady=6, cursor="hand2", command=_reset,
        ).pack(pady=(6, 8))

    def _open_signup_dialog(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("TAI Cloud — Sign Up")
        win.geometry("420x500")
        win.resizable(False, False)
        win.configure(bg=CARD)
        win.grab_set()

        tk.Label(
            win, text="Create TAI Cloud Account", font=(FONT_UI, 14, "bold"),
            fg=FG, bg=CARD,
        ).pack(pady=(14, 6))

        form = tk.Frame(win, bg=CARD)
        form.pack(fill="x", padx=30)

        tk.Label(form, text="Email", font=(FONT_UI, 10),
                 fg=MUTED, bg=CARD).pack(anchor="w")
        email_e = tk.Entry(form, font=(FONT_UI, 11), bg=BG, fg=FG,
                           insertbackground=FG, relief="flat")
        email_e.pack(fill="x", ipady=4, pady=(2, 6))

        tk.Label(form, text="Password", font=(FONT_UI, 10),
                 fg=MUTED, bg=CARD).pack(anchor="w")
        pass_frame = tk.Frame(form, bg=BG)
        pass_frame.pack(fill="x", pady=(2, 2))
        pass_e = tk.Entry(pass_frame, font=(FONT_UI, 11), bg=BG, fg=FG,
                          show="\u2022", insertbackground=FG, relief="flat")
        pass_e.pack(side="left", fill="x", expand=True, ipady=4, padx=(8, 0))
        pass_eye = tk.Label(pass_frame, text="\U0001F441", font=(FONT_UI, 12),
                            fg=MUTED, bg=BG, cursor="hand2")
        pass_eye.pack(side="right", padx=(0, 6))
        pass_visible = [False]
        def _toggle_pass(_e=None):
            pass_visible[0] = not pass_visible[0]
            pass_e.configure(show="" if pass_visible[0] else "\u2022")
            pass_eye.configure(fg=FG if pass_visible[0] else MUTED)
        pass_eye.bind("<Button-1>", _toggle_pass)

        strength_frame = tk.Frame(form, bg=CARD)
        strength_frame.pack(fill="x", pady=(0, 6))

        checks = [
            ("len", "8+ characters"),
            ("upper", "Uppercase letter"),
            ("digit", "Digit"),
            ("symbol", "Special symbol (!@#...)"),
        ]
        check_labels = {}
        for key, text in checks:
            row = tk.Frame(strength_frame, bg=CARD)
            row.pack(fill="x", pady=1)
            dot = tk.Label(row, text="\u25cf", font=(FONT_UI, 8),
                           fg=DANGER, bg=CARD, width=2)
            dot.pack(side="left")
            lbl = tk.Label(row, text=text, font=(FONT_UI, 9),
                           fg=MUTED, bg=CARD)
            lbl.pack(side="left")
            check_labels[key] = (dot, lbl)

        tk.Label(form, text="Confirm Password", font=(FONT_UI, 10),
                 fg=MUTED, bg=CARD).pack(anchor="w")
        pass2_frame = tk.Frame(form, bg=BG)
        pass2_frame.pack(fill="x", pady=(2, 4))
        pass2_e = tk.Entry(pass2_frame, font=(FONT_UI, 11), bg=BG, fg=FG,
                           show="\u2022", insertbackground=FG, relief="flat")
        pass2_e.pack(side="left", fill="x", expand=True, ipady=4, padx=(8, 0))
        pass2_eye = tk.Label(pass2_frame, text="\U0001F441", font=(FONT_UI, 12),
                             fg=MUTED, bg=BG, cursor="hand2")
        pass2_eye.pack(side="right", padx=(0, 6))
        pass2_visible = [False]
        def _toggle_pass2(_e=None):
            pass2_visible[0] = not pass2_visible[0]
            pass2_e.configure(show="" if pass2_visible[0] else "\u2022")
            pass2_eye.configure(fg=FG if pass2_visible[0] else MUTED)
        pass2_eye.bind("<Button-1>", _toggle_pass2)

        msg = tk.Label(win, text="", font=(FONT_UI, 9), fg=DANGER, bg=CARD)
        msg.pack(pady=(4, 0))

        def _check_strength(*_args) -> None:
            pw = pass_e.get()
            results = {
                "len": len(pw) >= 8,
                "upper": any(c.isupper() for c in pw),
                "digit": any(c.isdigit() for c in pw),
                "symbol": any(not c.isalnum() and not c.isspace() for c in pw),
            }
            for key, (dot, lbl) in check_labels.items():
                ok = results[key]
                dot.configure(fg=ACCENT if ok else DANGER)
                lbl.configure(fg=ACCENT if ok else MUTED)

        pass_e.bind("<KeyRelease>", _check_strength)

        def _err(text: str) -> None:
            msg.configure(text=text, fg=DANGER)

        def _pw_ok(pw: str) -> bool:
            if len(pw) < 8:
                return False
            if not any(c.isupper() for c in pw):
                return False
            if not any(c.isdigit() for c in pw):
                return False
            if not any(not c.isalnum() and not c.isspace() for c in pw):
                return False
            return True

        def do_signup() -> None:
            email = email_e.get().strip()
            pw = pass_e.get()
            if "@" not in email or "." not in email.split("@")[-1]:
                _err("Please enter a valid email address")
                return
            if not _pw_ok(pw):
                _err("Password needs: 8+ chars, uppercase, digit, symbol")
                return
            if pw != pass2_e.get():
                _err("Passwords do not match")
                return
            try:
                out = taicloud.api_send_code(email, pw)
            except taicloud.TaiCloudError as exc:
                hint = {
                    "email_taken": "This email is already registered",
                    "weak_password": "Password needs: 8+ chars, uppercase, digit, symbol",
                    "invalid_email": "Invalid email format",
                }.get(exc.code, exc.code)
                _err(f"Signup failed: {hint}")
                return
            win.destroy()
            self._show_verify_code(email, out.get("code", ""))

        tk.Button(
            win, text="SIGN UP", font=(FONT_UI, 10, "bold"), fg="#ffffff",
            bg=ACCENT, activebackground="#2ea043", relief="flat",
            padx=20, pady=6, cursor="hand2", command=do_signup,
        ).pack(pady=10)

        tk.Label(
            win,
            text="Signup gives you a Recovery Key — save it securely!",
            font=(FONT_UI, 8), fg=MUTED, bg=CARD,
        ).pack(pady=(4, 8))

    def _show_verify_code(self, email: str, code: str) -> None:
        win = tk.Toplevel(self.root)
        win.title("TAI — Email Verification")
        win.geometry("420x280")
        win.resizable(False, False)
        win.configure(bg=CARD)
        win.grab_set()

        tk.Label(
            win, text="Email Verification", font=(FONT_UI, 14, "bold"),
            fg=FG, bg=CARD,
        ).pack(pady=(18, 6))
        tk.Label(
            win, text=f"6-digit code sent to:", font=(FONT_UI, 10),
            fg=MUTED, bg=CARD,
        ).pack()
        tk.Label(
            win, text=email, font=(FONT_UI, 11, "bold"),
            fg=FG, bg=CARD,
        ).pack(pady=(0, 10))

        tk.Label(
            win, text="Check your inbox, then enter the code below:",
            font=(FONT_UI, 10), fg=MUTED, bg=CARD,
        ).pack(padx=30, anchor="w")
        code_e = tk.Entry(win, font=(FONT_MONO, 14), bg=BG, fg=FG,
                          insertbackground=FG, relief="flat", justify="center")
        code_e.pack(fill="x", padx=30, ipady=6, pady=(4, 8))
        code_e.focus_set()

        msg = tk.Label(win, text="", font=(FONT_UI, 9), fg=DANGER, bg=CARD)
        msg.pack()

        def _verify():
            entered = code_e.get().strip()
            if not entered:
                msg.configure(text="Enter the code")
                return
            try:
                out = taicloud.api_verify_code(email, entered)
            except taicloud.TaiCloudError as exc:
                hint = {
                    "invalid_code": "Wrong code, try again",
                    "code_expired": "Code expired, sign up again",
                    "no_pending_verification": "Code expired, sign up again",
                }.get(exc.code, exc.code)
                msg.configure(text=f"Failed: {hint}", fg=DANGER)
                return
            win.destroy()
            _create_desktop_icon()
            self.log(f"[CLOUD] Account created: {email}")
            self._show_recovery_key(out["recovery_key"], email)
            self.refresh_account_ui()

        tk.Button(
            win, text="VERIFY & CREATE ACCOUNT", font=(FONT_UI, 10, "bold"),
            fg="#ffffff", bg=ACCENT, activebackground="#2ea043",
            relief="flat", padx=20, pady=6, cursor="hand2", command=_verify,
        ).pack(pady=(4, 8))

        code_e.bind("<Return>", lambda e: _verify())

    def _show_recovery_key(self, key: str, email: str) -> None:
        win = tk.Toplevel(self.root)
        win.title("Recovery Key")
        win.geometry("500x360")
        win.resizable(False, False)
        win.configure(bg=CARD)
        win.grab_set()

        tk.Label(
            win, text="Recovery Key", font=(FONT_UI, 16, "bold"),
            fg=FG, bg=CARD,
        ).pack(pady=(18, 4))
        tk.Label(
            win, text=f"Account: {email}", font=(FONT_UI, 10),
            fg=MUTED, bg=CARD,
        ).pack(pady=(0, 12))

        key_card = tk.Frame(win, bg=BG, highlightbackground=ACCENT,
                             highlightthickness=1)
        key_card.pack(padx=30, fill="x", pady=(0, 12))
        tk.Label(key_card, text=key, font=(FONT_MONO, 16, "bold"),
                 fg=ACCENT, bg=BG, pady=10, padx=14).pack()

        tk.Label(
            win,
            text=(
                "This key is stored HASHED on our servers — we can never show it again.\n"
                "Lost password + lost key = data unrecoverable.\n"
                "Save or print this key now."
            ),
            font=(FONT_UI, 9), fg=DANGER, bg=CARD, justify="center",
        ).pack(pady=(0, 10))

        btns = tk.Frame(win, bg=CARD)
        btns.pack(pady=6)

        def copy_key() -> None:
            self.root.clipboard_clear()
            self.root.clipboard_append(key)
            copy_btn.configure(text="Copied!", bg=ACCENT)

        copy_btn = tk.Button(
            btns, text="Copy Key", font=(FONT_UI, 10, "bold"), fg="#ffffff",
            bg=ACCENT2, activebackground="#388bfd", relief="flat",
            padx=20, pady=6, cursor="hand2", command=copy_key,
        )
        copy_btn.pack(side="left", padx=6)
        tk.Button(
            btns, text="Saved", font=(FONT_UI, 10, "bold"), fg="#ffffff",
            bg=ACCENT, activebackground="#2ea043", relief="flat",
            padx=20, pady=6, cursor="hand2", command=win.destroy,
        ).pack(side="left", padx=6)

    def pick_file(self) -> None:
        file_paths = filedialog.askopenfilenames(
            title="Select files to upload"
        )
        if not file_paths:
            return
        self.load_btn.configure(state="disabled")
        self.status.configure(text="Processing...", fg="#d29922")
        self.root.update_idletasks()
        done = 0
        try:
            for file_path in file_paths:
                win, cb = self._show_progress(f"Uploading: {os.path.basename(file_path)}")
                try:
                    shortcut, cloud_target = self.offload(file_path, cb)
                    done += 1
                except Exception as exc:
                    self.log(f"[ERROR] {os.path.basename(file_path)}: {type(exc).__name__}: {exc}")
                finally:
                    win.destroy()
            if done == len(file_paths):
                self.status.configure(
                    text=f"Done! {done} files uploaded to cloud.",
                    fg=ACCENT,
                )
            else:
                self.status.configure(
                    text=f"{done}/{len(file_paths)} files uploaded — check activity.",
                    fg="#d29922",
                )
        finally:
            self.load_btn.configure(state="normal")
            try:
                self.refresh_account_ui()
            except Exception:
                pass

    def offload(self, file_path: str, progress_cb=None) -> tuple[str, str]:
        result = offload_core(
            file_path, make_shortcut=True, log=self.log, progress_cb=progress_cb
        )
        self.log(f"[OK] Cloud pe gayi : {result['cloud_name']}")
        self.log(f"[OK] Shortcut bana : {result['shortcut']}")
        return result["shortcut"], result["cloud_name"]


def _get_desktop_path() -> str:
    try:
        import subprocess
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "[Environment]::GetFolderPath('Desktop')"],
            capture_output=True, text=True, timeout=10,
        )
        path = r.stdout.strip()
        if path and os.path.isdir(path):
            return path
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), "Desktop")


def _create_desktop_icon() -> None:
    try:
        import subprocess, tempfile
        desktop = _get_desktop_path()
        lnk_path = os.path.join(desktop, "TAI - Tricrypt AI.lnk")
        ps = f"""
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut('{lnk_path}')
$s.TargetPath = 'E:\\tricrypt AI\\TAI.bat'
$s.WorkingDirectory = 'E:\\tricrypt AI\\tai'
$s.Description = 'Tricrypt AI'
$s.Save()
"""
        script = os.path.join(tempfile.gettempdir(), "tai_icon.ps1")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(ps)
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", script],
            capture_output=True, timeout=10,
        )
        try:
            os.remove(script)
        except OSError:
            pass
    except Exception:
        pass


def _remove_desktop_icon() -> None:
    try:
        import subprocess, tempfile
        desktop = _get_desktop_path()
        lnk_path = os.path.join(desktop, "TAI - Tricrypt AI.lnk")
        ps = f"Remove-Item -LiteralPath '{lnk_path}' -Force -ErrorAction SilentlyContinue"
        script = os.path.join(tempfile.gettempdir(), "tai_rmicon.ps1")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(ps)
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", script],
            capture_output=True, timeout=10,
        )
        try:
            os.remove(script)
        except OSError:
            pass
    except Exception:
        pass
    except Exception:
        pass


def _ensure_key_auto() -> bool:
    if taicrypt.master_key() is not None:
        return True
    auto_pw = secrets.token_urlsafe(32)
    taicrypt.setup_master_key(auto_pw)
    return True


def _app_lock_screen() -> bool:
    email = taicloud.account_email()
    if not email or not taicloud.has_session():
        return True
    root = tk.Tk()
    root.withdraw()
    import tkinter.simpledialog as sd
    for attempt in range(5):
        pw = sd.askstring(
            "TAI Lock", f"Enter password for {email}:", show="*", parent=root
        )
        if not pw:
            root.destroy()
            return False
        try:
            taicloud.api_login(email, pw)
            root.destroy()
            return True
        except taicloud.TaiCloudError:
            remaining = 4 - attempt
            if remaining > 0:
                messagebox.showwarning(
                    "TAI Lock", f"Wrong password! {remaining} attempts left."
                )
    root.destroy()
    return False


def _log_cli_error(msg: str) -> None:
    err_log = os.path.join(tempfile.gettempdir(), "tai_error.log")
    with open(err_log, "a", encoding="utf-8") as fh:
        fh.write(f"{msg}\n")


def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == "--restore":
        if taicrypt.master_key() is None:
            _log_cli_error("Key config nahi mila — pehle TAI app kholo")
            return
        if not cloud_active():
            _log_cli_error("Cloud login nahi hai — pehle TAI app mein login karo")
            return
        cloud_name = sys.argv[2]
        win = None
        try:
            win = tk.Tk()
            win.withdraw()
            answer = messagebox.askyesno(
                "TAI Cloud",
                f"Do you want to download the file?\n\n"
                f"Remember: After download, file will be deleted from cloud.\n"
                f"You will have to upload it again.",
                parent=win,
            )
            if not answer:
                win.destroy()
                return
            win.deiconify()
            win.title("TAI — Downloading")
            win.geometry("420x130")
            win.resizable(False, False)
            win.configure(bg=CARD)
            inner = tk.Frame(win, bg=CARD)
            inner.pack(fill="both", expand=True, padx=16, pady=12)
            lbl = tk.Label(
                inner, text="Downloading file from cloud...",
                font=(FONT_UI, 11, "bold"), fg=FG, bg=CARD,
            )
            lbl.pack(anchor="w")
            sub = tk.Label(inner, text="", font=(FONT_UI, 9),
                           fg=MUTED, bg=CARD)
            sub.pack(anchor="w", pady=(4, 6))
            bar = ttk.Progressbar(inner, maximum=100, length=370,
                                   style="TProgressbar")
            bar.pack(fill="x")
            pct = tk.Label(inner, text="0%", font=(FONT_UI, 10, "bold"),
                           fg=ACCENT, bg=CARD)
            pct.pack(anchor="e", pady=(4, 0))

            def cb(done: int, total: int) -> None:
                val = int(done * 100 / max(total, 1))
                bar["value"] = val
                sub.config(text=f"{done} / {total} parts downloading...")
                pct.config(text=f"{val}%")
                win.update_idletasks()
                win.update()

            mapping = cloud_load_mapping()
            entry = mapping.get(cloud_name)
            if not entry or "parts" not in entry:
                raise RuntimeError(f"Mapping mein nahi mila: {cloud_name}")
            original_name = entry.get("original", cloud_name)
            out_dir = os.path.join(tempfile.gettempdir(), "TAI")
            os.makedirs(out_dir, exist_ok=True)
            tmp_path = os.path.join(out_dir, f"TAI-dl-{original_name}")
            cloud_join_parts(entry["parts"], tmp_path, cloud_name, cb)

            save_dir = r"C:\TAI\Downloads"
            os.makedirs(save_dir, exist_ok=True)
            dest = os.path.join(save_dir, original_name)
            counter = 1
            while os.path.exists(dest):
                name, ext = os.path.splitext(original_name)
                dest = os.path.join(save_dir, f"{name} ({counter}){ext}")
                counter += 1
            shutil.copy2(tmp_path, dest)
            try:
                os.remove(tmp_path)
            except OSError:
                pass

            lbl.config(text="Deleting from cloud...")
            bar["value"] = 100
            pct.config(text="100%")
            win.update()

            delete_cloud_file(cloud_name)

            shortcut_path = entry.get("shortcut", "")
            for search_dir in [r"C:\TAI\Shortcuts", os.path.expanduser("~")]:
                if os.path.isdir(search_dir):
                    for f in os.listdir(search_dir):
                        if f.endswith(".lnk") and original_name.split(".")[0] in f:
                            try:
                                subprocess.run(
                                    ["cmd", "/c", "del", "/f", "/q", os.path.join(search_dir, f)],
                                    capture_output=True, timeout=5,
                                )
                            except Exception:
                                try:
                                    os.remove(os.path.join(search_dir, f))
                                except OSError:
                                    pass
            if shortcut_path and os.path.exists(shortcut_path):
                try:
                    subprocess.run(
                        ["cmd", "/c", "del", "/f", "/q", shortcut_path],
                        capture_output=True, timeout=5,
                    )
                except Exception:
                    try:
                        os.remove(shortcut_path)
                    except OSError:
                        pass

            win.destroy()
            os.startfile(dest)
            messagebox.showinfo(
                "TAI Cloud",
                f"File saved to: {dest}\n\n"
                f"Cloud se delete ho gayi.\n"
                f"Dobara upload karna mat bhoolna!",
            )
        except Exception as exc:
            if win is not None:
                try:
                    win.destroy()
                except Exception:
                    pass
            _log_cli_error(str(exc))
        return
    if len(sys.argv) >= 2 and sys.argv[1] == "--shuffle":
        if taicrypt.master_key() is None:
            _log_cli_error("Key config nahi mila — pehle TAI app kholo")
            return
        try:
            shuffle_part_names()
        except Exception as exc:
            _log_cli_error(f"SHUFFLE: {exc}")
        return
    if not _ensure_key_auto():
        return
    if not _app_lock_screen():
        return
    root = tk.Tk()
    TaiApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
