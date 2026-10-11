"""Тесты локализации: паритет ключей RU/EN/ZH и подстановка параметров."""
from __future__ import annotations

import unittest

from factory.i18n import LANGS, MESSAGES, choose, t


class I18nTest(unittest.TestCase):
    def test_langs(self) -> None:
        self.assertEqual(set(MESSAGES), set(LANGS))

    def test_key_parity(self) -> None:
        base = set(MESSAGES["ru"])
        for lang in LANGS:
            missing = base - set(MESSAGES[lang])
            extra = set(MESSAGES[lang]) - base
            self.assertFalse(missing, f"{lang}: нет ключей {sorted(missing)}")
            self.assertFalse(extra, f"{lang}: лишние ключи {sorted(extra)}")

    def test_no_empty_values(self) -> None:
        for lang, table in MESSAGES.items():
            for key, value in table.items():
                self.assertTrue(str(value).strip(), f"{lang}:{key} пустой")

    def test_format_and_fallback(self) -> None:
        self.assertIn("3", t("ru", "status.enabled_of", enabled=3, total=7))
        # неизвестный ключ возвращает сам ключ, а не падает
        self.assertEqual(t("ru", "no.such.key"), "no.such.key")

    def test_choose_default(self) -> None:
        self.assertEqual(choose(None), "ru")
        self.assertEqual(choose(env_value="en"), "en")


if __name__ == "__main__":
    unittest.main()
