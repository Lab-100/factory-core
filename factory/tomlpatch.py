"""Патч TOML-файла по месту: меняет значение ключа, сохраняя комментарии.

Поддерживает только то, что нужно настройкам фабрики: скалярные значения и
простые таблицы (без массивов таблиц/вложенных inline-структур).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import tomlw

_HEADER = re.compile(r"^\s*\[([^\[\]]+)\]\s*$")


def _header_name(line: str) -> str | None:
    match = _HEADER.match(line)
    return match.group(1).strip() if match else None


def apply(path: str | Path, dotted: str, value: Any) -> None:
    path = Path(path)
    parts = dotted.split(".")
    section = ".".join(parts[:-1])
    key = parts[-1]
    formatted = tomlw._fmt(value)

    lines: list[str] = path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    # Границы нужной секции
    start = 0
    end = len(lines)
    if not section:
        for index, line in enumerate(lines):
            if _header_name(line) is not None:
                end = index
                break
    if section:
        header_index = None
        for index, line in enumerate(lines):
            if _header_name(line) == section:
                header_index = index
                break
        if header_index is None:
            # создаём секцию в конце файла
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(f"[{section}]")
            lines.append(f"{key} = {formatted}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
            return
        start = header_index + 1
        for index in range(start, len(lines)):
            if _header_name(lines[index]) is not None:
                end = index
                break

    key_re = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for index in range(start, end):
        if key_re.match(lines[index]):
            indent = lines[index][: len(lines[index]) - len(lines[index].lstrip())]
            lines[index] = f"{indent}{key} = {formatted}"
            break
    else:
        # вставляем в конец секции, пропуская пустые строки перед следующей
        insert_at = end
        while insert_at > start and lines[insert_at - 1].strip() == "":
            insert_at -= 1
        lines.insert(insert_at, f"{key} = {formatted}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
