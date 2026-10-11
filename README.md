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
    settings.py  domains.py  task.py  ledger.py  dispatch.py
    adapters/    base.py lab100.py invr_tool.py llm_local.py region.py
    ui/          cli.py web.py tray.py
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
  в PATH и меню «Пуск».
- Обёртка-шим (§8.3): `scripts/factory.ps1` — переносимая точка входа
  («Lab100 → инструмент → каталог»): ищет рядом `factory.exe`, иначе запускает
  `python -m factory`; `factory.ps1 run <task.json>`, `factory.ps1 catalog`,
  `factory.ps1 doctor`.

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

## Зависимости

Ядро — только стандартная библиотека (Python ≥ 3.11). Опционально:
`pystray` + `Pillow` для трея. Дашборд — на стандартном `http.server`.

## Лицензия

Apache-2.0.
