from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

from media_manager.config_store import ConfigRepository, UserSettings


def _resource_dir() -> Path:
    bundled = getattr(sys, "_MEIPASS", None)
    return Path(bundled) if bundled else Path(__file__).resolve().parents[1]


def _data_dir() -> Path:
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]


def legacy_data_dir() -> Path:
    """Location used by releases before user-specific application data."""
    return _data_dir()


@dataclass(frozen=True)
class AppConfig:
    resource_dir: Path
    data_dir: Path
    playlist_path: Path
    thumbnail_cache_dir: Path
    real_debrid_token: str | None
    real_debrid_custom_id: str | None
    smb_folder: Path | None
    home_url: str | None
    ffmpeg_path: Path | None
    debrid_details_cache_path: Path | None = None
    library_playlist_path: Path | None = None
    host: str = "0.0.0.0"
    port: int = 8080
    library_port: int = 8081
    secret_key: str = ""

    @classmethod
    def load(
        cls,
        data_dir: Path | None = None,
        repository: ConfigRepository | None = None,
    ) -> "AppConfig":
        resources = _resource_dir()
        if data_dir is not None:
            data = Path(data_dir)
            legacy = {key: str(value) for key, value in dotenv_values(data / ".env").items() if value is not None}
            values = UserSettings(
                real_debrid_token=os.getenv("REAL_DEBRID_TOKEN", legacy.get("REAL_DEBRID_TOKEN", "")),
                real_debrid_custom_id=os.getenv("REAL_DEBRID_CUSTOM_ID", legacy.get("REAL_DEBRID_CUSTOM_ID", "")),
                smb_folder=os.getenv("SMB_FOLDER", legacy.get("SMB_FOLDER", "")),
                library_playlist_path=os.getenv("LIBRARY_PLAYLIST", legacy.get("LIBRARY_PLAYLIST", "")),
                ffmpeg_path=os.getenv("FFMPEG_PATH", legacy.get("FFMPEG_PATH", "")),
                home_url=os.getenv("HOME_URL", legacy.get("HOME_URL", "")),
                host=os.getenv("HOST", legacy.get("HOST", "0.0.0.0")),
                port=os.getenv("PORT", legacy.get("PORT", 8080)),
                library_port=os.getenv("LIBRARY_PORT", legacy.get("LIBRARY_PORT", 8081)),
                secret_key=os.getenv("SECRET_KEY", legacy.get("SECRET_KEY", "")),
            ).validated()
        else:
            repository = repository or ConfigRepository(legacy_dir=_data_dir())
            if not repository.is_configured:
                repository.migrate_legacy()
            repository.ensure_data_files()
            try:
                values = repository.load()
            except (OSError, ValueError):
                values = UserSettings().validated()
            data = repository.data_dir

        smb_value, ffmpeg_value = values.smb_folder, values.ffmpeg_path
        library_playlist_value = values.library_playlist_path
        bundled_ffmpeg = resources / "bin" / "ffmpeg.exe"
        discovered_ffmpeg = shutil.which("ffmpeg")
        ffmpeg = Path(ffmpeg_value) if ffmpeg_value else (
            bundled_ffmpeg if bundled_ffmpeg.exists() else (Path(discovered_ffmpeg) if discovered_ffmpeg else None)
        )
        return cls(
            resource_dir=resources, data_dir=data, playlist_path=data / "playlist.json",
            thumbnail_cache_dir=data / ".cache" / "thumbnails",
            real_debrid_token=values.real_debrid_token or None,
            real_debrid_custom_id=values.real_debrid_custom_id or None,
            smb_folder=Path(smb_value) if smb_value else None, home_url=values.home_url or None,
            ffmpeg_path=ffmpeg, debrid_details_cache_path=data / ".cache" / "real_debrid_details.json",
            library_playlist_path=Path(library_playlist_value) if library_playlist_value else data / "fullNew.pls",
            host=values.host, port=values.port, library_port=values.library_port,
            secret_key=values.secret_key,
        )

    def configuration_warnings(self) -> list[str]:
        warnings = []
        if not self.real_debrid_token:
            warnings.append("REAL_DEBRID_TOKEN is not configured; Debrid browsing is unavailable.")
        if not self.real_debrid_custom_id:
            warnings.append("REAL_DEBRID_CUSTOM_ID is not configured; bookmarks are unavailable.")
        if self.smb_folder is None:
            warnings.append("SMB_FOLDER is not configured; local media playback is unavailable.")
        if self.ffmpeg_path is None:
            warnings.append("FFmpeg is not available; uncached video thumbnails will use fallback artwork.")
        if not self.playlist_path.exists():
            warnings.append(f"Playlist not found: {self.playlist_path}")
        if self.library_playlist_path is None or not self.library_playlist_path.exists():
            warnings.append(f"Library playlist not found: {self.library_playlist_path or 'fullNew.pls'}")
        return warnings
