"""Точка входа: python -m factory ..."""
from __future__ import annotations

import sys

from .ui.cli import main


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    sys.exit(main())
