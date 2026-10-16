"""Тесты: Windows-скрипты (.cmd/.bat) переживают запуск в cmd.exe.

Реальная поломка, которую ловит этот файл: ``scripts/micromonitor.cmd``
был сохранён в UTF-8 **с BOM**. cmd.exe не умеет пропускать U+FEFF в начале
пакетного файла: байты EF BB BF читались как OEM-символы ``я╗┐``, и первая
команда превращалась в ``я╗┐@echo off`` — cmd отвечал «не является внутренней
или внешней командой». Русские комментарии, сохранённые в UTF-8, до
выполнения ``chcp 65001`` выводились кракозябрами (``╨Ь╨╕╨║╤А╨╛``).

Требования к каждому .cmd/.bat в репозитории:
  * UTF-8 **без** BOM;
  * только CRLF (проверяется на Windows, где рабочая копия нормализуется git);
  * если есть не-ASCII, то ``chcp 65001`` встречается до первой кириллицы;
  * в индексе git нет BOM — это ловится на любой операционной системе.
"""
from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOM = b"\xef\xbb\xbf"
CHCP = b"chcp 65001"
SKIP_DIRS = {".git", "node_modules", "build", "dist", "__pycache__", ".venv", "venv"}
IS_WINDOWS = os.name == "nt"


def batch_files(root: Path) -> list[Path]:
    """Все .cmd и .bat репозитория, кроме служебных каталогов."""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name.lower().endswith((".cmd", ".bat")):
                found.append(Path(dirpath) / name)
    return sorted(found)


def has_bare_lf(data: bytes) -> bool:
    """Есть ли LF, не перед которым стоит CR."""
    position = data.find(b"\n")
    while position != -1:
        if position == 0 or data[position - 1] != 0x0D:
            return True
        position = data.find(b"\n", position + 1)
    return False


def lines(data: bytes) -> list[bytes]:
    return data.replace(b"\r\n", b"\n").split(b"\n")


class BatchEncodingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.files = batch_files(ROOT)

    def test_batch_files_exist(self) -> None:
        self.assertTrue(self.files, "в репозитории нет ни одного .cmd/.bat — проверка пустая")

    def test_no_bom_in_working_copy(self) -> None:
        offenders = [str(p.relative_to(ROOT)) for p in self.files
                     if p.read_bytes().startswith(BOM)]
        self.assertFalse(
            offenders,
            "BOM в пакетном файле ломает cmd.exe (первая команда становится "
            "несуществующей): " + ", ".join(offenders),
        )

    @unittest.skipUnless(IS_WINDOWS, "CRLF в рабочей копии проверяем на Windows: "
                                    "на Linux нормализует core.autocrlf")
    def test_crlf_only(self) -> None:
        offenders = [str(p.relative_to(ROOT)) for p in self.files
                     if has_bare_lf(p.read_bytes())]
        self.assertFalse(
            offenders,
            "в пакетном файле есть «голые» LF вместо CRLF: " + ", ".join(offenders),
        )

    def test_chcp_before_cyrillic(self) -> None:
        """Кириллица в .cmd видна только после chcp 65001."""
        offenders: list[str] = []
        for path in self.files:
            data = path.read_bytes()
            if data.startswith(BOM) or all(byte < 0x80 for byte in data):
                continue
            head = b"\n".join(lines(data)[:3]).lower()
            if CHCP not in head:
                offenders.append(str(path.relative_to(ROOT)))
        self.assertFalse(
            offenders,
            "в файле есть кириллица, но нет `chcp 65001` в первых строках — "
            "русский выведется кракозябрами: " + ", ".join(offenders),
        )

    def test_gitattributes_forces_crlf(self) -> None:
        attrs = ROOT / ".gitattributes"
        self.assertTrue(attrs.exists(), "нет .gitattributes — CRLF для .cmd не закреплён")
        rules = [line.strip().lower() for line in
                 attrs.read_bytes().decode("utf-8", errors="replace").splitlines()
                 if line.strip() and not line.strip().startswith("#")]
        for suffix in ("*.cmd", "*.bat"):
            self.assertTrue(
                any(rule.startswith(suffix) and "eol=crlf" in rule for rule in rules),
                f"в .gitattributes нет правила `{suffix} text eol=crlf`",
            )

    def test_monitor_cmd_wrapper_intact(self) -> None:
        """Обёртка микромонитора: порядок строк и передача аргументов."""
        text = (ROOT / "scripts" / "micromonitor.cmd").read_bytes().decode("utf-8")
        rows = [line for line in text.splitlines() if line.strip()]
        self.assertTrue(rows[0].strip().lower() == "@echo off", rows[0])
        self.assertIn("chcp 65001", rows[1].lower(), rows[1])
        self.assertIn("micromonitor.ps1", text)
        self.assertIn("%*", text, "обёртка не передаёт аргументы в PowerShell")
        self.assertIn("if errorlevel 1 pause", text,
                      "при ошибке окно должно остаться, чтобы прочитать текст")


class GitIndexEncodingTest(unittest.TestCase):
    """BOM — свойство закоммиченного блоба: checkout его не создаёт и не убирает."""

    def test_index_blobs_have_no_bom(self) -> None:
        if not (ROOT / ".git").exists():
            self.skipTest("репозиторий не под контролем версий")
        listed = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT,
                                capture_output=True, check=False)
        if listed.returncode != 0:
            self.skipTest("git недоступен")
        paths = [p for p in listed.stdout.decode("utf-8", errors="replace").split("\0")
                 if p.lower().endswith((".cmd", ".bat"))]
        self.assertTrue(paths, "в индексе нет пакетных файлов — проверка пустая")
        offenders = []
        for rel in paths:
            blob = subprocess.run(["git", "show", f":{rel}"], cwd=ROOT,
                                  capture_output=True, check=False)
            if blob.returncode == 0 and blob.stdout.startswith(BOM):
                offenders.append(rel)
        self.assertFalse(offenders, "BOM попал в индекс git: " + ", ".join(offenders))


if __name__ == "__main__":
    unittest.main()