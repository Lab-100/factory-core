"""Единый сервис средств обхода («сетевая связка Lab-100») — ядро.

Модуль собирает в один реестр три средства, которые вместе дают доступ к
внешней сети:

  * ``region-spoof``     — подмена региона и локальный прокси (127.0.0.1:8888);
  * ``ai-gateway``       — шлюз маршрутизации: direct / пул proxybroker /
                           Cloudflare Worker (127.0.0.1:8898) + генерация PAC;
  * ``my-openai-gemini`` — прокси Gemini→OpenAI-формат (локально `node node.mjs`
                           либо готовый деплой на Vercel/Netlify/Cloudflare).

Модуль — только органы управления, без графики: реестр (:func:`tool_specs`),
параметры в ``config/settings.toml`` под ``[netsuite.*]`` (:func:`get_params`,
:func:`set_param`, :func:`set_bulk`), наблюдение (:func:`status`), приведение
фактических конфигов к настроенным (:func:`apply`), запуск/останов
(:func:`start`, :func:`stop`), проверка боем (:func:`check`), подготовка
(:func:`bootstrap`) и работа с базами маршрутов/прокси/доменов
(:func:`import_base`, :func:`export_base`).

Правила безопасности:

  * ничего не делается само собой — все действия только по явному вызову;
  * секреты никогда не возвращаются в открытом виде (маска ``***``);
  * останавливаются только те процессы, что записал сам сервис
    (реестр ``state/netsuite/pids.json``), чужие не трогаются;
  * внешние зависимости не используются — только стандартная библиотека.

Зависимостей от внешних пакетов нет: всё на ``subprocess``/``socket``/``urllib``.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import socket
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "invr.netsuite/1"

# Маска секрета: открытые значения наружу не отдаём.
SECRET_MASK = "***"

# Часовой пояс не важен — время в UTC (общий стиль фабрики).
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Коды регионов, которые понимает region-spoof (ISO-3166 alpha-2, 17 штук).
REGION_CODES = ("US", "GB", "DE", "NL", "FR", "CA", "AU", "JP", "SG",
                "CH", "SE", "NO", "FI", "DK", "IT", "ES", "PL")

# Виды «баз» (файлов данных), с которыми умеет работать сервис.
BASE_KINDS = ("routes", "proxies", "upstream_domains")


# --- реестр средств -----------------------------------------------------------

def _p(key: str, title: str, type_: str, default: Any, help_: str,
       choices: list | None = None, item_type: str | None = None) -> dict:
    """Описатель параметра: из него UI рисует поле, а CLI — приводит тип."""
    descr = {
        "key": key,
        "title": title,
        "type": type_,
        "default": default,
        "help": help_,
        "choices": list(choices or []),
    }
    if item_type:
        descr["item_type"] = item_type
    return descr


# Реестр средств обхода. Схемы параметров собраны из реальных полей конфигов
# (см. config.json у region-spoof/ai-gateway и запуск node.mjs у прокси Gemini).
TOOLS: list[dict] = [
    {
        "id": "region-spoof",
        "title": "region-spoof (регион и локальный прокси)",
        "description": ("Подмена региона: системный прокси, sing-box или mihomo; "
                        "локальный прокси на 127.0.0.1:8888 и перебор резервных портов."),
        "kind": "python-app",
        "default_port": 8888,
        "config_file": "config.json",
        "launcher": "applet.py",
        "params": [
            _p("enabled", "Включено", "bool", True,
               "Учитывать средство в сервисе обхода"),
            _p("root", "Корень", "path", "",
               "Каталог region-spoof (пусто — искать рядом или в Lab100-Factory)"),
            _p("python", "Интерпретатор Python", "path", "",
               "Путь к python/pythonw (пусто — текущий интерпретатор)"),
            _p("region", "Регион", "select", "US",
               "Код региона для подмены", choices=list(REGION_CODES)),
            _p("zone", "Зона", "select", "foreign",
               "foreign — зарубежная зона, ru — российская", choices=["foreign", "ru"]),
            _p("strategy", "Стратегия", "select", "system",
               "system — системный прокси, singbox/mihomo — своё ядро",
               choices=["system", "singbox", "mihomo"]),
            _p("proxy_port", "Порт прокси", "int", 8888,
               "Порт локального прокси region-spoof"),
            _p("spare_ports", "Резервные порты", "list", [8889, 8890, 8891, 8892],
               "Список резервных портов через запятую", item_type="int"),
            _p("auto_start", "Автозапуск", "bool", True,
               "Запускать прокси сразу при старте приложения"),
            _p("check_interval_sec", "Период проверки, с", "int", 45,
               "Как часто проверять живость прокси"),
            _p("verify_timeout_sec", "Таймаут проверки, с", "int", 18,
               "Сколько ждать ответа при проверке"),
            _p("failover_threshold", "Порог отказов", "int", 3,
               "Сколько неудач подряд до переключения"),
            _p("pb2_limit", "Лимит proxybroker2", "int", 40,
               "Скрытый параметр кода: сколько прокси брать из proxybroker2"),
            _p("pb2_timeout_sec", "Таймаут proxybroker2, с", "int", 10,
               "Скрытый параметр кода: таймаут сбора прокси"),
        ],
    },
    {
        "id": "ai-gateway",
        "title": "ai-gateway (шлюз маршрутизации запросов)",
        "description": ("Локальный шлюз на 127.0.0.1:8898: маршруты direct / "
                        "pool:proxybroker / cf:cloudflare_worker, генерация gateway.pac."),
        "kind": "python-app",
        "default_port": 8898,
        "config_file": "config.json",
        "launcher": "gateway.py",
        "params": [
            _p("enabled", "Включено", "bool", True,
               "Учитывать средство в сервисе обхода"),
            _p("root", "Корень", "path", "",
               "Каталог ai-gateway (пусто — Lab100-Factory/ai-gateway)"),
            _p("python", "Интерпретатор Python", "path", "",
               "Путь к python (пусто — текущий интерпретатор)"),
            _p("listen_host", "Адрес прослушивания", "str", "127.0.0.1",
               "Адрес локального шлюза"),
            _p("listen_port", "Порт шлюза", "int", 8898,
               "Порт локального шлюза"),
            _p("default_via", "Как ходить по умолчанию", "select", "direct",
               "direct — напрямую, pool — пул прокси, cf — Cloudflare Worker",
               choices=["direct", "pool:proxybroker", "cf:cloudflare_worker"]),
            _p("routes", "Маршруты", "list", [],
               "Строки вида «домен => via» (via: direct|pool:имя|cf:имя)",
               item_type="str"),
            _p("pb_host", "proxybroker: адрес", "str", "127.0.0.1",
               "Адрес пула прокси"),
            _p("pb_port", "proxybroker: порт", "int", 0,
               "Порт пула прокси (0 — не задан)"),
            _p("pb_type", "proxybroker: тип", "select", "http",
               "Схема подключения к пулу", choices=["http", "https", "socks5"]),
            _p("pb_user", "proxybroker: пользователь", "str", "",
               "Имя пользователя пула прокси"),
            _p("pb_password", "proxybroker: пароль", "secret", "",
               "Пароль пула прокси (хранится в настройках, наружу не отдаётся)"),
            _p("cf_host", "Cloudflare: адрес", "str", "127.0.0.1",
               "Адрес Cloudflare Worker"),
            _p("cf_port", "Cloudflare: порт", "int", 0,
               "Порт Cloudflare Worker (0 — не задан)"),
            _p("cf_secret", "Cloudflare: секрет", "secret", "",
               "Секрет Cloudflare Worker (наружу не отдаётся)"),
            _p("upstream_proxy", "Верхний прокси", "secret", "",
               "Прокси для исходящих запросов, напр. http://127.0.0.1:8888"),
            _p("upstream_domains", "Домены через верхний прокси", "list", [],
               "Список доменов, по одному на строку", item_type="str"),
            _p("make_pac", "Генерировать gateway.pac", "bool", True,
               "После применения настроек вызвать tools/make-pac.py"),
        ],
    },
    {
        "id": "my-openai-gemini",
        "title": "my-openai-gemini (Gemini в формате OpenAI)",
        "description": ("Прокси Gemini→OpenAI: локальный запуск `node node.mjs` "
                        "или уже развёрнутый сервис (Vercel/Netlify/Cloudflare)."),
        "kind": "node-app",
        "default_port": 8080,
        "config_file": ".env",
        "launcher": "node.mjs",
        "params": [
            _p("enabled", "Включено", "bool", True,
               "Учитывать средство в сервисе обхода"),
            _p("root", "Корень", "path", "",
               "Каталог my-openai-gemini (пусто — Lab100-Factory/my-openai-gemini)"),
            _p("mode", "Режим", "select", "local",
               "local — запускать node локально, deployed — готовый деплой",
               choices=["local", "deployed"]),
            _p("local_port", "Локальный порт", "int", 8080,
               "Порт локального запуска (переменная PORT)"),
            _p("base_url", "Адрес деплоя", "str", "",
               "Базовый адрес развёрнутого сервиса, напр. https://x.vercel.app"),
            _p("default_model", "Модель по умолчанию", "str", "gemini-2.5-flash",
               "Модель Gemini, если клиент не указал свою"),
            _p("node_path", "Путь к node", "path", "",
               "Путь к node (пусто — искать в PATH)"),
            _p("gemini_key", "Ключ Gemini", "secret", "",
               "Ключ только для локальных проверок (наружу не отдаётся)"),
            _p("deploy_target", "Площадка деплоя", "select", "vercel",
               "Куда разворачивать сервис",
               choices=["vercel", "netlify", "cloudflare"]),
        ],
    },
]

TOOLS_BY_ID: dict[str, dict] = {tool["id"]: tool for tool in TOOLS}

# Имена секций настроек: [netsuite], [netsuite.<id>].
SECTION_ROOT = "netsuite"


# --- реестр для UI ------------------------------------------------------------

def tool_ids() -> list[str]:
    """Идентификаторы средств в порядке реестра."""
    return [tool["id"] for tool in TOOLS]


def tool_specs() -> list[dict]:
    """Реестр средств с описателями параметров (копия — UI не портит реестр)."""
    return copy.deepcopy(TOOLS)


def spec(tool_id: str) -> dict | None:
    """Описатель одного средства или None."""
    found = TOOLS_BY_ID.get(str(tool_id))
    return copy.deepcopy(found) if found else None


def title(tool_id: str) -> str:
    """Человеческое название средства."""
    found = TOOLS_BY_ID.get(str(tool_id))
    return str(found["title"]) if found else str(tool_id)


def _descr(tool_id: str, key: str) -> dict | None:
    found = TOOLS_BY_ID.get(str(tool_id))
    if not found:
        return None
    for descr in found["params"]:
        if descr["key"] == key:
            return descr
    return None


# --- пути ---------------------------------------------------------------------

def _base(settings) -> Path:
    """Корень фабрики (рядом с settings.toml) — устойчиво к тестам."""
    if settings is not None and getattr(settings, "path", None) is not None:
        return Path(settings.path).parent.parent
    return Path.cwd()


def _factory_dir() -> Path:
    """Каталог Lab100-Factory: где лежат копии средств обхода."""
    configured = (os.environ.get("LAB100_FACTORY_DIR")
                  or os.environ.get("LAB100_DIR") or "").strip()
    if configured:
        return Path(configured)
    return Path.home() / "Documents" / "Lab100-Factory"


def default_root(settings, tool_id: str) -> Path:
    """Путь средства по умолчанию: настройка → соседи → Lab100-Factory."""
    factory = _factory_dir()
    if tool_id == "region-spoof":
        candidates: list[Path] = []
        if settings is not None:
            configured = str(settings.get("adapters.region_repo") or "").strip()
            if configured:
                candidates.append(Path(configured))
        for env_name in ("REGION_SPOOF_DIR", "REGION_SPOOF_ROOT"):
            value = (os.environ.get(env_name) or "").strip()
            if value:
                candidates.append(Path(value))
        if settings is not None:
            base = _base(settings)
            candidates += [base.parent / "region-spoof", base / "region-spoof"]
        candidates.append(factory / "region-spoof")
        for candidate in candidates:
            if (candidate / "config.json").exists():
                return candidate
        return candidates[0] if candidates else factory / "region-spoof"
    return factory / str(tool_id)


def root_of(settings, tool_id: str) -> Path:
    """Действующий корень средства: из параметров или по умолчанию."""
    configured = str(_stored(settings, tool_id, "root") or "").strip()
    return Path(configured) if configured else default_root(settings, tool_id)


def data_dir(settings) -> Path:
    """Каталог баз сервиса (``[netsuite] data_dir``)."""
    value = str((settings.get("netsuite.data_dir") if settings is not None else "")
                or "state/netsuite")
    path = Path(value)
    return path if path.is_absolute() else _base(settings) / path


def pids_path(settings) -> Path:
    """Файл реестра запущенных процессов сервиса."""
    return data_dir(settings) / "pids.json"


# --- параметры ----------------------------------------------------------------

def _settings_data(settings) -> dict:
    """Данные настроек; если словарь ещё пуст — подгружаем их из файла.

    Так сервис остаётся работоспособным и для «свежего» ``Settings``,
    который создан без чтения файла (например, в CLI или веб-панели).
    """
    if settings is None:
        return {}
    data = getattr(settings, "data", None)
    if isinstance(data, dict) and not data:
        path = getattr(settings, "path", None)
        if path is not None:
            path = Path(str(path))
            if path.exists():
                try:
                    loaded = tomllib.loads(path.read_text(encoding="utf-8"))
                except (OSError, tomllib.TOMLDecodeError):
                    loaded = {}
                if isinstance(loaded, dict) and loaded:
                    settings.data = loaded
                    data = loaded
    return data if isinstance(data, dict) else {}


def _dig(data: Any, dotted: str) -> Any:
    """Значение по точечному пути в словаре (None, если пути нет)."""
    current: Any = data
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _stored(settings, tool_id: str, key: str) -> Any:
    """Значение из настроек (как записано) или None, если не задано."""
    if settings is None:
        return None
    return _dig(_settings_data(settings), f"{SECTION_ROOT}.{tool_id}.{key}")


def _as_bool(value: Any, fallback: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "on", "yes", "1", "да"):
            return True
        if text in ("false", "off", "no", "0", "нет"):
            return False
    return fallback


def _as_int(value: Any, fallback: int = 0) -> int:
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text)
        except ValueError:
            try:
                return int(float(text))
            except ValueError:
                return fallback
    return fallback


def _split_items(value: Any) -> list[str]:
    """Разобрать список: из строки «a, b, c» или из настоящего списка."""
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("[") and text.endswith("]"):
            text = text[1:-1]
        parts = [part.strip().strip('"').strip("'") for part in text.split(",")]
        return [part for part in parts if part]
    if value is None:
        return []
    return [str(value)]


def coerce_value(tool_id: str, key: str, value: Any) -> Any:
    """Привести значение к типу параметра (по схеме средства)."""
    descr = _descr(tool_id, key)
    if descr is None:
        raise KeyError(f"неизвестный параметр: {tool_id}.{key}")
    type_ = descr["type"]
    default = descr["default"]
    if type_ == "bool":
        return _as_bool(value, fallback=bool(default))
    if type_ == "int":
        return _as_int(value, fallback=_as_int(default))
    if type_ == "float":
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)
    if type_ == "list":
        items = _split_items(value)
        if descr.get("item_type") == "int":
            return [_as_int(item, fallback=0) for item in items]
        return items
    if type_ == "select":
        text = str(value).strip()
        if descr.get("choices") and text not in descr["choices"]:
            raise ValueError(f"недопустимое значение {text!r}; "
                             f"ожидается одно из {descr['choices']}")
        return text
    # str / path / secret
    if type_ == "path":
        return str(value).strip()
    return str(value)


def _raw_params(settings, tool_id: str) -> dict:
    """Действующие значения средства (включая секреты — только для внутренних нужд)."""
    found = TOOLS_BY_ID.get(str(tool_id))
    if found is None:
        raise KeyError(f"неизвестное средство: {tool_id}")
    out: dict[str, Any] = {}
    for descr in found["params"]:
        stored = _stored(settings, tool_id, descr["key"])
        if stored is None:
            out[descr["key"]] = copy.deepcopy(descr["default"])
            continue
        try:
            out[descr["key"]] = coerce_value(tool_id, descr["key"], stored)
        except (KeyError, ValueError, TypeError):
            out[descr["key"]] = copy.deepcopy(descr["default"])
    # корень: если не задан, отдаём вычисленный путь по умолчанию
    if not out.get("root"):
        out["root"] = str(default_root(settings, tool_id))
    return out


def get_params(settings, tool_id: str) -> dict:
    """Действующие значения средства (секреты — только признак «задан/пусто»)."""
    raw = _raw_params(settings, tool_id)
    found = TOOLS_BY_ID.get(str(tool_id))
    if found is None:
        raise KeyError(f"неизвестное средство: {tool_id}")
    out: dict[str, Any] = {}
    for descr in found["params"]:
        key = descr["key"]
        value = raw.get(key)
        if descr["type"] == "secret":
            out[key] = SECRET_MASK if str(value or "") else ""
            out[f"{key}_set"] = bool(str(value or ""))
        else:
            out[key] = value
    return out


def set_param(settings, tool_id: str, key: str, value: Any) -> dict:
    """Записать один параметр средства и сохранить настройки."""
    if tool_id not in TOOLS_BY_ID:
        return {"ok": False, "error": f"неизвестное средство: {tool_id}"}
    descr = _descr(tool_id, key)
    if descr is None:
        return {"ok": False, "error": f"неизвестный параметр: {tool_id}.{key}"}
    try:
        coerced = coerce_value(tool_id, key, value)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    settings.set(f"{SECTION_ROOT}.{tool_id}.{key}", coerced)
    settings.save()
    result = {"ok": True, "tool": tool_id, "key": key}
    if descr["type"] == "secret":
        result["set"] = bool(str(coerced or ""))
        result["value"] = SECRET_MASK if coerced else ""
    else:
        result["value"] = coerced
    return result


def set_bulk(settings, tool_id: str, values: dict) -> dict:
    """Массовая правка параметров (для формы «Применить» в UI)."""
    if tool_id not in TOOLS_BY_ID:
        return {"ok": False, "error": f"неизвестное средство: {tool_id}"}
    applied: dict[str, Any] = {}
    errors: list[str] = []
    for key, value in dict(values or {}).items():
        if _descr(tool_id, key) is None:
            errors.append(f"неизвестный параметр: {key}")
            continue
        try:
            coerced = coerce_value(tool_id, key, value)
        except ValueError as exc:
            errors.append(f"{key}: {exc}")
            continue
        settings.set(f"{SECTION_ROOT}.{tool_id}.{key}", coerced)
        if _descr(tool_id, key)["type"] == "secret":
            applied[key] = SECRET_MASK if coerced else ""
        else:
            applied[key] = coerced
    settings.save()
    return {"ok": not errors, "tool": tool_id, "applied": applied, "errors": errors,
            "count": len(applied)}


def suite_enabled(settings) -> bool:
    """Общая галочка сервиса (``[netsuite] enabled``)."""
    if settings is None:
        return False
    section = _dig(_settings_data(settings), SECTION_ROOT)
    if not isinstance(section, dict):
        return False
    if "enabled" in section:
        return _as_bool(section["enabled"], fallback=True)
    # Явной галочки нет: сервис считается активным, если заданы его настройки.
    return bool(section)


# --- наблюдение ---------------------------------------------------------------

def _port_open(host: str, port: Any, timeout: float = 0.3) -> bool:
    """Открыт ли TCP-порт (короткая проверка, без внешних библиотек)."""
    try:
        number = int(port)
    except (TypeError, ValueError):
        return False
    if not number or not host:
        return False
    try:
        with socket.create_connection((str(host), number), timeout=timeout):
            return True
    except OSError:
        return False


def _binaries(settings, tool_id: str) -> dict:
    """Наличие ключевых файлов средства."""
    root = root_of(settings, tool_id)
    if tool_id == "region-spoof":
        return {
            "singbox": _find_first(root, ("bin", "singbox"), "sing-box.exe"),
            "mihomo": _find_first(root, ("bin", "mihomo"), "mihomo.exe"),
            "wintun": _find_first(root, ("bin", "wintun", "bin", "amd64"), "wintun.dll"),
            "applet": (root / "applet.py").exists(),
        }
    if tool_id == "ai-gateway":
        return {"gateway": (root / "gateway.py").exists(),
                "make_pac": (root / "tools" / "make-pac.py").exists()}
    return {"node_mjs": (root / "node.mjs").exists(),
            "package_json": (root / "package.json").exists()}


def _find_first(root: Path, subdirs: tuple, name: str) -> bool:
    """Есть ли файл ``name`` где-нибудь под ``root/subdirs`` (версии в подпапках)."""
    base = root.joinpath(*subdirs)
    try:
        if not base.is_dir():
            return False
        for candidate in base.rglob(name):
            if candidate.is_file():
                return True
    except OSError:
        return False
    return False


def running_pid(settings, tool_id: str) -> dict | None:
    """Живой процесс средства по реестру pid (чужой pid игнорируется)."""
    record = (_load_pids(settings) or {}).get(str(tool_id))
    if not isinstance(record, dict):
        return None
    pid = _as_int(record.get("pid"), 0)
    if pid <= 0:
        return None
    actual = _process_image_name(pid)
    if actual is None:
        return None
    expected = str(record.get("name") or "").lower()
    if expected and actual != expected:
        # pid переиспользован другим процессом — не наш
        return None
    return {**record, "pid": pid, "image": actual}


def _load_pids(settings) -> dict:
    path = pids_path(settings)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_pids(settings, data: dict) -> None:
    path = pids_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def _process_image_name(pid: int) -> str | None:
    """Имя образа процесса по pid (Windows — tasklist, иначе — сигнал 0).

    Без внешних зависимостей: ``psutil`` не используется намеренно.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    if sys.platform.startswith("win"):
        try:
            proc = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                                  capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=5, check=False)
        except Exception:  # noqa: BLE001 - список процессов не критичен
            return None
        for line in (proc.stdout or "").splitlines():
            parts = [part.strip().strip('"') for part in line.split(",")]
            if len(parts) >= 2 and parts[1].isdigit() and int(parts[1]) == pid and parts[0]:
                return parts[0].lower()
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    return "?"  # имя на POSIX не спрашиваем: достаточно факта живости


