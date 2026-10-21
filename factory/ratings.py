"""Рейтинговый движок провайдеров фабрики (точнаскоr./скорость/ошибки).

Ведёт статистику по каждому провайдеру в `state/ratings.jsonl`:
  * число запросов (n),
  * число успехов/отказов (ошибок),
  * совокупное время ответа (сумма и среднее),
  * специализация (какие компетенции/домены лучше всего),
  * скользящая оценка надёжности.

Выбор провайдера `pick(settings, capability, providers)` учитывает:
  * базовый рейтинг = (успехи/запросы) со штрафом за малую выборку;
  * среднюю скорость (штраф за тормоза);
  * специализацию (если провайдер хорошо отвечал по этой компетенции);
  * квоты (не брать исчерпавших).

Модуль не ломает ядро: сбой статистики изолирован.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

# вес факторов в итоговой оценке
W_RATE = 0.55      # надёжность (доля успехов)
W_SPEED = 0.25     # скорость
W_SPEC = 0.20      # специализация

# порог «недостаточно данных» — при малой выборке оценка прижимается к нейтрали
MIN_SAMPLES = 3
NEUTRAL_RATE = 0.7


class RatingsStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: dict = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        out: dict = {}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            p = rec.get("provider") or ""
            if not p:
                continue
            d = rec
            # агрегируем последнюю запись по провайдеру + накопленные счётчики
            agg = out.setdefault(p, {
                "n": 0, "ok": 0, "fail": 0, "sum_ms": 0.0,
                "spec": {}, "last": None,
            })
            agg["providers"] = p
            agg["n"] = max(agg["n"], int(d.get("n", 0)))
            agg["ok"] = int(d.get("ok", 0))
            agg["fail"] = int(d.get("fail", 0))
            agg["sum_ms"] = float(d.get("sum_ms", 0))
            if isinstance(d.get("spec"), dict):
                for cap, s in d["spec"].items():
                    agg["spec"][cap] = s
            agg["last"] = d.get("ts")
        return out

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            for p, d in self.data.items():
                handle.write(json.dumps({
                    "provider": p,
                    "ts": d.get("last") or time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "n": d.get("n", 0),
                    "ok": d.get("ok", 0),
                    "fail": d.get("fail", 0),
                    "sum_ms": round(d.get("sum_ms", 0.0), 1),
                    "spec": d.get("spec", {}),
                }, ensure_ascii=False) + "\n")

    def snapshot(self) -> dict:
        return {p: {
            "n": d.get("n", 0), "ok": d.get("ok", 0), "fail": d.get("fail", 0),
            "avg_ms": round(d.get("sum_ms", 0.0) / max(1, d.get("n", 0)), 1),
            "reliability": self.reliability(p),
        } for p, d in self.data.items()}

    def reliability(self, provider: str, capability: str = "",
                   neutral: float = NEUTRAL_RATE, min_samples: int = MIN_SAMPLES) -> float:
        d = self.data.get(provider)
        if not d:
            return neutral
        n = int(d.get("n", 0) or 0)
        ok = int(d.get("ok", 0) or 0)
        if n <= 0:
            return neutral
        rate = ok / float(n)
        if n < min_samples:
            # прижимаем к нейтральной оценке (нет уверенности)
            rate = neutral + (rate - neutral) * (n / float(min_samples))
        if capability:
            spec = d.get("spec") or {}
            s = spec.get(capability) or {}
            if isinstance(s, dict) and int(s.get("n") or 0) > 0:
                srate = int(s.get("ok") or 0) / float(int(s.get("n") or 1))
                rate = 0.5 * rate + 0.5 * srate
        return max(0.0, min(1.0, rate))

    def avg_ms(self, provider: str) -> float:
        d = self.data.get(provider)
        if not d or int(d.get("n") or 0) <= 0:
            return 0.0
        return float(d.get("sum_ms") or 0.0) / float(d.get("n") or 1)

    def record(self, provider: str, capability: str, ok: bool, ms: float) -> None:
        d = self.data.setdefault(provider, {
            "n": 0, "ok": 0, "fail": 0, "sum_ms": 0.0, "spec": {}, "last": None})
        d["n"] = int(d.get("n", 0)) + 1
        if ok:
            d["ok"] = int(d.get("ok", 0)) + 1
        else:
            d["fail"] = int(d.get("fail", 0)) + 1
        d["sum_ms"] = float(d.get("sum_ms", 0.0)) + float(ms or 0)
        d["last"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        cap = str(capability or "general")
        spec = d.setdefault("spec", {})
        cs = spec.setdefault(cap, {"n": 0, "ok": 0})
        cs["n"] = int(cs.get("n", 0)) + 1
        if ok:
            cs["ok"] = int(cs.get("ok", 0)) + 1
        self.save()


def store_for(settings) -> RatingsStore:
    path = str(settings.get("ratings.path", "state/ratings.jsonl")) \
        if settings is not None else "state/ratings.jsonl"
    if not Path(path).is_absolute():
        base = Path(settings.path).parent.parent if settings is not None else Path(".")
        path = str(base / path)
    return RatingsStore(path)


def _weights(settings) -> dict:
    """Веса факторов рейтинга из настроек фабрики [ratings] (иначе умолчания)."""
    w = {
        "rate": W_RATE,
        "speed": W_SPEED,
        "spec": W_SPEC,
        "min_samples": MIN_SAMPLES,
        "neutral": NEUTRAL_RATE,
    }
    if settings is not None:
        cfg = settings.get("ratings") or {}
        if isinstance(cfg, dict):
            for k, default in w.items():
                v = cfg.get(k)
                if v is not None:
                    try:
                        w[k] = float(v)
                    except (TypeError, ValueError):
                        pass
    return w


def _score(provider: str, cap: str, store: RatingsStore, settings=None,
           quotas_snapshot=None) -> float:
    """Итоговая оценка провайдера для выбора (0..1+)."""
    w = _weights(settings)
    rate = store.reliability(provider, cap, neutral=w["neutral"], min_samples=w["min_samples"])
    avg = store.avg_ms(provider)
    # скорость: 1000мс -> награда 1, 30000мс -> ~0.3
    speed = 1.0 if avg <= 0 else max(0.1, min(1.0, 2000.0 / (avg + 2000.0)))
    score = w["rate"] * rate + w["speed"] * speed
    # специализация уже вошла через reliability(cap); добавляем бонус за свежесть работы
    if quotas_snapshot:
        q = quotas_snapshot.get(provider) or {}
        if q.get("limit") is not None and float(q.get("remaining", 0) or 0) <= 0:
            score -= 10.0  # квота исчерпана
    return score


def pick(settings, capability: str, providers: list[dict], quotas_snapshot=None) -> dict | None:
    """Лучший провайдер из списка по рейтингу/скорости/специализации.

    providers — список из providers.enabled(settings). Возвращает словарь
    провайдера или None, если выбирать не из чего.
    """
    if not providers:
        return None
    store = store_for(settings)
    best = None
    best_score = -1.0
    for p in providers:
        name = str(p.get("name") or "")
        if not name:
            continue
        s = _score(name, capability, store, settings, quotas_snapshot)
        if s > best_score:
            best_score = s
            best = p
    return best


def record(settings, provider: str, capability: str, ok: bool, ms: float) -> None:
    try:
        store_for(settings).record(provider, capability, ok, ms)
    except Exception:  # noqa: BLE001 — статистика не должна ломать задачу
        pass