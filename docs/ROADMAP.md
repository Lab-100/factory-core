# Дорожная карта фабрики Lab-100 — статус выполнения

Источник: `База_Знаний_Фабрика_Lab-100.md` (разделы 7 и 8).
Статусы: ✅ выполнено · нет незакрытых пунктов.

## Этапы (раздел 7)

| № | Этап | Результат | Статус | Где реализовано |
|---|---|---|---|---|
| 0 | Инвентаризация | авто-каталог инструментов/провайдеров/регионов | ✅ | `factory/inventory.py`, `factory inventory` |
| 1 | Контракт | `invr.task/1` + `config/domains.toml` | ✅ | `factory/task.py`, `schemas/invr.task.schema.json`, `config/domains.toml` |
| 2 | Диспетчер MVP | декомпозиция → Lab100 + шимы INVR + ledger | ✅ | `factory/dispatch.py`, `factory/adapters/*`, `factory/ledger.py` |
| 3 | Связки | region-spoof + backup-util (ChangeId) + orch-metrics + infra-graph | ✅ | `factory/adapters/region.py`, `factory/regionlib.py`, `factory/integrations/*` |
| 4 | Пульт | CLI + дашборд домены/очередь/квоты | ✅ | `factory/ui/cli.py`, `factory/ui/web.py`, `factory/ui/tray.py`, `factory/quotas.py` |
| 5 | Супервизия | watchdog: наблюдение и авто-восстановление | ✅ | `factory/watchdog.py`, `factory watchdog` |

## Быстрые победы (раздел 8)

| № | Пункт | Статус | Где реализовано |
|---|---|---|---|
| 8.1 | Единый манифест фабрики со ссылками на 4 репо | ✅ | `project.json` (`invr.project/1`), `factory manifest` |
| 8.2 | `config/domains.toml` — подвижные домены | ✅ | `config/domains.toml`, `config/profiles/*.toml` |
| 8.3 | Обёртка-шим `factory` в реестре склада | ✅ | `pyproject.toml` (`[project.scripts] factory`), `scripts/*.ps1` |
| 8.4 | Единый журнал: квоты + ChangeId + метрики → ledger | ✅ | `factory/ledger.py`, `factory/dispatch.py` (`_observe`/`_record_quotas`), `state/ledger.jsonl`, `factory ledger` |

## Компоненты §5 (чего не хватало)

| № | Компонент | Статус | Где реализовано |
|---|---|---|---|
| 5.1 | Контракт `invr.task/1` | ✅ | `factory/task.py`, `schemas/invr.task.schema.json` |
| 8.2/5.2 | Реестр доменов `domains.toml` | ✅ | `config/domains.toml`, `config/profiles/*.toml` |
| 5.3 | Диспетчер (план → исполнители → инструменты) | ✅ | `factory/dispatch.py` (`CAPABILITY_ROUTE`, шаги `tool:<имя>` → `invr`) |
| 5.4 | Единый журнал `ledger.jsonl` | ✅ | `factory/ledger.py`; события `metrics`/`quota`/`change`/`catalog` |
| 5.5 | Пульт оператора (CLI + дашборд) | ✅ | `factory/ui/cli.py`, `factory/ui/web.py` |
| **5.6** | Каталог артефактов `catalog/` | ✅ | `factory/dispatch.py` (`_write_catalog`: `task.json`, `result.json`, `report.md`) |

## Публикация (Windows + GitHub)

| Пункт | Статус | Где реализовано |
|---|---|---|
| Автовход в GitHub (device flow) | ✅ | `factory/auth.py`, `factory auth login` |
| Пуш + тег + GitHub Release | ✅ | `factory/release.py`, `factory release` |
| Windows-инсталл (`factory.exe` + zip) | ✅ | `factory.spec`, `scripts/build_exe.ps1`, `scripts/make_zip.ps1`, `scripts/install.ps1` |
| CI (проверка тестов на push) | ✅ | `.github/workflows/ci.yml` |
| Интерфейсы RU/EN/ZH (дефолт RU) | ✅ | `factory/i18n.py` |

## Отсутствие заглушек

Все внешние вызовы (Lab100, INVR-шимы, region-spoof, backup-util, orch-metrics,
infra-graph, Ollama) — реальные: при отсутствии репозитория/инструмента
возвращается честный статус `unavailable` (или локальный приёмник
`state/metrics.jsonl` / `state/infra.jsonl`), а не фиктивная реализация.
Аудит (AST-скан на пустые тела/`NotImplementedError`/дубли определений) не
выявил заглушек; `except … pass` встречается только как осознанная изоляция
внешних сбоев.
