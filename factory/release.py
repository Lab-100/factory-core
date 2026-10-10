"""Автоматический выпуск релиза + Windows-версии factory-core.

`factory release` делает весь конвейер сам:
  1. сборка factory.exe (PyInstaller, при необходимости ставится сам);
  2. упаковка dist/factory-core-win.zip;
  3. тег v<version> (версия из pyproject.toml) + пуш в main;
  4. создание GitHub Release и загрузка zip-ассета.

Токен: переменная окружения GITHUB_TOKEN, либо файл state/github.token
(записывается командой `factory auth login`, каталог state/ в .gitignore),
либо `gh auth token`, если установлен GitHub CLI.

Пуш идёт через git-протокол, а при его недоступности (например, всплывающее
окно Git Credential Manager) — автоматически через Git Data API, без окон.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import tomllib
import urllib.parse
import urllib.request
from pathlib import Path

from .i18n import make_translator
from .paths import repo_root
from .settings import load as _load_settings

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


def _try_git_push(push_url: str, root: Path, ref: str, timeout: int = 60) -> bool:
    """Пробуем git push с ограниченным временем; True, если прошёл.
    Без credential-окна: GIT_TERMINAL_PROMPT=0 + отключённый helper."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GCM_INTERACTIVE", "Never")
    try:
        proc = subprocess.run(
            ["git", "-c", "credential.helper=", "push", "--force", push_url, ref],
            cwd=str(root), capture_output=True, text=True, timeout=timeout, env=env)
        return proc.returncode == 0
    except subprocess.TimeoutExpired:
        return False


def _api_create_ref(token: str, repo: str, ref: str, sha: str) -> None:
    """Указатель (ветка/тег) через Git Data API. ref без префикса refs/."""
    status, data = api("POST", f"{GITHUB_API}/repos/{repo}/git/refs", token,
                       {"ref": f"refs/{ref}", "sha": sha})
    if status == 422:
        api("PATCH", f"{GITHUB_API}/repos/{repo}/git/refs/{ref}", token,
            {"sha": sha, "force": True})


def _api_bootstrap(token: str, repo: str, log, tr) -> None:
    """Пустой репозиторий GitHub заперт для blobs/trees/commits, пока нет
    первого коммита. Создаём его через Contents API (PUT файла)."""
    log(tr("rel.bootstrap"))
    status, data = api("PUT", f"{GITHUB_API}/repos/{repo}/contents/.factory-bootstrap", token, {
        "message": "Фабрика Lab-100 — начальный коммит",
        "content": base64.b64encode(b"factory-core\n").decode("ascii"),
    })
    if status >= 300:
        raise RuntimeError(f"не удалось создать стартовый коммит: {data}")


def _api_push(token: str, repo: str, root: Path, log, tr) -> str:
    """Пуш всех отслеживаемых файлов через Git Data API (без git-протокола).
    Возвращает sha созданного коммита на remote."""
    listing = subprocess.run(["git", "ls-files"], cwd=str(root),
                             capture_output=True, text=True, timeout=30)
    files = [line for line in listing.stdout.splitlines() if line.strip()]
    log(tr("rel.api_loading", count=len(files)))
    blobs: dict[str, str] = {}
    for rel in files:
        path = root / rel
        if not path.is_file():
            continue
        payload = {
            "content": base64.b64encode(path.read_bytes()).decode("ascii"),
            "encoding": "base64",
        }
        status, data = api("POST", f"{GITHUB_API}/repos/{repo}/git/blobs", token, payload)
        if status == 409 and "empty" in str(data.get("message", "")).lower():
            _api_bootstrap(token, repo, log, tr)
            status, data = api("POST", f"{GITHUB_API}/repos/{repo}/git/blobs", token, payload)
        if status >= 300:
            raise RuntimeError(f"не удалось загрузить blob {rel}: {data}")
        blobs[rel] = data["sha"]
    tree = [{"path": rel, "mode": "100644", "type": "blob", "sha": sha}
            for rel, sha in sorted(blobs.items())]
    status, data = api("POST", f"{GITHUB_API}/repos/{repo}/git/trees", token,
                       {"tree": tree})
    if status >= 300:
        raise RuntimeError(f"не удалось создать tree: {data}")
    parents: list[str] = []
    status, ref_data = api("GET", f"{GITHUB_API}/repos/{repo}/git/ref/heads/main", token)
    if status == 200:
        parents = [ref_data["object"]["sha"]]
    status, data = api("POST", f"{GITHUB_API}/repos/{repo}/git/commits", token, {
        "message": "Фабрика Lab-100 — автоматический выпуск (Git Data API)",
        "tree": data["sha"],
        "parents": parents,
    })
    if status >= 300:
        raise RuntimeError(f"не удалось создать commit: {data}")
    commit_sha = data["sha"]
    _api_create_ref(token, repo, "heads/main", commit_sha)
    log(f"   push OK: main -> {commit_sha[:8]}")
    return commit_sha


