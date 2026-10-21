"""Тесты сервиса средств обхода (netsuite): реестр, параметры, применение, базы.

Здесь ничего реально не запускается и в сеть не уходит: все проверки идут на
временных каталогах с фальшивыми файлами (applet.py, gateway.py, node.mjs),
а проверка портов — на заведомо свободном порту.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from factory import netsuite
from factory.integrations import build_integrations
from factory.settings import Settings

# Заведомо свободный порт для проверки «порт закрыт».
FREE_PORT = 65123


class NetsuiteBase(unittest.TestCase):
    """Общая обвязка: временный корень фабрики и пустой settings.toml."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        self.settings_path = self.root / "config" / "settings.toml"
        self.settings_path.write_text("", encoding="utf-8")
        self.settings = Settings(self.settings_path, {})

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _make_root(self, tool_id: str, files: tuple[str, ...] = ()) -> Path:
        """Создать фальшивый корень средства с нужными файлами запуска."""
        root = self.root / "Lab100-Factory" / tool_id
        root.mkdir(parents=True, exist_ok=True)
        for name in files:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# заглушка\n", encoding="utf-8")
        return root


class RegistryTest(NetsuiteBase):
    """Реестр средств и схемы параметров."""

    def test_all_tools_with_required_keys(self) -> None:
        specs = netsuite.tool_specs()
        self.assertEqual([item["id"] for item in specs],
                         ["region-spoof", "ai-gateway", "my-openai-gemini",
                          "ai-sni-proxy"])
        for item in specs:
            for key in ("id", "title", "description", "kind", "default_port",
                        "config_file", "params", "launcher"):
                self.assertIn(key, item, f"{item['id']}: нет ключа {key}")
            self.assertTrue(str(item["title"]).strip(), f"{item['id']}: пустой title")
            self.assertTrue(item["params"], f"{item['id']}: схема параметров пуста")
            for descr in item["params"]:
                for key in ("key", "title", "type", "default", "help", "choices"):
                    self.assertIn(key, descr, f"{item['id']}.{descr}: нет {key}")
                self.assertIn(descr["type"],
                              ("bool", "int", "float", "str", "list", "select",
                               "path", "secret"))
                self.assertTrue(str(descr["title"]).strip(), "title параметра не по-русски")

    def test_spec_copy_is_isolated(self) -> None:
        copy = netsuite.tool_specs()
        copy[0]["title"] = "испорчено"
        self.assertNotEqual(netsuite.tool_specs()[0]["title"], "испорчено")

    def test_param_tables_present(self) -> None:
        by_id = {item["id"]: {d["key"] for d in item["params"]}
                 for item in netsuite.tool_specs()}
        region_expected = {"enabled", "root", "python", "region", "zone", "strategy",
                           "proxy_port", "spare_ports", "auto_start",
                           "check_interval_sec", "verify_timeout_sec",
                           "failover_threshold", "pb2_limit", "pb2_timeout_sec"}
        self.assertTrue(region_expected <= by_id["region-spoof"],
                        sorted(region_expected - by_id["region-spoof"]))
        gateway_expected = {"enabled", "root", "python", "listen_host", "listen_port",
                            "default_via", "routes", "pb_host", "pb_port", "pb_type",
                            "pb_user", "pb_password", "cf_host", "cf_port", "cf_secret",
                            "upstream_proxy", "upstream_domains", "make_pac"}
        self.assertTrue(gateway_expected <= by_id["ai-gateway"],
                        sorted(gateway_expected - by_id["ai-gateway"]))
        gemini_expected = {"enabled", "root", "mode", "local_port", "base_url",
                           "default_model", "node_path", "gemini_key", "deploy_target"}
        self.assertTrue(gemini_expected <= by_id["my-openai-gemini"],
                        sorted(gemini_expected - by_id["my-openai-gemini"]))
        sni_expected = {"enabled", "root", "entry", "list_url", "hosts_path"}
        self.assertTrue(sni_expected <= by_id["ai-sni-proxy"],
                        sorted(sni_expected - by_id["ai-sni-proxy"]))
        # выборы полей совпадают с реальными значениями средств
        select = {d["key"]: d["choices"] for d in netsuite.tool_specs()[0]["params"]
                  if d["type"] == "select"}
        self.assertEqual(select["strategy"], ["system", "singbox", "mihomo"])
        self.assertEqual(select["zone"], ["foreign", "ru"])
        self.assertEqual(len(select["region"]), 17)


