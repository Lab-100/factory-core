"""Тесты проектных настроек: поиск, слияние, применение, инициализация."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from factory import projects
from factory.settings import Settings


class ProjectsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_find_project_nearest(self) -> None:
        deep = self.root / "a" / "b" / "c"
        deep.mkdir(parents=True)
        # ближайший вверх — в корне
        target = self._write("factory.toml", 'x = 1\n')
        # сравниваем по идентичности файла: на Windows временный каталог может
        # приходить как 8.3-имя (RUNNER~1), и строковое равенство не подходит
        self.assertTrue(os.path.samefile(projects.find_project(deep), target))

    def test_find_project_prefers_closer(self) -> None:
        deep = self.root / "a" / "b"
        deep.mkdir(parents=True)
        self._write("factory.toml", "a = 1\n")
        near = self._write("a/.factory/settings.toml", "b = 2\n")
        self.assertTrue(os.path.samefile(projects.find_project(deep), near))

    def test_find_project_none(self) -> None:
        lonely = self.root / "empty"
        lonely.mkdir()
        self.assertIsNone(projects.find_project(lonely))

    def test_load_and_project_root(self) -> None:
        path = self._write("proj/.factory/settings.toml", '[ui]\nlang = "en"\n')
        data = projects.load_project(path)
        self.assertEqual(data["ui"]["lang"], "en")
        self.assertEqual(projects.project_root(path), self.root / "proj")

    def test_deep_merge(self) -> None:
        base = {"ui": {"lang": "ru", "mode": "web"}, "automation": {"mode": "auto"}}
        layer = {"ui": {"lang": "en"}}
        merged = projects.deep_merge(base, layer)
        self.assertEqual(merged["ui"]["lang"], "en")
        self.assertEqual(merged["ui"]["mode"], "web")
        self.assertEqual(merged["automation"]["mode"], "auto")

    def test_apply_project(self) -> None:
        path = self._write("proj/.factory/settings.toml", '[ui]\nlang = "en"\n')
        settings = Settings(self.root / "config" / "settings.toml",
                            {"ui": {"lang": "ru", "mode": "web"}})
        layer = projects.apply_project(settings, path=path)
        self.assertEqual(layer["ui"]["lang"], "en")
        self.assertEqual(settings.get("ui.lang"), "en")
        self.assertEqual(settings.get("ui.mode"), "web")
        self.assertEqual(settings.project_path, path)

    def test_apply_project_absent_is_noop(self) -> None:
        settings = Settings(self.root / "config" / "settings.toml", {"ui": {"lang": "ru"}})
        layer = projects.apply_project(settings, start=self.root / "empty")
        self.assertEqual(layer, {})
        self.assertIsNone(settings.project_path)
        self.assertEqual(settings.get("ui.lang"), "ru")

    def test_init_project(self) -> None:
        path = projects.init_project(self.root)
        self.assertTrue(path.is_file())
        self.assertIn("[automation]", path.read_text(encoding="utf-8"))
        # повторный вызов без force не перезаписывает
        path.write_text("# изменено\n", encoding="utf-8")
        projects.init_project(self.root)
        self.assertEqual(path.read_text(encoding="utf-8"), "# изменено\n")
        projects.init_project(self.root, force=True)
        self.assertNotEqual(path.read_text(encoding="utf-8"), "# изменено\n")


if __name__ == "__main__":
    unittest.main()
