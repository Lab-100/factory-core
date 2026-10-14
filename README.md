# factory-core — «Фабрика Lab-100»

Единый верхний слой (пульт + диспетчер), который связывает существующие репозитории
в одно «предприятие» для выполнения задач оператора в разных областях знаний.

**Исходные репозитории не изменяются и не поглощаются** — фабрика только управляет ими:

| Компонент | Роль | Репозиторий |
|---|---|---|
| Склад инструментов | версионированные инструменты + шимы | `Lab-100/scripts-tools` |
| Монтаж/наладка | развёртывание инфраструктуры | `Lab-100/devstation-deploy` |
| Цех исполнителей (ИИ) | пул агентов, компетенции, квоты | `Lab-100/Lab100` |
| Логистика/доступ | смена региона для заблокированных ресурсов | `Lab-100/region-spoof` |

## Ключевые принципы

1. **Подвижные (динамические) домены.** Домены описаны в `config/domains.toml`,
   а не в коде: включаются/выключаются, меняют приоритет, добавляются новые —
   через конфиг и профили, с горячей перезагрузкой (`reload`) без рестарта.
2. **Автоматизация по умолчанию, полуавтомат — по настройке.** Режим `auto`
   выполняет всю цепочку; `semi` останавливается на контрольных точках и ждёт
   подтверждения оператора. Переключается в меню настроек.
3. **Два интерфейса.** CLI всегда доступен; дашборд — `web` или `tray`
   (выбор пользователя в настройках).
4. **Единый журнал** `state/ledger.jsonl` — задачи, шаги, инструменты, метрики,
   квоты, откаты и каталог артефактов в одной ленте.

## Архитектура

```
Пульт (CLI / web / tray)
        │  заказ-наряд invr.task/1
        ▼
Диспетчер (factory.dispatch)
   │ домены → план → исполнители → артефакты
   ▼
Адаптеры: lab100 (пул ИИ) · invr (шимы) · llm_local (ollama) · region (region-spoof)
        │
        ▼
Единый журнал + каталог артефактов
```

## Структура

```
factory-core/
  config/
    settings.toml        настройки (режим, интерфейс, пути)
    domains.toml         БАЗОВЫЕ домены (подвижные)
    profiles/default.toml наложения профиля на домены
  schemas/invr.task.schema.json
  factory/
    settings.py  domains.py  task.py  ledger.py  dispatch.py  context.py
    inventory.py  quotas.py  watchdog.py  jobs.py  catalog.py  doctor.py
    arsenal.py  nodes.py  supervisor.py  logs.py
    adapters/    base.py lab100.py invr_tool.py llm_local.py region.py
    integrations/ metrics.py infra.py backup.py
    ui/          cli.py web.py tray.py
    auth.py  release.py  i18n.py  regionlib.py  paths.py  tomlw.py  tomlpatch.py
  tests/
```

## Быстрый старт

