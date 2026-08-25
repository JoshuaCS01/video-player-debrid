from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from app import create_app
from media_manager.config import AppConfig
from media_manager.debrid import DebridError, RealDebridClient
from media_manager.pls_library import PLSLibrary
from media_manager.storage import PlaylistStore
from media_manager.thumbnails import ThumbnailService


@pytest.fixture()
def configured_app(tmp_path: Path):
    playlist = tmp_path / "playlist.json"
    playlist.write_text(json.dumps([
        {"id": 1, "title": "Remote title", "url": "https://example.test/video", "stars": ["Person"]},
        {"id": 2, "title": "Local title", "url": "192.168.1.2/Locker/VR/folder/movie.mp4", "stars": []},
    ]), encoding="utf-8")
    first_folder, second_folder = tmp_path / "first", tmp_path / "nested" / "second"
    first_folder.mkdir()
    second_folder.mkdir(parents=True)
    first_video, second_video = first_folder / "one.mp4", second_folder / "two.mkv"
    first_video.write_bytes(b"first-video")
    second_video.write_bytes(b"second-video")
    library_playlist = tmp_path / "fullNew.pls"
    library_playlist.write_text(
        "[playlist]\n"
        f"File1={first_video}\nTitle1=First library video\n"
        f"File2={second_video}\nTitle2=Second library video\n"
        f"File3={first_video}\nTitle3=Duplicate playlist entry\n"
        f"File4={tmp_path / 'missing.mp4'}\nTitle4=Missing library video\n"
        "NumberOfEntries=4\nVersion=2\n",
        encoding="utf-8",
    )
    source_root = Path(__file__).resolve().parents[1]
    settings = AppConfig(
        resource_dir=source_root, data_dir=tmp_path, playlist_path=playlist,
        thumbnail_cache_dir=tmp_path / ".cache", real_debrid_token=None,
        real_debrid_custom_id=None, smb_folder=tmp_path, home_url=None,
        ffmpeg_path=None, library_playlist_path=library_playlist, secret_key="test",
    )
    app = create_app(settings)
    app.config.update(TESTING=True)
    return app


def test_home_and_health(configured_app):
    client = configured_app.test_client()
    response = client.get("/")
    assert response.status_code == 200
    assert b"<h1>Favorites</h1>" in response.data
    assert b"Remote title" in response.data
    health = client.get("/health").json
    assert health["status"] == "ok"
    assert health["items"] == 2
    assert health["thumbnails"]["worker_limit"] == 2


def test_play_preserves_remote_and_local_routes(configured_app):
    client = configured_app.test_client()
    assert client.get("/play/1").location == "https://example.test/video"
    assert client.get("/play/2").location.endswith("/smb/folder/movie.mp4")
    assert client.get("/play/404").status_code == 404


def test_heresphere_api_exposes_ordered_library_and_direct_sources(configured_app, caplog):
    client = configured_app.test_client()
    library = client.post("/heresphere", json={})
    assert library.status_code == 200
    assert library.content_type == "application/json"
    assert "Content-Disposition" not in library.headers
    assert library.headers["HereSphere-JSON-Version"] == "1"
    assert library.headers["Cache-Control"] == "no-store"
    assert library.json["access"] == 1
    assert library.json["library"][0]["name"] == "Favorites"
    assert library.json["library"][0]["list"] == [
        "http://localhost/heresphere/video/1",
        "http://localhost/heresphere/video/2",
    ]
    main_page_api = client.post("/", json={})
    assert main_page_api.json == library.json
    assert main_page_api.headers["HereSphere-JSON-Version"] == "1"

    caplog.clear()
    metadata = client.post("/heresphere/video/2", json={"needsMediaSource": False})
    assert metadata.status_code == 200
    assert metadata.content_type == "application/json"
    assert "Content-Disposition" not in metadata.headers
    assert metadata.headers["HereSphere-JSON-Version"] == "1"
    assert metadata.json["media"][0]["sources"][0]["url"].endswith("/smb/folder/movie.mp4")
    assert metadata.json["writeHSP"] is False
    assert "Clicked video:" not in caplog.text

    with caplog.at_level(logging.INFO):
        playable = client.post("/heresphere/video/1", json={"needsMediaSource": True})
    assert playable.json["media"][0]["sources"][0]["url"] == "https://example.test/video"
    assert "Clicked video: Remote title" in caplog.text


