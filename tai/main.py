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
from tkinter import filedialog, messagebox, simpledialog, ttk
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

BG = "#0d1117"
PANEL = "#161b22"
FG = "#e6edf3"
MUTED = "#8b949e"
ACCENT = "#3fb950"
DANGER = "#f85149"


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


SHORTCUTS_DIR = os.path.join(STORAGE_DIR, "Shortcuts")


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
    sp = shortcut_path.replace("'", "''")
    pe = python_exe.replace("'", "''")
    mp = MAIN_PY.replace("'", "''")
    cn = cloud_name.replace("'", "''")
    wd = os.path.dirname(MAIN_PY).replace("'", "''")
    script = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{sp}'); "
        f"$s.TargetPath = '{pe}'; "
        f"$s.Arguments = '\"{mp}\" --restore \"{cn}\"'; "
        f"$s.WorkingDirectory = '{wd}'; "
        "$s.Save()"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
    )
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
    est_parts = max(1, (size + MAX_PLAIN - 1) // MAX_PLAIN)
    if quota["used_bytes"] + est_parts * CHUNK_SIZE > quota["quota_bytes"]:
        raise RuntimeError(
            f"Quota kam hai! Chahiye ~{est_parts * CHUNK_SIZE / (1024**2):.1f} MB, "
            f"bacha hai {(quota['quota_bytes'] - quota['used_bytes']) / (1024**2):.1f} MB"
        )
    os.makedirs(STAGING_DIR, exist_ok=True)
    staged_target = unique_path(STAGING_DIR, random_cloud_name(original_name))
    if log:
        log(f"[..] Uploading to TAI Cloud: {original_name}")
    shutil.move(file_path, staged_target)
    cloud_name = os.path.basename(staged_target)
    uploaded_ids: list[str] = []
    shortcut_path = None
    try:
        parts = cloud_split_into_parts(staged_target, cloud_name, progress_cb, uploaded_ids)
        if make_shortcut:
            shortcut_path = file_path + ".lnk"
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
        root.geometry("640x440")
        root.configure(bg=BG)

        header = tk.Frame(root, bg=BG)
        header.pack(fill="x", padx=20, pady=(18, 8))

        tk.Label(
            header,
            text="TAI",
            font=("Consolas", 26, "bold"),
            fg=ACCENT,
            bg=BG,
        ).pack(side="left")

        tk.Label(
            header,
            text="  Tricrypt AI — Cloud Offloader",
            font=("Consolas", 12),
            fg=MUTED,
            bg=BG,
        ).pack(side="left", pady=(10, 0))

        account_frame = tk.Frame(header, bg=BG)
        account_frame.pack(side="right")

        self.quota_lbl = tk.Label(
            account_frame, text="", font=("Consolas", 9), fg=MUTED, bg=BG
        )
        self.quota_lbl.pack(side="left", padx=(0, 10))

        self.account_btn = tk.Button(
            account_frame,
            text="☁  LOGIN",
            font=("Consolas", 10, "bold"),
            fg="#ffffff",
            bg="#1f6feb",
            activebackground="#388bfd",
            activeforeground="#ffffff",
            relief="flat",
            padx=14,
            pady=4,
            cursor="hand2",
            command=self._open_auth_dialog,
        )
        self.account_btn.pack(side="left")

        self.status = tk.Label(
            root,
            text="Ready. File select karo — TAI use cloud pe bhej dega.",
            font=("Consolas", 10),
            fg=MUTED,
            bg=BG,
            anchor="w",
        )
        self.status.pack(fill="x", padx=20)

        btn_frame = tk.Frame(root, bg=BG)
        btn_frame.pack(pady=14)

        self.load_btn = tk.Button(
            btn_frame,
            text="📂  FILES LOAD KARO",
            font=("Consolas", 13, "bold"),
            fg="#ffffff",
            bg=ACCENT,
            activebackground="#2ea043",
            activeforeground="#ffffff",
            relief="flat",
            padx=24,
            pady=10,
            cursor="hand2",
            command=self.pick_file,
        )
        self.load_btn.pack()

        log_label = tk.Label(
            root,
            text="ACTIVITY LOG",
            font=("Consolas", 9, "bold"),
            fg=MUTED,
            bg=BG,
            anchor="w",
        )
        log_label.pack(fill="x", padx=20)

        self.log_box = ScrolledText(
            root,
            font=("Consolas", 9),
            fg=FG,
            bg=PANEL,
            relief="flat",
            state="disabled",
            height=14,
        )
        self.log_box.pack(fill="both", expand=True, padx=20, pady=(4, 16))

        self.log("TAI started.")
        self.log(f"Cloud location: {CLOUD_DIR}")
        self.refresh_account_ui()

    def log(self, msg: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert(tk.END, msg + "\n")
        self.log_box.see(tk.END)
        self.log_box.configure(state="disabled")

    def _show_progress(self, title: str):
        win = tk.Toplevel(self.root)
        win.title(title)
        win.geometry("400x120")
        win.resizable(False, False)
        win.configure(bg=PANEL)
        lbl = tk.Label(
            win, text="0 / 0 parts", font=("Consolas", 9), fg=FG, bg=PANEL
        )
        lbl.pack(pady=(14, 4))
        bar = ttk.Progressbar(win, maximum=100, length=360)
        bar.pack(padx=16)
        pct = tk.Label(
            win, text="0%", font=("Consolas", 10, "bold"), fg=ACCENT, bg=PANEL
        )
        pct.pack(pady=4)

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
            self.account_btn.configure(text=f"☁  {email}")
            try:
                q = taicloud.api_me()
                used_gb = q["used_bytes"] / (1024**3)
                quota_gb = q["quota_bytes"] / (1024**3)
                self.quota_lbl.configure(text=f"{used_gb:.2f} / {quota_gb:.0f} GB", fg=MUTED)
            except taicloud.TaiCloudError:
                self.quota_lbl.configure(text="☁ offline", fg=DANGER)
        else:
            self.account_btn.configure(text="☁  LOGIN")
            self.quota_lbl.configure(text="")

    def _open_auth_dialog(self) -> None:
        if cloud_active():
            if messagebox.askyesno(
                "TAI Cloud", f"{taicloud.account_email()} se logout kar dein?"
            ):
                taicloud.clear_session()
                self.refresh_account_ui()
                self.log("[CLOUD] Logout done.")
            return
        win = tk.Toplevel(self.root)
        win.title("TAI Cloud — Login / Signup")
        win.geometry("400x270")
        win.resizable(False, False)
        win.configure(bg=PANEL)
        win.grab_set()

        tk.Label(
            win, text="TAI CLOUD ACCOUNT", font=("Consolas", 12, "bold"),
            fg=ACCENT, bg=PANEL,
        ).pack(pady=(14, 8))

        row1 = tk.Frame(win, bg=PANEL)
        row1.pack(fill="x", padx=24)
        tk.Label(row1, text="Email:", font=("Consolas", 10), fg=FG, bg=PANEL).pack(side="left")
        email_e = tk.Entry(row1, font=("Consolas", 10), bg=BG, fg=FG,
                           insertbackground=FG, relief="flat")
        email_e.pack(side="left", fill="x", expand=True, padx=(8, 0), ipady=3)

        row2 = tk.Frame(win, bg=PANEL)
        row2.pack(fill="x", padx=24, pady=(8, 0))
        tk.Label(row2, text="Pass :", font=("Consolas", 10), fg=FG, bg=PANEL).pack(side="left")
        pass_e = tk.Entry(row2, font=("Consolas", 10), bg=BG, fg=FG, show="*",
                          insertbackground=FG, relief="flat")
        pass_e.pack(side="left", fill="x", expand=True, padx=(8, 0), ipady=3)

        row3 = tk.Frame(win, bg=PANEL)
        row3.pack(fill="x", padx=24, pady=(8, 0))
        tk.Label(row3, text="Confirm:", font=("Consolas", 10), fg=FG, bg=PANEL).pack(side="left")
        pass2_e = tk.Entry(row3, font=("Consolas", 10), bg=BG, fg=FG, show="*",
                           insertbackground=FG, relief="flat")
        pass2_e.pack(side="left", fill="x", expand=True, padx=(8, 0), ipady=3)

        msg = tk.Label(win, text="", font=("Consolas", 9), fg=DANGER, bg=PANEL)
        msg.pack(pady=(6, 0))

        btns = tk.Frame(win, bg=PANEL)
        btns.pack(pady=10)

        def _err(text: str) -> None:
            msg.configure(text=text, fg=DANGER)

        def do_login() -> None:
            email = email_e.get().strip()
            pw = pass_e.get()
            if not email or not pw:
                _err("Email aur password dono bharo")
                return
            try:
                taicloud.api_login(email, pw)
            except taicloud.TaiCloudError as exc:
                hint = "Email/password galat" if exc.code == "bad_credentials" else exc.code
                _err(f"Login fail: {hint}")
                return
            win.destroy()
            self.log(f"[CLOUD] Login: {email}")
            self.refresh_account_ui()

        def do_signup() -> None:
            email = email_e.get().strip()
            pw = pass_e.get()
            if "@" not in email or "." not in email.split("@")[-1]:
                _err("Sahi email likho")
                return
            if len(pw) < 8:
                _err("Password min 8 characters")
                return
            if pw != pass2_e.get():
                _err("Dono passwords match nahi kar rahe")
                return
            try:
                out = taicloud.api_signup(email, pw)
            except taicloud.TaiCloudError as exc:
                hint = {
                    "email_taken": "Ye email pehle se registered hai",
                    "weak_password": "Password min 8 characters",
                    "invalid_email": "Email format galat",
                }.get(exc.code, exc.code)
                _err(f"Signup fail: {hint}")
                return
            win.destroy()
            self.log(f"[CLOUD] Account ban gaya: {email}")
            self._show_recovery_key(out["recovery_key"], email)
            self.refresh_account_ui()

        tk.Button(
            btns, text="LOGIN", font=("Consolas", 10, "bold"), fg="#ffffff",
            bg="#1f6feb", activebackground="#388bfd", relief="flat",
            padx=18, pady=5, cursor="hand2", command=do_login,
        ).pack(side="left", padx=6)
        tk.Button(
            btns, text="NAYA ACCOUNT", font=("Consolas", 10, "bold"), fg="#ffffff",
            bg="#238636", activebackground="#2ea043", relief="flat",
            padx=18, pady=5, cursor="hand2", command=do_signup,
        ).pack(side="left", padx=6)

        tk.Label(
            win,
            text="Signup pe Recovery Key milegi — use sambhal ke rakhna!",
            font=("Consolas", 8), fg=MUTED, bg=PANEL,
        ).pack(pady=(4, 8))

    def _show_recovery_key(self, key: str, email: str) -> None:
        win = tk.Toplevel(self.root)
        win.title("RECOVERY KEY — sirf ek baar dikhegi!")
        win.geometry("480x330")
        win.resizable(False, False)
        win.configure(bg=PANEL)
        win.grab_set()

        tk.Label(
            win, text=f"Account: {email}", font=("Consolas", 10), fg=FG, bg=PANEL
        ).pack(pady=(16, 8))
        tk.Label(
            win, text="RECOVERY KEY", font=("Consolas", 9, "bold"), fg=MUTED, bg=PANEL
        ).pack()
        key_lbl = tk.Label(
            win, text=key, font=("Consolas", 15, "bold"), fg=ACCENT, bg=BG,
            relief="solid", bd=1, pady=8, padx=14,
        )
        key_lbl.pack(pady=6)
        tk.Label(
            win,
            text=(
                "⚠  Ye key server par sirf HASHED form mein hai — hum ise\n"
                "    dobara kabhi nahi dikha sakte!\n"
                "Password bhool gaye + ye key nahi hai\n"
                "→ DATA HAMESHA KE LIYE GAYAB.\n"
                "Ise abhi kisi jagah save/print kar lo."
            ),
            font=("Consolas", 9), fg=DANGER, bg=PANEL, justify="center",
        ).pack(pady=8)

        btns = tk.Frame(win, bg=PANEL)
        btns.pack(pady=6)

        def copy_key() -> None:
            self.root.clipboard_clear()
            self.root.clipboard_append(key)
            copy_btn.configure(text="COPIED ✓", bg="#238636")

        copy_btn = tk.Button(
            btns, text="📋 COPY KARO", font=("Consolas", 10, "bold"), fg="#ffffff",
            bg="#1f6feb", activebackground="#388bfd", relief="flat",
            padx=16, pady=5, cursor="hand2", command=copy_key,
        )
        copy_btn.pack(side="left", padx=6)
        tk.Button(
            btns, text="SAVE HO GAYA ✓", font=("Consolas", 10, "bold"), fg="#ffffff",
            bg="#238636", activebackground="#2ea043", relief="flat",
            padx=16, pady=5, cursor="hand2", command=win.destroy,
        ).pack(side="left", padx=6)

    def pick_file(self) -> None:
        file_paths = filedialog.askopenfilenames(
            title="Select files (ek saath multiple bhi select kar sakte ho)"
        )
        if not file_paths:
            return
        self.load_btn.configure(state="disabled")
        self.status.configure(text="Processing...", fg="#d29922")
        self.root.update_idletasks()
        done = 0
        try:
            for file_path in file_paths:
                win, cb = self._show_progress(f"Processing: {os.path.basename(file_path)}")
                try:
                    shortcut, cloud_target = self.offload(file_path, cb)
                    self.log(f"[OK] Cloud pe gayi : {cloud_target}")
                    self.log(f"[OK] Shortcut bana : {shortcut}")
                    done += 1
                except Exception as exc:
                    self.log(f"[ERROR] {os.path.basename(file_path)}: {exc}")
                finally:
                    win.destroy()
            if done == len(file_paths):
                self.status.configure(
                    text=f"Done! {done} files cloud pe hain, shortcuts PC pe.",
                    fg=ACCENT,
                )
            else:
                self.status.configure(
                    text=f"{done}/{len(file_paths)} files gayin — error log dekho.",
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
        self.log(f"[MAP] {result['original']}  ->  {result['cloud_name']}")
        self.log(f"[CUT] {result['parts']} parts x 100KB -> Storage")
        return result["shortcut"], result["cloud_name"]


def _ensure_key_interactive() -> bool:
    if taicrypt.master_key() is not None:
        return True
    root = tk.Tk()
    root.withdraw()
    while True:
        p1 = simpledialog.askstring(
            "TAI-Crypt Setup", "Master password banayo (min 8 chars):", show="*"
        )
        if not p1:
            root.destroy()
            return False
        p2 = simpledialog.askstring("TAI-Crypt Setup", "Password dobara likho:", show="*")
        if p1 == p2 and len(p1) >= 8:
            taicrypt.setup_master_key(p1)
            root.destroy()
            return True
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
        win = None
        try:
            win = tk.Tk()
            win.title("TAI — Restore")
            win.geometry("400x120")
            win.resizable(False, False)
            win.configure(bg=PANEL)
            lbl = tk.Label(
                win,
                text="Cloud se parts aa rahe hain...",
                font=("Consolas", 9),
                fg=FG,
                bg=PANEL,
            )
            lbl.pack(pady=(14, 4))
            bar = ttk.Progressbar(win, maximum=100, length=360)
            bar.pack(padx=16)
            pct = tk.Label(
                win, text="0%", font=("Consolas", 10, "bold"), fg=ACCENT, bg=PANEL
            )
            pct.pack(pady=4)

            def cb(done: int, total: int) -> None:
                val = int(done * 100 / max(total, 1))
                bar["value"] = val
                lbl.config(text=f"{done} / {total} parts rejoin ho rahe hain")
                pct.config(text=f"{val}%")
                win.update_idletasks()
                win.update()

            out_path = restore_cloud(sys.argv[2], cb)
            win.destroy()
            os.startfile(out_path)
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
    if not _ensure_key_interactive():
        return
    root = tk.Tk()
    TaiApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