```bash
# статус и домены
python -m factory status
python -m factory domains list
python -m factory domains disable docs

# настройки
python -m factory settings get automation.mode
python -m factory settings set automation.mode semi
python -m factory settings set ui.mode web

# запуск задачи из файла
python -m factory run examples/task.example.json --dry-run
python -m factory run examples/task.search.json       # реальный поиск через Lab100

# диагностика и инфраструктура
python -m factory doctor              # исполнители (адаптеры) и интеграции (наблюдаемость)
python -m factory jobs                # очередь задач
python -m factory region status       # регион (region-spoof)
python -m factory region switch --region US

# интерфейсы
python -m factory ui --mode cli     # текущий
python -m factory ui --mode web     # дашборд http://127.0.0.1:8787 (запуск задач, очередь, подтверждения)
python -m factory ui --mode tray    # трей (нужен pystray)
python -m factory ui --mode desktop # окно приложения (нужен pywebview: pip install .[desktop])

# автовход в GitHub (device flow: код + автооткрытие браузера, токен → state/github.token)
python -m factory auth login
python -m factory auth status        # под какой аккаунт вошли
python -m factory auth logout

# автоматический релиз: сборка factory.exe → zip → пуш → тег → GitHub Release + ассет
python -m factory release            # v<версия из pyproject.toml>
python -m factory release --draft    # черновик
python -m factory release --no-build # использовать уже собранный dist/ (без PyInstaller)

# инвентаризация, квоты, супервизия, единый журнал, манифест
python -m factory inventory           # карта «что умеем»: инструменты/провайдеры/регионы (этап 0)
python -m factory inventory --json    # та же карта в JSON
python -m factory arsenal             # арсенал: элементы и их состояния (что работает / что нет)
python -m factory arsenal --class service   # только службы
python -m factory arsenal --json      # снимок арсенала в JSON
python -m factory supervise --once    # супервизор арсенала: наблюдение и авто-восстановление
python -m factory supervise           # непрерывно (Ctrl+C для остановки)
python -m factory supervise --no-heal # без автоподъёма служб
python -m factory logs                # журналы арсенала (сводно)
python -m factory logs --sources      # список источников журналов
python -m factory logs --source opencode.log --limit 100
python -m factory logs --element ollama   # журналы по конкретному элементу
python -m factory quotas              # лимиты и расход исполнителей Lab100 (этап 4)
python -m factory quotas --reset exa  # сбросить зеркальный учёт (без аргумента — по всем)
python -m factory watchdog --once     # супервизия: проверка и авто-восстановление (этап 5)
python -m factory watchdog            # непрерывное наблюдение (Ctrl+C для остановки)
python -m factory ledger --limit 50   # единый журнал: задачи/шаги/метрики/квоты/изменения (§8.4)
python -m factory ledger --kind quota # фильтр по типу события (quota, metrics, change, catalog, …)
python -m factory manifest            # манифест фабрики project.json (invr.project/1) (§8.1)
python -m factory catalog             # каталог артефактов: результаты задач по доменам (§5.6)
python -m factory catalog --domain news        # только один домен
python -m factory catalog show news/t1         # отчёт записи (report.md)
python -m factory catalog --json               # каталог в JSON

# задача с вызовом инструментов-шимов склада INVR (§4.5)
python -m factory run examples/task.tools.json --dry-run
```

## Автовход и релизы

- `factory auth login` — авторизация GitHub через device flow: фабрика сама
  запрашивает код, открывает браузер и ждёт подтверждения, после чего сохраняет
  токен в `state/github.token` (каталог `state/` в `.gitignore` — токен не
  попадает в git). `--resume <файл>` продолжает уже начатую авторизацию.
- `factory release` — весь конвейер одной командой: сборка `factory.exe`
  (PyInstaller, при необходимости ставится сам), упаковка
  `dist/factory-core-win.zip` (exe + конфиги + `install.ps1`/`uninstall.ps1`),
  пуш ветки `main`, тег `v<версия>`, создание GitHub Release и загрузка
  архива как ассета. Токен берётся из `GITHUB_TOKEN`, `state/github.token`
  или `gh auth token`.
- Windows-установка: распакуйте `factory-core-win.zip` из релиза и запустите
  `install.ps1` — поставит в `%LOCALAPPDATA%\Programs\FactoryLab100`, добавит
  в PATH и меню «Пуск». Дистрибутив самодостаточен: внутри `factory.exe`
  (Python на целевой машине не нужен), конфиги/схемы/примеры/документация,
  все инструменты фабрики — каталог `scripts/` и его копия `tools.zip`
  (установщик распаковывает её сам, если каталога рядом нет), а также готовый
  `factory-setup.exe`. Устанавливается на любую Windows 10/11 без прав
  администратора (x64). Установщик `factory-setup.exe` (Inno Setup) ставит то же
  самое — вместе с каталогом `scripts/` — и регистрирует программу в «Программы
  и компоненты» со штатным удалением.
- Установщик как Windows-приложения: `scripts/make_installer.ps1` собирает
  `dist/factory-setup.exe` (Inno Setup 6) — установка без прав администратора,
  ярлыки «окно»/«дашборд», добавление в PATH, необязательный автозапуск,
  запись в «Программы и компоненты» и штатное удаление (каталог в PATH
  вычищается деинсталлятором). Нужен установленный Inno Setup
  (`winget install --id JRSoftware.InnoSetup`); скрипт сам найдёт `ISCC.exe`
  и подскажет, если его нет. Готовый `factory-setup.exe` публикуется вторым
  ассетом в GitHub Release.
