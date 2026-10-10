# Протокол управления регионом (region-spoof ↔ factory-core)

Документ описывает, как «фабрика» (`factory-core`) переключает регион/зону
через репозиторий `Lab-100/region-spoof`, и какие минимальные доработки нужны
в самом `region-spoof` для **горячего** переключения.

## Зачем

`region-spoof` меняет регион (Regions_List) / зону (РФ ↔ зарубежный) для доступа
к ресурсам, блокирующим по геолокации (Exa 403, Gemini «User location is not
supported», OpenAI и т.п.). Чтобы это делалось автоматически по задаче, нужен
предсказуемый неинтерактивный интерфейс.

## Контракт `invr.region/1` (CLI, stdout = JSON)

Исполняемый файл `regionctl` (или `regionctl.py`) в корне `region-spoof`:

```
regionctl status
regionctl list
regionctl switch --region <CODE> [--zone foreign|local]
```

Ответ — JSON в **последней строке** stdout:

```json
{"ok": true, "region": "US", "zone": "foreign", "last_working": "NL",
 "available_regions": ["US", "DE", "NL"], "applied": true}
```

- `ok` — успех операции;
- `region`, `zone` — новое (или текущее) состояние;
- `applied` — применил ли запущенный GUI смену немедленно (`false` — только
  записано в `config.json`).

Готовый скрипт: `factory-core/integrations/region-spoof/regionctl.py`.
Скопируйте его в корень `region-spoof` (рядом с `config.json`).

## Как это использует фабрика

`factory/regionlib.py::find_cli()` ищет по порядку:
1. `region-spoof/regionctl.py` или `region-spoof/region-spoof.py`;
2. `region-spoof.cmd` / `.bat` / `.exe`;
3. `region-spoof` в `PATH`.

Если CLI найден — адаптер `factory.adapters.region` вызывает его (`switch`).
Если нет — фабрика **правит `config.json` напрямую** (регион/зона), но тогда
запущенный GUI применит изменения только при следующем чтении конфига.

## Требуемые доработки в region-spoof (для `applied: true`)

Минимум (уже покрыт готовым `regionctl.py`):

- файл `regionctl.py` в корне; правит `config.json` и создаёт `regionctl.signal`.

Для горячего применения без перезапуска — одно из:

1. **Перечитать конфиг по сигналу.** GUI раз в N секунд проверяет
   `regionctl.signal` (его mtime) и, если он новее конфига, применяет
   `region`/`zone` на лету. Скрипт `regionctl.py` уже создаёт этот файл.
2. **Кнопка/таймер внутри приложения.** В приложении есть вызов
   `regionctl`-совместимой функции либо кнопка «перечитать конфиг», которую
   может дёрнуть фабрика (через тот же CLI).

После этого адаптер вернёт `applied: true`, и фабрика сможет переключать
регион в середине задачи (например, перед обращением к заблокированному
провайдеру) полностью автоматически.

## Проверка

```bash
# в корне region-spoof
python regionctl.py status
python regionctl.py switch --region US
python regionctl.py switch --zone local

# через фабрику
factory region status
factory region switch --region US
```

## Настройка фабрики

`config/settings.toml`:

```toml
[adapters]
region_enabled = true
region_repo = ""   # пусто — ищем REGION_SPOOF_DIR / ../region-spoof; можно указать явно
```