def _push_and_tag(token: str, repo: str, root: Path, tag: str, push_url: str, log, tr) -> str:
    """Пуш main и тега. Возвращает sha коммита на remote."""
    if _try_git_push(push_url, root, "HEAD:main"):
        log(tr("rel.push_git"))
        head = _git(root, "rev-parse", "HEAD").stdout.strip()
        _git(root, "tag", "-f", "-a", tag, "-m", f"Фабрика Lab-100 {tag}")
        if not _try_git_push(push_url, root, tag):
            log(tr("rel.tag_git_fail"))
            _api_create_ref(token, repo, f"tags/{tag}", head)
        return head
    sha = _api_push(token, repo, root, log, tr)
    log(tr("rel.push_api"))
    _api_create_ref(token, repo, f"tags/{tag}", sha)
    return sha


def release(token: str, tag: str | None = None, draft: bool = False, skip_build: bool = False,
            repo: str = "Lab-100/factory-core", progress=None, translator=None) -> dict:
    def log(message: str) -> None:
        print(message)
        if progress:
            progress(message)

    root = repo_root()
    tr = translator or make_translator(_load_settings())
    tag = tag or f"v{version()}"

    # 1) сборка + упаковка Windows-версии
    if not skip_build:
        log(tr("rel.step1"))
        build_exe(root)
        log(tr("rel.step2"))
    if not (root / "dist" / "factory.exe").exists():
        log(tr("rel.step1"))
        build_exe(root)
    make_zip(root)
    zip_path = root / "dist" / "factory-core-win.zip"
    log(tr("rel.archive", path=zip_path, size=zip_path.stat().st_size // 1024))

    # 2) репозиторий (создаём, если нет) + ветка main + пуш
    if _ensure_repo(repo, token, log):
        log(tr("rel.repo_created", repo=repo))
    _git(root, "branch", "-M", "main")
    push_url = f"https://x-access-token:{token}@github.com/{repo}.git"

    # 3) пуш + тег: сначала git-протокол, при недоступности — Git Data API
    _push_and_tag(token, repo, root, tag, push_url, log, tr)
    log(tr("rel.tag", tag=tag))

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
    log(tr("rel.release", url=release_data.get("html_url")))

    # 5) ассет (перезапись при повторе)
    asset_name = zip_path.name
    for asset in release_data.get("assets", []):
        if asset.get("name") == asset_name:
            api("DELETE", f"{GITHUB_API}/repos/{repo}/releases/assets/{asset['id']}", token)
    upload_url = release_data.get("upload_url") or \
        f"https://uploads.github.com/repos/{repo}/releases/{release_id}/assets"
    upload_url = upload_url.split("{", 1)[0]
    upload_url = f"{upload_url}?name={urllib.parse.quote(asset_name)}"
    api_status, asset = api("POST", upload_url, token, zip_path.read_bytes(), content_type="application/zip")
    if api_status >= 400:
        raise RuntimeError(f"не удалось загрузить ассет: {asset}")
    log(tr("rel.asset", url=asset.get("browser_download_url")))
    return {"tag": tag, "release_url": release_data.get("html_url"), "asset": asset.get("browser_download_url")}


def _release_body() -> str:
    return (
        "Фабрика Lab-100 — пульт и диспетчер для объединения репозиториев Lab-100.\n\n"
        "- Подвижные (динамические) домены: config/domains.toml + профили, горячая перезагрузка.\n"
        "- Диспетчер задач invr.task/1: план → исполнение, режим auto/semi, журнал, очередь.\n"
        "- Адаптеры: Lab100, INVR-Tools, локальная LLM (Ollama), регион (region-spoof).\n"
        "- Интерфейсы: CLI, веб-дашборд (запуск задач, подтверждения шагов), трей.\n"
        "- Языки интерфейсов: русский (по умолчанию), English, 中文 (ui.lang).\n"
        "- Windows: factory.exe + install.ps1 (PATH, меню «Пуск»), uninstall.ps1.\n"
        "- Автоматический релиз: factory release (без credential-окон Git)."
    )