- Обёртка-шим (§8.3): `scripts/factory.ps1` — переносимая точка входа
  («Lab100 → инструмент → каталог»): ищет рядом `factory.exe`, иначе запускает
  `python -m factory`; `factory.ps1 run <task.json>`, `factory.ps1 catalog`,
  `factory.ps1 doctor`.
- Внешние ИИ-провайдеры: `factory providers list` показывает установленные среды
  (opencode, claude-code, cursor, windsurf, void, zen, workai, multitool) и их
  переключатель. `factory providers enable <имя>` включает провайдера; направить
  на него компетенции можно через `routing.<компетенция> = "external_agent"`
  в `config/settings.toml` или включить `providers.auto_route = true`. В дашборде
  есть раздел «Провайдеры».

## Инвентаризация, квоты, супервизия и единый журнал

Полнота фабрики по дорожной карте (см. [`docs/ROADMAP.md`](docs/ROADMAP.md)):

- **Инвентаризация (этап 0)** — `factory inventory` обходит 4 репозитория и строит
  карту «что умеем»: версионированные инструменты и шимы (`scripts-tools`),
  стадии развёртывания (`devstation-deploy`), провайдеров/компетенции/квоты
  (`Lab100`), регионы (`region-spoof`). Карта сохраняется в `state/inventory.json`
  и доступна в дашборде (`/api/inventory`).
- **Квоты (этап 4)** — `factory quotas` читает лимиты из `Lab100/config/agents.toml`
  (`[quota.*]`) и ведёт зеркальный учёт расхода (`state/quotas.json`), который
  диспетчер пополняет по факту шагов: провайдер берётся из задачи, а иначе — из
  фактического вывода Lab100 (`источник: …`). Доступно в дашборде (`/api/quotas`).
- **Инструменты склада (§4.5)** — задача может вызвать шимы INVR напрямую: поле
  `tools` (`["doc-extract", {"name": "browsertool", "args": [...]}]`) или флаг
  `constraints.use_domain_tools` (взять инструменты подошедшего домена). По
  умолчанию автозапуск выключен — в реестре есть не-исполняемые ключи.
- **Интеграции (этап 3)** — `backup-util` выдаёт `ChangeId` для изменённых файлов
  (при отсутствии шима — локальный `ChangeId`, `source: local`); `orch-metrics`
  принимает метрики шага (иначе — `state/metrics.jsonl`); `infra-graph` получает
  карту потока работ (иначе — `state/infra.jsonl`). Факт каждой интеграции
  дублируется событием в единый журнал.
- **Каталог артефактов (§5.6)** — по завершении задачи диспетчер пишет
  `task.json`, `result.json` и `report.md` в каталог домена
  (`catalog/<домен>/<задача>`, переопределяется `outputs.catalog`); путь попадает
  в результат и в журнал. Просмотр — `factory catalog` (список/`show`/`--json`,
  фильтр `--domain`) и раздел «Каталог артефактов» в дашборде (`/api/catalog`,
  `/api/catalog/show?path=`).
- **Супервизия (этап 5)** — `factory watchdog` наблюдает за адаптерами, снимает
  зависшие задачи (`job-timeout`), перезагружает домены при изменении
  `config/domains.toml`/профилей и пишет события в `state/watchdog.jsonl` и ledger.
- **Единый журнал (§8.4)** — `factory ledger` показывает поток событий
  (`task.start`/`step`/`metrics`/`quota`/`change`/`catalog`/`watchdog`/`task.done`)
  в одной ленте: квоты Lab100, метрики orch-metrics и ChangeId backup-util
  сливаются в один файл.
- **Манифест (§8.1)** — `project.json` (`invr.project/1`) связывает 4 репозитория,
  контракты и точки входа в один проект; `factory manifest` его печатает.

## Арсенал, супервизор и журналы (без Docker)

Фабрика — не только диспетчер задач, но и **пульт над всем арсеналом**: она
каталогизирует и проверяет каждый элемент пяти классов и показывает оператору,
*что работает, а что нет*.

