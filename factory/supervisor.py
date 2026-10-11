"""Супервизор арсенала — «что работает / что нет» + авто-восстановление.

Развивает :mod:`factory.watchdog` (тот следит за адаптерами и очередью) до
полного арсенала: инструменты, службы, агенты и окружение OpenCode.

Ключевые возможности:

  * **снимок состояния** всех узлов (готовый для дашборда и CLI);
  * **режим работы**: ``attached`` — вместе с OpenCode (чужie узлы не трогаем,
    читаем состояние mcp-watchdog), ``standalone`` — автономно без OpenCode;
  * **авто-восстановление** служб с ``owner = "factory"`` по настройке
    (``supervision.autostart``; ``auto`` → включено только в standalone);
  * **ручные действия** ``up`` / ``down`` / ``restart`` с проверкой права
    управления (coexistence — не мешать владельцам).

Все наблюдения и действия пишутся в ``state/supervisor.jsonl`` и в единый
журнал (``ledger.jsonl``, события ``kind = "supervise"``).
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import arsenal, nodes
from .ledger import Ledger
from .paths import repo_root

MODES = ("attached", "standalone")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Supervisor:
    def __init__(self, settings, root=None, ledger: Ledger | None = None):
        self.settings = settings
        self.root = Path(root) if root else repo_root()
        self.ledger = ledger or Ledger(
            self.root / str(settings.get("ledger_path", "state/ledger.jsonl")))
        self.log_path = self.root / "state" / "supervisor.jsonl"
        self._starts: list[float] = []
        self._last_states: dict[str, str] = {}
        self._no_heal = False

    # --- режим и политика ---
    @property
    def mode(self) -> str:
        value = str(self.settings.get("operation.mode", "attached") or "attached")
        return value if value in MODES else "attached"

    @property
    def coexistence(self) -> bool:
        return bool(self.settings.get("supervision.coexistence", True))

    @property
    def autostart(self) -> bool:
        if self._no_heal:
            return False
        raw = self.settings.get("supervision.autostart", "auto")
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in ("true", "on", "yes", "1"):
            return True
        if text in ("false", "off", "no", "0"):
            return False
        return self.mode == "standalone"  # auto

    def _max_starts(self) -> int:
        try:
            return int(self.settings.get("supervision.max_starts_per_hour", 6))
        except (TypeError, ValueError):
            return 6

    def _budget_ok(self) -> bool:
        limit = self._max_starts()
        if limit <= 0:
            return True
        cutoff = time.time() - 3600
        self._starts = [t for t in self._starts if t >= cutoff]
        return len(self._starts) < limit

    # --- снимок ---
    def snapshot(self, *, timeout: float = 1.5, save: bool = True) -> dict:
        data = arsenal.build(self.settings)
        result = nodes.snapshot(self.settings, data, mode=self.mode, timeout=timeout)
        self._bridge_watchdog(result["nodes"])
        self._resummarize(result)
        result["generated"] = _now()
        result["base"] = str(self.root)
        result["arsenal_summary"] = data.get("summary", {})
        if save:
            self._save_snapshot(result)
        return result

    def _save_snapshot(self, result: dict) -> None:
        try:
            path = self.root / str(self.settings.get("arsenal.path", "state/arsenal.json"))
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"schema": "invr.arsenal.snapshot/1",
                       "generated": result.get("generated"),
                       "mode": result.get("mode"),
                       "summary": result.get("summary"),
                       "nodes": result.get("nodes", []),
                       "arsenal_summary": result.get("arsenal_summary", {})}
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        except OSError:
            pass

    # --- мост к mcp-watchdog (attached) ---
    def _bridge_watchdog(self, node_list: list[dict]) -> None:
        """Прочитать состояние mcp-watchdog и уточнить чужие (opencode) узлы."""
        if self.mode != "attached":
            return
        raw = str(self.settings.get("sources.mcp_watchdog_state", "") or "").strip()
        if not raw:
            return
        path = Path(raw)
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
        except (OSError, json.JSONDecodeError):
            return
        table = self._watchdog_table(data)
        if not table:
            return
        mapping = {"active": "active", "idle": "idle", "unloaded": "idle",
                   "need-guard": "need-guard", "fault": "fault"}
        for node in node_list:
            if node.get("owner") == "factory":
                continue
            key = str(node.get("name") or "").lower()
            state = table.get(key)
            if not state:
                continue
            node["watchdog_state"] = state
            node["source_state"] = "mcp-watchdog"
            mapped = mapping.get(str(state).lower())
            if mapped:
                node["state"] = mapped

    @staticmethod
    def _watchdog_table(data: dict) -> dict[str, str]:
        """Терпимо вытащить таблицу «узел → состояние» из current.json."""
        if not isinstance(data, dict):
            return {}
        table: dict[str, str] = {}
        for key in ("nodes", "services", "state", "current"):
            section = data.get(key)
            if isinstance(section, dict):
                for name, value in section.items():
                    if isinstance(value, dict) and value.get("state"):
                        table[str(name).lower()] = str(value["state"])
                    elif isinstance(value, str):
                        table[str(name).lower()] = value
        # плоский формат {name: state}
        for name, value in data.items():
            if isinstance(value, str) and value in (
                    "active", "idle", "unloaded", "need-guard", "fault"):
                table.setdefault(str(name).lower(), value)
        return table

    @staticmethod
    def _resummarize(result: dict) -> None:
        counts: dict[str, int] = {state: 0 for state in nodes.STATES}
        for node in result.get("nodes", []):
            counts[node["state"]] = counts.get(node["state"], 0) + 1
        summary = result.setdefault("summary", {})
        summary["total"] = len(result.get("nodes", []))
        summary["by_state"] = counts
        summary["working"] = counts.get("active", 0)
        summary["attention"] = sum(counts.get(s, 0) for s in nodes.ATTENTION)

    # --- действия ---
    def can_manage(self, element: dict) -> tuple[bool, str]:
        if element.get("meta", {}).get("docker"):
            return False, "служба исключена (проект без Docker)"
        if element.get("class") != "service":
            return False, "управление доступно только для служб"
        owner = str(element.get("owner") or "external")
        if owner == "factory":
            return True, ""
        if self.coexistence:
            return False, f"владелец {owner}: coexistence включён — не вмешиваемся"
        if self.mode != "standalone":
            return False, f"владелец {owner}: управление только в режиме standalone"
        return True, ""

    def action(self, element_id: str, act: str, *, timeout: float = 20.0) -> dict:
        """Выполнить действие над службой: up | down | restart."""
        data = arsenal.build(self.settings)
        element = arsenal.by_id(data).get(element_id)
        if element is None:
            return {"ok": False, "error": f"элемент не найден: {element_id}"}
        allowed, reason = self.can_manage(element)
        if not allowed:
            self._record_action(element_id, act, ok=False, detail=reason)
            return {"ok": False, "error": reason, "element": element_id}

        meta = element.get("meta") or {}
        start = list(meta.get("start") or [])
        stop = list(meta.get("stop") or [])
        results: list[dict] = []
        if act in ("down", "restart"):
            results.append(self._exec(stop, timeout=timeout, background=False, label="stop"))
        if act in ("up", "restart"):
            results.append(self._exec(start, timeout=timeout, background=True, label="start"))
        ok = all(item["ok"] for item in results) if results else False
        detail = "; ".join(f"{r['label']}: {r['detail']}" for r in results) or "нет команды"
        self._record_action(element_id, act, ok=ok, detail=detail)
        return {"ok": ok, "element": element_id, "action": act,
                "detail": detail, "steps": results}

    @staticmethod
    def _exec(command: list, *, timeout: float, background: bool, label: str) -> dict:
        if not command:
            return {"label": label, "ok": False, "detail": "команда не задана"}
        try:
            if background:
                flags = 0
                if sys.platform.startswith("win"):
                    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | \
                            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                subprocess.Popen(command, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                 creationflags=flags, close_fds=True)
                return {"label": label, "ok": True, "detail": "запущено"}
            proc = subprocess.run(command, capture_output=True, text=True,
                                  timeout=timeout, check=False)
            ok = proc.returncode == 0
            detail = (proc.stdout or proc.stderr or "").strip().splitlines()
            return {"label": label, "ok": ok,
                    "detail": (detail[0] if detail else f"код {proc.returncode}")}
        except FileNotFoundError:
            return {"label": label, "ok": False, "detail": f"команда не найдена: {command[0]}"}
        except Exception as exc:  # noqa: BLE001 - сбой запуска не критичен
            return {"label": label, "ok": False, "detail": f"{type(exc).__name__}: {exc}"}

    def _record_action(self, element_id: str, act: str, *, ok: bool, detail: str) -> None:
        try:
            self.ledger.append({"kind": "supervise.action", "element": element_id,
                                "action": act, "ok": ok, "detail": detail})
        except OSError:
            pass

    # --- цикл ---
    def tick(self, *, timeout: float = 1.5) -> dict:
        snapshot = self.snapshot(timeout=timeout)
        actions: list[dict] = []
        if self.autostart:
            for node in snapshot.get("nodes", []):
                if node.get("state") != "fault" or node.get("owner") != "factory":
                    continue
                if node.get("class") != "service":
                    continue
                element = self._element_for(node, snapshot)
                if element is None:
                    continue
                allowed, _ = self.can_manage(element)
                if not allowed or not (element.get("meta") or {}).get("start"):
                    continue
                if not self._budget_ok():
                    actions.append({"action": "skip-budget", "element": node["id"]})
                    break
                result = self.action(node["id"], "up", timeout=timeout)
                self._starts.append(time.time())
                actions.append({"action": "auto-up", "element": node["id"],
                                "ok": result.get("ok"), "detail": result.get("detail")})
        # фиксируем смены состояний
        for node in snapshot.get("nodes", []):
            previous = self._last_states.get(node["id"])
            if previous is not None and previous != node["state"]:
                actions.append({"action": "state-change", "element": node["id"],
                                "from": previous, "to": node["state"]})
            self._last_states[node["id"]] = node["state"]

        result = {
            "ts": _now(),
            "mode": self.mode,
            "summary": snapshot.get("summary", {}),
            "actions": actions,
        }
        self._record(result)
        return result

    def _element_for(self, node: dict, snapshot: dict) -> dict | None:
        # пересобираем арсенал, но кэшируем на время тика через by_id
        data = getattr(self, "_arsenal_cache", None)
        if data is None:
            data = arsenal.build(self.settings)
            self._arsenal_cache = data
        return arsenal.by_id(data).get(node["id"])

    def _record(self, result: dict) -> None:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(result, ensure_ascii=False) + "\n")
        except OSError:
            pass
        if result.get("actions"):
            try:
                self.ledger.append({
                    "kind": "supervise",
                    "mode": result.get("mode"),
                    "working": result.get("summary", {}).get("working"),
                    "attention": result.get("summary", {}).get("attention"),
                    "actions": result["actions"],
                })
            except OSError:
                pass

    def run(self, interval: float = 30, iterations: int | None = None, on_tick=None) -> int:
        done = 0
        while True:
            self._arsenal_cache = None
            result = self.tick()
            if on_tick is not None:
                on_tick(result)
            done += 1
            if iterations is not None and done >= iterations:
                break
            time.sleep(interval)
        return done
