#!/usr/bin/env python3
"""regionctl — CLI-протокол управления регионом для region-spoof.

Назначение: дать программе-фабрике (factory-core) возможность переключать
регион автоматически, не открывая GUI.

Размещение: положить этот файл в корень репозитория region-spoof
(рядом с config.json). Фабрика находит его сама
(см. factory/regionlib.py::find_cli).

Контракт (см. docs/REGION_PROTOCOL.md):
    regionctl.py status
    regionctl.py list
    regionctl.py switch --region US [--zone foreign|local]

В последней строке stdout печатается JSON. Пример:
    {"ok": true, "region": "US", "zone": "foreign", "applied": false}

ВАЖНО: этот скрипт правит config.json. Чтобы запущенный GUI применил смену
сразу, добавьте вызов regionctl из самого приложения (кнопка/таймер) или
реализуйте чтение config.json на лету. Файл-сигнал: создаётся
regionctl.signal рядом с config.json как намёк приложению перечитать настройки.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

CONFIG = Path(__file__).resolve().parent / "config.json"
SIGNAL = Path(__file__).resolve().parent / "regionctl.signal"


def load() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8-sig"))


def save(config: dict) -> None:
    CONFIG.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    SIGNAL.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")


def status() -> dict:
    config = load()
    return {
        "ok": True,
        "region": config.get("region"),
        "zone": config.get("zone"),
        "strategy": config.get("strategy"),
        "last_working": config.get("last_working"),
        "available_regions": [r.get("code") for r in config.get("regions", [])],
        "applied": False,
    }


def switch(region: str | None = None, zone: str | None = None) -> dict:
    config = load()
    if region:
        config["region"] = region
        config["zone"] = "foreign"
    if zone:
        config["zone"] = zone
    save(config)
    return status()


def listing() -> dict:
    config = load()
    return {
        "ok": True,
        "regions": config.get("regions", []),
        "strategies": config.get("strategies", []),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="regionctl", description="Управление регионом region-spoof")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("status", help="текущий регион/зона")
    sub.add_parser("list", help="доступные регионы/стратегии")
    switch_parser = sub.add_parser("switch", help="переключить регион")
    switch_parser.add_argument("--region", default=None, help="код региона, напр. US, DE, NL")
    switch_parser.add_argument("--zone", default=None, choices=["foreign", "local"], help="зона")
    args = parser.parse_args(argv)

    if args.command in (None, "status"):
        result = status()
    elif args.command == "list":
        result = listing()
    else:
        result = switch(args.region, args.zone)

    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
