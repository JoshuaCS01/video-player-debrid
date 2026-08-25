"""Windows desktop host for Home Media Manager."""

from __future__ import annotations

import logging
import queue
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from app import create_app
from media_manager.config import AppConfig
from media_manager.server import ServerController, lan_address


class QueueLogHandler(logging.Handler):
    def __init__(self, messages: queue.Queue[str]):
        super().__init__()
        self.messages = messages

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.put(self.format(record))


class DesktopApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.settings = AppConfig.load()
        self.messages: queue.Queue[str] = queue.Queue()
        self.local_url = f"http://127.0.0.1:{self.settings.port}"
        self.client_url = f"http://{lan_address()}:{self.settings.port}"
        self.server = ServerController(create_app(self.settings), self.settings.host, self.settings.port, self._server_error)
        self.status = tk.StringVar(value="Stopped")
        self.status_detail = tk.StringVar(value="The media server is not running.")
        self._build()
        self._configure_logging()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(150, self._drain_logs)

    def _build(self) -> None:
        self.root.title("Home Media Manager")
        self.root.geometry("760x570")
        self.root.minsize(650, 500)
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")

        shell = ttk.Frame(self.root, padding=24)
        shell.pack(fill="both", expand=True)
        ttk.Label(shell, text="Home Media Manager", font=("Segoe UI Semibold", 20)).pack(anchor="w")
        ttk.Label(shell, text="Serve your media library to devices on this network.").pack(anchor="w", pady=(2, 20))

        status_card = ttk.LabelFrame(shell, text=" Server ", padding=16)
        status_card.pack(fill="x")
        status_row = ttk.Frame(status_card)
        status_row.pack(fill="x")
        ttk.Label(status_row, textvariable=self.status, font=("Segoe UI Semibold", 13)).pack(side="left")
        self.start_button = ttk.Button(status_row, text="Start server", command=self.start)
        self.start_button.pack(side="right")
        self.stop_button = ttk.Button(status_row, text="Stop", command=self.stop, state="disabled")
        self.stop_button.pack(side="right", padx=(0, 8))
        ttk.Label(status_card, textvariable=self.status_detail).pack(anchor="w", pady=(4, 14))

        url_row = ttk.Frame(status_card)
        url_row.pack(fill="x")
        ttk.Label(url_row, text="Client address:").pack(side="left")
        ttk.Label(url_row, text=self.client_url, font=("Consolas", 10)).pack(side="left", padx=8)
        ttk.Button(url_row, text="Open library", command=lambda: webbrowser.open(self.local_url)).pack(side="right")
        ttk.Button(url_row, text="Copy", command=self.copy_url).pack(side="right", padx=(0, 8))

        warnings = self.settings.configuration_warnings()
        if warnings:
            warning_card = ttk.LabelFrame(shell, text=" Configuration ", padding=12)
            warning_card.pack(fill="x", pady=(14, 0))
            ttk.Label(warning_card, text="\n".join(f"• {item}" for item in warnings), justify="left", wraplength=680).pack(anchor="w")

        log_card = ttk.LabelFrame(shell, text=" Activity ", padding=10)
        log_card.pack(fill="both", expand=True, pady=(14, 0))
        self.log_view = tk.Text(log_card, height=10, state="disabled", wrap="word", font=("Consolas", 9), borderwidth=0)
        scrollbar = ttk.Scrollbar(log_card, command=self.log_view.yview)
        self.log_view.configure(yscrollcommand=scrollbar.set)
        self.log_view.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def _configure_logging(self) -> None:
        handler = QueueLogHandler(self.messages)
        handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)s  %(message)s", "%H:%M:%S"))
        logging.getLogger().setLevel(logging.INFO)
        logging.getLogger().addHandler(handler)
        logging.info("Ready. Client devices will use %s", self.client_url)

    def start(self) -> None:
        try:
            self.server.start()
        except OSError as exc:
            self._server_error(exc)
            return
        self.status.set("Running")
        self.status_detail.set(f"Available to this PC and your local network on port {self.settings.port}.")
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        logging.info("Media server started at %s", self.client_url)

    def stop(self) -> None:
        self.server.stop()
        self.status.set("Stopped")
        self.status_detail.set("The media server is not running.")
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        logging.info("Media server stopped")

    def copy_url(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(self.client_url)
        self.status_detail.set("Client address copied to the clipboard.")

    def _server_error(self, error: Exception) -> None:
        logging.error("Server error: %s", error)
        self.root.after(0, lambda: messagebox.showerror("Server error", str(error), parent=self.root))

    def _drain_logs(self) -> None:
        lines = []
        while True:
            try:
                lines.append(self.messages.get_nowait())
            except queue.Empty:
                break
        if lines:
            self.log_view.configure(state="normal")
            self.log_view.insert("end", "\n".join(lines) + "\n")
            self.log_view.see("end")
            self.log_view.configure(state="disabled")
        self.root.after(150, self._drain_logs)

    def close(self) -> None:
        self.server.stop()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    DesktopApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
