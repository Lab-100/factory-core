"""Минимальный сериализатор TOML: только то, что нужно настройкам и профилям."""
from __future__ import annotations

from typing import Any


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(value, list):
        return "[" + ", ".join(_fmt(v) for v in value) + "]"
    raise TypeError(f"unsupported TOML value: {type(value)!r}")


def _emit(table: dict, path: list[str], lines: list[str], header: bool) -> None:
    scalars = {k: v for k, v in table.items() if not isinstance(v, dict)}
    subtables = {k: v for k, v in table.items() if isinstance(v, dict)}
    if header:
        if lines and lines[-1] != "":
            lines.append("")
        lines.append("[" + ".".join(path) + "]")
    for key, value in scalars.items():
        lines.append(f"{key} = {_fmt(value)}")
    for key, value in subtables.items():
        _emit(value, path + [key], lines, header=True)


def dumps(data: dict) -> str:
    lines: list[str] = []
    _emit(data, [], lines, header=False)
    return "\n".join(lines).rstrip() + "\n"
