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
4. **Единый журнал** `state/ledger.jsonl` — задачи, шаги, квоты, откаты.

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
python -m factory doctor              # какие исполнители доступны
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
