from __future__ import annotations

import os
import secrets
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _resource_dir() -> Path:
    bundled = getattr(sys, "_MEIPASS", None)
    return Path(bundled) if bundled else Path(__file__).resolve().parents[1]


def _data_dir() -> Path:
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]


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
    secret_key: str = ""

    @classmethod
    def load(cls, data_dir: Path | None = None) -> "AppConfig":
        resources, data = _resource_dir(), Path(data_dir) if data_dir else _data_dir()
        load_dotenv(data / ".env", override=False)
        smb_value, ffmpeg_value = os.getenv("SMB_FOLDER", "").strip(), os.getenv("FFMPEG_PATH", "").strip()
        library_playlist_value = os.getenv("LIBRARY_PLAYLIST", "").strip()
        bundled_ffmpeg = resources / "bin" / "ffmpeg.exe"
        discovered_ffmpeg = shutil.which("ffmpeg")
        ffmpeg = Path(ffmpeg_value) if ffmpeg_value else (
            bundled_ffmpeg if bundled_ffmpeg.exists() else (Path(discovered_ffmpeg) if discovered_ffmpeg else None)
        )
        try:
            port = int(os.getenv("PORT", "8080"))
        except ValueError as exc:
            raise ValueError("PORT must be a number") from exc
        if not 1 <= port <= 65535:
            raise ValueError("PORT must be between 1 and 65535")
        return cls(
            resource_dir=resources, data_dir=data, playlist_path=data / "playlist.json",
            thumbnail_cache_dir=data / ".cache" / "thumbnails",
            real_debrid_token=os.getenv("REAL_DEBRID_TOKEN") or None,
            real_debrid_custom_id=os.getenv("REAL_DEBRID_CUSTOM_ID") or None,
            smb_folder=Path(smb_value) if smb_value else None, home_url=os.getenv("HOME_URL") or None,
            ffmpeg_path=ffmpeg, debrid_details_cache_path=data / ".cache" / "real_debrid_details.json",
            library_playlist_path=Path(library_playlist_value) if library_playlist_value else data / "fullNew.pls",
            host=os.getenv("HOST", "0.0.0.0"), port=port,
            secret_key=os.getenv("SECRET_KEY") or secrets.token_hex(24),
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
