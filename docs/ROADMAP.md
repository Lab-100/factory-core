# Дорожная карта фабрики Lab-100 — статус выполнения

Источник: `База_Знаний_Фабрика_Lab-100.md` (разделы 7 и 8).
Статусы: ✅ выполнено · ⚠️ зависит от внешней среды (см. раздел ниже).

## Этапы (раздел 7)

| № | Этап | Результат | Статус | Где реализовано |
|---|---|---|---|---|
| 0 | Инвентаризация | авто-каталог инструментов/провайдеров/регионов | ✅ | `factory/inventory.py`, `factory inventory` |
| 1 | Контракт | `invr.task/1` + `config/domains.toml` | ✅ | `factory/task.py`, `schemas/invr.task.schema.json`, `config/domains.toml` |
| 2 | Диспетчер MVP | декомпозиция → Lab100 + шимы INVR + ledger | ✅ | `factory/dispatch.py`, `factory/adapters/*`, `factory/ledger.py` |
| 3 | Связки | region-spoof + backup-util (ChangeId) + orch-metrics + infra-graph | ✅ | `factory/adapters/region.py`, `factory/regionlib.py`, `factory/integrations/*` |
| 4 | Пульт | CLI + дашборд домены/очередь/квоты | ✅ | `factory/ui/cli.py`, `factory/ui/web.py`, `factory/ui/tray.py`, `factory/quotas.py` |
| 5 | Супервизия | watchdog: наблюдение и авто-восстановление | ✅ | `factory/watchdog.py`, `factory watchdog` |
| 6 | Арсенал | единый пульт над всеми элементами (инструменты/службы/агенты/окружение) + журналы + режимы | ✅ | `factory/arsenal.py`, `factory/nodes.py`, `factory/supervisor.py`, `factory/logs.py`, `factory arsenal`/`supervise`/`logs`, `/api/arsenal`/`/api/logs` |

## Арсенал и режимы (проект без Docker)

| Возможность | Реализация |
|---|---|
| Каталог элементов 5 классов (`repo`/`tool`/`agent`/`service`/`env`) | `factory/arsenal.py` (`invr.arsenal/1`) |
| Состояния узлов (`active`/`idle`/`unloaded`/`need-guard`/`fault`/`unknown`) | `factory/nodes.py` (`invr.nodes/1`) |
| Супервизор: наблюдение + авто-восстановление + режимы `attached`/`standalone` | `factory/supervisor.py`, `factory supervise`, настройки `[operation]`/`[supervision]` |
| Журналы по элементу и сводно (фабрика, mcp-watchdog, OpenCode, службы) | `factory/logs.py` (`invr.logs/1`), `factory logs`, `/api/logs` |
| Визуализация «что работает / что нет» + перезапуск службы | `factory/ui/web.py` (`/api/arsenal`, `/api/supervise/action`) |
| Docker-службы исключены (`docker`/`gordon`/`model-runner`) | показаны как `unloaded`, а не как сбой |
| Docker-инструменты склада исключены (`dmr-model`/`dmr-router`/`gordon`/`gordon-chat`) | `factory/arsenal.py` (`DOCKER_TOOLS`), показаны как `unloaded` |
| Корень склада инструментов: `INVR_TOOLS_ROOT`/`DEVSTATION_TOOLS`, иначе автообнаружение (`scripts-tools`, `~/.devstation/tools`) | `factory/paths.py` (`tools_root`); используется `factory/inventory.py`, `factory/arsenal.py`, `factory/logs.py` — конфиг переносим |

## Быстрые победы (раздел 8)

| № | Пункт | Статус | Где реализовано |
|---|---|---|---|
| 8.1 | Единый манифест фабрики со ссылками на 4 репо | ✅ | `project.json` (`invr.project/1`), `factory manifest` |
| 8.2 | `config/domains.toml` — подвижные домены | ✅ | `config/domains.toml`, `config/profiles/*.toml` |
| 8.3 | Обёртка-шим `factory` в реестре склада | ✅ | `pyproject.toml` (`[project.scripts] factory`), `scripts/factory.ps1` (переносимая обёртка), `scripts/*.ps1` |
| 8.4 | Единый журнал: квоты + ChangeId + метрики → ledger | ✅ | `factory/ledger.py`, `factory/dispatch.py` (`_observe`/`_record_quotas`), `state/ledger.jsonl`, `factory ledger` |

## Компоненты §5 (чего не хватало)

