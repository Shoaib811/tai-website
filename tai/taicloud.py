import json
import os

import requests

import taicrypt

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tai_cloud_config.json")
DEFAULT_SERVER = "https://api.code99.pk/api/v1"

_state = {"token": None}


class TaiCloudError(RuntimeError):
    def __init__(self, code: str, status: int):
        super().__init__(f"{code} (HTTP {status})")
        self.code = code
        self.status = status


def _load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)


def server_url() -> str:
    env = os.environ.get("TAI_SERVER_URL")
    if env:
        return env.rstrip("/")
    return _load_config().get("server", DEFAULT_SERVER)


def set_server(url: str) -> None:
    cfg = _load_config()
    cfg["server"] = url.rstrip("/")
    _save_config(cfg)


def account_email() -> str | None:
    return _load_config().get("email")


def get_token() -> str | None:
    if _state["token"]:
        return _state["token"]
    wrapped = _load_config().get("token_wrapped")
    if not wrapped:
        return None
    try:
        token = taicrypt.dpapi_unwrap(bytes.fromhex(wrapped)).decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    _state["token"] = token
    return token


def has_session() -> bool:
    return get_token() is not None


def set_session(token: str, email: str) -> None:
    cfg = _load_config()
    cfg["token_wrapped"] = taicrypt.dpapi_protect(token.encode("utf-8")).hex()
    cfg["email"] = email
    _save_config(cfg)
    _state["token"] = token


def clear_session() -> None:
    cfg = _load_config()
    cfg.pop("token_wrapped", None)
    cfg.pop("email", None)
    _save_config(cfg)
    _state["token"] = None


def _request(method: str, path: str, *, json_body=None, data=None, auth=True, timeout=60):
    headers = {}
    if auth:
        token = get_token()
        if not token:
            raise TaiCloudError("not_logged_in", 401)
        headers["Authorization"] = f"Bearer {token}"
    url = server_url() + path
    try:
        resp = requests.request(method, url, json=json_body, data=data, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise TaiCloudError("network_error", 0) from exc
    if resp.status_code >= 400:
        try:
            code = resp.json().get("error", "http_error")
        except ValueError:
            code = "http_error"
        raise TaiCloudError(code, resp.status_code)
    if resp.status_code == 204 or not resp.content:
        return {}
    ctype = resp.headers.get("content-type", "")
    if ctype.startswith("application/json"):
        return resp.json()
    return resp.content


def api_signup(email: str, password: str) -> dict:
    out = _request("POST", "/auth/signup", json_body={"email": email, "password": password}, auth=False)
    set_session(out["token"], email)
    return out


def api_login(email: str, password: str) -> dict:
    out = _request("POST", "/auth/login", json_body={"email": email, "password": password}, auth=False)
    set_session(out["token"], email)
    return out


def api_recover(email: str, recovery_key: str, new_password: str) -> dict:
    out = _request(
        "POST",
        "/auth/recover",
        json_body={"email": email, "recovery_key": recovery_key, "new_password": new_password},
        auth=False,
    )
    set_session(out["token"], email)
    return out


def api_me() -> dict:
    return _request("GET", "/me")


def api_upload_part(blob: bytes) -> dict:
    return _request("POST", "/parts", data=blob, timeout=120)


def api_download_part(part_id: str) -> bytes:
    out = _request("GET", f"/parts/{part_id}", timeout=120)
    if isinstance(out, dict):
        raise TaiCloudError("bad_response", 500)
    return out


def api_delete_part(part_id: str) -> None:
    _request("DELETE", f"/parts/{part_id}")


def api_put_map(blob: bytes) -> dict:
    return _request("PUT", "/map", data=blob, timeout=120)


def api_get_map() -> bytes | None:
    try:
        out = _request("GET", "/map", timeout=60)
    except TaiCloudError as exc:
        if exc.code == "no_map":
            return None
        raise
    if isinstance(out, dict):
        return None
    return out


def api_purge() -> None:
    _request("DELETE", "/purge")