def _config_matches(settings, tool_id: str) -> bool:
    """Соответствует ли фактический конфиг настроенному."""
    root = root_of(settings, tool_id)
    raw = _raw_params(settings, tool_id)
    path = root / str(TOOLS_BY_ID[tool_id]["config_file"])
    if not path.exists():
        return False
    if tool_id == "my-openai-gemini" and raw.get("mode") == "deployed":
        return True  # локального файла нет по определению
    try:
        if path.suffix == ".json":
            data = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
        else:
            data = _parse_env(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return False
    expected = _expected_config(tool_id, raw)
    return all(_same(data.get(key), value) for key, value in expected.items())


def _same(left: Any, right: Any) -> bool:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return int(left) == int(right)
    if isinstance(left, list) and isinstance(right, list):
        return [str(x) for x in left] == [str(x) for x in right]
    return left == right


def _expected_config(tool_id: str, raw: dict) -> dict:
    """Плоское ожидание «ключ → значение» для проверки applied."""
    if tool_id == "region-spoof":
        return {
            "region": raw["region"],
            "strategy": raw["strategy"],
            "zone": raw["zone"],
            "proxy_port": raw["proxy_port"],
            "spare_ports": raw["spare_ports"],
            "auto_start": raw["auto_start"],
            "check_interval_sec": raw["check_interval_sec"],
            "verify_timeout_sec": raw["verify_timeout_sec"],
            "failover_threshold": raw["failover_threshold"],
            "pb2_limit": raw["pb2_limit"],
            "pb2_timeout_sec": raw["pb2_timeout_sec"],
        }
    if tool_id == "ai-gateway":
        routes = [_split_route(item) for item in raw.get("routes") or []]
        return {
            "listen": {"host": raw["listen_host"], "port": raw["listen_port"]},
            "default_via": raw["default_via"],
            "upstream_proxy": raw["upstream_proxy"],
            "upstream_domains": raw["upstream_domains"],
            "routes": [[p, v] for p, v in routes if p],
        }
    return {"PORT": raw["local_port"], "DEFAULT_MODEL": raw["default_model"]}


def _parse_env(text: str) -> dict:
    """Простое чтение .env: KEY=VALUE, «#» — комментарий."""
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _issues(settings, tool_id: str, raw: dict, binaries: dict) -> list[str]:
    """Человекочитаемые проблемы средства (по-русски)."""
    root = root_of(settings, tool_id)
    out: list[str] = []
    if not root.is_dir():
        out.append(f"каталог не найден: {root}")
        return out
    if tool_id == "region-spoof":
        if not binaries.get("applet"):
            out.append("нет файла запуска applet.py")
        if raw.get("strategy") in ("singbox", "mihomo") and not binaries.get(raw["strategy"]):
            out.append(f"ядро {raw['strategy']} не скачано — нужен download_binaries.py")
        if raw.get("strategy") == "singbox" and not binaries.get("wintun"):
            out.append("нет wintun.dll (нужен для sing-box на Windows)")
        if not (root / "download_binaries.py").exists():
            out.append("нет download_binaries.py — подготовка недоступна")
    elif tool_id == "ai-gateway":
        if not binaries.get("gateway"):
            out.append("нет файла запуска gateway.py")
        if raw.get("default_via", "").startswith("pool:") and not raw.get("pb_port"):
            out.append("выбран пул proxybroker, но pb_port не задан")
        if raw.get("default_via", "").startswith("cf:") and not raw.get("cf_port"):
            out.append("выбран Cloudflare Worker, но cf_port не задан")
        if raw.get("make_pac") and not binaries.get("make_pac"):
            out.append("нет tools/make-pac.py — PAC генерироваться не будет")
    else:
        if raw.get("mode") == "local":
            if not binaries.get("node_mjs"):
                out.append("нет файла запуска node.mjs")
            if not shutil.which(str(raw.get("node_path") or "node")):
                out.append("node не найден в PATH")
        elif not str(raw.get("base_url") or "").strip():
            out.append("не задан адрес деплоя base_url")
    if not out:
        out = []
    return out


def _tool_status(settings, tool_id: str) -> dict:
    """Снимок состояния одного средства."""
    found = TOOLS_BY_ID[tool_id]
    raw = _raw_params(settings, tool_id)
    root = root_of(settings, tool_id)
    host = str(raw.get("listen_host") or "127.0.0.1")
    if tool_id == "region-spoof":
        port = _as_int(raw.get("proxy_port"), 8888)
    elif tool_id == "ai-gateway":
        port = _as_int(raw.get("listen_port"), 8898)
    else:
        port = _as_int(raw.get("local_port"), 8080)
    binaries = _binaries(settings, tool_id)
    proc = running_pid(settings, tool_id)
    required_ok = bool(str(raw.get("root") or "").strip()) and (
        bool(str(raw.get("base_url") or "").strip())
        if tool_id == "my-openai-gemini" and raw.get("mode") == "deployed" else True)
    return {
        "id": tool_id,
        "title": found["title"],
        "enabled": _as_bool(raw.get("enabled"), True),
        "root": str(root),
        "root_exists": root.is_dir(),
        "port": port,
        "host": host,
        "port_open": _port_open(host, port),
        "running": bool(proc),
        "pid": (proc or {}).get("pid"),
        "binaries": binaries,
        "params_ok": required_ok,
        "applied": _config_matches(settings, tool_id),
        "issues": _issues(settings, tool_id, raw, binaries),
    }


def status(settings, tool_id: str | None = None) -> dict:
    """Состояние средств обхода (одного или всех сразу).

    Без ``tool_id`` — сводка по всем средствам (``tools`` + ``summary``);
    с ``tool_id`` — снимок одного средства (та же строка, что и в сводке).
    """
    if tool_id:
        item = str(tool_id)
        if item not in TOOLS_BY_ID:
            return {"ok": False, "error": f"неизвестное средство: {item}"}
        row = _tool_status(settings, item)
        row["ok"] = True
        return row
    rows: list[dict] = [_tool_status(settings, item) for item in tool_ids()]
    return {
        "schema": SCHEMA,
        "generated": _now(),
        "suite_enabled": suite_enabled(settings),
        "data_dir": str(data_dir(settings)),
        "tools": rows,
        "summary": {
            "total": len(rows),
            "enabled": sum(1 for row in rows if row["enabled"]),
            "running": sum(1 for row in rows if row["running"]),
            "applied": sum(1 for row in rows if row["applied"]),
            "with_issues": sum(1 for row in rows if row["issues"]),
        },
    }


# --- применение настроек ------------------------------------------------------

def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _default_regions() -> list[dict]:
    """Умолчание списка регионов (когда config.json ещё нет)."""
    return [{"code": code, "title": code} for code in REGION_CODES]


def apply_region_spoof(settings, raw: dict) -> dict:
    """Сгенерировать config.json region-spoof по настройкам."""
    root = Path(str(raw["root"]))
    warnings: list[str] = []
    if not root.is_dir():
        return {"ok": False, "changed": [], "warnings": [f"каталог не найден: {root}"]}
    path = root / "config.json"
    current = _read_json(path)
    regions = current.get("regions") or _default_regions()
    if not current.get("regions"):
        warnings.append("список regions взят из умолчания — проверьте коды")
    config = {
        "region": raw["region"],
        "strategy": raw["strategy"],
        "last_working": current.get("last_working", ""),
        "zone": raw["zone"],
        "proxy_port": _as_int(raw["proxy_port"]),
        "spare_ports": [_as_int(item) for item in raw.get("spare_ports") or []],
        "scan_ports": current.get("scan_ports") or [],
        "pb2_start_timeout_sec": current.get("pb2_start_timeout_sec", 10),
        "check_interval_sec": _as_int(raw["check_interval_sec"]),
        "verify_timeout_sec": _as_int(raw["verify_timeout_sec"]),
        "failover_threshold": _as_int(raw["failover_threshold"]),
        "auto_start": _as_bool(raw["auto_start"]),
        "pb2_limit": _as_int(raw["pb2_limit"]),
        "pb2_timeout_sec": _as_int(raw["pb2_timeout_sec"]),
        "regions": regions,
    }
    changed = [key for key, value in config.items() if not _same(current.get(key), value)]
    _write_json(path, config)
    return {"ok": True, "path": str(path), "changed": changed, "warnings": warnings}


def apply_ai_gateway(settings, raw: dict) -> dict:
    """Сгенерировать config.json ai-gateway (+ PAC при make_pac)."""
    root = Path(str(raw["root"]))
    warnings: list[str] = []
    if not root.is_dir():
        return {"ok": False, "changed": [], "warnings": [f"каталог не найден: {root}"]}
    path = root / "config.json"
    current = _read_json(path)
    routes: list[dict] = []
    for item in raw.get("routes") or []:
        pair = _split_route(item)
        if pair and pair[0]:
            routes.append({"pattern": pair[0], "via": pair[1]})
    proxybroker: dict = {"host": str(raw["pb_host"]), "port": _as_int(raw["pb_port"])}
    if raw.get("pb_type"):
        proxybroker["type"] = str(raw["pb_type"])
    if raw.get("pb_user"):
        proxybroker["user"] = str(raw["pb_user"])
    if raw.get("pb_password"):
        proxybroker["password"] = str(raw["pb_password"])
    worker: dict = {"host": str(raw["cf_host"]), "port": _as_int(raw["cf_port"])}
    if raw.get("cf_secret"):
        worker["secret"] = str(raw["cf_secret"])
    config = {
        "listen": {"host": str(raw["listen_host"]), "port": _as_int(raw["listen_port"])},
        "default_via": str(raw["default_via"]),
        "regionSpoofRoot": current.get("regionSpoofRoot")
        or str(default_root(settings, "region-spoof")),
        "upstream_proxy": str(raw.get("upstream_proxy") or ""),
        "upstream_domains": [str(item) for item in raw.get("upstream_domains") or []],
        "pools": {"proxybroker": proxybroker, "cloudflare_worker": worker},
        "routes": routes,
    }
    changed = [key for key, value in config.items() if not _same(current.get(key), value)]
    _write_json(path, config)
    pac: dict = {"requested": bool(raw.get("make_pac")), "ok": None, "detail": ""}
    if raw.get("make_pac"):
        script = root / "tools" / "make-pac.py"
        if not script.exists():
            pac["ok"] = False
            pac["detail"] = "нет tools/make-pac.py"
            warnings.append("PAC не сгенерирован: нет tools/make-pac.py")
        else:
            argv = [_python_arg(settings, raw), str(script), str(path),
                    "-o", str(root / "gateway.pac")]
            if str(raw.get("upstream_proxy") or ""):
                argv += ["--proxy", str(raw["upstream_proxy"])]
            run = _run(argv, cwd=root, timeout=60)
            pac["ok"] = bool(run.get("ok"))
            pac["detail"] = str(run.get("detail") or "")[:500]
            if not run.get("ok"):
                warnings.append(f"PAC: сбой генерации ({pac['detail']})")
    return {"ok": True, "path": str(path), "changed": changed, "warnings": warnings,
            "pac": pac}


def apply_my_openai_gemini(settings, raw: dict) -> dict:
    """Записать локальный `.env` прокси Gemini (только при mode=local)."""
    root = Path(str(raw["root"]))
    if raw.get("mode") != "local":
        return {"ok": True, "changed": [], "warnings": [],
                "note": "режим deployed: локальный запуск не нужен"}
    if not root.is_dir():
        return {"ok": False, "changed": [], "warnings": [f"каталог не найден: {root}"]}
    path = root / ".env"
    current = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    lines = [
        "# Сгенерировано фабрикой Lab-100 (factory netsuite apply).",
        f"PORT={_as_int(raw['local_port'])}",
        f"DEFAULT_MODEL={raw['default_model']}",
    ]
    if str(raw.get("gemini_key") or ""):
        lines.append(f"GEMINI_API_KEY={raw['gemini_key']}")
    text = "\n".join(lines) + "\n"
    changed = [] if text == current else ["PORT", "DEFAULT_MODEL"]
    path.write_text(text, encoding="utf-8")
    return {"ok": True, "path": str(path), "changed": changed, "warnings": []}


def apply(settings, tool_id: str | None = None) -> dict:
    """Привести фактические конфиги средств в соответствие с настройками."""
    ids = [str(tool_id)] if tool_id else tool_ids()
    results: dict[str, dict] = {}
    changed: list[str] = []
    warnings: list[str] = []
    for item in ids:
        if item not in TOOLS_BY_ID:
            return {"ok": False, "error": f"неизвестное средство: {item}"}
        raw = _raw_params(settings, item)
        if item == "region-spoof":
            outcome = apply_region_spoof(settings, raw)
        elif item == "ai-gateway":
            outcome = apply_ai_gateway(settings, raw)
        else:
            outcome = apply_my_openai_gemini(settings, raw)
        results[item] = outcome
        for field in outcome.get("changed") or []:
            changed.append(f"{item}:{field}" if isinstance(field, str) else str(item))
        warnings += [f"{item}: {text}" for text in outcome.get("warnings") or []]
    return {"ok": all(r.get("ok") for r in results.values()),
            "changed": changed, "warnings": warnings, "tools": results}


# --- запуск и остановка -------------------------------------------------------

def _python_arg(settings, raw: dict) -> str:
    """Интерпретатор Python: из параметров или текущий."""
    configured = str(raw.get("python") or "").strip()
    return configured or sys.executable


def _run(argv: list[str], *, cwd: Path | None = None, timeout: float = 30.0,
         env: dict | None = None) -> dict:
    """Синхронный вызов с запечатанным выводом (UTF-8, без внешних библиотек)."""
    try:
        proc = subprocess.run(argv, cwd=str(cwd) if cwd else None, capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, check=False, env=env)
    except FileNotFoundError:
        return {"ok": False, "code": None, "detail": f"команда не найдена: {argv[0]}",
                "stdout": "", "stderr": ""}
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": None, "detail": f"таймаут {timeout} с",
                "stdout": "", "stderr": ""}
    except Exception as exc:  # noqa: BLE001 - сбой запуска не критичен
        return {"ok": False, "code": None, "detail": f"{type(exc).__name__}: {exc}",
                "stdout": "", "stderr": ""}
    return {"ok": proc.returncode == 0, "code": proc.returncode,
            "detail": ((proc.stdout or "").strip() or (proc.stderr or "").strip())[-500:],
            "stdout": proc.stdout or "", "stderr": proc.stderr or ""}