class ParamsTest(NetsuiteBase):
    """Параметры: чтение, запись, приведение типов, секреты."""

    def test_defaults(self) -> None:
        params = netsuite.get_params(self.settings, "region-spoof")
        self.assertTrue(params["enabled"])
        self.assertEqual(params["region"], "US")
        self.assertEqual(params["proxy_port"], 8888)
        self.assertEqual(params["spare_ports"], [8889, 8890, 8891, 8892])
        self.assertEqual(params["pb2_limit"], 40)
        self.assertEqual(params["pb2_timeout_sec"], 10)
        # корень по умолчанию вычисляется, а не пустой
        self.assertTrue(str(params["root"]).strip())

    def test_set_param_types_and_persistence(self) -> None:
        ok = netsuite.set_param(self.settings, "region-spoof", "auto_start", "false")
        self.assertTrue(ok["ok"])
        self.assertIs(ok["value"], False)
        netsuite.set_param(self.settings, "region-spoof", "proxy_port", "9999")
        netsuite.set_param(self.settings, "region-spoof", "spare_ports", "9001, 9002")
        # секция с дефисом читается и после перечитывания файла
        fresh = Settings(self.settings_path, {})
        params = netsuite.get_params(fresh, "region-spoof")
        self.assertIs(params["auto_start"], False)
        self.assertEqual(params["proxy_port"], 9999)
        self.assertEqual(params["spare_ports"], [9001, 9002])

    def test_set_param_rejects_unknown(self) -> None:
        result = netsuite.set_param(self.settings, "нет-такого", "x", "1")
        self.assertFalse(result["ok"])
        result = netsuite.set_param(self.settings, "region-spoof", "нет-такого", "1")
        self.assertFalse(result["ok"])
        self.assertIn("нет-такого", result["error"])
        # значение вне списка выборов не принимается
        result = netsuite.set_param(self.settings, "region-spoof", "strategy", "wireguard")
        self.assertFalse(result["ok"])
        self.assertIn("ожидается", result["error"])

    def test_secret_masked(self) -> None:
        netsuite.set_param(self.settings, "ai-gateway", "pb_password", "top-secret")
        params = netsuite.get_params(self.settings, "ai-gateway")
        self.assertEqual(params["pb_password"], "***")
        self.assertNotIn("top-secret", json.dumps(params, ensure_ascii=False))
        self.assertTrue(params["pb_password_set"])
        # маска не попадает и в статус
        status_text = json.dumps(netsuite.status(self.settings), ensure_ascii=False)
        self.assertNotIn("top-secret", status_text)

    def test_set_bulk(self) -> None:
        result = netsuite.set_bulk(self.settings, "ai-gateway", {
            "listen_port": "8899",
            "make_pac": "no",
            "pb_password": "pw",
            "routes": ["a.example => direct", "b.example => pool:proxybroker"],
            "нет-такого": "1",
            "default_via": "неправильно",
        })
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0].startswith("неизвестный параметр"), True)
        self.assertTrue(any("неправильно" in item for item in result["errors"]))
        params = netsuite.get_params(self.settings, "ai-gateway")
        self.assertEqual(params["listen_port"], 8899)
        self.assertIs(params["make_pac"], False)
        self.assertEqual(len(params["routes"]), 2)
        self.assertEqual(params["pb_password"], "***")

    def test_suite_toggle(self) -> None:
        self.assertFalse(netsuite.suite_enabled(self.settings))  # секции нет
        netsuite.set_param(self.settings, "region-spoof", "enabled", "true")
        self.assertTrue(netsuite.suite_enabled(self.settings))
        settings = Settings(self.settings_path, {})
        settings.set("netsuite.enabled", False)
        settings.save()
        self.assertFalse(netsuite.suite_enabled(settings))


