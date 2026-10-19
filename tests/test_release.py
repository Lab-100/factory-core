"""Тесты упаковки дистрибутива и поиска средств сборки."""
from __future__ import annotations

import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from factory.release import _source_root, find_iscc, make_zip


class MakeZipTest(unittest.TestCase):
    def _root(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "dist").mkdir(parents=True)
        (root / "dist" / "factory.exe").write_bytes(b"MZ-fake")
        for name in ("config", "schemas", "examples", "docs"):
            (root / name).mkdir()
            (root / name / "marker.txt").write_text(name, encoding="utf-8")
        for name in ("README.md", "LICENSE", "project.json"):
            (root / name).write_text(name, encoding="utf-8")
        (root / "scripts").mkdir()
        (root / "scripts" / "install.ps1").write_text("# install", encoding="utf-8")
        (root / "scripts" / "uninstall.ps1").write_text("# uninstall", encoding="utf-8")
        (root / "scripts" / "factory.ps1").write_text("# shim", encoding="utf-8")
        (root / "scripts" / "factory.iss").write_text("; installer", encoding="utf-8")
        return root

    def _names(self, path: Path) -> set[str]:
        with zipfile.ZipFile(path) as archive:
            return {name.replace("\\", "/") for name in archive.namelist()}

    def test_archive_has_product_and_tools(self) -> None:
        root = self._root()
        archive_path = make_zip(root)
        self.assertTrue(archive_path.exists())
        names = self._names(archive_path)
        # продукт и данные
        self.assertIn("factory.exe", names)
        self.assertIn("project.json", names)
        self.assertIn("config/marker.txt", names)
        self.assertIn("docs/marker.txt", names)
        # обёртки установки в корне архива
        self.assertIn("install.ps1", names)
        self.assertIn("uninstall.ps1", names)
        self.assertIn("factory.ps1", names)
        # инструменты: каталог и он же архивом
        self.assertIn("scripts/factory.iss", names)
        self.assertIn("tools.zip", names)

    def test_tools_zip_unpacks_scripts(self) -> None:
        root = self._root()
        archive_path = make_zip(root)
        package = archive_path.parent / "package"
        with zipfile.ZipFile(package / "tools.zip") as tools:
            names = set(tools.namelist())
        self.assertIn("install.ps1", names)
        self.assertIn("factory.iss", names)

    def test_setup_executable_included_when_present(self) -> None:
        root = self._root()
        (root / "dist" / "factory-setup.exe").write_bytes(b"MZ-setup")
        names = self._names(make_zip(root))
        self.assertIn("factory-setup.exe", names)

    def test_requires_exe(self) -> None:
        root = self._root()
        (root / "dist" / "factory.exe").unlink()
        with self.assertRaises(RuntimeError):
            make_zip(root)


class FindIsccTest(unittest.TestCase):
    def test_returns_path_or_none(self) -> None:
        found = find_iscc()
        self.assertTrue(found is None or Path(found).name.lower() == "iscc.exe")


class SourceRootTest(unittest.TestCase):
    def _source(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "config").mkdir()
        (root / "config" / "settings.toml").write_text("", encoding="utf-8")
        (root / "run.py").write_text("# run", encoding="utf-8")
        return root

    def test_returns_itself_when_source(self) -> None:
        src = self._source()
        self.assertEqual(_source_root(src), src)

    def test_finds_source_in_parent_when_root_is_install_dir(self) -> None:
        src = self._source()
        install = src / "AppData" / "Local" / "FactoryLab100"
        install.mkdir(parents=True)
        self.assertEqual(_source_root(install), src)

    def test_returns_root_when_no_source_nearby(self) -> None:
        src = self._source()
        orphan = src.parent / "совсем-другая-папка"
        orphan.mkdir(parents=True, exist_ok=True)
        # исходников рядом нет — возвращаем сам корень (сборка честно
        # упадёт с понятной ошибкой, а не соберёт из чужого каталога)
        self.assertEqual(_source_root(orphan), orphan)

    def test_finds_source_via_env(self) -> None:
        src = self._source()
        orphan = src.parent / "установка-без-исходников"
        orphan.mkdir(parents=True, exist_ok=True)
        old = os.environ.get("FACTORY_SOURCE")
        os.environ["FACTORY_SOURCE"] = str(src)
        try:
            self.assertEqual(_source_root(orphan), src)
        finally:
            if old is None:
                os.environ.pop("FACTORY_SOURCE", None)
            else:
                os.environ["FACTORY_SOURCE"] = old


if __name__ == "__main__":
    unittest.main()
