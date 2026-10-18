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
    result_text = ""
    done_text = ""
    fail_reason = ""
    for raw in lines:
        line = raw.strip()
        if not line.startswith("{"):
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = str(item.get("type") or "")
        if t == "run_result":
            text = item.get("text")
            if text:
                result_text = str(text)
            continue
        if t == "agent_event":
            ev = item.get("event") or {}
            if ev.get("type") == "done":
                text = ev.get("text")
                if text:
                    done_text = str(text)
                reason = str(ev.get("reason") or "")
                if reason and reason not in ("done", "completed", "success", "stop"):
                    fail_reason = reason
    text = result_text or done_text
    if text:
        return text.strip()
    if fail_reason:
        return "[Cline: %s]" % fail_reason
    return ""


def main(argv) -> int:
    if len(argv) < 1:
        sys.stderr.write("usage: cline_bridge.py <cline-cli> [model] [args...]\n")
        return 2
    cmd = argv[0]
    model = str(argv[1]).strip() if len(argv) > 1 else ""
    try:
        prompt = sys.stdin.read()
    except Exception:  # noqa: BLE001
        prompt = ""

    args = [cmd, "--json", "--auto-approve", "true"]
    if model:
        args.extend(["-m", model])
    args.append(prompt)
    proc = subprocess.run(
        args,
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