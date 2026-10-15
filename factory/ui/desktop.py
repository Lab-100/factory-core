"""Оконный интерфейс фабрики — дашборд внутри нативного окна (pywebview).

Окно не отдельная реализация: оно поднимает штатный веб-дашборд
(`factory/ui/web.py`) на локальном порту и показывает его в системном окне
(pywebview). При отсутствии pywebview печатается понятная подсказка, как
включить оконный режим (``pip install .[desktop]``) или открыть дашборд в
браузере (``factory ui --mode web``).

Зависимость опциональна — модуль не ломает установку без неё.
"""
from __future__ import annotations

import socket
import threading
from http.server import ThreadingHTTPServer

from . import web


def _free_port() -> int:
    """Свободный порт на localhost."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_server(host: str, port: int) -> tuple[ThreadingHTTPServer, int]:
    """Поднять сервер дашборда; при занятом порте — на свободном."""
    try:
        return ThreadingHTTPServer((host, port), web._handler_class()), port
    except OSError:
        fallback = _free_port()
        return ThreadingHTTPServer((host, fallback), web._handler_class()), fallback


def serve(host: str | None = None, port: int | None = None) -> int:
    roots = web.build_context()
    settings = roots[1]
    tr = web.make_translator(settings)
    host = host or str(settings.get("ui.web.host", "127.0.0.1"))
    port = int(port or settings.get("ui.web.port", 8787))
    try:
        import webview  # type: ignore
    except Exception:  # noqa: BLE001
        print(tr("desktop.unavailable"))
        if not bool(settings.get("ui.desktop_fallback_web", True)):
            print(tr("desktop.hint_web"))
            return 1
        print(tr("desktop.fallback_web"))
        return web.serve(host=host, port=port)

    server, actual = _start_server(host, port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://{host}:{actual}/"
    print(tr("desktop.opening", url=url))
    try:
        from ..single import record_instance

        record_instance(url)
    except Exception:  # noqa: BLE001 — адрес для повторного запуска не критичен
        pass
    webview.create_window(tr("desktop.title"), url, width=1240, height=840)
    try:
        webview.start()
    finally:
        server.shutdown()
        server.server_close()
    return 0