class ApplyTest(NetsuiteBase):
    """Применение настроек к фактическим конфигам средств."""

    def test_apply_region_spoof_config(self) -> None:
        root = self._make_root("region-spoof", ("applet.py", "download_binaries.py"))
        netsuite.set_param(self.settings, "region-spoof", "root", str(root))
        netsuite.set_param(self.settings, "region-spoof", "region", "DE")
        netsuite.set_param(self.settings, "region-spoof", "strategy", "singbox")
        result = netsuite.apply(self.settings, "region-spoof")
        self.assertTrue(result["ok"], result)
        config = json.loads((root / "config.json").read_text(encoding="utf-8"))
        for key in ("region", "strategy", "zone", "proxy_port", "spare_ports",
                    "auto_start", "check_interval_sec", "verify_timeout_sec",
                    "failover_threshold", "pb2_limit", "pb2_timeout_sec", "regions"):
            self.assertIn(key, config, f"нет ключа {key} в config.json")
        self.assertEqual(config["region"], "DE")
        self.assertEqual(config["strategy"], "singbox")
        self.assertEqual(config["pb2_limit"], 40)
        self.assertEqual(config["pb2_timeout_sec"], 10)
        # повторное применение без изменений — без правок
        again = netsuite.apply(self.settings, "region-spoof")
        self.assertEqual(again["changed"], [])

    def test_apply_gateway_config_without_make_pac(self) -> None:
        root = self._make_root("ai-gateway", ("gateway.py",))  # tools/make-pac.py нет
        netsuite.set_param(self.settings, "ai-gateway", "root", str(root))
        netsuite.set_param(self.settings, "ai-gateway", "listen_port", "8898")
        netsuite.set_param(self.settings, "ai-gateway", "default_via", "pool:proxybroker")
        netsuite.set_param(self.settings, "ai-gateway", "pb_port", "8888")
        netsuite.set_param(self.settings, "ai-gateway", "routes",
                           ["api.example => pool:proxybroker"])
        netsuite.set_param(self.settings, "ai-gateway", "make_pac", "true")
        result = netsuite.apply(self.settings, "ai-gateway")
        self.assertTrue(result["ok"], result)
        config = json.loads((root / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(config["listen"], {"host": "127.0.0.1", "port": 8898})
        self.assertEqual(config["default_via"], "pool:proxybroker")
        self.assertEqual(config["pools"]["proxybroker"]["port"], 8888)
        self.assertEqual(config["routes"], [{"pattern": "api.example",
                                             "via": "pool:proxybroker"}])
        # грациозная деградация: make-pac.py отсутствует — не падаем, предупреждаем
        self.assertEqual(result["tools"]["ai-gateway"]["pac"]["ok"], False)
        self.assertTrue(any("make-pac" in item for item in result["warnings"]), result)
        # в статусе это видно как замечание
        status = netsuite.status(self.settings, "ai-gateway")
        self.assertTrue(any("make-pac" in item for item in status["issues"]), status)

    def test_apply_gemini_env(self) -> None:
        root = self._make_root("my-openai-gemini", ("node.mjs",))
        netsuite.set_param(self.settings, "my-openai-gemini", "root", str(root))
        netsuite.set_param(self.settings, "my-openai-gemini", "local_port", "9090")
        result = netsuite.apply(self.settings, "my-openai-gemini")
        self.assertTrue(result["ok"], result)
        env = (root / ".env").read_text(encoding="utf-8")
        self.assertIn("PORT=9090", env)
        self.assertIn("DEFAULT_MODEL=gemini-2.5-flash", env)
        self.assertIs(result["tools"]["my-openai-gemini"]["ok"], True)

    def test_apply_missing_root_warns(self) -> None:
        netsuite.set_param(self.settings, "region-spoof", "root",
                           str(self.root / "нет-такого-каталога"))
        result = netsuite.apply(self.settings, "region-spoof")
        self.assertFalse(result["ok"])
        self.assertTrue(result["warnings"])
        self.assertFalse(result["changed"])


class StatusTest(NetsuiteBase):
    """Наблюдение: отсутствие корней, закрытый порт, проблемы."""

    def test_missing_roots(self) -> None:
        missing = str(self.root / "нет-корня")
        netsuite.set_param(self.settings, "region-spoof", "root", missing)
        netsuite.set_param(self.settings, "region-spoof", "proxy_port", str(FREE_PORT))
        netsuite.set_param(self.settings, "ai-gateway", "root", missing)
        netsuite.set_param(self.settings, "ai-gateway", "listen_port", str(FREE_PORT))
        netsuite.set_param(self.settings, "my-openai-gemini", "root", missing)
        netsuite.set_param(self.settings, "my-openai-gemini", "local_port", str(FREE_PORT))
        netsuite.set_param(self.settings, "ai-sni-proxy", "root", missing)
        data = netsuite.status(self.settings)
        self.assertEqual(len(data["tools"]), 4)
        for row in data["tools"]:
            self.assertFalse(row["root_exists"], row["id"])
            self.assertFalse(row["port_open"], row["id"])
            self.assertFalse(row["applied"], row["id"])
            self.assertTrue(row["issues"], row["id"])
            self.assertTrue(row["running"] is False, row["id"])
        self.assertEqual(data["summary"]["total"], 4)
        self.assertEqual(data["summary"]["running"], 0)

    def test_applied_and_binaries(self) -> None:
        root = self._make_root("region-spoof", ("applet.py",))
        (root / "bin" / "singbox" / "1.3").mkdir(parents=True)
        (root / "bin" / "singbox" / "1.3" / "sing-box.exe").write_text("x", encoding="utf-8")
        netsuite.set_param(self.settings, "region-spoof", "root", str(root))
        netsuite.set_param(self.settings, "region-spoof", "strategy", "singbox")
        netsuite.apply(self.settings, "region-spoof")
        row = netsuite.status(self.settings, "region-spoof")
        self.assertTrue(row["root_exists"])
        self.assertTrue(row["binaries"]["singbox"])
        self.assertFalse(row["binaries"]["mihomo"])  # не скачивали
        self.assertTrue(row["applied"])
        # без ядра mihomo/для singbox есть предупреждения о wintun
        self.assertTrue(any("wintun" in item for item in row["issues"]), row["issues"])

    def test_no_secrets_in_status(self) -> None:
        netsuite.set_param(self.settings, "ai-gateway", "cf_secret", "секрет-не-виден")
        text = json.dumps(netsuite.status(self.settings), ensure_ascii=False)
        self.assertNotIn("секрет-не-виден", text)

    def test_unknown_tool(self) -> None:
        result = netsuite.status(self.settings, "нет-такого")
        self.assertFalse(result.get("ok", True))


class BasesTest(NetsuiteBase):
    """Импорт/экспорт баз: маршруты, прокси, домены."""

    def _base_file(self, name: str, text: str) -> Path:
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_parse_routes_tolerant(self) -> None:
        path = self._base_file("routes.txt",
                               "# комментарий\n"
                               "\n"
                               "  \n"
                               "api.example => pool:proxybroker\n"
                               "web.example\tcf:cloudflare_worker\n"
                               "cdn.example, direct  # хвостовой комментарий\n"
                               "old.example direct\n"
                               "покриважено-но-пусто\n")
        result = netsuite.import_base(self.settings, "ai-gateway", "routes", str(path))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["count"], 4, result["items"])
        params = netsuite.get_params(self.settings, "ai-gateway")
        self.assertEqual(params["routes"], [
            "api.example => pool:proxybroker",
            "web.example => cf:cloudflare_worker",
            "cdn.example => direct",
            "old.example => direct",
        ])

    def test_parse_domains_and_proxies_roundtrip(self) -> None:
        domains = self._base_file("domains.txt",
                                  "# заголовок\n"
                                  "example.com\n"
                                  "\n"
                                  "  api.example.org  \n"
                                  "# хвост\n"
                                  "shop.example.net\n")
        result = netsuite.import_base(self.settings, "ai-gateway",
                                      "upstream_domains", str(domains))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["count"], 3)
        out = self.root / "domains-out.txt"
        exported = netsuite.export_base(self.settings, "ai-gateway",
                                        "upstream_domains", out)
        self.assertTrue(exported["ok"], exported)
        self.assertEqual(set(out.read_text(encoding="utf-8").splitlines()),
                         {"example.com", "api.example.org", "shop.example.net"})

        proxies = self._base_file("proxies.txt",
                                  "# пул\n"
                                  "8.8.8.8:3128\n"
                                  "\n"
                                  "1.2.3.4:8080:user:pass\n")
        result = netsuite.import_base(self.settings, "ai-gateway", "proxies", str(proxies))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["count"], 2)
        params = netsuite.get_params(self.settings, "ai-gateway")
        self.assertEqual(params["pb_host"], "8.8.8.8")   # взята первая строка
        self.assertEqual(params["pb_port"], 3128)
        # круговой обмен: экспорт → импорт → тот же результат
        out = self.root / "proxies-out.txt"
        exported = netsuite.export_base(self.settings, "ai-gateway", "proxies", out)
        self.assertTrue(exported["ok"], exported)
        self.assertEqual(out.read_text(encoding="utf-8").strip(), "8.8.8.8:3128")
        fresh = Settings(self.settings_path, {})
        again = netsuite.import_base(fresh, "ai-gateway", "proxies", out)
        self.assertTrue(again["ok"], again)
        params = netsuite.get_params(fresh, "ai-gateway")
        self.assertEqual(params["pb_host"], "8.8.8.8")
        self.assertEqual(params["pb_port"], 3128)

    def test_base_errors(self) -> None:
        result = netsuite.import_base(self.settings, "ai-gateway", "routes",
                                      str(self.root / "нет-файла.txt"))
        self.assertFalse(result["ok"])
        result = netsuite.import_base(self.settings, "ai-gateway", "нет-вида",
                                      str(self.root))
        self.assertFalse(result["ok"])
        result = netsuite.import_base(self.settings, "region-spoof", "routes",
                                      str(self.root))
        self.assertFalse(result["ok"])
        self.assertIn("ai-gateway", result["error"])
        result = netsuite.export_base(self.settings, "ai-gateway", "routes",
                                      str(self.root / "out.txt"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)


class IntegrationTest(NetsuiteBase):
    """Интеграция наблюдаемости (секция doctor / веб-панель)."""

    def test_health_without_config(self) -> None:
        integration = build_integrations(self.settings)["netsuite"]
        health = integration.health()
        self.assertEqual(health["integration"], "netsuite")
        self.assertFalse(health["available"])
        self.assertFalse(health["configured"])
        self.assertIsNone(health["shim"])
        self.assertEqual(health["sink"], "netsuite")
        self.assertTrue(str(health["hint"]).strip(), "подсказка пуста")
        # строка остаётся пригодной для doctor и панели
        from factory.doctor import run_doctor, summarize
        data = run_doctor(self.settings)
        self.assertEqual(len(data["integrations"]), 4)
        row = next(item for item in data["integrations"]
                   if item["integration"] == "netsuite")
        for key in ("integration", "available", "sink"):
            self.assertIn(key, row)
        ready, total = summarize(data["integrations"])
        self.assertEqual(total, 4)

    def test_health_with_config(self) -> None:
        netsuite.set_param(self.settings, "region-spoof", "enabled", "true")
        # средство настроено, но корень отсутствует → «готовность» честно False
        netsuite.set_param(self.settings, "region-spoof", "root",
                           str(self.root / "нет-корня"))
        integration = build_integrations(self.settings)["netsuite"]
        health = integration.health()
        self.assertTrue(health["configured"])
        self.assertFalse(health["available"])  # корень отсутствует → не готов
        emit = integration.emit({"kind": "probe"})
        self.assertTrue(emit["ok"])
        self.assertEqual(emit["sink"], "netsuite")


class ControlTest(NetsuiteBase):
    """Управление: только безопасные отказные пути (без реальных запусков)."""

    def test_start_without_root(self) -> None:
        netsuite.set_param(self.settings, "region-spoof", "root",
                           str(self.root / "нет-корня"))
        result = netsuite.start(self.settings, "region-spoof")
        self.assertFalse(result["ok"])
        self.assertIn("каталог не найден", result["error"])

    def test_start_without_launcher(self) -> None:
        root = self._make_root("ai-gateway")  # gateway.py нет
        netsuite.set_param(self.settings, "ai-gateway", "root", str(root))
        result = netsuite.start(self.settings, "ai-gateway")
        self.assertFalse(result["ok"])
        self.assertIn("gateway.py", result["error"])

    def test_start_deployed_gemini_refused(self) -> None:
        root = self._make_root("my-openai-gemini", ("node.mjs",))
        netsuite.set_param(self.settings, "my-openai-gemini", "root", str(root))
        netsuite.set_param(self.settings, "my-openai-gemini", "mode", "deployed")
        result = netsuite.start(self.settings, "my-openai-gemini")
        self.assertFalse(result["ok"])
        self.assertIn("deployed", result["error"])

    def test_stop_unknown_tool(self) -> None:
        result = netsuite.stop(self.settings, "нет-такого")
        self.assertFalse(result["ok"])

    def test_bootstrap_paths(self) -> None:
        # region-spoof без download_binaries.py → отказ
        root = self._make_root("region-spoof", ("applet.py",))
        netsuite.set_param(self.settings, "region-spoof", "root", str(root))
        result = netsuite.bootstrap(self.settings, "region-spoof")
        self.assertFalse(result["ok"])
        self.assertIn("download_binaries.py", result["error"])
        # ai-gateway: наличие gateway.py
        groot = self._make_root("ai-gateway", ("gateway.py",))
        netsuite.set_param(self.settings, "ai-gateway", "root", str(groot))
        self.assertTrue(netsuite.bootstrap(self.settings, "ai-gateway")["ok"])
        # my-openai-gemini: node.mjs + node в PATH
        groot = self._make_root("my-openai-gemini", ("node.mjs",))
        netsuite.set_param(self.settings, "my-openai-gemini", "root", str(groot))
        self.assertTrue(netsuite.bootstrap(self.settings, "my-openai-gemini")["ok"])

    def test_check_unknown_tool(self) -> None:
        result = netsuite.check(self.settings, "нет-такого")
        self.assertFalse(result["ok"])


class AiSniProxyTest(NetsuiteBase):
    """ai-sni-proxy: реестр, параметры, состояние hosts-блока, контроль."""

    def _make_sni_root(self) -> Path:
        return self._make_root("ai-sni-proxy",
                               ("bin/ai-sni-proxy.ps1", "domains/fallback.txt",
                                "windows/enable.bat", "windows/disable.bat"))

    def test_params_defaults(self) -> None:
        params = netsuite.get_params(self.settings, "ai-sni-proxy")
        self.assertFalse(params["enabled"])            # безопасное умолчание
        self.assertEqual(params["entry"], "84.38.189.217")
        self.assertTrue(str(params["root"]).strip())

    def test_status_hosts_block_detection(self) -> None:
        root = self._make_sni_root()
        netsuite.set_param(self.settings, "ai-sni-proxy", "root", str(root))
        hosts = Path(self.root) / "hosts-test"
        netsuite.set_param(self.settings, "ai-sni-proxy", "hosts_path", str(hosts))
        row = netsuite.status(self.settings, "ai-sni-proxy")
        self.assertTrue(row["root_exists"])
        self.assertEqual(str(Path(row["hosts_path"])), str(hosts))
        self.assertFalse(row["hosts_has_block"])
        # блок в hosts → статус «применено/включено»
        hosts.write_text(
            "# comment\n"
            "# >>> ai-sni-proxy: begin, do not edit by hand >>>\n"
            "# enabled 2026-01-01\n"
            "84.38.189.217\tapi.openai.com\n"
            "# <<< ai-sni-proxy: end <<<\n",
            encoding="utf-8",
        )
        row = netsuite.status(self.settings, "ai-sni-proxy")
        self.assertTrue(row["hosts_has_block"])
        self.assertTrue(row["applied"])

    def test_start_missing_script(self) -> None:
        root = self._make_root("ai-sni-proxy")  # bin/ai-sni-proxy.ps1 нет
        netsuite.set_param(self.settings, "ai-sni-proxy", "root", str(root))
        result = netsuite.start(self.settings, "ai-sni-proxy")
        self.assertFalse(result["ok"])
        self.assertIn("ai-sni-proxy.ps1", result["error"])

    def test_bootstrap_with_script_and_fallback(self) -> None:
        root = self._make_sni_root()
        netsuite.set_param(self.settings, "ai-sni-proxy", "root", str(root))
        result = netsuite.bootstrap(self.settings, "ai-sni-proxy")
        self.assertTrue(result["ok"], result)
        steps = {step["step"] for step in result["steps"]}
        self.assertIn("bin/ai-sni-proxy.ps1", steps)

    def test_bootstrap_missing_script_refused(self) -> None:
        root = self._make_root("ai-sni-proxy")
        netsuite.set_param(self.settings, "ai-sni-proxy", "root", str(root))
        result = netsuite.bootstrap(self.settings, "ai-sni-proxy")
        self.assertFalse(result["ok"])

    def test_apply_detects_block_state(self) -> None:
        root = self._make_sni_root()
        netsuite.set_param(self.settings, "ai-sni-proxy", "root", str(root))
        hosts = Path(self.root) / "hosts-test"
        netsuite.set_param(self.settings, "ai-sni-proxy", "hosts_path", str(hosts))
        result = netsuite.apply(self.settings, "ai-sni-proxy")
        self.assertTrue(result["ok"], result)
        # блока нет → считается «изменённым» (нужен start)
        self.assertTrue(any("hosts_state" in item for item in result["changed"]))


if __name__ == "__main__":
    unittest.main()
