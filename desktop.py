"""Windows desktop host for Home Media Manager."""

from __future__ import annotations

import logging
import queue
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from app import create_app
from media_manager.config import AppConfig, legacy_data_dir
from media_manager.config_store import ConfigRepository, appdata_config_dir
from media_manager.server import HereSphereServerGroup, lan_address
from media_manager.setup_ui import run_setup_dialog
from media_manager.single_instance import SingleInstance, bring_window_to_foreground
from media_manager.tray import TrayController


class QueueLogHandler(logging.Handler):
    def __init__(self, messages: queue.Queue[str]):
        super().__init__()
        self.messages = messages

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.put(self.format(record))


class DesktopApp:
    def __init__(self, root: tk.Tk, instance: SingleInstance, config_repository: ConfigRepository):
        self.root = root
        self.instance = instance
        self.config_repository = config_repository
        self.settings = AppConfig.load(repository=config_repository)
        self.messages: queue.Queue[str] = queue.Queue()
        self.ui_actions: queue.Queue[tuple[str, str | None]] = queue.Queue()
        client_host = lan_address()
        self.local_url = f"http://127.0.0.1:{self.settings.port}"
        self.client_url = f"http://{client_host}:{self.settings.port}"
        self.library_client_url = f"http://{client_host}:{self.settings.library_port}/library"
        shared_app = create_app(self.settings)
        self.server = HereSphereServerGroup(
            shared_app,
            self.settings.host,
            self.settings.port,
            self.settings.library_port,
            self._server_error,
        )
        self.favorites_server = self.server.favorites
        self.library_server = self.server.library
        self.tray = TrayController(
            lambda: self.request_ui_action("show"),
            lambda: self.request_ui_action("exit"),
        )
        self._closing = False
        self._closed = False
        self.status = tk.StringVar(value="Stopped")
        self.status_detail = tk.StringVar(value="The media server is not running.")
        self._build()
        self._configure_logging()
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        self.tray.start()
        self.instance.start_restore_monitor(lambda: self.request_ui_action("show"))
        self.root.after(150, self._drain_logs)
        self.root.after_idle(self.start)

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
        ttk.Button(url_row, text="Settings", command=self.open_settings).pack(side="right", padx=(0, 8))

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
        if self._closing or self._closed:
            return
        try:
            self.server.start()
        except Exception:
            self.status.set("Stopped")
            self.status_detail.set("The media server could not be started.")
            self.start_button.configure(state="normal")
            self.stop_button.configure(state="disabled")
            return
        self.status.set("Running")
        self.status_detail.set(
            f"Favorites on port {self.settings.port}; Library Web Stream on port {self.settings.library_port}."
        )
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        logging.info("Media server started at %s; Library Web Stream at %s", self.client_url, self.library_client_url)

    def stop(self) -> None:
        self.server.stop()
        if self._closed:
            return
        self.status.set("Stopped")
        self.status_detail.set("The media server is not running.")
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        logging.info("Media server stopped")

    def copy_url(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(self.client_url)
        self.status_detail.set("Client address copied to the clipboard.")

    def open_settings(self) -> None:
        if run_setup_dialog(self.root, self.config_repository, first_run=False):
            messagebox.showinfo(
                "Settings saved",
                "Settings were saved securely. Exit and reopen Home Media Manager to apply them.",
                parent=self.root,
            )

    def _server_error(self, error: Exception) -> None:
        logging.error("Server error: %s", error)
        if not self._closing and not self._closed:
            self.request_ui_action("server_error", str(error))

    def request_ui_action(self, action: str, detail: str | None = None) -> None:
        self.ui_actions.put((action, detail))

    def _drain_ui_actions(self) -> None:
        while not self._closing and not self._closed:
            try:
                action, detail = self.ui_actions.get_nowait()
            except queue.Empty:
                return
            if action == "show":
                self.show_window()
            elif action == "exit":
                self.close()
            elif action == "server_error":
                messagebox.showerror("Server error", detail or "Unknown server error", parent=self.root)

    def hide_to_tray(self) -> None:
        if self._closing or self._closed:
            return
        self.root.withdraw()
        logging.info("Home Media Manager hidden to the system tray")

    def show_window(self) -> None:
        if self._closing or self._closed:
            return
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.update_idletasks()
        bring_window_to_foreground(self.root.winfo_id())
        self.root.focus_force()

    def _drain_logs(self) -> None:
        if self._closing or self._closed:
            return
        self._drain_ui_actions()
        if self._closing or self._closed:
            return
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
        if self._closing or self._closed:
            return
        self._closing = True
        try:
            self.server.stop()
        except Exception as exc:
            self._closing = False
            logging.exception("Unable to stop all media listeners")
            messagebox.showerror("Shutdown error", str(exc), parent=self.root)
            return
        try:
            self.tray.stop()
            self.instance.close()
        except Exception as exc:
            self._closing = False
            logging.exception("Unable to complete desktop shutdown")
            messagebox.showerror("Shutdown error", str(exc), parent=self.root)
            return
        self._closed = True
        self.root.quit()
        self.root.destroy()


def main() -> None:
    instance = SingleInstance()
    if not instance.acquire():
        return
    desktop = None
    try:
        root = tk.Tk()
        root.withdraw()
        repository = ConfigRepository(legacy_dir=legacy_data_dir())
        try:
            repository.migrate_profile(appdata_config_dir())
            repository.migrate_legacy()
            if repository.is_configured:
                repository.load()
            elif not run_setup_dialog(root, repository, first_run=True):
                root.destroy()
                return
        except Exception as exc:
            messagebox.showwarning(
                "Configuration needs attention",
                f"The saved configuration could not be loaded. Please review the settings.\n\n{exc}",
                parent=root,
            )
            if not run_setup_dialog(root, repository, first_run=not repository.is_configured):
                root.destroy()
                return
        root.deiconify()
        desktop = DesktopApp(root, instance, repository)
        root.mainloop()
    except KeyboardInterrupt:
        logging.info("Ctrl+C received; stopping media listeners")
    finally:
        if desktop is not None:
            desktop.close()
        else:
            instance.close()


if __name__ == "__main__":
    main()
