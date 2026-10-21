"""Запуск нужных служб перед задачей — «фабрика поднимает процесс и сообщает».

Проверяет арсенал: если служба (owner=factory, enabled) требуется запросом
(infra/вузлы) и сейчас не в строю (state != active/idle), поднимает её через
супервизор и возвращает список заметок для чата сессии.
"""
from __future__ import annotations

import threading
from pathlib import Path

from . import arsenal, nodes
from .ledger import Ledger
from .paths import repo_root


class ServiceStarter:
    def __init__(self, settings, root=None, ledger: Ledger | None = None):
        self.settings = settings
        self.root = Path(root) if root else repo_root()
        self.ledger = ledger or Ledger(
            self.root / str(settings.get("ledger_path", "state/ledger.jsonl")))
        self._lock = threading.Lock()

    # --- какие службы нужны ---
    def _required_services(self) -> list[str]:
        """Службы, которые фабрика держит в строю и поднимает по требованию."""
        names: list[str] = []
        if self.settings.get("adapters.llm_local_enabled", False):
            names.append("ollama")
        if str(self.settings.get("routing.answer", "") or "").strip() in ("external_agent", "llm_local") or \
           str(self.settings.get("routing.code", "") or "").strip() in ("external_agent", "llm_local"):
            names.append("ollama")
        # web-чаты: адаптер web_chat
        if self.settings.get("providers.web_chat.enabled", False) or \
           str(self.settings.get("routing.web-chat", "") or "").strip():
            names.append("webchat")
        return sorted({name for name in names if name})

    # --- состояние ---
    def _state_of(self, data: dict, element_id: str) -> str | None:
        elements = arsenal.by_id(data)
        el = elements.get(element_id)
        if el is None:
            return None
        probe = el.get("probe") or {}
        # Простейшая проверка: для http — доступность url уже в nodes.snapshot,
        # здесь доверяем арсеналу: enabled службы достаточно для запуска.
        return el.get("available") and "active" or ("idle" if el.get("enabled") else "unloaded")

    # --- запуск ---
    def start_if_needed(self, session_id: str | None = None,
                        on_note=None) -> list[dict]:
        """Поднять недостающие службы; вернуть список заметок {text}."""
        from .supervisor import Supervisor

        notes: list[dict] = []
        if not self.settings.get("supervision.autostart", "auto"):
            return notes
        sup = Supervisor(self.settings, root=self.root, ledger=self.ledger)
        data = arsenal.build(self.settings)
        required = self._required_services()
        for name in required:
            element = arsenal.by_id(data).get(f"service:{name}")
            if element is None:
                continue
            if not element.get("enabled"):
                continue
            # состояние из арсенала/узлов
            state = self._state_of(data, f"service:{name}")
            if state in ("active", "idle"):
                continue
            allowed, _reason = sup.can_manage(element)
            if not allowed:
                continue
            started = sup.action(f"service:{name}", "up", timeout=30)
            if started.get("ok"):
                note = {"text": f"🚀 служба {name} запущена фабрикой по запросу",
                        "kind": "startup"}
                notes.append(note)
                if on_note:
                    on_note(note["text"])
            else:
                note = {"text": f"⚠️ не удалось поднять службу {name}: "
                                f"{started.get('detail', '')}",
                        "kind": "startup-error"}
                notes.append(note)
                if on_note:
                    on_note(note["text"])
        return notes