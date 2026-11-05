from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from media_manager.config_store import ConfigRepository, UserSettings


class SetupDialog:
    """Small Tk frontend over the platform-neutral configuration repository."""

    def __init__(self, parent: tk.Misc, repository: ConfigRepository, first_run: bool):
        self.parent = parent
        self.repository = repository
        self.first_run = first_run
        self.saved = False
        try:
            initial = repository.load_for_edit()
        except Exception:
            initial = UserSettings()
        self.initial = initial
        self.window = tk.Toplevel(parent)
        self.window.withdraw()
        self.window.title("Home Media Manager Setup" if first_run else "Home Media Manager Settings")
        self.window.geometry("650x560")
        self.window.minsize(600, 520)
        self.window.protocol("WM_DELETE_WINDOW", self.cancel)

        self.token = tk.StringVar(value=initial.real_debrid_token)
        self.custom_id = tk.StringVar(value=initial.real_debrid_custom_id)
        self.smb_folder = tk.StringVar(value=initial.smb_folder)
        self.library_playlist = tk.StringVar(value=initial.library_playlist_path)
        self.ffmpeg_path = tk.StringVar(value=initial.ffmpeg_path)
        self.host = tk.StringVar(value=initial.host)
        self.port = tk.StringVar(value=str(initial.port))
        self.library_port = tk.StringVar(value=str(initial.library_port))
        self._build()
        self._show()
        self.window.grab_set()

    def _show(self) -> None:
        """Map the dialog even when the first-run root window is withdrawn."""
        self.window.update_idletasks()
        width = max(self.window.winfo_width(), 650)
        height = max(self.window.winfo_height(), 560)
        left = max((self.window.winfo_screenwidth() - width) // 2, 0)
        top = max((self.window.winfo_screenheight() - height) // 2, 0)
        self.window.geometry(f"+{left}+{top}")
        if self.parent.winfo_viewable():
            self.window.transient(self.parent)
        self.window.deiconify()
        self.window.lift()
        self.window.attributes("-topmost", True)

        def finish_showing() -> None:
            self.window.attributes("-topmost", False)
            self.window.focus_force()

        self.window.after_idle(finish_showing)

    def _build(self) -> None:
        shell = ttk.Frame(self.window, padding=22)
        shell.pack(fill="both", expand=True)
        ttk.Label(shell, text="Welcome to Home Media Manager" if self.first_run else "Settings", font=("Segoe UI Semibold", 18)).pack(anchor="w")
        ttk.Label(
            shell,
            text=(
                "Configure the features you use. Real-Debrid, SMB media, a PLS library, and FFmpeg are optional."
                if self.first_run else "Changes are saved securely and apply the next time the application starts."
            ),
            wraplength=590,
        ).pack(anchor="w", pady=(4, 16))

        form = ttk.Frame(shell)
        form.pack(fill="both", expand=True)
        form.columnconfigure(1, weight=1)
        row = 0
        row = self._entry(form, row, "Real-Debrid API token", self.token, show="•")
        row = self._entry(form, row, "Real-Debrid custom ID", self.custom_id, show="•")
        row = self._path_entry(form, row, "Local / SMB media folder", self.smb_folder, self._choose_smb)
        row = self._path_entry(form, row, "PLS library (optional)", self.library_playlist, self._choose_pls)
        row = self._path_entry(form, row, "FFmpeg executable (optional)", self.ffmpeg_path, self._choose_ffmpeg)
        row = self._entry(form, row, "Listen host", self.host)
        row = self._entry(form, row, "Favorites port", self.port)
        self._entry(form, row, "Library port", self.library_port)

        ttk.Label(
            shell,
            text="Credentials are encrypted for your Windows account. They are not written to config.json or application logs.",
            foreground="#555555",
            wraplength=590,
        ).pack(anchor="w", pady=(12, 8))
        buttons = ttk.Frame(shell)
        buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(buttons, text="Cancel", command=self.cancel).pack(side="right")
        ttk.Button(buttons, text="Save and Continue" if self.first_run else "Save", command=self.save).pack(side="right", padx=(0, 8))

    @staticmethod
    def _entry(parent, row: int, label: str, variable: tk.StringVar, show: str | None = None) -> int:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=6)
        ttk.Entry(parent, textvariable=variable, show=show or "").grid(row=row, column=1, columnspan=2, sticky="ew", pady=6)
        return row + 1

    @staticmethod
    def _path_entry(parent, row: int, label: str, variable: tk.StringVar, command) -> int:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=6)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=6)
        ttk.Button(parent, text="Browse…", command=command).grid(row=row, column=2, padx=(8, 0), pady=6)
        return row + 1

    def _choose_smb(self) -> None:
        selected = filedialog.askdirectory(parent=self.window, title="Select local or SMB media folder")
        if selected:
            self.smb_folder.set(selected)

    def _choose_pls(self) -> None:
        selected = filedialog.askopenfilename(parent=self.window, title="Select PLS library", filetypes=[("PLS playlists", "*.pls"), ("All files", "*.*")])
        if selected:
            self.library_playlist.set(selected)

    def _choose_ffmpeg(self) -> None:
        selected = filedialog.askopenfilename(parent=self.window, title="Select ffmpeg.exe", filetypes=[("FFmpeg", "ffmpeg.exe"), ("Executables", "*.exe"), ("All files", "*.*")])
        if selected:
            self.ffmpeg_path.set(selected)

    def save(self) -> None:
        settings = UserSettings(
            real_debrid_token=self.token.get(),
            real_debrid_custom_id=self.custom_id.get(),
            smb_folder=self.smb_folder.get(),
            library_playlist_path=self.library_playlist.get(),
            ffmpeg_path=self.ffmpeg_path.get(),
            host=self.host.get(),
            port=self.port.get(),
            library_port=self.library_port.get(),
            secret_key=self.initial.secret_key,
        )
        try:
            self.repository.save(settings)
        except Exception as exc:
            messagebox.showerror("Could not save settings", str(exc), parent=self.window)
            return
        self.saved = True
        self.window.destroy()

    def cancel(self) -> None:
        self.window.destroy()

    def run(self) -> bool:
        self.window.wait_window()
        return self.saved


def run_setup_dialog(parent: tk.Misc, repository: ConfigRepository, first_run: bool) -> bool:
    return SetupDialog(parent, repository, first_run).run()