def _spawn(argv: list[str], *, cwd: Path) -> dict:
    """Отвязанный запуск процесса (Windows — DETACHED_PROCESS)."""
    flags = 0
    if sys.platform.startswith("win"):
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | \
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        proc = subprocess.Popen(argv, cwd=str(cwd), stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                creationflags=flags, close_fds=True)
    except FileNotFoundError:
        return {"ok": False, "error": f"команда не найдена: {argv[0]}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "pid": proc.pid}


def start(settings, tool_id: str) -> dict:
    """Запустить средство (отвязанно) и запомнить pid в реестре сервиса."""
    if tool_id not in TOOLS_BY_ID:
        return {"ok": False, "error": f"неизвестное средство: {tool_id}"}
    raw = _raw_params(settings, tool_id)
    root = Path(str(raw["root"]))
    if not root.is_dir():
        return {"ok": False, "error": f"каталог не найден: {root}"}
    if tool_id == "region-spoof":
        argv = [_python_arg(settings, raw), "applet.py"]
    elif tool_id == "ai-gateway":
        argv = [_python_arg(settings, raw), "gateway.py"]
    else:
        if raw.get("mode") != "local":
            return {"ok": False,
                    "error": "режим deployed: сервис уже развёрнут, локальный запуск не нужен"}
        node = shutil.which(str(raw.get("node_path") or "node"))
        if not node:
            return {"ok": False, "error": "node не найден в PATH"}
        argv = [node, "node.mjs"]
    launcher = root / str(TOOLS_BY_ID[tool_id]["launcher"])
    if not launcher.exists():
        return {"ok": False, "error": f"нет файла запуска: {launcher.name}"}
    live = running_pid(settings, tool_id)
    if live:
        return {"ok": True, "already": True, "pid": live["pid"],
                "note": "процесс уже запущен"}
    spawned = _spawn(argv, cwd=root)
    if not spawned.get("ok"):
        return spawned
    record = {
        "pid": spawned["pid"],
        "name": Path(argv[0]).name.lower(),
        "argv": argv,
        "root": str(root),
        "ts": _now(),
    }
    pids = _load_pids(settings)
    pids[tool_id] = record
    _save_pids(settings, pids)
    return {"ok": True, "pid": spawned["pid"], "argv": argv, "root": str(root)}


def stop(settings, tool_id: str) -> dict:
    """Остановить средство: штатно (--stop-all) либо по своему pid."""
    if tool_id not in TOOLS_BY_ID:
        return {"ok": False, "error": f"неизвестное средство: {tool_id}"}
    raw = _raw_params(settings, tool_id)
    root = Path(str(raw["root"]))
    steps: list[dict] = []
    live = running_pid(settings, tool_id)
    if tool_id == "region-spoof" and (root / "applet.py").exists():
        # Штатный путь: приложение само снимает системный прокси.
        run = _run([_python_arg(settings, raw), "applet.py", "--stop-all"],
                   cwd=root, timeout=60)
        steps.append({"step": "applet --stop-all", "ok": bool(run.get("ok")),
                      "detail": str(run.get("detail"))[:200]})
    if live:
        killed = _kill_pid(int(live["pid"]))
        steps.append({"step": f"kill {live['pid']}", "ok": bool(killed.get("ok")),
                      "detail": killed.get("detail", "")})
    pids = _load_pids(settings)
    if tool_id in pids:
        pids.pop(tool_id, None)
        _save_pids(settings, pids)
    if not steps:
        return {"ok": True, "note": "нечего останавливать", "steps": []}
    return {"ok": all(step["ok"] for step in steps), "steps": steps}


def _kill_pid(pid: int) -> dict:
    """Завершить процесс: Windows — taskkill, иначе — SIGTERM."""
    try:
        if sys.platform.startswith("win"):
            run = _run(["taskkill", "/PID", str(pid), "/T", "/F"], timeout=20)
            return {"ok": bool(run.get("ok")), "detail": str(run.get("detail"))[:200]}
        os.kill(int(pid), 15)  # SIGTERM
        return {"ok": True, "detail": "SIGTERM отправлен"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


# --- проверка боем и подготовка ----------------------------------------------

def _last_json(text: str) -> dict | None:
    """Последний разбираемый JSON-объект в выводе команды."""
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def check(settings, tool_id: str | None = None) -> dict:
    """Проверка боем: боевой опрос каждого средства (или одного)."""
    ids = [str(tool_id)] if tool_id else tool_ids()
    rows: list[dict] = []
    for item in ids:
        if item not in TOOLS_BY_ID:
            return {"ok": False, "error": f"неизвестное средство: {item}"}
        raw = _raw_params(settings, item)
        root = Path(str(raw["root"]))
        if item == "region-spoof":
            if not (root / "applet.py").exists():
                rows.append({"id": item, "ok": False,
                             "detail": f"нет applet.py в {root}"})
                continue
            run = _run([_python_arg(settings, raw), "applet.py", "--check"],
                       cwd=root, timeout=25)
            data = _last_json(run.get("stdout") or "")
            rows.append({"id": item, "ok": bool(data) or bool(run.get("ok")),
                         "detail": str(run.get("detail"))[:300], "data": data})
        elif item == "ai-gateway":
            host = str(raw["listen_host"])
            port = _as_int(raw["listen_port"])
            opened = _port_open(host, port, timeout=1.0)
            rows.append({"id": item, "ok": opened, "host": host, "port": port,
                         "detail": (f"{host}:{port} отвечает" if opened
                                    else f"{host}:{port} закрыт")})
        else:
            rows.append(_check_gemini(raw))
    return {"schema": SCHEMA, "generated": _now(), "tools": rows,
            "summary": {"total": len(rows), "ok": sum(1 for r in rows if r.get("ok"))}}


def _check_gemini(raw: dict) -> dict:
    """Проверка прокси Gemini: локальный /models или адрес деплоя /v1/models."""
    if raw.get("mode") == "deployed":
        base = str(raw.get("base_url") or "").strip().rstrip("/")
        if not base:
            return {"id": "my-openai-gemini", "ok": False,
                    "detail": "не задан адрес деплоя base_url"}
        url = f"{base}/v1/models"
    else:
        url = f"http://127.0.0.1:{_as_int(raw.get('local_port'), 8080)}/models"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310 - свои адреса
            return {"id": "my-openai-gemini", "ok": True, "url": url,
                    "detail": f"HTTP {resp.status}"}
    except urllib.error.HTTPError as exc:
        # сервис ответил (значит, жив), даже если запрос не понравился
        return {"id": "my-openai-gemini", "ok": True, "url": url,
                "detail": f"HTTP {exc.code}"}
    except Exception as exc:  # noqa: BLE001 - недоступность не критична
        return {"id": "my-openai-gemini", "ok": False, "url": url,
                "detail": f"недоступно ({type(exc).__name__})"}


def bootstrap(settings, tool_id: str) -> dict:
    """Подготовка средства: скачать ядра, проверить запускающий файл, найти node."""
    if tool_id not in TOOLS_BY_ID:
        return {"ok": False, "error": f"неизвестное средство: {tool_id}"}
    raw = _raw_params(settings, tool_id)
    root = Path(str(raw["root"]))
    if not root.is_dir():
        return {"ok": False, "error": f"каталог не найден: {root}", "steps": []}
    if tool_id == "region-spoof":
        script = root / "download_binaries.py"
        if not script.exists():
            return {"ok": False, "steps": [],
                    "error": "нет download_binaries.py — нечем качать ядра"}
        run = _run([_python_arg(settings, raw), str(script), "--dir", str(root / "bin")],
                   cwd=root, timeout=1800)
        return {"ok": bool(run.get("ok")), "steps": [{
            "step": "download_binaries.py --dir bin", "ok": bool(run.get("ok")),
            "detail": str(run.get("detail"))[:500]}]}
    if tool_id == "ai-gateway":
        launcher = root / "gateway.py"
        if not launcher.exists():
            return {"ok": False, "steps": [],
                    "error": "нет gateway.py — копия ai-gateway не развёрнута"}
        return {"ok": True, "steps": [{"step": "проверка gateway.py", "ok": True,
                                       "detail": f"найден: {launcher}"}]}
    node = shutil.which(str(raw.get("node_path") or "node"))
    launcher = root / "node.mjs"
    ok = bool(node) and launcher.exists()
    steps = [{"step": "поиск node", "ok": bool(node), "detail": node or "не найден"},
             {"step": "проверка node.mjs", "ok": launcher.exists(),
              "detail": str(launcher)}]
    steps += [{"step": "проверка package.json", "ok": (root / "package.json").exists(),
               "detail": "зависимости ставятся командой npm install"}]
    return {"ok": ok, "steps": steps}


# --- базы: маршруты, прокси, домены -------------------------------------------

def _strip_comment(line: str) -> str:
    """Убрать «#»-комментарий (только вне кавычек — упрощённо, по первому #)."""
    if "#" in line:
        return line.split("#", 1)[0]
    return line


def _split_route(line: str) -> tuple[str, str] | None:
    """Разобрать строку маршрута: «домен<разделитель>via»."""
    text = _strip_comment(str(line)).strip()
    if not text:
        return None
    for separator in ("=>", "\t", "|", ";"):
        if separator in text:
            pattern, _, via = text.partition(separator)
            pattern, via = pattern.strip(), via.strip()
            if pattern and via:
                return pattern, via
            return None
    if "," in text:
        pattern, _, via = text.partition(",")
        pattern, via = pattern.strip(), via.strip()
        if pattern and via:
            return pattern, via
        return None
    parts = text.split(None, 1)
    if len(parts) == 2 and parts[1].strip():
        return parts[0].strip(), parts[1].strip()
    return None


def parse_routes(text: str) -> list[str]:
    """Разобрать файл маршрутов в список строк «домен => via»."""
    out: list[str] = []
    for line in (text or "").splitlines():
        pair = _split_route(line)
        if pair:
            out.append(f"{pair[0]} => {pair[1]}")
    return out


def parse_proxies(text: str) -> list[str]:
    """Разобрать файл прокси: строки «host:port[:user:pass]»."""
    out: list[str] = []
    for line in (text or "").splitlines():
        item = _strip_comment(line).strip().strip(",")
        if not item:
            continue
        out.append(item)
    return out


def parse_upstream_domains(text: str) -> list[str]:
    """Разобрать файл доменов: по строке на домен."""
    out: list[str] = []
    for line in (text or "").splitlines():
        item = _strip_comment(line).strip().strip(",").strip('"')
        if item:
            out.append(item)
    return out


def _read_base(path: Path) -> dict:
    text = path.read_text(encoding="utf-8-sig")
    return {
        "routes": parse_routes(text),
        "proxies": parse_proxies(text),
        "upstream_domains": parse_upstream_domains(text),
    }


def _split_proxy(item: str) -> dict:
    """Разобрать «host:port[:user:pass]» в поля пула прокси."""
    parts = str(item).split(":")
    out = {"host": "", "port": 0, "user": "", "password": ""}
    if len(parts) >= 2:
        out["host"] = parts[0].strip()
        out["port"] = _as_int(parts[1].strip(), 0)
    if len(parts) >= 3:
        out["user"] = parts[2].strip()
    if len(parts) >= 4:
        out["password"] = parts[3].strip()
    return out


def import_base(settings, tool_id: str, kind: str, path: str | Path) -> dict:
    """Импорт базы (маршруты/прокси/домены) из файла в настройки средства."""
    if tool_id not in TOOLS_BY_ID:
        return {"ok": False, "error": f"неизвестное средство: {tool_id}"}
    if kind not in BASE_KINDS:
        return {"ok": False, "error": f"неизвестный вид базы: {kind}"}
    if tool_id != "ai-gateway":
        return {"ok": False,
                "error": f"базы вида {kind} поддерживает только ai-gateway"}
    source = Path(path)
    if not source.is_file():
        return {"ok": False, "error": f"файл не найден: {source}"}
    try:
        parsed = _read_base(source)
    except OSError as exc:
        return {"ok": False, "error": f"не удалось прочитать файл: {exc}"}
    items = parsed.get(kind) or []
    warnings: list[str] = []
    if not items:
        warnings.append("в файле не нашлось подходящих строк")
    applied: dict[str, Any] = {}
    if kind == "routes":
        applied["routes"] = items
    elif kind == "upstream_domains":
        applied["upstream_domains"] = items
    else:
        warnings.append("использована первая строка; остальные сохранены в архиве базы")
        first = _split_proxy(items[0]) if items else {}
        if first:
            applied = {"pb_host": first["host"], "pb_port": first["port"],
                       "pb_user": first["user"], "pb_password": first["password"]}
    result = set_bulk(settings, tool_id, applied)
    archive = _archive_base(settings, tool_id, kind, source)
    return {"ok": bool(result.get("ok")), "tool": tool_id, "kind": kind,
            "source": str(source), "count": len(items), "items": items[:20],
            "applied": result.get("applied", {}), "errors": result.get("errors", []),
            "archive": archive, "warnings": warnings}


def _archive_base(settings, tool_id: str, kind: str, source: Path) -> str:
    """Сохранить копию базы в каталоге данных сервиса (история импорта)."""
    target_dir = data_dir(settings) / str(tool_id)
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{kind}.txt"
        shutil.copyfile(source, target)
        return str(target)
    except OSError:
        return ""


def export_base(settings, tool_id: str, kind: str, path: str | Path) -> dict:
    """Экспорт базы из настроек средства в файл."""
    if tool_id not in TOOLS_BY_ID:
        return {"ok": False, "error": f"неизвестное средство: {tool_id}"}
    if kind not in BASE_KINDS:
        return {"ok": False, "error": f"неизвестный вид базы: {kind}"}
    if tool_id != "ai-gateway":
        return {"ok": False,
                "error": f"базы вида {kind} поддерживает только ai-gateway"}
    raw = _raw_params(settings, tool_id)
    if kind == "routes":
        lines = [str(item) for item in raw.get("routes") or []]
    elif kind == "upstream_domains":
        lines = [str(item) for item in raw.get("upstream_domains") or []]
    else:
        lines = []
        host = str(raw.get("pb_host") or "")
        port = _as_int(raw.get("pb_port"), 0)
        if host and port:
            line = f"{host}:{port}"
            if str(raw.get("pb_user") or ""):
                line += f":{raw['pb_user']}"
                if str(raw.get("pb_password") or ""):
                    line += f":{raw['pb_password']}"
            lines = [line]
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": f"не удалось записать файл: {exc}"}
    return {"ok": True, "tool": tool_id, "kind": kind, "path": str(target),
            "count": len(lines), "items": lines[:20]}


# --- сводка для наблюдаемости -------------------------------------------------

def summary(settings) -> dict:
    """Короткая сводка сервиса для интеграции «здоровье» и панели."""
    if settings is None or not settings.get("netsuite"):
        return {"enabled": False, "configured": False, "running": 0, "issues": [],
                "hint": ("сервис не настроен: задайте параметры командой "
                         "`factory netsuite list`, затем `factory netsuite set …`")}
    data = status(settings)
    issues: list[str] = []
    for row in data.get("tools", []):
        for problem in row.get("issues") or []:
            issues.append(f"{row['id']}: {problem}")
        if row["enabled"] and not row["applied"] and row["root_exists"]:
            issues.append(f"{row['id']}: настройки не применены (`factory netsuite apply`)")
    return {
        "enabled": bool(data.get("suite_enabled")),
        "configured": True,
        "tools": data.get("summary", {}).get("total", 0),
        "on": data.get("summary", {}).get("enabled", 0),
        "running": data.get("summary", {}).get("running", 0),
        "applied": data.get("summary", {}).get("applied", 0),
        # «Готово» — включённые средства, у которых на месте рабочий каталог.
        "ready": sum(1 for row in data.get("tools", [])
                     if row["enabled"] and row["root_exists"]),
        "issues": issues[:20],
        "hint": "" if not issues else "есть замечания: `factory netsuite status`",
    }
