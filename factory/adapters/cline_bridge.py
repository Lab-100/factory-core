"""Мост external_agent -> Cline.

Читает промпт из stdin и вызывает CLI Cline (cline-cli), возвращая его ответ
в stdout. Нужен, потому что фабрика запускает внешнего агента как
`subprocess.run(cmd, input=prompt)`: промпт подаётся через stdin, а ответ
считывается из stdout. CLI Cline принимает промпт аргументом, поэтому здесь
он передаётся отдельно, а итоговый текст извлекается из JSON-вывода.

Использование:
    python cline_bridge.py <cline-cli> [аргументы Cline...]
    промпт подаётся в stdin.
"""

from __future__ import annotations

import json
import subprocess
import sys


def extract_text(lines) -> str:
    """Собирает итоговый текстовый ответ из JSON-строк вывода Cline."""
    parts = []
    for raw in lines:
        line = raw.strip()
        if not line.startswith("{"):
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = str(item.get("type") or "")
        # Итоговый текст бывает в run_result.text и в agent_event.event.text
        if t == "run_result":
            text = item.get("text")
            if text:
                parts.append(str(text))
            continue
        if t == "agent_event":
            ev = item.get("event") or {}
            if ev.get("type") == "done":
                text = ev.get("text")
                if text:
                    parts.append(str(text))
                reason = ev.get("reason")
                if reason and str(reason) != "done":
                    parts.append("[Cline: %s]" % reason)
    return "\n".join(p for p in parts if p).strip()


def main(argv) -> int:
    if len(argv) < 1:
        sys.stderr.write("usage: cline_bridge.py <cline-cli> [args...]\n")
        return 2
    cmd = argv[0]
    try:
        prompt = sys.stdin.read()
    except Exception:  # noqa: BLE001
        prompt = ""

    proc = subprocess.run(
        [cmd, "--json", "--auto-approve", "true", prompt],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip() or proc.stdout
        sys.stdout.write(detail[:2000])
        return proc.returncode
    out = extract_text((proc.stdout or "").splitlines()) or (proc.stdout or "").strip()
    sys.stdout.write(out[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))