"""Автоматический выпуск релиза + Windows-версии factory-core.

`factory release` делает весь конвейер сам:
  1. сборка factory.exe (PyInstaller, при необходимости ставится сам);
  2. упаковка dist/factory-core-win.zip;
  3. тег v<version> (версия из pyproject.toml) + пуш в main;
  4. создание GitHub Release и загрузка zip-ассета.

Токен: переменная окружения GITHUB_TOKEN, либо файл state/github.token
(записывается командой `factory auth <token>`, каталог state/ в .gitignore),
либо `gh auth token`, если установлен GitHub CLI.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tomllib
import urllib.parse
import urllib.request
from pathlib import Path

from .paths import repo_root

GITHUB_API = "https://api.github.com"


def find_token() -> str | None:
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    root = repo_root()
    token_file = root / "state" / "github.token"
    if token_file.exists():
        return token_file.read_text(encoding="utf-8").strip() or None
    try:
        gh = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=30)
        if gh.returncode == 0 and gh.stdout.strip():
            return gh.stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    return None


def save_token(token: str) -> Path:
    root = repo_root()
    path = root / "state" / "github.token"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token.strip(), encoding="utf-8")
    if os.name == "nt":  # pragma: no cover — windows
        try:
            import ctypes

            ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x80)  # FILE_ATTRIBUTE_NORMAL
        except Exception:  # noqa: BLE001
            pass
    return path


def version() -> str:
    root = repo_root()
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        with pyproject.open("rb") as handle:
            data = tomllib.load(handle)
        val = data.get("project", {}).get("version")
        if val:
            return str(val)
    return "0.1.0"


def api(method: str, url: str, token: str, data=None, content_type: str | None = None) -> tuple[int, dict | list]:
    body = None
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if data is not None:
        if isinstance(data, bytes):
            body = data
            headers["Content-Type"] = content_type or "application/octet-stream"
        else:
            body = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=120) as resp:
            raw = resp.read()
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
            return resp.status, parsed
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")[:1000]
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"message": raw}
        return exc.code, parsed


def build_exe(root: Path) -> None:
    """Сборка dist/factory.exe (PyInstaller) — сам ставит, если нет."""
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "pyinstaller"],
                       check=True, timeout=600)
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--name", "factory",
           "--add-data", f"config{os.pathsep}config",
           "--add-data", f"schemas{os.pathsep}schemas",
           "--add-data", f"examples{os.pathsep}examples",
           "run.py"]
    subprocess.run(cmd, cwd=str(root), check=True, timeout=900)
    exe = root / "dist" / "factory.exe"
    if not exe.exists():
        raise RuntimeError("не создан dist/factory.exe")


def make_zip(root: Path) -> Path:
    """Упаковка dist/factory-core-win.zip (exe + конфиги + install/uninstall)."""
    exe = root / "dist" / "factory.exe"
    if not exe.exists():
        raise RuntimeError("сначала соберите exe (factory release соберёт сам)")
    package = root / "dist" / "package"
    shutil.rmtree(package, ignore_errors=True)
    package.mkdir(parents=True, exist_ok=True)
    shutil.copy2(exe, package / "factory.exe")
    for name in ("config", "schemas", "examples"):
        shutil.copytree(root / name, package / name, dirs_exist_ok=True)
    for name in ("README.md", "LICENSE"):
        if (root / name).exists():
            shutil.copy2(root / name, package / name)
    for name in ("install.ps1", "uninstall.ps1"):
        src = root / "scripts" / name
        if src.exists():
            shutil.copy2(src, package / name)
    if (root / "docs" / "REGION_PROTOCOL.md").exists():
        shutil.copy2(root / "docs" / "REGION_PROTOCOL.md", package / "REGION_PROTOCOL.md")
    zip_path = root / "dist" / "factory-core-win.zip"
    zip_path.unlink(missing_ok=True)
    shutil.make_archive(str(zip_path.with_suffix("")), "zip", root_dir=package)
    return zip_path


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True, timeout=300)


def _ensure_repo(repo: str, token: str, log) -> bool:
    """Создаём публичный репозиторий, если его ещё нет. Возвращает True, если создан."""
    status, _ = api("GET", f"{GITHUB_API}/repos/{repo}", token)
    if status == 200:
        return False
    owner, name = repo.split("/", 1)
    status, data = api("POST", f"{GITHUB_API}/user/repos", token, {
        "name": name,
        "description": "Фабрика Lab-100: пульт и диспетчер (подвижные домены, очередь задач, web/tray/cli)",
        "private": False,
        "has_issues": True,
        "has_wiki": False,
    })
    if status >= 300:
        raise RuntimeError(f"не удалось создать репозиторий {repo}: {data}")
    return True


def release(token: str, tag: str | None = None, draft: bool = False, skip_build: bool = False,
            repo: str = "Lab-100/factory-core", progress=None) -> dict:
    def log(message: str) -> None:
        print(message)
        if progress:
            progress(message)

    root = repo_root()
    tag = tag or f"v{version()}"

    # 1) сборка + упаковка Windows-версии
    if not skip_build:
        log("1/5 собираю factory.exe (PyInstaller)...")
        build_exe(root)
        log("2/5 упаковываю dist/factory-core-win.zip...")
    if not (root / "dist" / "factory.exe").exists():
        log("1/5 собираю factory.exe (PyInstaller)...")
        build_exe(root)
    make_zip(root)
    zip_path = root / "dist" / "factory-core-win.zip"
    log(f"   архив: {zip_path} ({zip_path.stat().st_size // 1024} КБ)")

    # 2) репозиторий (создаём, если нет) + ветка main + пуш
    if _ensure_repo(repo, token, log):
        log(f"   репозиторий {repo} создан")
    _git(root, "branch", "-M", "main")
    push_url = f"https://x-access-token:{token}@github.com/{repo}.git"
    push = _git(root, "push", "-u", push_url, "HEAD:main")
    log("3/5 пуш в main: " + ("OK" if push.returncode == 0 else push.stderr[-300:]))

    # 3) тег
    _git(root, "tag", "-a", tag, "-m", f"Фабрика Lab-100 {tag}")
    _git(root, "push", push_url, tag)
    log(f"4/5 тег {tag}")

    # 4) релиз (идемпотентно)
    status, release_data = api("POST", f"{GITHUB_API}/repos/{repo}/releases", token, {
        "tag_name": tag,
        "name": tag,
        "body": _release_body(),
        "draft": draft,
        "prerelease": False,
    })
    if status == 422:
        status, release_data = api("GET", f"{GITHUB_API}/repos/{repo}/releases/tags/{tag}", token)
    if status >= 400:
        raise RuntimeError(f"не удалось создать релиз {tag}: {release_data}")
    release_id = release_data["id"]
    log(f"5/5 релиз: {release_data.get('html_url')}")

    # 5) ассет (перезапись при повторе)
    asset_name = zip_path.name
    for asset in release_data.get("assets", []):
        if asset.get("name") == asset_name:
            api("DELETE", f"{GITHUB_API}/repos/{repo}/releases/assets/{asset['id']}", token)
    upload_url = f"{GITHUB_API}/repos/{repo}/releases/{release_id}/assets?name={urllib.parse.quote(asset_name)}"
    api_status, asset = api("POST", upload_url, token, zip_path.read_bytes(), content_type="application/zip")
    if api_status >= 400:
        raise RuntimeError(f"не удалось загрузить ассет: {asset}")
    log(f"   ассет: {asset.get('browser_download_url')}")
    return {"tag": tag, "release_url": release_data.get("html_url"), "asset": asset.get("browser_download_url")}


def _release_body() -> str:
    return (
        "Фабрика Lab-100 — пульт и диспетчер для объединения репозиториев Lab-100.\n\n"
        "- Подвижные (динамические) домены: config/domains.toml + профили, горячая перезагрузка.\n"
        "- Диспетчер задач invr.task/1: план → исполнение, режим auto/semi, журнал, очередь.\n"
        "- Адаптеры: Lab100, INVR-Tools, локальная LLM (Ollama), регион (region-spoof).\n"
        "- Интерфейсы: CLI, веб-дашборд (запуск задач, подтверждения шагов), трей.\n"
        "- Windows: factory.exe + install.ps1 (PATH, меню «Пуск»), uninstall.ps1.\n"
        "- Автоматический релиз: factory release."
    )