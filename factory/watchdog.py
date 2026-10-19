"""Супервизия фабрики — этап 5 дорожной карты («фабрика работает сама»).

Watchdog периодически проверяет фабрику и самовосстанавливается:

  * перечитывает домены при изменении конфигов (горячая перезагрузка);
  * следит за доступностью адаптеров (фиксирует смены состояния);
  - снимает зависшие задачи очереди (`running`/`awaiting-approval` дольше порога).

Все наблюдения и действия пишутся в `state/watchdog.jsonl` и в единый журнал
(`ledger.jsonl`, события `kind = "watchdog"`).
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from .adapters import build_adapters
from .context import build_registry
from .jobs import JobManager
from .ledger import Ledger
from .paths import repo_root


class Watchdog:
    def __init__(self, settings, root=None, ledger: Ledger | None = None):
        self.settings = settings
        self.root = Path(root) if root else repo_root()
        self.ledger = ledger or Ledger(self.root / str(settings.get("ledger_path", "state/ledger.jsonl")))
        self.log_path = self.root / "state" / "watchdog.jsonl"
        self._mtimes: dict[str, float] = {}
        self._health: dict[str, bool] = {}

    def _path(self, key: str, default: str) -> Path:
        return self.root / str(self.settings.get(key, default))

    @staticmethod
    def _mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    def tick(self) -> dict:
        actions: list[dict] = []

        # 1) горячая перезагрузка домены при изменении конфигов
        watched = {
            "domains": self._path("domains_file", "config/domains.toml"),
            "profiles": self._path("profiles_dir", "config/profiles"),
        }
        reloaded = False
        for key, path in watched.items():
            current = self._mtime(path)
            previous = self._mtimes.get(key)
            if previous is not None and current != previous:
                reloaded = True
                actions.append({"action": "reload-domains", "reason": f"{key} changed"})
            self._mtimes[key] = current
        if reloaded:
            build_registry(self.root, self.settings)

        # 2) здоровье адаптеров и переходы доступности
        health: dict[str, bool] = {}
        for name, adapter in build_adapters(self.settings).items():
            try:
                health[name] = bool(adapter.available())
            except Exception:  # noqa: BLE001
                health[name] = False
            previous = self._health.get(name)
            if previous is not None and previous != health[name]:
                actions.append({"action": "adapter-change", "adapter": name,
                                "from": previous, "to": health[name]})
        self._health = health

        # 3) самовосстановление очереди задач
        healed = self.heal_jobs()
        actions += [{"action": "job-timeout", "job": job_id} for job_id in healed]

        # 4) фоновая уборка WorkAI, поднятого мостом: живёт только пока идёт
        #    серия запросов. Если последний запрос был давно (порог
        #    watchdog.workai_idle_s, по умолчанию 300 с) и активных задач нет —
        #    окно закрывается, маркер снимается.
        actor_wrk = self._collect_idle_workai()
        actions.extend(actor_wrk)

        result = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "health": health,
            "actions": actions,
        }
        self._record(result)
        return result

    def heal_jobs(self) -> list[str]:
        timeout = float(self.settings.get("watchdog.stuck_after_s", 600))
        return JobManager(self.root).heal_stuck(timeout)

    def _collect_idle_workai(self) -> list[dict]:
        """Закрыть WorkAI, поднятый мостом external_agent->WorkAI, если он
        простаивает дольше порога и нет активных задач.

        Мост-экземпляр оставляет окно после ответа (серия запросов идёт на
        одном окне) и пишет маркер в ``state/workai_bridge.txt``. Watchdog
        периодически проверяет: активных задач нет и с маркера прошло больше
        ``watchdog.workai_idle_s`` секунд — окно закрывается.
        """
        marker = self.root / "state" / "workai_bridge.txt"
        if not marker.exists():
            return []
        try:
            last = float(marker.read_text(encoding="utf-8").strip() or 0)
        except (OSError, ValueError):
            return []
        idle = float(self.settings.get("watchdog.workai_idle_s", 300))
        if time.time() - last < idle:
            return []
        # Есть ли активные задачи (running/awaiting) — если да, ещё работаем.
        jobs = JobManager(self.root).list()
        if any(j.get("status") in ("running", "awaiting-approval") for j in jobs):
            return []
        # WorkAI, вероятно, поднят мостом — закрываем.
        try:
            import subprocess
            subprocess.run(["taskkill", "/IM", "WorkAI.exe", "/T", "/F"],
                           capture_output=True, timeout=15)
        except Exception:  # noqa: BLE001
            return []
        try:
            marker.unlink()
        except OSError:
            pass
        return [{"action": "workai-idle-close",
                 "reason": f"простой > {int(idle)} с, задач нет"}]

    def _record(self, result: dict) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")
        if result["actions"]:
            self.ledger.append({"kind": "watchdog", "actions": result["actions"]})

    def run(self, interval: float = 30, iterations: int | None = None, on_tick=None) -> int:
        """Цикл супервизии. `iterations=None` — до остановки оператором."""
        done = 0
        while True:
            result = self.tick()
            if on_tick is not None:
                on_tick(result)
            done += 1
            if iterations is not None and done >= iterations:
                break
            time.sleep(interval)
        return done
