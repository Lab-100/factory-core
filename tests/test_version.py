"""Сверка версии пакета с pyproject.toml — версии не должны расходиться."""
from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

import factory


class VersionTest(unittest.TestCase):
    def test_matches_pyproject(self) -> None:
        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        with pyproject.open("rb") as handle:
            expected = tomllib.load(handle)["project"]["version"]
        self.assertEqual(factory.__version__, expected)

    def test_matches_project_manifest(self) -> None:
        import json

        manifest = Path(__file__).resolve().parent.parent / "project.json"
        expected = json.loads(manifest.read_text(encoding="utf-8"))["version"]
        self.assertEqual(factory.__version__, expected)


if __name__ == "__main__":
    unittest.main()