| Класс | Что это | Примеры |
|---|---|---|
| `repo` | исходные репозитории | scripts-tools, Lab100, region-spoof |
| `tool` | инструменты склада | doc-extract, browsertool, backup-util |
| `agent` | исполнители Lab100 | firecrawl, exa, ollama, gemini |
| `service` | службы | Ollama, browsertool, Firecrawl, OpenCode, mcp-watchdog |
| `env` | окружение OpenCode | `AGENTS.md`, `opencode.json`, `plugin/`, `skills/` |

**Состояния узла:** `active` (работает), `idle` (поднимет владелец),
`unloaded` (не развёрнут/выключен), `need-guard` (нужен оператор),
`fault` (сбой), `unknown` (нет способа проверки).

**Режимы работы** (`operation.mode`): `attached` — совместно с OpenCode (чужие
узлы не трогаем, состояние берём и у mcp-watchdog) · `standalone` — автономно,
без OpenCode (фабрика сама поднимает свои службы).

Что даёт:

- `factory arsenal` и раздел «Арсенал» в дашборде (`/api/arsenal`) — карта
  состояния всего арсенала, кнопка перезапуска для служб;
- `factory supervise [--once|--no-heal]` и `factory watchdog` — супервизия;
  авто-восстановление служб с `owner = "factory"` по настройке
  `supervision.autostart` (`auto` → включено только в `standalone`), с
  ограничителем `max_starts_per_hour`; чужие узлы не трогаются при
  `supervision.coexistence = true`;
- `factory logs [--source …] [--element …]` и раздел «Журналы» в дашборде
  (`/api/logs`) — просмотр журналов по каждому элементу и сводно (фабрика,
  mcp-watchdog, OpenCode, службы);
- исключённые Docker-службы (`docker`, `gordon`, `model-runner`) и
  Docker-инструменты склада (`dmr-model`, `dmr-router`, `gordon`, `gordon-chat`)
  показаны как `unloaded`, а не как сбой.

Настройка служб — секция `[services.<name>]` в `config/settings.toml`
(`kind`, `url`/`host`+`port`/`command`/`match`/`path`, `owner`, `start`, `stop`,
`log`). Значения по умолчанию — в `factory/arsenal.py`.

Пути к состоянию и журналу mcp-watchdog (`[sources]`) можно не задавать — они
выводятся из корня склада инструментов INVR-Tools: `<tools-root>\mcp-watchdog\state\current.json`
и `<tools-root>\Logs\mcp-watchdog.log`. Корень берётся из `INVR_TOOLS_ROOT`
(`DEVSTATION_TOOLS`), а если переменные не заданы — находится автоматически
рядом с фабрикой (`scripts-tools`, `tools`) или в `~/.devstation/tools`.
Тот же автообнаруженный склад использует инвентаризация (`repo:scripts-tools`
и класс `tool` в арсенале). Поэтому конфиг остаётся переносимым.

## Языки интерфейсов

Интерфейсы (CLI, веб-дашборд, трей) работают на русском (по умолчанию),
английском или китайском — на выбор пользователя:

```bash
python -m factory settings set ui.lang ru   # русский (по умолчанию)
python -m factory settings set ui.lang en   # English
python -m factory settings set ui.lang zh   # 中文
```

Также можно задать язык окружением `FACTORY_LANG=ru|en|zh`. В веб-дашборде
язык переключается в секции «Настройки» (язык применяется сразу).

## Тесты

```bash
python -m unittest discover -s tests        # изолированные тесты (без внешних вызовов)
python -m unittest discover -s tests -v      # подробно
FACTORY_LIVE=1 python -m unittest tests.test_live   # живой прогон на реальной установке
```

Изолированные тесты не требуют исходных репозиториев и внешних сервисов: адаптеры
подставные, шимы INVR создаются во временном каталоге. Живой прогон
(`FACTORY_LIVE=1`) собирает настоящий контекст, инвентарь и делает сквозной
dry-run через реальные адаптеры.

## Зависимости

Ядро — только стандартная библиотека (Python ≥ 3.11). Опционально:
`pystray` + `Pillow` для трея. Дашборд — на стандартном `http.server`.

## Лицензия

Apache-2.0.