def test_main_page_preserves_direct_links_and_excludes_heresphere_controls_from_debrid(configured_app):
    client = configured_app.test_client()
    main = client.get("/")
    assert b"data-shuffle-button" not in main.data
    assert b"data-heresphere-help" in main.data
    assert b"Remove from Favorites" in main.data
    assert b"window.confirm" in main.data
    assert b"data-play-link" in main.data
    assert b'href="/play/1"' in main.data
    assert b'href="/heresphere' not in main.data

    debrid = client.get("/debrid")
    assert b"data-heresphere-help" not in debrid.data

    script = (Path(__file__).resolve().parents[1] / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert 'link.setAttribute("download", "")' in script
    assert "window.location.assign" not in script
    assert 'searchParams.set("shuffle"' not in script
    css = (Path(__file__).resolve().parents[1] / "static" / "css" / "app.css").read_text(encoding="utf-8")
    assert "margin-left: auto" in css and "justify-content: flex-end" in css
    assert ".search {" in css and "position: relative" in css
    assert ".library-page-controls .search { flex-basis: auto; }" in css


def test_remove_favorite_requires_confirm_markup_and_persists_without_deleting_media(configured_app):
    client = configured_app.test_client()
    settings = configured_app.config["APP_SETTINGS"]
    before = configured_app.extensions["playlist_store"].all()
    assert [item["id"] for item in before] == [1, 2]

    page = client.get("/")
    assert b'Remove &quot;Remote title&quot; from Favorites?' in page.data or b'Remove \\"Remote title\\" from Favorites?' in page.data
    response = client.post("/favorites/1/remove", follow_redirects=True)
    assert response.status_code == 200
    assert b"Removed" in response.data
    assert [item["id"] for item in configured_app.extensions["playlist_store"].all()] == [2]

    restarted = create_app(settings)
    assert [item["id"] for item in restarted.extensions["playlist_store"].all()] == [2]
    assert (settings.library_playlist_path.parent / "first" / "one.mp4").is_file()


def test_pls_library_preserves_order_duplicates_and_missing_entries(configured_app):
    library = configured_app.extensions["pls_library"]
    items = library.all()
    assert [item["title"] for item in items] == [
        "First library video", "Second library video", "Duplicate playlist entry", "Missing library video",
    ]
    assert items[0]["url"] == items[2]["url"]
    assert items[0]["id"] != items[2]["id"]
    assert items[0]["available"] is True
    assert items[1]["available"] is True
    assert items[3]["available"] is False


def test_library_page_playback_and_heresphere_discovery(configured_app, caplog):
    client = configured_app.test_client()
    page = client.get("/library")
    assert page.status_code == 200
    assert b"<h1>Library</h1>" in page.data
    assert b"First library video" in page.data and b"Second library video" in page.data
    assert b"Duplicate playlist entry" in page.data and b"Missing library video" in page.data
    assert b"Missing or unavailable" in page.data
    assert b"Remove from Favorites" not in page.data
    assert b"<video" not in page.data

    with caplog.at_level(logging.INFO):
        redirect_response = client.get("/library/play/1")
    assert redirect_response.location.endswith("/library/stream/1")
    assert "Clicked video: First library video" in caplog.text
    ranged = client.get("/library/stream/1", headers={"Range": "bytes=0-4"})
    assert ranged.status_code == 206 and ranged.data == b"first"
    assert client.get("/library/stream/4").status_code == 404

    api = client.post("/library", json={})
    assert api.status_code == 200
    assert api.headers["HereSphere-JSON-Version"] == "1"
    assert api.json["library"][0]["name"] == "Library"
    assert api.json["library"][0]["list"] == [
        f"http://localhost/library/heresphere/video/{item_id}" for item_id in (1, 2, 3, 4)
    ]
    video = client.post("/library/heresphere/video/2", json={"needsMediaSource": False})
    assert video.json["media"][0]["sources"][0]["url"] == "http://localhost/library/stream/2"
    assert b"data-heresphere-help" not in client.get("/debrid").data


def test_smb_route_streams_encoded_nested_paths_and_logs_clicks(tmp_path: Path, caplog):
    media = tmp_path / "media"
    nested = media / "nested"
    nested.mkdir(parents=True)
    example_name = "VRCosplayX_RESIDENT_EVIL_VILLAGE_Lady_Dimitrescu_A_XXX_Parody_7k_180_180x180_3dh_LR.mp4"
    complex_name = "O'Brien & [café] #100%.mp4"
    (media / example_name).write_bytes(b"example-video")
    (media / "another.mp4").write_bytes(b"another-video")
    (nested / complex_name).write_bytes(b"nested-video")
    playlist = tmp_path / "playlist.json"
    playlist.write_text(json.dumps([
        {"id": 1, "title": example_name, "url": f"host/Locker/VR/{example_name}", "stars": []},
        {"id": 2, "title": "another.mp4", "url": "host/Locker/VR/another.mp4", "stars": []},
        {"id": 3, "title": complex_name, "url": f"host/Locker/VR/nested/{complex_name}", "stars": []},
    ]), encoding="utf-8")
    source_root = Path(__file__).resolve().parents[1]
    settings = AppConfig(
        resource_dir=source_root, data_dir=tmp_path, playlist_path=playlist,
        thumbnail_cache_dir=tmp_path / ".cache", real_debrid_token=None,
        real_debrid_custom_id=None, smb_folder=media, home_url=None,
        ffmpeg_path=None, secret_key="test",
    )
    app = create_app(settings)
    app.config.update(TESTING=True)
    client = app.test_client()

    with caplog.at_level(logging.INFO):
        example_redirect = client.get("/play/1")
    assert example_redirect.location.endswith(f"/smb/{example_name}")
    assert f"Clicked video: {example_name}" in caplog.text
    example = client.get(example_redirect.location, headers={"Range": "bytes=0-6"})
    assert example.status_code == 206 and example.data == b"example"

    assert client.get(client.get("/play/2").location).status_code == 200
    complex_redirect = client.get("/play/3")
    assert "/smb/nested/" in complex_redirect.location
    assert "%23" in complex_redirect.location and "%25" in complex_redirect.location
    complex_response = client.get(complex_redirect.location)
    assert complex_response.status_code == 200 and complex_response.data == b"nested-video"


def test_smb_route_distinguishes_missing_inaccessible_and_unsafe_paths(configured_app, tmp_path: Path):
    client = configured_app.test_client()
    missing = client.get("/smb/does-not-exist.mp4")
    assert missing.status_code == 404
    assert b"SMB file not found" in missing.data
    encoded_question = client.get("/smb/name%3Fpart.mp4")
    assert encoded_question.status_code == 404
    assert b"Invalid SMB media path" in encoded_question.data

    traversal = client.get("/smb/%2E%2E/outside.mp4")
    assert traversal.status_code == 404

    settings = replace(
        configured_app.config["APP_SETTINGS"],
        smb_folder=tmp_path / "unavailable-share",
    )
    inaccessible = create_app(settings).test_client().get("/smb/movie.mp4")
    assert inaccessible.status_code == 503
    assert b"SMB share is inaccessible" in inaccessible.data


def test_debrid_missing_configuration_is_displayed(configured_app):
    response = configured_app.test_client().get("/debrid")
    assert response.status_code == 200
    assert b"REAL_DEBRID_TOKEN is not configured" in response.data


def test_thumbnail_fallback(configured_app):
    response = configured_app.test_client().get("/thumbnail/1")
    assert response.status_code == 200
    assert response.mimetype == "image/svg+xml"


def test_playlist_add_is_atomic_and_deduplicated(tmp_path: Path):
    path = tmp_path / "playlist.json"
    path.write_text("[]", encoding="utf-8")
    store = PlaylistStore(path)
    assert store.add_bookmark("Title", "https://example.test/file") is True
    assert store.add_bookmark("Title", "https://example.test/file") is False
    assert store.all()[0]["id"] == 1


def test_local_filename_and_remote_detection():
    assert ThumbnailService.is_remote("https://example.test/a.mp4")
    assert ThumbnailService.local_filename({"url": "host/Locker/VR/folder/a.mp4"}) == "folder/a.mp4"


def test_debrid_client_does_not_call_network_without_token():
    with pytest.raises(DebridError, match="not configured"):
        RealDebridClient(None).list_torrents()


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_debrid_filters_rar_case_insensitively_and_keeps_mixed_torrents():
    client = RealDebridClient("test-token")
    client._session.get = lambda *args, **kwargs: FakeResponse([
        {"id": "video", "filename": "movie.mkv", "links": ["video-link"], "files": [
            {"path": "/movie.mkv", "selected": 1},
        ]},
        {"id": "lower", "filename": "archive.rar", "links": ["rar-link"], "files": [
            {"path": "/archive.rar", "selected": 1},
        ]},
        {"id": "upper", "filename": "ARCHIVE.RAR", "links": ["rar-link"], "files": [
            {"path": "/ARCHIVE.RAR", "selected": 1},
        ]},
        {"id": "multipart", "filename": "game", "links": ["part-link", "exe-link"], "files": [
            {"path": "/data.part01.rar", "selected": 1},
            {"path": "/setup.exe", "selected": 1},
        ]},
        {"id": "mixed", "filename": "bundle", "links": ["rar-link", "movie-link", "text-link"], "files": [
            {"path": "/archive.RAR", "selected": 1},
            {"path": "/episode.mp4", "selected": 1},
            {"path": "/readme.txt", "selected": 1},
        ]},
    ])

    items = client.list_torrents()
    assert [item["filename"] for item in items] == ["movie.mkv", "episode.mp4"]
    assert items[1]["rd_link"] == "movie-link"
    assert RealDebridClient.is_rar_filename("archive.RAR")
    assert RealDebridClient.is_rar_filename("archive.r00")
    assert RealDebridClient.is_rar_filename("data.part01.rar")


def test_real_debrid_details_are_persisted_and_reused(tmp_path: Path):
    cache = tmp_path / "details.json"
    listing = [{"id": "torrent-1", "filename": "Movie", "hash": "abc", "status": "downloaded", "links": ["link"]}]
    detail = {**listing[0], "files": [{"path": "/movie.mkv", "selected": 1}]}

    first = RealDebridClient("token", details_cache_path=cache)
    first._session.get = lambda *args, **kwargs: FakeResponse(listing)
    first._fetch_detail = Mock(return_value=detail)
    assert [item["filename"] for item in first.list_torrents()] == ["movie.mkv"]
    assert first._fetch_detail.call_count == 1
    assert cache.is_file()

    restarted = RealDebridClient("token", details_cache_path=cache)
    restarted._session.get = lambda *args, **kwargs: FakeResponse(listing)
    restarted._fetch_detail = Mock(side_effect=AssertionError("detail API should not be called"))
    assert [item["filename"] for item in restarted.list_torrents()] == ["movie.mkv"]
    restarted._fetch_detail.assert_not_called()


def test_multi_video_torrents_are_grouped_by_parent_and_keep_only_videos():
    client = RealDebridClient("test-token")
    client._session.get = lambda *args, **kwargs: FakeResponse([
        {"id": "single", "filename": "Single movie", "links": ["movie-link"], "files": [
            {"path": "/movie.mkv", "selected": 1},
        ]},
        {"id": "series", "filename": "My Name Is Earl Complete Series", "links": ["one", "rar", "two", "text"], "files": [
            {"path": "/S01E01.mkv", "selected": 1},
            {"path": "/extras.rar", "selected": 1},
            {"path": "/S01E02.mp4", "selected": 1},
            {"path": "/readme.txt", "selected": 1},
        ]},
    ])

    flat = client.list_torrents()
    library = client.list_library()
    assert [video["filename"] for video in flat] == ["movie.mkv", "S01E01.mkv", "S01E02.mp4"]
    assert len(library) == 2
    assert library[0]["kind"] == "video"
    assert library[1]["kind"] == "group"
    assert library[1]["torrent_id"] == "series"
    assert library[1]["video_count"] == 2
    assert [video["filename"] for video in library[1]["videos"]] == ["S01E01.mkv", "S01E02.mp4"]


def test_group_children_are_loaded_separately_and_video_click_is_logged(configured_app, caplog):
    debrid = configured_app.extensions["debrid_client"]
    single = {"id": 0, "kind": "video", "filename": "movie.mkv", "torrent_filename": "Movie", "rd_link": "single-link"}
    child_one = {"id": 1, "filename": "S01E01.mkv", "torrent_filename": "Series", "rd_link": "one"}
    child_two = {"id": 2, "filename": "S01E02.mp4", "torrent_filename": "Series", "rd_link": "two"}
    group = {
        "id": 1, "kind": "group", "filename": "My Name Is Earl Complete Series",
        "torrent_id": "series", "video_count": 2, "videos": [child_one, child_two],
        "search_text": "My Name Is Earl Complete Series",
    }
    debrid._items = [single, child_one, child_two]
    debrid._groups = [single, group]
    debrid._loaded_at = time.monotonic()
    debrid.unrestrict = Mock(return_value="https://example.test/stream")
    client = configured_app.test_client()

    page = client.get("/debrid")
    assert page.status_code == 200
    assert b"movie.mkv" in page.data
    assert b"My Name Is Earl Complete Series" in page.data
    assert b"2 videos" in page.data
    assert b"S01E01.mkv" not in page.data
    assert b"data-group-url" in page.data

    # An expansion reuses the relationships already loaded for the page, even
    # after the normal list cache lifetime has elapsed.
    debrid._loaded_at = 0
    debrid._session.get = Mock(side_effect=AssertionError("group expansion must not call Real-Debrid"))
    caplog.clear()
    children = client.get("/debrid/group/1")
    assert children.status_code == 200
    assert b"S01E01.mkv" in children.data and b"S01E02.mp4" in children.data
    assert b".rar" not in children.data and b"readme.txt" not in children.data
    assert "Clicked video:" not in caplog.text
    debrid._session.get.assert_not_called()

    debrid._loaded_at = time.monotonic()
    with caplog.at_level(logging.INFO):
        played = client.get("/debrid/play/1")
    assert played.location == "https://example.test/stream"
    assert "Clicked video: S01E01.mkv" in caplog.text


def test_library_route_only_checks_disk_cache(configured_app):
    service = configured_app.extensions["thumbnail_service"]
    response = configured_app.test_client().get("/")
    assert response.status_code == 200
    assert service.metrics()["queued_jobs"] == 0


def test_batch_thumbnail_status(configured_app):
    response = configured_app.test_client().get("/thumbnails/status?ids=1,2,999")
    assert response.status_code == 200
    assert set(response.json["items"]) == {"1", "2"}
    metrics = configured_app.extensions["thumbnail_service"].metrics()
    assert metrics["status_batches"] == 1
    assert metrics["status_items"] == 2


def test_duplicate_thumbnail_jobs_are_not_queued(tmp_path: Path):
    service = ThumbnailService(None, tmp_path)
    service._start_workers = lambda: setattr(service, "_workers_started", True)
    video = {"id": 1, "title": "Movie", "url": "https://my.real-debrid.com/example/movie.mp4"}
    service._schedule(video)
    service._schedule(video)
    assert service._jobs.qsize() == 1
    assert service.metrics()["duplicate_skips"] == 1


def test_ffmpeg_failure_categories():
    assert ThumbnailService._classify_failure("Resource temporarily unavailable")[0] == "resource_limit"
    assert ThumbnailService._classify_failure("moov atom not found")[0] == "invalid_input"
    assert ThumbnailService._classify_failure("TLS connection failed")[0] == "network"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg is not installed")
def test_actual_video_thumbnail_is_generated_served_and_reused(tmp_path: Path):
    ffmpeg = shutil.which("ffmpeg")
    media = tmp_path / "media"
    media.mkdir()
    video = media / "sample.mp4"
    subprocess.run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "color=c=0x7c3aed:s=320x180:d=11", "-c:v", "mpeg4", "-y", str(video),
    ], check=True, timeout=30)
    playlist = tmp_path / "playlist.json"
    playlist.write_text(json.dumps([
        {"id": 1, "title": "Actual sample video", "url": "host/Locker/VR/sample.mp4", "stars": []},
    ]), encoding="utf-8")
    source_root = Path(__file__).resolve().parents[1]
    settings = AppConfig(
        resource_dir=source_root, data_dir=tmp_path, playlist_path=playlist,
        thumbnail_cache_dir=tmp_path / ".cache", real_debrid_token=None,
        real_debrid_custom_id=None, smb_folder=media, home_url=None,
        ffmpeg_path=Path(ffmpeg), secret_key="test",
    )
    app = create_app(settings)
    service = app.extensions["thumbnail_service"]
    item = app.extensions["playlist_store"].get(1)

    generated = service.generate_now(item)
    assert generated is not None and generated.is_file() and generated.stat().st_size > 0
    modified = generated.stat().st_mtime_ns
    assert service.generate_now(item) == generated
    assert generated.stat().st_mtime_ns == modified

    response = app.test_client().get("/thumbnail/1")
    assert response.status_code == 200
    assert response.mimetype == "image/jpeg"

    restarted = create_app(settings)
    restarted_service = restarted.extensions["thumbnail_service"]
    restarted_service._run_ffmpeg = Mock(side_effect=AssertionError("FFmpeg should not run for a cache hit"))
    second_response = restarted.test_client().get("/thumbnail/1")
    assert second_response.status_code == 200
    assert second_response.mimetype == "image/jpeg"
    other_client_response = restarted.test_client().get("/thumbnail/1")
    assert other_client_response.status_code == 200
    assert other_client_response.mimetype == "image/jpeg"
    restarted_service._run_ffmpeg.assert_not_called()
