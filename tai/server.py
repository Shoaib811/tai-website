import json
import os
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import main
import taicrypt

HOST = "127.0.0.1"
PORT = 8765


class TaiApiHandler(BaseHTTPRequestHandler):
    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, code: int, message: str) -> None:
        self._json(code, {"ok": False, "error": message})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/status":
            mapping = main.load_mapping()
            self._json(
                200,
                {
                    "ok": True,
                    "service": "TAI-Crypt API",
                    "version": "1.0",
                    "files": len(mapping),
                    "cloud": main.CLOUD_DIR,
                },
            )
            return
        if parsed.path == "/files":
            mapping = main.load_mapping()
            files = [
                {
                    "cloud_name": name,
                    "original": entry.get("original"),
                    "size": entry.get("size"),
                    "parts": len(entry.get("parts", [])),
                }
                for name, entry in mapping.items()
            ]
            self._json(200, {"ok": True, "count": len(files), "files": files})
            return
        if parsed.path.startswith("/restore/"):
            cloud_name = parsed.path[len("/restore/") :].strip("/")
            if not cloud_name:
                self._error(400, "cloud_name missing")
                return
            try:
                out_path = main.restore_cloud(cloud_name)
            except FileNotFoundError as exc:
                self._error(404, str(exc))
                return
            except Exception as exc:
                self._error(500, f"Restore fail: {exc}")
                return
            try:
                with open(out_path, "rb") as fh:
                    data = fh.read()
            finally:
                try:
                    os.remove(out_path)
                except OSError:
                    pass
            original = main.load_mapping().get(cloud_name, {}).get("original", cloud_name)
            self.send_response(200)
            self.send_header(
                "Content-Disposition", f'attachment; filename="{original}"'
            )
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            self.wfile.write(data)
            return
        self._error(404, "Unknown endpoint")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/protect":
            self._error(404, "Unknown endpoint")
            return
        query = parse_qs(parsed.query)
        name = (query.get("name") or [""])[0].strip()
        if not name:
            self._error(400, "?name= filename do (e.g. /protect?name=doc.pdf)")
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            self._error(400, "Body mein file bytes bhejo")
            return
        data = self.rfile.read(length)
        safe_name = os.path.basename(name.replace("\\", "/"))
        tmp_dir = tempfile.mkdtemp(prefix="tai_api_")
        tmp_path = os.path.join(tmp_dir, safe_name)
        with open(tmp_path, "wb") as fh:
            fh.write(data)
        try:
            result = main.offload_core(tmp_path, make_shortcut=False)
        except Exception as exc:
            self._error(500, f"Protect fail: {exc}")
            return
        finally:
            shutil_cleanup(tmp_dir)
        self._json(
            200,
            {
                "ok": True,
                "cloud_name": result["cloud_name"],
                "original": result["original"],
                "size": result["size"],
                "parts": result["parts"],
                "restore_url": f"http://{HOST}:{PORT}/restore/{result['cloud_name']}",
            },
        )

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/file/"):
            self._error(404, "Unknown endpoint")
            return
        cloud_name = parsed.path[len("/file/") :].strip("/")
        with main.map_lock():
            mapping = main.load_mapping()
            entry = mapping.pop(cloud_name, None)
            if entry is None:
                self._error(404, f"File nahi mili: {cloud_name}")
                return
        for part in entry.get("parts", []):
            try:
                os.remove(main.find_part(part["name"]))
            except (OSError, FileNotFoundError):
                pass
        sc = entry.get("shortcut")
        if sc:
            try:
                os.remove(os.path.join(main.SHORTCUTS_DIR, os.path.basename(sc)))
            except OSError:
                pass
            main.save_mapping(mapping)
        self._json(200, {"ok": True, "deleted": cloud_name})

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[TAI-API] %s - %s\n" % (self.address_string(), fmt % args))


def shutil_cleanup(folder: str) -> None:
    import shutil

    shutil.rmtree(folder, ignore_errors=True)


def run() -> None:
    if taicrypt.master_key() is None:
        print("[TAI-API] ERROR: Key config nahi mila.")
        print("[TAI-API] Pehle TAI.bat kholo (password setup ho jayega), phir server chalao.")
        sys.exit(1)
    server = ThreadingHTTPServer((HOST, PORT), TaiApiHandler)
    print(f"[TAI-API] TAI-Crypt REST API chal rahi hai: http://{HOST}:{PORT}")
    print("[TAI-API] Endpoints:")
    print("   GET    /status")
    print("   GET    /files")
    print("   POST   /protect?name=<filename>   (body = file bytes)")
    print(f"   GET    /restore/<cloud_name>")
    print(f"   DELETE /file/<cloud_name>")
    print("[TAI-API] Band karne ke liye: Ctrl+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[TAI-API] Server band.")


if __name__ == "__main__":
    run()
