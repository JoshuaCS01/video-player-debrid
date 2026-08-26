"""Flask entry point for Home Media Manager."""

from __future__ import annotations

import logging
import os
import stat
import threading
import time
from urllib.parse import urlsplit, urlunsplit

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, send_file, send_from_directory, url_for
from werkzeug.security import safe_join

from media_manager.config import AppConfig
from media_manager.debrid import DebridError, RealDebridClient
from media_manager.pls_library import PLSLibrary
from media_manager.server import (
    FAVORITES_COLLECTION,
    HERESPHERE_COLLECTION_ENV,
    LIBRARY_COLLECTION,
    HereSphereServerGroup,
)
from media_manager.storage import PlaylistStore
from media_manager.thumbnails import ThumbnailService


def create_app(config: AppConfig | None = None) -> Flask:
    """Create the app without starting a server or making API calls."""
    settings = config or AppConfig.load()
    app = Flask(__name__, template_folder=str(settings.resource_dir / "templates"), static_folder=str(settings.resource_dir / "static"))
    app.config.update(SECRET_KEY=settings.secret_key, APP_SETTINGS=settings)

    playlist = PlaylistStore(settings.playlist_path)
    full_library = PLSLibrary(settings.library_playlist_path or settings.data_dir / "fullNew.pls")
    debrid_client = RealDebridClient(settings.real_debrid_token, details_cache_path=settings.debrid_details_cache_path)
    thumbnails = ThumbnailService(settings.smb_folder, settings.thumbnail_cache_dir, settings.ffmpeg_path)
    app.extensions.update(
        playlist_store=playlist, pls_library=full_library,
        debrid_client=debrid_client, thumbnail_service=thumbnails,
    )

    discovery_paths = {
        "/", "/library", "/library/", "/heresphere", "/heresphere/",
        "/library/heresphere", "/library/heresphere/",
    }

    @app.before_request
    def log_heresphere_discovery_request():
        if request.path in discovery_paths:
            app.logger.info(
                "HereSphere discovery request: method=%s path=%s user_agent=%s",
                request.method,
                request.path,
                request.user_agent.string or "-",
            )

    def listener_collection() -> str | None:
        return request.environ.get(HERESPHERE_COLLECTION_ENV)

    def origin_url(port: int, path: str) -> str:
        current = urlsplit(request.host_url)
        hostname = current.hostname or "127.0.0.1"
        rendered_host = f"[{hostname}]" if ":" in hostname else hostname
        netloc = f"{rendered_host}:{port}"
        return urlunsplit((current.scheme, netloc, path, "", ""))

    @app.context_processor
    def listener_navigation_urls():
        collection = listener_collection()
        return {
            "favorites_site_url": (
                origin_url(settings.port, url_for("home"))
                if collection == LIBRARY_COLLECTION else url_for("home")
            ),
            "library_site_url": (
                origin_url(settings.library_port, url_for("library_page"))
                if collection == FAVORITES_COLLECTION else url_for("library_page")
            ),
            "debrid_site_url": (
                origin_url(settings.port, url_for("debrid"))
                if collection == LIBRARY_COLLECTION else url_for("debrid")
            ),
        }

    @app.route("/", methods=["GET", "POST"])
    def home():
        if request.method == "POST":
            return heresphere_library()
        if listener_collection() == LIBRARY_COLLECTION:
            return redirect(url_for("library_page"))
        started = time.perf_counter()
        try:
            stored_videos, library_error = playlist.all(), None
            videos = [dict(video, thumbnail_cached=thumbnails.has_disk_cache(video)) for video in stored_videos]
        except (OSError, ValueError) as exc:
            app.logger.exception("Unable to load playlist")
            videos, library_error = [], str(exc)
        response = render_template("index.html", videos=videos, library_error=library_error)
        elapsed_ms = (time.perf_counter() - started) * 1000
        app.logger.info("Favorites response: %d items, %d cached thumbnails, %.1f ms", len(videos), sum(video.get("thumbnail_cached", False) for video in videos), elapsed_ms)
        return response

    @app.get("/library")
    def library_page():
        if listener_collection() == FAVORITES_COLLECTION:
            return redirect(origin_url(settings.library_port, url_for("library_page")))
        started = time.perf_counter()
        try:
            stored_videos, library_error = full_library.all(), None
            videos = [dict(video, thumbnail_cached=thumbnails.has_disk_cache(video)) for video in stored_videos]
        except (OSError, ValueError) as exc:
            app.logger.warning("Unable to load fullNew.pls: %s", exc)
            videos, library_error = [], str(exc)
        response = render_template("library.html", videos=videos, library_error=library_error)
        elapsed_ms = (time.perf_counter() - started) * 1000
        app.logger.info("PLS library response: %d items, %d cached thumbnails, %.1f ms", len(videos), sum(video.get("thumbnail_cached", False) for video in videos), elapsed_ms)
        return response

    @app.get("/debrid")
    def debrid():
        try:
            videos, api_error = debrid_client.list_library(), None
        except DebridError as exc:
            app.logger.warning("Real-Debrid listing failed: %s", exc)
            videos, api_error = [], str(exc)
        return render_template("debrid.html", videos=videos, api_error=api_error)

    def heresphere_response(payload: dict):
        response = jsonify(payload)
        response.headers["HereSphere-JSON-Version"] = "1"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.route("/heresphere", methods=["GET", "POST"])
    @app.route("/heresphere/", methods=["GET", "POST"])
    def heresphere_library():
        if listener_collection() == LIBRARY_COLLECTION:
            return library_heresphere()
        try:
            videos = playlist.all()
        except (OSError, ValueError) as exc:
            app.logger.exception("Unable to build HereSphere library")
            abort(503, str(exc))
        app.logger.info("HereSphere collection: Favorites")
        return heresphere_response({
            "access": 1,
            "library": [{
                "name": "Favorites",
                "list": [url_for("heresphere_video", item_id=video["id"], _external=True) for video in videos],
            }],
        })

    @app.route("/library/heresphere", methods=["GET", "POST"])
    @app.route("/library/heresphere/", methods=["GET", "POST"])
    def library_heresphere():
        try:
            videos = full_library.all()
        except (OSError, ValueError) as exc:
            app.logger.exception("Unable to build HereSphere Library from fullNew.pls")
            abort(503, str(exc))
        app.logger.info("HereSphere collection: Library")
        return heresphere_response({
            "access": 1,
            "library": [{
                "name": "Library",
                "list": [url_for("library_heresphere_video", item_id=video["id"], _external=True) for video in videos],
            }],
        })

    @app.route("/heresphere/video/<int:item_id>", methods=["GET", "POST"])
    def heresphere_video(item_id: int):
        video = playlist.get(item_id)
        if video is None:
            abort(404, "Video not found")
        media_url = str(video.get("url", ""))
        if thumbnails.is_remote(media_url):
            source_url = media_url
        else:
            filename = thumbnails.local_filename(video)
            if not filename:
                abort(404, "Local media filename is invalid")
            source_url = url_for("smb", filename=filename, _external=True)
        request_data = request.get_json(silent=True) or {}
        if request_data.get("needsMediaSource", True):
            app.logger.info("Clicked video: %s", video.get("title") or f"item {item_id}")
        tags = [{"name": f"Performer:{name}"} for name in video.get("stars", []) if name]
        return heresphere_response({
            "access": 1,
            "title": video.get("title") or f"Video {item_id}",
            "description": video.get("title") or "",
            "thumbnailImage": url_for("thumbnail", item_id=item_id, _external=True),
            "tags": tags,
            "media": [{"name": "Direct", "sources": [{"url": source_url}]}],
            "isFavorite": False,
            "writeFavorite": False,
            "writeRating": False,
            "writeTags": False,
            "writeHSP": False,
        })

    @app.route("/library/heresphere/video/<int:item_id>", methods=["GET", "POST"])
    def library_heresphere_video(item_id: int):
        try:
            video = full_library.get(item_id)
        except (OSError, ValueError) as exc:
            abort(503, str(exc))
        if video is None:
            abort(404, "Library video not found")
        source_url = (
            str(video["url"]) if thumbnails.is_remote(str(video["url"]))
            else url_for("library_stream", item_id=item_id, _external=True)
        )
        request_data = request.get_json(silent=True) or {}
        if request_data.get("needsMediaSource", True):
            app.logger.info("Clicked video: %s", video["title"])
        tags = [] if video["available"] else [{"name": "Status:Unavailable"}]
        return heresphere_response({
            "access": 1,
            "title": video["title"],
            "description": video.get("missing_reason") or video["title"],
            "thumbnailImage": url_for("library_thumbnail", item_id=item_id, _external=True),
            "tags": tags,
            "media": [{"name": "Direct", "sources": [{"url": source_url}]}],
            "isFavorite": False,
            "writeFavorite": False,
            "writeRating": False,
            "writeTags": False,
            "writeHSP": False,
        })

    @app.get("/debrid/group/<int:group_id>")
    def debrid_group(group_id: int):
        try:
            group = debrid_client.get_group(group_id)
        except IndexError:
            abort(404, "Video group not found")
        return render_template("debrid_children.html", group=group)

    @app.get("/debrid/play/<int:item_id>")
    def debrid_play(item_id: int):
        try:
            item = debrid_client.get_torrent(item_id)
            app.logger.info("Clicked video: %s", item["filename"])
            return redirect(debrid_client.unrestrict(item["rd_link"]))
        except IndexError:
            abort(404, "Video not found")
        except DebridError as exc:
            abort(502, str(exc))

    @app.get("/play/<int:item_id>")
    def play(item_id: int):
        video = playlist.get(item_id)
        if video is None:
            abort(404, "Video not found")
        app.logger.info("Clicked video: %s", video.get("title") or f"item {item_id}")
        media_url = str(video.get("url", ""))
        if thumbnails.is_remote(media_url):
            return redirect(media_url, code=302)
        filename = thumbnails.local_filename(video)
        if not filename:
            abort(404, "Local media filename is invalid")
        return redirect(url_for("smb", filename=filename))

    @app.get("/library/play/<int:item_id>")
    def library_play(item_id: int):
        try:
            video = full_library.get(item_id)
        except (OSError, ValueError) as exc:
            abort(503, str(exc))
        if video is None:
            abort(404, "Library video not found")
        app.logger.info("Clicked video: %s", video["title"])
        if thumbnails.is_remote(str(video["url"])):
            return redirect(str(video["url"]), code=302)
        return redirect(url_for("library_stream", item_id=item_id))

    @app.get("/library/stream/<int:item_id>")
    def library_stream(item_id: int):
        try:
            video = full_library.get(item_id)
        except (OSError, ValueError) as exc:
            abort(503, str(exc))
        if video is None:
            abort(404, "Library video not found")
        if thumbnails.is_remote(str(video["url"])):
            return redirect(str(video["url"]), code=302)
        source_path = video.get("source_path")
        if not source_path:
            abort(404, "Library file has no usable path")
        try:
            file_status = os.stat(source_path)
        except (FileNotFoundError, NotADirectoryError):
            app.logger.warning("Library file is missing: %s", source_path)
            abort(404, "Library file is missing or unavailable")
        except PermissionError as exc:
            app.logger.error("Library file is inaccessible: %s", exc)
            abort(503, "Library file is inaccessible")
        except OSError as exc:
            app.logger.error("Library file access failed: %s", exc)
            abort(503, "Library file access failed")
        if not stat.S_ISREG(file_status.st_mode):
            abort(404, "Library file is missing or unavailable")
        return send_file(source_path, conditional=True)

    @app.get("/smb/<path:filename>")
    def smb(filename: str):
        app.logger.info("SMB request: %s", filename)
        if settings.smb_folder is None:
            app.logger.error("SMB share is unavailable: SMB_FOLDER is not configured")
            abort(503, "SMB_FOLDER is not configured")
        resolved = safe_join(str(settings.smb_folder), filename)
        if resolved is None:
            app.logger.warning("SMB path rejected because it is outside SMB_FOLDER: %s", filename)
            abort(404, "Invalid SMB media path")
        app.logger.info("SMB resolved path: %s", resolved)
        try:
            file_status = os.stat(resolved)
        except (FileNotFoundError, NotADirectoryError):
            try:
                os.stat(settings.smb_folder)
            except OSError as root_error:
                app.logger.error("SMB share is inaccessible: %s", root_error)
                abort(503, "SMB share is inaccessible")
            app.logger.warning("SMB file not found: %s", filename)
            abort(404, "SMB file not found")
        except PermissionError as exc:
            app.logger.error("SMB share or file is inaccessible: %s", exc)
            abort(503, "SMB share or file is inaccessible")
        except OSError as exc:
            if getattr(exc, "winerror", None) == 123:
                app.logger.warning("SMB path is not a valid Windows filename: %s", filename)
                abort(404, "Invalid SMB media path")
            app.logger.error("SMB share access failed: %s", exc)
            abort(503, "SMB share access failed")
        if not stat.S_ISREG(file_status.st_mode):
            app.logger.warning("SMB path is not a regular file: %s", filename)
            abort(404, "SMB file not found")
        return send_file(resolved, conditional=True)

    @app.post("/bookmark/<int:item_id>")
    def bookmark(item_id: int):
        try:
            video = debrid_client.get_torrent(item_id)
            bookmark_url = debrid_client.custom_download_url(
                settings.real_debrid_custom_id, video.get("torrent_filename", video["filename"]),
                video.get("bookmark_path", video["filename"]),
            )
            added = playlist.add_bookmark(video["filename"], bookmark_url)
            flash("Added to Favorites." if added else "Already in Favorites.", "success")
        except IndexError:
            abort(404, "Video not found")
        except (DebridError, OSError, ValueError) as exc:
            app.logger.warning("Bookmark failed: %s", exc)
            flash(str(exc), "error")
        return redirect(url_for("debrid"))

    @app.post("/favorites/<int:item_id>/remove")
    def remove_favorite(item_id: int):
        try:
            removed = playlist.remove(item_id)
            if removed is None:
                flash("Favorite was not found; nothing was removed.", "error")
            else:
                flash(f'Removed "{removed.get("title") or "item"}" from Favorites.', "success")
        except (OSError, ValueError) as exc:
            app.logger.exception("Unable to remove favorite %s", item_id)
            flash(f"Could not remove Favorite: {exc}", "error")
        return redirect(url_for("home"))

    @app.get("/thumbnail/<int:item_id>")
    def thumbnail(item_id: int):
        video = playlist.get(item_id)
        if video is None:
            abort(404)
        image = thumbnails.thumbnail_for(video)
        if image is None:
            response = send_from_directory(app.static_folder, "img/placeholder.svg")
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Thumbnail-State"] = thumbnails.status_for(video)["state"]
            return response
        return send_file(image, conditional=True, max_age=86400)

    @app.get("/library/thumbnail/<int:item_id>")
    def library_thumbnail(item_id: int):
        try:
            video = full_library.get(item_id)
        except (OSError, ValueError):
            video = None
        if video is None:
            abort(404)
        image = thumbnails.thumbnail_for(video)
        if image is None:
            response = send_from_directory(app.static_folder, "img/placeholder.svg")
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Thumbnail-State"] = thumbnails.status_for(video)["state"]
            return response
        return send_file(image, conditional=True, max_age=86400)

    @app.get("/thumbnail/<int:item_id>/status")
    def thumbnail_status(item_id: int):
        video = playlist.get(item_id)
        if video is None:
            abort(404)
        thumbnails.record_status_poll(1)
        return thumbnails.status_for(video)

    @app.get("/library/thumbnail/<int:item_id>/status")
    def library_thumbnail_status(item_id: int):
        try:
            video = full_library.get(item_id)
        except (OSError, ValueError):
            video = None
        if video is None:
            abort(404)
        thumbnails.record_status_poll(1)
        return thumbnails.status_for(video)

    @app.get("/thumbnails/status")
    def thumbnail_status_batch():
        requested_ids = []
        for value in request.args.get("ids", "").split(",")[:200]:
            try:
                requested_ids.append(int(value))
            except ValueError:
                continue
        videos = {video.get("id"): video for video in playlist.all()}
        statuses = {str(item_id): thumbnails.status_for(videos[item_id]) for item_id in requested_ids if item_id in videos}
        thumbnails.record_status_poll(len(statuses))
        return {"items": statuses}

    @app.get("/library/thumbnails/status")
    def library_thumbnail_status_batch():
        requested_ids = []
        for value in request.args.get("ids", "").split(",")[:200]:
            try:
                requested_ids.append(int(value))
            except ValueError:
                continue
        try:
            videos = {video["id"]: video for video in full_library.all()}
        except (OSError, ValueError):
            videos = {}
        statuses = {str(item_id): thumbnails.status_for(videos[item_id]) for item_id in requested_ids if item_id in videos}
        thumbnails.record_status_poll(len(statuses))
        return {"items": statuses}

    @app.get("/health")
    def health():
        return {"status": "ok", "items": playlist.count(), "thumbnails": thumbnails.metrics()}

    @app.errorhandler(404)
    @app.errorhandler(500)
    @app.errorhandler(502)
    @app.errorhandler(503)
    def http_error(error):
        if request.path.startswith(("/thumbnail/", "/library/thumbnail/")):
            return send_from_directory(app.static_folder, "img/placeholder.svg")
        return render_template("error.html", error=error), getattr(error, "code", 500)

    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    settings = AppConfig.load()
    servers = HereSphereServerGroup(
        create_app(settings), settings.host, settings.port, settings.library_port,
    )
    servers.start()
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        servers.stop()
