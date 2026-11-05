from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from media_manager.config import AppConfig
from media_manager.config_store import ConfigRepository, UserSettings, user_config_dir
from media_manager.credentials import DpapiCredentialStore, MemoryCredentialStore


def test_user_settings_store_keeps_credentials_out_of_config_json(tmp_path: Path):
    credentials = MemoryCredentialStore()
    repository = ConfigRepository(root=tmp_path / "appdata", credential_store=credentials)
    saved = repository.save(UserSettings(
        real_debrid_token="private-token",
        real_debrid_custom_id="private-custom-id",
        smb_folder=r"\\server\media",
        library_playlist_path=r"C:\Media\library.pls",
        secret_key="private-flask-secret",
    ))

    config_text = repository.config_path.read_text(encoding="utf-8")
    assert "private-token" not in config_text
    assert "private-custom-id" not in config_text
    assert "private-flask-secret" not in config_text
    assert json.loads(config_text)["smb_folder"] == r"\\server\media"
    assert credentials.load()["real_debrid_token"] == "private-token"
    assert repository.load() == saved
    assert json.loads((repository.data_dir / "playlist.json").read_text()) == []


def test_legacy_configuration_and_user_data_are_copied_without_deletion(tmp_path: Path):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / ".env").write_text(
        "REAL_DEBRID_TOKEN=legacy-token\n"
        "REAL_DEBRID_CUSTOM_ID=legacy-id\n"
        "SMB_FOLDER=\\\\server\\share\n"
        "PORT=8080\nLIBRARY_PORT=8081\n",
        encoding="utf-8",
    )
    (legacy / "playlist.json").write_text('[{"id": 1, "title": "Saved", "url": "https://example.test/video"}]', encoding="utf-8")
    (legacy / "fullNew.pls").write_text("[playlist]\nNumberOfEntries=0\nVersion=2\n", encoding="utf-8")
    cache = legacy / ".cache" / "thumbnails"
    cache.mkdir(parents=True)
    (cache / "cached.jpg").write_bytes(b"image")

    credentials = MemoryCredentialStore()
    repository = ConfigRepository(root=tmp_path / "new", legacy_dir=legacy, credential_store=credentials)
    assert repository.migrate_legacy() is True
    assert repository.migrate_legacy() is False

    settings = repository.load()
    assert settings.real_debrid_token == "legacy-token"
    assert settings.real_debrid_custom_id == "legacy-id"
    assert settings.library_playlist_path == str(legacy / "fullNew.pls")
    assert (repository.data_dir / "playlist.json").read_text(encoding="utf-8") == (legacy / "playlist.json").read_text(encoding="utf-8")
    assert (repository.data_dir / ".cache" / "thumbnails" / "cached.jpg").read_bytes() == b"image"
    assert (legacy / ".env").exists()
    assert (legacy / "playlist.json").exists()


def test_app_config_uses_user_data_paths_and_optional_missing_features(tmp_path: Path):
    repository = ConfigRepository(root=tmp_path / "profile", credential_store=MemoryCredentialStore())
    repository.save(UserSettings())
    config = AppConfig.load(repository=repository)

    assert config.data_dir == repository.data_dir
    assert config.playlist_path == repository.data_dir / "playlist.json"
    assert config.thumbnail_cache_dir == repository.data_dir / ".cache" / "thumbnails"
    assert config.real_debrid_token is None
    assert config.smb_folder is None
    assert config.port == 8080 and config.library_port == 8081


def test_invalid_saved_configuration_falls_back_without_exposing_credentials(tmp_path: Path):
    credentials = MemoryCredentialStore({"real_debrid_token": "do-not-expose"})
    repository = ConfigRepository(root=tmp_path / "profile", credential_store=credentials)
    repository.root.mkdir(parents=True)
    repository.config_path.write_text('{"host":"0.0.0.0","port":"invalid"}', encoding="utf-8")

    with pytest.raises(ValueError) as error:
        repository.load()
    assert "do-not-expose" not in str(error.value)

    config = AppConfig.load(repository=repository)
    assert config.port == 8080
    assert config.real_debrid_token is None


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI is required")
def test_dpapi_credential_round_trip_is_encrypted(tmp_path: Path):
    store = DpapiCredentialStore(tmp_path / "credentials.bin")
    values = {"real_debrid_token": "dpapi-secret", "secret_key": "flask-secret"}
    store.save(values)
    assert store.load() == values
    raw = store.path.read_bytes()
    assert b"dpapi-secret" not in raw
    assert b"flask-secret" not in raw


def test_user_config_directory_can_be_overridden_for_headless_runs(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME_MEDIA_MANAGER_CONFIG_DIR", str(tmp_path / "custom"))
    assert user_config_dir() == tmp_path / "custom"


def test_packaged_app_uses_portable_userdata_beside_executable(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("HOME_MEDIA_MANAGER_CONFIG_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "HomeMediaManager" / "HomeMediaManager.exe"))

    assert user_config_dir() == tmp_path / "HomeMediaManager" / "userdata"


def test_previous_appdata_profile_is_copied_to_portable_folder_without_deletion(tmp_path: Path):
    previous = tmp_path / "previous-profile"
    (previous / "data" / ".cache" / "thumbnails").mkdir(parents=True)
    (previous / "config.json").write_text(
        '{"host":"0.0.0.0","port":8080,"library_port":8081}', encoding="utf-8",
    )
    (previous / "credentials.bin").write_bytes(b"encrypted-credentials")
    (previous / "data" / "playlist.json").write_text('[{"id":1}]', encoding="utf-8")
    (previous / "data" / ".cache" / "thumbnails" / "saved.jpg").write_bytes(b"thumbnail")
    portable = ConfigRepository(
        root=tmp_path / "application" / "userdata",
        credential_store=MemoryCredentialStore(),
    )

    assert portable.migrate_profile(previous) is True
    assert portable.migrate_profile(previous) is False
    assert portable.config_path.read_text(encoding="utf-8") == (previous / "config.json").read_text(encoding="utf-8")
    assert portable.credentials_path.read_bytes() == b"encrypted-credentials"
    assert (portable.data_dir / "playlist.json").read_text(encoding="utf-8") == '[{"id":1}]'
    assert (portable.data_dir / ".cache" / "thumbnails" / "saved.jpg").read_bytes() == b"thumbnail"
    assert (previous / "config.json").exists()
    assert (previous / "data" / "playlist.json").exists()


def test_data_without_legacy_env_is_preserved_but_still_requires_first_run(tmp_path: Path):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "playlist.json").write_text('[{"title": "Existing"}]', encoding="utf-8")
    (legacy / "fullNew.pls").write_text("[playlist]\nFile1=movie.mp4\n", encoding="utf-8")
    repository = ConfigRepository(
        root=tmp_path / "appdata",
        legacy_dir=legacy,
        credential_store=MemoryCredentialStore(),
    )

    assert repository.migrate_legacy() is True
    assert repository.is_configured is False
    saved = json.loads((repository.data_dir / "playlist.json").read_text(encoding="utf-8"))
    assert saved[0]["title"] == "Existing"
    assert repository.load_for_edit().library_playlist_path == str(legacy / "fullNew.pls")
