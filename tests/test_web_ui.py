"""Тесты оформления веб-интерфейса: без новых окон, точечное обновление,
перетаскиваемые столбцы, вкладки настроек, размер шрифта, версия в шапке и
понятность состояния узлов в разделе «инфраструктура»."""
from __future__ import annotations

import unittest

from factory import __version__
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
        self.assertIn("setBlock(box,'<div class=\"tblwrap\"><table><tr><th>'+T.thUpdated", text)


class ReadabilityTest(unittest.TestCase):
    """Стандарты читаемости: страница не растягивается вбок, блоки кода
    переносятся, шапка с разделами закреплена, таблицы прокручиваются в
    своей обёртке, а не тянут весь макет."""

    def test_grid_track_does_not_grow_from_content(self) -> None:
        self.assertIn("grid-template-columns:minmax(0,1fr)", page())

    def test_sections_cannot_overflow(self) -> None:
        self.assertIn("section{background:#161a22", page())
        text = page()
        start = text.index("section{background")
        chunk = text[start:text.index("}", start)]
        self.assertIn("min-width:0", chunk)

    def test_pre_wraps_long_lines(self) -> None:
        text = page()
        start = text.index("\n pre{")
        chunk = text[start:text.index("}", start)]
        self.assertIn("white-space:pre-wrap", chunk)
        self.assertIn("overflow-wrap:anywhere", chunk)

    def test_tables_live_in_scroll_wrapper(self) -> None:
        text = page()
        self.assertIn(".tblwrap{overflow-x:auto", text)
        self.assertIn('<div class="tblwrap"><table data-fx=', text)

    def test_header_is_sticky(self) -> None:
        self.assertIn("header{position:sticky;top:0;z-index:40", page())

    def test_touch_targets_are_large_enough(self) -> None:
        text = page()
        self.assertIn("min-height:32px", text)
        self.assertIn("min-height:34px", text)


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


class VersionBadgeTest(unittest.TestCase):
    def test_version_shown_in_header(self) -> None:
        for lang in ("ru", "en", "zh"):
            self.assertIn(f'<span class="ver">v{__version__}</span>', page(lang))

    def test_version_is_not_loud(self) -> None:
        text = page()
        start = text.index(".ver{")
        chunk = text[start:text.index("}", start)]
        self.assertIn("font-size:12px", chunk)
        self.assertIn("#8b93a7", chunk)


class InfraStateClarityTest(unittest.TestCase):
    """Счётчики состояний и объяснение цветов — из уже приходящих данных."""

    def test_state_counters_block_present(self) -> None:
        text = page()
        self.assertIn('id="infraSummary"', text)      # счётчики + всего узлов
        self.assertIn("T.infraCounts", text)
        self.assertIn("T.infraTotal.replace('{total}'", text)
        self.assertIn('<span class="chip total">', text)

    def test_legend_explains_grey_and_red(self) -> None:
        self.assertIn('id="infraLegend"', page())
        self.assertIn("выгружен по требованию", page("ru"))
        self.assertIn("not a failure", page("en"))
        self.assertIn("按需卸载", page("zh"))

    def test_shot_time_and_refresh_button(self) -> None:
        text = page()
        self.assertIn('id="shotTime"', text)
        self.assertIn('onclick="shotGraph()"', text)
        self.assertIn("Обновить снимок", page("ru"))
        self.assertIn("Refresh snapshot", page("en"))
        self.assertIn("刷新快照", page("zh"))

    def test_png_not_reloaded_every_poll(self) -> None:
        """Без ?t= в адресе: кэш и 304 не дают качать PNG каждые 5 секунд."""
        text = page()
        self.assertNotIn("shot.png?t=", text)
        self.assertIn("img.src='/api/infra/shot.png'", text)
        self.assertIn("if(shotMtime!==mt||!img.getAttribute('src'))", text)

    def test_infra_polling_not_faster_than_5s(self) -> None:
        text = page()
        self.assertIn("setInterval(function(){loadArsenal();loadInfra();loadMonitoring();},5000)", text)


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


