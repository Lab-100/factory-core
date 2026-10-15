"""Тесты оформления веб-интерфейса: без новых окон, точечное обновление,
перетаскиваемые столбцы, вкладки настроек и размер шрифта."""
from __future__ import annotations

import unittest

from factory.ui.web import _page


def page(lang: str = "ru") -> str:
    return _page(lang)


class NoNewWindowsTest(unittest.TestCase):
    def test_base_target_self(self) -> None:
        self.assertIn('<base target="_self">', page())

    def test_window_open_is_disabled(self) -> None:
        self.assertIn("window.open=function(){return null}", page())

    def test_blank_links_are_blocked(self) -> None:
        self.assertIn("a.target==='_blank'", page())

    def test_middle_click_is_blocked(self) -> None:
        self.assertIn("auxclick", page())


class PatchUpdateTest(unittest.TestCase):
    def test_patch_rows_helper_exists(self) -> None:
        self.assertIn("function patchRows(", page())

    def test_rows_updated_in_place(self) -> None:
        text = page()
        # строка на месте не трогается, переставленные — переносятся
        self.assertIn("if(anchor===tr) anchor=anchor.nextElementSibling", text)
        self.assertIn("tbody.insertBefore(tr,anchor)", text)

    def test_only_changed_cells_rewritten(self) -> None:
        text = page()
        self.assertIn("if(!old||old.h!==c.h) td.innerHTML=c.h", text)

    def test_jobs_list_uses_patch(self) -> None:
        text = page()
        self.assertIn("patchRows(tbl.tBodies[0],jobs", text)

    def test_ledger_uses_set_block(self) -> None:
        text = page()
        self.assertIn("setBlock(box,'<table><tr><th>'+T.thUpdated", text)


class ColumnWidthTest(unittest.TestCase):
    def test_colgroup_present(self) -> None:
        self.assertIn("<colgroup>", page())

    def test_resizer_handle(self) -> None:
        text = page()
        self.assertIn("function makeResizable(", text)
        self.assertIn("cursor:col-resize", text)
        self.assertIn("col-resize", text)

    def test_widths_saved(self) -> None:
        self.assertIn("localStorage.setItem(store,", page())


class SettingsTabsTest(unittest.TestCase):
    def test_tabs_defined(self) -> None:
        text = page()
        for pane in ("pane-ui", "pane-auto", "pane-net", "pane-service"):
            self.assertIn(f'data-pane="{pane}"', text)

    def test_font_size_control(self) -> None:
        text = page()
        self.assertIn('id="fsRange"', text)
        self.assertIn("function saveFontSize(", text)
        self.assertIn("ui.font_size", text)

    def test_font_size_applied_from_storage(self) -> None:
        text = page()
        self.assertIn("const FS_KEY='factory.font_size'", text)
        self.assertIn("restoreFontSize();", text)


class TranslationTest(unittest.TestCase):
    def test_all_languages_have_tabs(self) -> None:
        for lang in ("ru", "en", "zh"):
            text = page(lang)
            self.assertIn("data-pane=\"pane-ui\"", text)

    def test_tab_labels_differ_by_language(self) -> None:
        def label(html: str, key: str) -> str:
            start = html.index(f"{key}:'") + len(key) + 2
            return html[start:html.index("'", start)]

        self.assertEqual(label(page("ru"), "tabUi"), "Интерфейс")
        self.assertEqual(label(page("en"), "tabUi"), "Interface")
        self.assertEqual(label(page("zh"), "tabUi"), "界面")


if __name__ == "__main__":
    unittest.main()
