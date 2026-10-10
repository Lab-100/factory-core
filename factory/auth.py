"""Автовход фабрики в GitHub: `factory auth login|status|logout`.

Device flow (GitHub device grant):
  - `factory auth login` — запрашивает код у GitHub, автоматически открывает
    браузер, ждёт подтверждения, сохраняет токен в state/github.token
    (каталог state/ в .gitignore — токен не попадает в репозиторий);
  - `factory auth login --resume <файл>` — продолжает уже начатый flow
    (например, код, который пользователь уже подтвердил в браузере);
  - `factory auth status` — проверяет сохранённый токен через GET /user;
  - `factory auth logout` — удаляет токен.

Токен далее используется командой `factory release` автоматически:
пуш идёт через git-протокол без credential-окон (GIT_TERMINAL_PROMPT=0),
а при недоступности — через Git Data API.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

from .i18n import make_translator

CLIENT_ID = "178c6fc778ccc68e1d6a"  # приложение GitHub CLI (device flow)
# public_repo — запись в публичные репозитории; workflow — файлы .github/workflows (CI).
SCOPE = "public_repo,workflow"
GITHUB_API = "https://api.github.com"
_DEVICE_URL = "https://github.com/login/device/code"
_TOKEN_URL = "https://github.com/login/oauth/access_token"


def token_path() -> Path:
    from .paths import repo_root

    return repo_root() / "state" / "github.token"


def save_token(token: str) -> Path:
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token.strip(), encoding="utf-8")
    return path


def load_token() -> str | None:
    path = token_path()
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        return value or None
    return None


def delete_token() -> bool:
    path = token_path()
    if path.exists():
        path.unlink()
        return True
    return False


def _tr(translator=None):
    """Переводчик активного языка (переданный или из настроек)."""
    if translator is not None:
        return translator
    from .paths import repo_root
    from .settings import load as _load_settings

    return make_translator(_load_settings(repo_root() / "config" / "settings.toml"))


def _post_form(url: str, fields: dict) -> dict:
    data = urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"error": f"http {exc.code}", "error_description": raw[:300]}
    except Exception as exc:  # noqa: BLE001 — сеть недоступна
        return {"error": "network", "error_description": str(exc)}


def _api_get(path: str, token: str) -> dict:
    request = urllib.request.Request(
        GITHUB_API + path,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            literal = resp.read().decode("utf-8")
        return json.loads(literal) if literal else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {"message": raw}
        raise RuntimeError(f"GitHub {exc.code}: {body.get('message', body)}") from exc


def _start_flow(tr) -> dict:
    resp = _post_form(_DEVICE_URL, {"client_id": CLIENT_ID, "scope": SCOPE})
    if "device_code" not in resp:
        raise RuntimeError(tr("auth.flow_failed", resp=resp))
    return resp


def _poll_flow(flow: dict, max_wait: int, tr) -> str:
    """Ждём авторизацию, уважая интервалы сервера (slow_down и т.п.)."""
    device_code = flow["device_code"]
    interval = max(int(flow.get("interval", 5)), 5)
    deadline = time.time() + max_wait
    while time.time() < deadline:
        time.sleep(interval)
        answer = _post_form(
            _TOKEN_URL,
            {
                "client_id": CLIENT_ID,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
        )
        if "access_token" in answer:
            return answer["access_token"]
        error = answer.get("error")
        if error == "authorization_pending":
            interval = max(int(flow.get("interval", 5)), 5)
        elif error == "slow_down":
            interval = max(int(answer.get("interval", interval + 5)), interval + 5)
        elif error in (
            "expired_token",
            "access_denied",
            "authorization_declined",
            "incorrect_client_credentials",
            "incorrect_device_code",
        ):
            raise RuntimeError(
                tr("auth.failed", error=error, desc=answer.get("error_description", ""))
            )
        else:
            interval = max(int(answer.get("interval", interval + 5)), interval + 5)
    raise RuntimeError(tr("auth.timeout"))


def status() -> dict:
    tr = _tr()
    token = load_token()
    if not token:
        return {"logged_in": False, "message": tr("auth.need_token")}
    try:
        user = _api_get("/user", token)
    except RuntimeError as exc:
        return {"logged_in": False, "message": str(exc)}
    return {
        "logged_in": True,
        "login": user.get("login"),
        "name": user.get("name"),
        "token_file": str(token_path()),
    }


def login(open_browser: bool = True, resume: Path | None = None, max_wait: int = 600,
          translator=None) -> dict:
    """Автовход. Если сохранённый токен рабочий — используем его.
    resume: файл с уже начатым device flow (device_code) — сначала
    пробуем допередать его (например, пользователь уже подтвердил код)."""
    tr = _tr(translator)
    existing = load_token()
    if existing:
        try:
            user = _api_get("/user", existing)
            return {
                "status": "already",
                "login": user.get("login"),
                "token_file": str(token_path()),
            }
        except RuntimeError:
            pass  # токен недействителен — перевыпускаем

    flow = None
    if resume is not None and resume.exists():
        try:
            data = json.loads(resume.read_text(encoding="utf-8"))
            if data.get("device_code"):
                flow = {
                    "device_code": data["device_code"],
                    "interval": int(data.get("interval", 5)),
                    "verification_uri": data.get(
                        "verification_uri", "https://github.com/login/device"
                    ),
                    "user_code": data.get("user_code", ""),
                }
        except Exception:  # noqa: BLE001
            flow = None
    if flow is None:
        flow = _start_flow(tr)

    uri = flow.get("verification_uri", "https://github.com/login/device")
    code = flow.get("user_code", "")
    print(tr("auth.title"))
    print(f"{tr('auth.open_url')} {uri}")
    print(f"{tr('auth.code')} {code}")
    if resume is not None:
        print(tr("auth.resume"))
    if open_browser and uri:
        try:
            webbrowser.open(uri)
            print(tr("auth.browser"))
        except Exception:  # noqa: BLE001
            pass

    token = _poll_flow(flow, max_wait=max_wait, tr=tr)
    save_token(token)
    user = _api_get("/user", token)
    return {"status": "ok", "login": user.get("login"), "token_file": str(token_path())}


def logout() -> dict:
    removed = delete_token()
    return {"logged_in": False, "removed": removed}