class ChatTabTest(unittest.TestCase):
    """Вкладка «Чат»: строка запроса, лента ответов с прокруткой, столбик
    чатов по 5 штук с навигацией ↑/↓, создание и удаление чатов."""

    def test_chat_and_board_tabs(self) -> None:
        text = page()
        self.assertIn('id="vtabs"', text)
        self.assertIn('data-view="chat"', text)
        self.assertIn('data-view="board"', text)

    def test_tab_labels_localized(self) -> None:
        self.assertIn(">Чат<", page("ru"))
        self.assertIn(">Chat<", page("en"))
        self.assertIn(">聊天<", page("zh"))

    def test_request_line_and_send(self) -> None:
        text = page()
        self.assertIn('id="chatInput"', text)
        self.assertIn("function chatSend(", text)
        self.assertIn("chatSend()", text)

    def test_answer_field_scrolls(self) -> None:
        text = page()
        self.assertIn('id="chatLog"', text)
        start = text.index(".chatlog{")
        chunk = text[start:text.index("}", start)]
        self.assertIn("overflow-y:auto", chunk)
        self.assertIn("max-height" if "max-height" in chunk else "height:", chunk)

    def test_five_chats_per_window(self) -> None:
        text = page()
        self.assertIn("const CHAT_PAGE=5;", text)
        self.assertIn("chatItems.slice(chatOffset,chatOffset+CHAT_PAGE)", text)

    def test_list_navigation_up_down(self) -> None:
        text = page()
        self.assertIn("function chatShift(", text)
        self.assertIn("id=\"chatUp\"", text)
        self.assertIn("id=\"chatDown\"", text)
        self.assertIn("chatShift(-1)", text)
        self.assertIn("chatShift(1)", text)
        # навигация сдвигает окно по списку, а не перерисовывает разметку
        self.assertIn("Math.max(0,Math.min(chatOffset+dir,max))", text)

    def test_list_navigation_edges_disabled(self) -> None:
        """На границах списка кнопки вверх/вниз блокируются."""
        text = page()
        self.assertIn("up.disabled=chatOffset<=0", text)
        self.assertIn("down.disabled=chatOffset>=Math.max(0,total-CHAT_PAGE)", text)

    def test_labels_do_not_wrap(self) -> None:
        """Надписи не переносятся и не выходят за границы кнопок/объектов:
        кнопки, заголовки и навигация — одна строка, длинное обрезается
        многоточием."""
        text = page()
        # правила CSS: white-space:nowrap + ellipsis у всех кнопок и заголовка
        self.assertIn(".vtab{", text)
        for rule in (
            ".chatnew{width:100%;font-size:15px;padding:12px 14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
            ".chatbtn{flex:1;min-width:0;text-align:left;background:#0f1115;border:1px solid #262b36;color:#e6e6e6;padding:12px 14px;font-size:15px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
            ".chatpager button{min-width:56px;font-size:14px;min-height:44px;padding:6px 10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
            ".chathead b{color:#fff;font-weight:600;font-size:17px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0;max-width:100%}",
            ".chatrow button{font-size:16px;min-height:48px;padding:8px 22px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
        ):
            self.assertIn(rule, text)

    def test_new_and_delete_buttons(self) -> None:
        text = page()
        self.assertIn("function chatNew(", text)
        self.assertIn("function delChat(", text)
        self.assertIn('class="chatnew"', text)
        self.assertIn('class="chatdel"', text)

    def test_chat_uses_sessions_api(self) -> None:
        text = page()
        self.assertIn("/api/sessions/delete", text)
        self.assertIn("'Content-Type':'application/json'},body:JSON.stringify({title:''})", text)
        self.assertIn("goal:text,session:chatCurrent", text)

    def test_board_view_hides_chat(self) -> None:
        text = page()
        self.assertIn("body.view-chat main > section:not([data-view=chat]){display:none}", text)
        self.assertIn("main > section[data-view=chat]{display:none}", text)

    def test_chat_section_excluded_from_section_nav(self) -> None:
        self.assertIn("filter(s=>!s.dataset.view)", page())

    def test_board_is_default_view(self) -> None:
        """По умолчанию открывается пульт (весь дашборд как был), чат —
        дополнительная вкладка поверх существующего интерфейса."""
        text = page()
        self.assertIn("chatTab(localStorage.getItem('factory.view')==='chat'?'chat':'board');", text)
        # без класса view-chat чат скрыт, все остальные разделы на месте
        self.assertIn("main > section[data-view=chat]{display:none}", text)

    def test_chat_is_full_screen(self) -> None:
        """Чат занимает всю высоту экрана, а не малую часть вверху."""
        text = page()
        self.assertIn("height:calc(100vh - 170px)", text)
        self.assertIn(".chatlog{flex:1;min-height:0;overflow-y:auto", text)
        self.assertIn("body.view-chat main{max-width:none", text)

    def test_chat_text_is_large_enough(self) -> None:
        """Крупный читаемый текст: сообщения и строка запроса не мелкие
        (11–14 кегль — это примерно 15–19px)."""
        text = page()
        self.assertIn("font-size:17px;line-height:1.55", text)
        self.assertIn(".chatrow input{flex:1;font-size:17px;min-height:48px", text)

    def test_chat_live_status_indicator(self) -> None:
        """В шапке чата есть живой индикатор: что выполняется, сколько
        времени прошло, модель; лента опрашивается и обновляется сама."""
        text = page()
        self.assertIn('id="chatRun"', text)
        self.assertIn("function chatRun(", text)
        self.assertIn("jobs.find(x=>x.session===chatCurrent)", text)
        self.assertIn("chatRun()", text)
        # автообновление ленты раз в 3 секунды
        self.assertIn("openChat(chatCurrent);chatRun()", text)

    def test_system_messages_rendered_in_chat(self) -> None:
        """Статусные (системные) сообщения отображаются отдельным стилем."""
        text = page()
        self.assertIn(".msg.system{", text)
        self.assertIn("m.role||'system'", text)

    def test_system_messages_aligned_left_with_bar(self) -> None:
        """Статусные сообщения — в крайнее лево, с полоской-кантом слева,
        без центрирования."""
        text = page()
        self.assertIn(".msg.system{align-self:flex-start;", text)
        self.assertIn("border-left:4px solid #2d6cdf", text)
        self.assertNotIn("align-self:center", text)

    def test_chat_timer_ticks_and_freezes(self) -> None:
        """В ленте есть живой счётчик, который тикает, пока идёт процесс,
        и останавливается (data-frozen) при приходе ответа."""
        text = page()
        self.assertIn('class="chattimer" data-jid=', text)
        self.assertIn("function tickChatTimers(", text)
        self.assertIn("startChatTimers()", text)
        self.assertIn('data-frozen="1"', text)

    def test_chat_timeout_retries_and_log_analysis(self) -> None:
        """Через 60 с без реакции — «Реакции нету», повторный запрос (до 3),
        затем лог сессии передаётся нейросети с пометкой в чате."""
        text = page()
        self.assertIn("elapsed>=60", text)
        self.assertIn("retryCount<3", text)
        self.assertIn("T.chatNoReaction", text)
        self.assertIn("T.chatRetry.replace", text)
        self.assertIn("analyzeChatLog(", text)
        self.assertIn("Проанализируй лог чата", text)
        self.assertIn("s.messages.map(m=>'['+(m.role||'?')+'] '+(m.text||'')).join('\\n')", text)

    def test_retry_labels_localized(self) -> None:
        self.assertIn("Реакции нету. Пробую повторный запрос.", page("ru"))
        self.assertIn("No reaction. Trying the request again.", page("en"))
        self.assertIn("没有反应。正在重试请求。", page("zh"))


if __name__ == "__main__":
    unittest.main()
