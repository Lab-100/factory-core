"""Логика управления region-spoof (общая для адаптера и CLI).

Реализует простой протокол: найти репозиторий region-spoof, прочитать/записать
его config.json (region/zone), вернуть состояние. Если у region-spoof появится
собственный CLI (см. docs/REGION_PROTOCOL.md), адаптер сможет звать его вместо
прямой правки конфига.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def find_repo(settings=None) -> Path | None:
    candidates: list[Path] = []
    if settings is not None:
        configured = str(settings.get("adapters.region_repo") or "")
        if configured:
            candidates.append(Path(configured))
    for env_name in ("REGION_SPOOF_DIR", "REGION_SPOOF_ROOT"):
        value = os.environ.get(env_name)
        if value:
            candidates.append(Path(value))
    if settings is not None:
        root = settings.path.parent.parent
        candidates += [root.parent / "region-spoof", root / "region-spoof", Path.cwd() / "region-spoof"]
    for candidate in candidates:
        if candidate and (candidate / "config.json").exists():
            return candidate
    return None


def load_config(repo: str | Path) -> dict:
    return json.loads((Path(repo) / "config.json").read_text(encoding="utf-8-sig"))


def save_config(repo: str | Path, config: dict) -> None:
    (Path(repo) / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def status(repo: str | Path) -> dict:
    config = load_config(repo)
    return {
        "repo": str(repo),
        "region": config.get("region"),
        "zone": config.get("zone"),
        "strategy": config.get("strategy"),
        "last_working": config.get("last_working"),
        "available_regions": [r.get("code") for r in config.get("regions", [])],
    }


def switch(repo: str | Path, region: str | None = None, zone: str | None = None) -> dict:
    config = load_config(repo)
    if region:
        config["region"] = region
        config["zone"] = "foreign"
    if zone:
        config["zone"] = zone
    save_config(repo, config)
    return status(repo)


def find_cli(repo: str | Path) -> list[str] | None:
    """Внешний CLI-протокол region-spoof, если он есть (см. docs/REGION_PROTOCOL.md)."""
    repo = Path(repo)
    for name in ("regionctl.py", "region-spoof.py"):
        script = repo / name
        if script.exists():
            return [sys.executable, str(script)]
    for name in ("region-spoof.cmd", "region-spoof.bat", "region-spoof.exe"):
        binary = repo / name
        if binary.exists():
            return [str(binary)]
    exe = shutil.which("region-spoof")
    if exe:
        return [exe]
    return None


def call_cli(cli: list[str], action: str, **kwargs) -> dict:
    argv = [*cli, action]
    for key, value in kwargs.items():
        if value is not None:
            argv += [f"--{key.replace('_', '-')}", str(value)]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    out = proc.stdout.strip()
    try:
        data = json.loads(out.splitlines()[-1]) if out else {}
    except json.JSONDecodeError:
        data = {"raw": out}
    data.setdefault("ok", proc.returncode == 0)
    if proc.returncode != 0 and "error" not in data:
        data["error"] = (proc.stderr.strip() or f"exit {proc.returncode}")[-500:]
    data["argv"] = argv
    return data
