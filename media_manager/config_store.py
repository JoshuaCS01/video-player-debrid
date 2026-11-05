from __future__ import annotations

import json
import os
import secrets
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from dotenv import dotenv_values

from media_manager.credentials import CredentialStore, DpapiCredentialStore, MemoryCredentialStore


CONFIG_VERSION = 1
APP_DIRECTORY = "HomeMediaManager"
SECRET_FIELDS = ("real_debrid_token", "real_debrid_custom_id", "secret_key")


def appdata_config_dir() -> Path:
    """Return the profile location used by non-portable releases."""
    if os.name == "nt":
        base = os.getenv("LOCALAPPDATA", "").strip()
        return (Path(base) if base else Path.home() / "AppData" / "Local") / APP_DIRECTORY
    base = os.getenv("XDG_CONFIG_HOME", "").strip()
    return (Path(base) if base else Path.home() / ".config") / APP_DIRECTORY


def user_config_dir() -> Path:
    override = os.getenv("HOME_MEDIA_MANAGER_CONFIG_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "userdata"
    return appdata_config_dir()


@dataclass
class UserSettings:
    real_debrid_token: str = ""
    real_debrid_custom_id: str = ""
    smb_folder: str = ""
    library_playlist_path: str = ""
    ffmpeg_path: str = ""
    home_url: str = ""
    host: str = "0.0.0.0"
    port: int = 8080
    library_port: int = 8081
    secret_key: str = ""

    def validated(self) -> "UserSettings":
        try:
            port, library_port = int(self.port), int(self.library_port)
        except (TypeError, ValueError) as exc:
            raise ValueError("Ports must be numbers") from exc
        if not 1 <= port <= 65535 or not 1 <= library_port <= 65535:
            raise ValueError("Ports must be between 1 and 65535")
        if port == library_port:
            raise ValueError("Favorites and Library ports must be different")
        host = str(self.host or "").strip()
        if not host:
            raise ValueError("Host cannot be empty")
        return UserSettings(
            real_debrid_token=str(self.real_debrid_token or "").strip(),
            real_debrid_custom_id=str(self.real_debrid_custom_id or "").strip(),
            smb_folder=str(self.smb_folder or "").strip(),
            library_playlist_path=str(self.library_playlist_path or "").strip(),
            ffmpeg_path=str(self.ffmpeg_path or "").strip(),
            home_url=str(self.home_url or "").strip(),
            host=host,
            port=port,
            library_port=library_port,
            secret_key=str(self.secret_key or "").strip() or secrets.token_hex(24),
        )


class ConfigRepository:
    """Store platform-neutral settings and delegate secret persistence."""

    def __init__(
        self,
        root: Path | None = None,
        legacy_dir: Path | None = None,
        credential_store: CredentialStore | None = None,
    ):
        self.root = Path(root) if root else user_config_dir()
        self.data_dir = self.root / "data"
        self.config_path = self.root / "config.json"
        self.credentials_path = self.root / "credentials.bin"
        self.legacy_dir = Path(legacy_dir) if legacy_dir else None
        self.credential_store = credential_store or (
            DpapiCredentialStore(self.credentials_path) if os.name == "nt" else MemoryCredentialStore()
        )

    @property
    def is_configured(self) -> bool:
        return self.config_path.is_file()

    def has_legacy_configuration(self) -> bool:
        legacy = self.legacy_dir
        return bool(legacy and any((legacy / name).exists() for name in (".env", "playlist.json", "fullNew.pls", ".cache")))

    def load(self) -> UserSettings:
        return self.load_for_edit().validated()

    def load_for_edit(self) -> UserSettings:
        stored: dict = {}
        if self.config_path.exists():
            stored = json.loads(self.config_path.read_text(encoding="utf-8"))
            if not isinstance(stored, dict):
                raise ValueError("Configuration file must contain a JSON object")
        legacy_values: dict[str, str] = {}
        if not self.config_path.exists() and self.legacy_dir:
            legacy_env = self.legacy_dir / ".env"
            if legacy_env.is_file():
                legacy_values = {
                    key: str(value) for key, value in dotenv_values(legacy_env).items()
                    if value is not None
                }
            legacy_playlist = self.legacy_dir / "fullNew.pls"
            if legacy_playlist.is_file() and not legacy_values.get("LIBRARY_PLAYLIST"):
                legacy_values["LIBRARY_PLAYLIST"] = str(legacy_playlist)
        credentials = self.credential_store.load() if self.config_path.exists() else {}
        values = UserSettings(
            real_debrid_token=credentials.get("real_debrid_token", legacy_values.get("REAL_DEBRID_TOKEN", "")),
            real_debrid_custom_id=credentials.get("real_debrid_custom_id", legacy_values.get("REAL_DEBRID_CUSTOM_ID", "")),
            secret_key=credentials.get("secret_key", legacy_values.get("SECRET_KEY", "")),
            smb_folder=stored.get("smb_folder", legacy_values.get("SMB_FOLDER", "")),
            library_playlist_path=stored.get("library_playlist_path", legacy_values.get("LIBRARY_PLAYLIST", "")),
            ffmpeg_path=stored.get("ffmpeg_path", legacy_values.get("FFMPEG_PATH", "")),
            home_url=stored.get("home_url", legacy_values.get("HOME_URL", "")),
            host=stored.get("host", legacy_values.get("HOST", "0.0.0.0")),
            port=stored.get("port", legacy_values.get("PORT", 8080)),
            library_port=stored.get("library_port", legacy_values.get("LIBRARY_PORT", 8081)),
        )
        return self._environment_overrides(values)

    def save(self, settings: UserSettings) -> UserSettings:
        settings = settings.validated()
        self.root.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_playlist()
        payload = asdict(settings)
        credentials = {name: payload.pop(name) for name in SECRET_FIELDS}
        payload["version"] = CONFIG_VERSION
        self.credential_store.save(credentials)
        self._atomic_json_write(self.config_path, payload)
        return settings

    def migrate_legacy(self) -> bool:
        if self.is_configured or not self.has_legacy_configuration():
            return False
        legacy = self.legacy_dir
        playlist = legacy / "playlist.json"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if playlist.is_file() and not (self.data_dir / "playlist.json").exists():
            shutil.copy2(playlist, self.data_dir / "playlist.json")
        legacy_cache = legacy / ".cache"
        if legacy_cache.is_dir():
            shutil.copytree(legacy_cache, self.data_dir / ".cache", dirs_exist_ok=True)
        # A legacy .env identifies an existing installation and can be migrated
        # without interrupting it. Data files alone are copied, but still leave
        # a genuinely new install unconfigured so the first-run wizard appears.
        if (legacy / ".env").is_file():
            self.save(self.load_for_edit())
        return True

    def migrate_profile(self, source_root: Path) -> bool:
        """Copy a previous profile into a new portable root without deleting it."""
        source = Path(source_root)
        if self.is_configured or not (source / "config.json").is_file():
            return False
        if os.path.normcase(os.path.abspath(source)) == os.path.normcase(os.path.abspath(self.root)):
            return False

        self.root.mkdir(parents=True, exist_ok=True)
        source_data = source / "data"
        if source_data.is_dir():
            shutil.copytree(source_data, self.data_dir, dirs_exist_ok=True)
        source_credentials = source / "credentials.bin"
        if source_credentials.is_file():
            shutil.copy2(source_credentials, self.credentials_path)
        # Copy config last. Its presence marks the target profile as complete.
        shutil.copy2(source / "config.json", self.config_path)
        return True

    def ensure_data_files(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_playlist()

    def _ensure_playlist(self) -> None:
        playlist = self.data_dir / "playlist.json"
        if not playlist.exists():
            self._atomic_json_write(playlist, [])

    @staticmethod
    def _atomic_json_write(path: Path, payload) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f"{path.stem}-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
            os.replace(temporary, path)
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    @staticmethod
    def _environment_overrides(settings: UserSettings) -> UserSettings:
        values = asdict(settings)
        mapping = {
            "REAL_DEBRID_TOKEN": "real_debrid_token",
            "REAL_DEBRID_CUSTOM_ID": "real_debrid_custom_id",
            "SMB_FOLDER": "smb_folder",
            "LIBRARY_PLAYLIST": "library_playlist_path",
            "FFMPEG_PATH": "ffmpeg_path",
            "HOME_URL": "home_url",
            "HOST": "host",
            "PORT": "port",
            "LIBRARY_PORT": "library_port",
            "SECRET_KEY": "secret_key",
        }
        for environment_name, field_name in mapping.items():
            if environment_name in os.environ:
                values[field_name] = os.environ[environment_name]
        return UserSettings(**values)
