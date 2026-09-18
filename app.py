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
            "Instagram Follower Tracker",
            f"The application could not start:\n\n{exc}\n\n"
            "See logs/tracker.log for details.",
            parent=root,
        )
        root.destroy()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
