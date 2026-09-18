"""Double-clickable desktop entry point for packaged Windows builds."""

from __future__ import annotations

import logging
import multiprocessing
import tkinter as tk
from tkinter import messagebox

from gui import launch_gui


LOGGER = logging.getLogger(__name__)


def main() -> int:
    """Launch the GUI and display startup failures without a console window."""

    multiprocessing.freeze_support()
    try:
        launch_gui()
    except Exception as exc:  # A windowed executable has no console for tracebacks.
        LOGGER.exception("Desktop application failed during startup")
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Instagram Follow Tracker",
            f"程式無法啟動：\n\n{exc}\n\n請查看 logs/tracker.log。",
            parent=root,
        )
        root.destroy()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
