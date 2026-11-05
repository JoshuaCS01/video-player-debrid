from __future__ import annotations

import threading
from collections.abc import Callable

import pystray
from PIL import Image

import sys
from pathlib import Path


class TrayController:
    """Own the Windows tray icon without calling Tk from the tray thread."""

    def __init__(self, on_show: Callable[[], None], on_exit: Callable[[], None]):
        self.on_show = on_show
        self.on_exit = on_exit
        self._icon = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @staticmethod
    def _image() -> Image.Image:
        if getattr(sys, "frozen", False):
            base_path = Path(sys._MEIPASS)
        else:
            base_path = Path(__file__).resolve().parent.parent

        icon_path = base_path / "static" / "img" / "logo.png"
        return Image.open(icon_path).convert("RGBA")

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        with self._lock:
            if self.running:
                return
            menu = pystray.Menu(
                pystray.MenuItem("Show Home Media Manager", self._show, default=True),
                pystray.MenuItem("Exit", self._exit),
            )
            self._icon = pystray.Icon("HomeMediaManager", self._image(), "Home Media Manager", menu)
            self._thread = threading.Thread(
                target=self._icon.run, name="home-media-tray", daemon=True,
            )
            self._thread.start()

    def _show(self, _icon=None, _item=None) -> None:
        self.on_show()

    def _exit(self, _icon=None, _item=None) -> None:
        self.on_exit()

    def stop(self) -> None:
        with self._lock:
            icon, thread = self._icon, self._thread
            if icon is None:
                return
            self._icon = self._thread = None
        icon.stop()
        if thread and thread is not threading.current_thread() and thread.is_alive():
            thread.join(timeout=5)
        if thread and thread.is_alive():
            raise RuntimeError("System tray thread did not stop")