| № | Компонент | Статус | Где реализовано |
|---|---|---|---|
| 5.1 | Контракт `invr.task/1` | ✅ | `factory/task.py`, `schemas/invr.task.schema.json` |
| 8.2/5.2 | Реестр доменов `domains.toml` | ✅ | `config/domains.toml`, `config/profiles/*.toml` |
| 5.3 | Диспетчер (план → исполнители → инструменты) | ✅ | `factory/dispatch.py` (`CAPABILITY_ROUTE`, шаги `tool:<имя>` → `invr`) |
| 5.4 | Единый журнал `ledger.jsonl` | ✅ | `factory/ledger.py`; события `metrics`/`quota`/`change`/`catalog` |
| 5.5 | Пульт оператора (CLI + дашборд) | ✅ | `factory/ui/cli.py`, `factory/ui/web.py`; `factory doctor` показывает и адаптеры, и интеграции |
| **5.6** | Каталог артефактов `catalog/` | ✅ | запись — `factory/dispatch.py` (`_write_catalog`); просмотр — `factory/catalog.py`, `factory catalog`, `/api/catalog`, раздел дашборда |
| **5.7** | Сессии — история запросов и ответов | ✅ | `factory/sessions.py` (`invr.session/1`), `factory sessions` (`list`/`new`/`show`/`add`/`rm`), привязка задачи `factory run --session <id>`, запись итога из `factory/jobs.py` |
| **5.8** | Вложения и экспорт | ✅ | вложения — `factory/attachments.py` (`state/uploads/<scope>/`), загрузка `POST /api/upload` (multipart), поле `inputs.attachments`; экспорт — `factory/export.py`, `factory export <job|session> <id>` (`md`/`json`/`zip`), `/api/export`; кнопки в дашборде |

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

## Зависимости среды (что включается только при внешних сервисах)

Фабрика спроектирована без Docker и деградирует честно: отсутствие источника —
это `available = false`, а не сбой. Часть возможностей оживает лишь при
наличии внешних репозиториев/сервисов; ниже — что это и как подключить.

| Элемент | Нужен для | Как подключить |
|---|---|---|
| `Lab100` | ИИ-исполнители: `web-search`/`deep-research`/`scrape` | рядом с `factory-core`, `LAB100_DIR` или `adapters.lab100_dir` |
| склад `scripts-tools` (шимы INVR) | шаги `tool:<имя>` | `INVR_TOOLS_ROOT`/`DEVSTATION_TOOLS` или авто-поиск (`~/.devstation/tools`) |
| `region-spoof` | смена региона при гео-блоке (§4.5) | рядом, `REGION_SPOOF_DIR`/`REGION_SPOOF_ROOT` или `adapters.region_repo` |
| `devstation-deploy` | этапы развёртывания (инвентарь) | рядом или `adapters.deploy_dir` |
| Ollama | локальная LLM и `vision` (мультимодальные модели) | `adapters.ollama_host` (по умолчанию `http://127.0.0.1:11434`) |
| `backup-util`/`orch-metrics`/`infra-graph` | учёт изменений, метрики, карта потока | шимы склада INVR; иначе — локальные приёмники `state/*.jsonl` |

Проверить готовность: `factory doctor` (адаптеры и интеграции) и
`factory inventory` (источники; для отсутствующих печатается подсказка пути).
Незакрытыми остаются только сценарии, требующие отсутствующих сервисов: без
`region-spoof` реактивная смена региона не выполняется, без Ollama — шаги
`llm_local`, без `Lab100` — облачный поиск/скрейп.

## Проверка (тесты)

| Покрытие | Как проверяется |
|---|---|
| Диспетчер, домены, квоты, каталог, ledger, интеграции | изолированные тесты `tests/` (внешние вызовы — на подставных адаптерах) |
| Реальный запуск шима INVR (подпроцесс) | `tests/test_adapters.py::InvrShimExecutionTest` (шим создаётся во временном каталоге) |
| Очередь задач и полуавтомат | `tests/test_jobs.py` (фоновый прогон, `awaiting-approval` → `approve`, авто-восстановление) |
| Сессии (создание/просмотр/добавление/привязка задачи) | `tests/test_sessions.py` |
| Вложения (санитизация, уникализация, лимит, защита путей) и экспорт (md/json/zip) | `tests/test_attachments.py`, `tests/test_export.py` |
| Веб-дашборд `/api/*` | `tests/test_web.py` (реальный `http.server` на свободном порту: страница, настройки, домены, журнал, квоты, инвентарь, каталог, doctor, очередь, подтверждение, арсенал, журналы, действие супервизора) |
| Арсенал, узлы, супервизор, журналы | `tests/test_arsenal.py`, `tests/test_nodes.py`, `tests/test_supervisor.py`, `tests/test_logs.py` |
| Реактивная смена региона при гео-блоке, критерии приёмки, vision-изображения (§4.5) | `tests/test_dispatch.py` (`test_region_retry_on_geoblock`, `test_acceptance_recorded`, `test_vision_step_carries_images`), `tests/test_adapters.py::LocalLlmImagesTest` |
| Подсказки путей для отсутствующих источников (4.4) | `tests/test_inventory.py::test_absent_source_has_hint` |
| Живой контекст/инвентарь/сквозной dry-run | `FACTORY_LIVE=1 python -m unittest tests.test_live` (в CI пропускается) |

Реальные внешние вызовы (Lab100, Ollama, region-spoof за пределами dry-run)
локально не воспроизводятся без установленных репозиториев и сервисов —
поэтому для них предусмотрен опциональный живой прогон.

