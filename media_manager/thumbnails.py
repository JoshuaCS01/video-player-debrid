from __future__ import annotations

import hashlib
import logging
import queue
import shutil
import subprocess
import threading
import time
from collections import Counter
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse


LOGGER = logging.getLogger(__name__)


class ThumbnailService:
    """Serve persistent cache hits and generate visible misses in bounded workers."""

    IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
    REAL_DEBRID_HOSTS = {"real-debrid.com", "www.real-debrid.com", "my.real-debrid.com"}
    WORKER_COUNT = 2
    THREADS_PER_PROCESS = 1
    COOLDOWNS = {"timeout": 600, "network": 600, "resource_limit": 900, "invalid_input": 21600, "ffmpeg": 3600}

    def __init__(self, media_folder: Path | None, cache_dir: Path, ffmpeg_path: Path | None = None):
        self.media_folder = Path(media_folder) if media_folder else None
        self.cache_dir = Path(cache_dir)
        self.ffmpeg = str(ffmpeg_path) if ffmpeg_path else shutil.which("ffmpeg")
        self._jobs: queue.Queue[dict] = queue.Queue()
        self._pending: set[str] = set()
        self._ready_paths: dict[str, Path] = {}
        self._disk_cache_keys = self._load_cache_index()
        self._failures: dict[str, tuple[float, str, str]] = {}
        self._counters: Counter[str] = Counter()
        self._active_workers = 0
        self._lock = threading.RLock()
        self._workers_started = False

    @staticmethod
    def is_remote(media_url: str) -> bool:
        return urlparse(media_url).scheme.lower() in {"http", "https"}

    @staticmethod
    def local_filename(video: dict) -> str | None:
        media_url = str(video.get("url", "")).replace("\\", "/")
        if "/VR/" in media_url:
            return media_url.split("/VR/", 1)[1]
        if media_url and not ThumbnailService.is_remote(media_url):
            return PurePosixPath(media_url).name or None
        return None

    def has_disk_cache(self, video: dict) -> bool:
        """Fast local-only cache check suitable for the main library route."""
        with self._lock:
            return self._cache_key(video) in self._disk_cache_keys

    def thumbnail_for(self, video: dict) -> Path | None:
        image = self._existing_thumbnail(video)
        key = self._cache_key(video)
        with self._lock:
            self._counters["cache_hits" if image is not None else "cache_misses"] += 1
            if image is not None:
                self._ready_paths[key] = image
        if image is not None:
            LOGGER.debug("Thumbnail cache hit: %s", self._title(video))
            return image
        self._schedule(video)
        return None

    def status_for(self, video: dict) -> dict[str, str]:
        key = self._cache_key(video)
        disk_cache = self._cache_path(key)
        with self._lock:
            known_ready = self._ready_paths.get(key)
        if self._valid_image(disk_cache) or (known_ready is not None and self._valid_image(known_ready)):
            return {"state": "ready"}
        with self._lock:
            if key in self._pending:
                return {"state": "pending"}
            failure = self._failures.get(key)
        if failure:
            return {"state": "failed", "category": failure[1], "reason": failure[2]}
        return {"state": "missing"}

    def record_status_poll(self, item_count: int) -> None:
        with self._lock:
            self._counters["status_batches"] += 1
            self._counters["status_items"] += item_count

    def metrics(self) -> dict[str, int]:
        with self._lock:
            result = dict(self._counters)
            for name in (
                "cache_hits", "cache_misses", "jobs_queued", "duplicate_skips", "cooldown_skips",
                "generated", "failures", "status_batches", "status_items", "max_active_workers",
            ):
                result.setdefault(name, 0)
            result.update({
                "active_workers": self._active_workers,
                "pending_jobs": len(self._pending),
                "queued_jobs": self._jobs.qsize(),
                "worker_limit": self.WORKER_COUNT,
                "ffmpeg_threads_per_process": self.THREADS_PER_PROCESS,
            })
        return result

    def generate_now(self, video: dict) -> Path | None:
        existing = self._existing_thumbnail(video)
        if existing is not None:
            return existing
        key, title = self._cache_key(video), self._title(video)
        if not self.ffmpeg:
            self._record_failure(key, "ffmpeg", "FFmpeg was not found", title)
            return None

        source, source_kind, error = self._source_for(video)
        if source is None:
            self._record_failure(key, "invalid_input", error or "no usable video source", title)
            return None

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        destination = self._cache_path(key)
        temporary = destination.with_suffix(".tmp.jpg")
        LOGGER.info("Thumbnail generating: %s", title)

        try:
            first = self._run_ffmpeg(source, source_kind, temporary, safe_seek=False, timeout=35)
            if first.returncode != 0 and source_kind == "remote" and self._seek_fallback_warranted(first.stderr):
                temporary.unlink(missing_ok=True)
                LOGGER.debug("Retrying thumbnail with safe seek: %s", title)
                first = self._run_ffmpeg(source, source_kind, temporary, safe_seek=True, timeout=30)
            if first.returncode != 0:
                category, reason = self._classify_failure(first.stderr)
                self._record_failure(key, category, reason, title, first.stderr, source)
                temporary.unlink(missing_ok=True)
                return None
            if not self._valid_image(temporary):
                self._record_failure(key, "ffmpeg", "FFmpeg produced no usable image", title)
                temporary.unlink(missing_ok=True)
                return None
            temporary.replace(destination)
        except subprocess.TimeoutExpired as exc:
            temporary.unlink(missing_ok=True)
            self._record_failure(key, "timeout", f"timeout after {exc.timeout:.0f} seconds", title)
            return None
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            self._record_failure(key, "ffmpeg", f"process error: {exc}", title)
            return None

        with self._lock:
            self._failures.pop(key, None)
            self._ready_paths[key] = destination
            self._disk_cache_keys.add(key)
            self._counters["generated"] += 1
        LOGGER.info("Thumbnail generated: %s", title)
        return destination

    def _run_ffmpeg(self, source: str, source_kind: str, destination: Path, safe_seek: bool, timeout: int):
        command = [
            self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
            "-filter_threads", str(self.THREADS_PER_PROCESS),
        ]
        if source_kind == "remote":
            command.extend([
                "-rw_timeout", "15000000", "-reconnect", "1", "-reconnect_streamed", "1",
                "-reconnect_delay_max", "2", "-user_agent", "HomeMediaManager/0.2",
            ])
        command.extend(["-threads", str(self.THREADS_PER_PROCESS)])
        if not safe_seek:
            command.extend(["-ss", "00:00:10"])
        command.extend(["-i", source])
        if safe_seek:
            command.extend(["-ss", "00:00:10"])
        command.extend([
            "-map", "0:v:0", "-an", "-sn", "-dn", "-frames:v", "1",
            "-vf", "scale=640:-2:flags=fast_bilinear", "-threads", str(self.THREADS_PER_PROCESS),
            "-q:v", "4", "-f", "image2", "-y", str(destination),
        ])
        return subprocess.run(
            command, timeout=timeout, capture_output=True, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    @staticmethod
    def _seek_fallback_warranted(stderr: str) -> bool:
        message = (stderr or "").lower()
        return any(pattern in message for pattern in (
            "moov atom not found", "invalid data found", "received no packets", "nothing was written",
        ))

    @staticmethod
    def _classify_failure(stderr: str) -> tuple[str, str]:
        message = (stderr or "").lower()
        if any(pattern in message for pattern in (
            "resource temporarily unavailable", "cannot allocate memory", "parsed_scale", "could not open encoder",
        )):
            return "resource_limit", "FFmpeg resource limit"
        if any(pattern in message for pattern in (
            "connection", "timed out", "http error", "server returned", "tls", "i/o error", "403 forbidden", "401 unauthorized",
        )):
            return "network", "remote connection failed"
        if any(pattern in message for pattern in (
            "moov atom not found", "invalid data found", "received no packets", "nothing was written", "error opening input",
        )):
            return "invalid_input", "remote input is not a usable video stream"
        return "ffmpeg", "FFmpeg could not decode a frame"

    def _existing_thumbnail(self, video: dict) -> Path | None:
        # Disk cache is intentionally checked first: cache hits never touch the video or network.
        key = self._cache_key(video)
        destination = self._cache_path(key)
        with self._lock:
            indexed = key in self._disk_cache_keys
        if indexed:
            if self._valid_image(destination):
                return destination
            with self._lock:
                self._disk_cache_keys.discard(key)
        source, source_kind, _ = self._source_for(video, require_access=False)
        if source_kind == "local" and source:
            local_source = Path(source)
            if local_source.is_file():
                for suffix in self.IMAGE_SUFFIXES:
                    sidecar = local_source.with_suffix(suffix)
                    if self._valid_image(sidecar):
                        return sidecar
        return None

    def _source_for(self, video: dict, require_access: bool = True) -> tuple[str | None, str, str | None]:
        media_url = str(video.get("url", "")).strip()
        if self.is_remote(media_url):
            host = (urlparse(media_url).hostname or "").lower()
            if host not in self.REAL_DEBRID_HOSTS and not host.endswith(".real-debrid.com"):
                return None, "remote", f"unsupported remote host {host or 'unknown'}"
            return media_url, "remote", None
        if self.media_folder is None:
            return None, "local", "SMB_FOLDER is not configured"
        filename = self.local_filename(video)
        if not filename:
            return None, "local", "playlist entry has no usable local filename"
        relative = Path(filename)
        if relative.is_absolute() or ".." in relative.parts:
            return None, "local", "local filename is outside SMB_FOLDER"
        source = self.media_folder / relative
        if require_access and not source.is_file():
            return None, "local", f"video is missing or inaccessible: {filename}"
        return str(source), "local", None

    def _schedule(self, video: dict) -> None:
        key = self._cache_key(video)
        with self._lock:
            failure = self._failures.get(key)
            if failure and time.monotonic() < failure[0]:
                self._counters["cooldown_skips"] += 1
                return
            if key in self._pending:
                self._counters["duplicate_skips"] += 1
                return
            self._pending.add(key)
            self._counters["jobs_queued"] += 1
            if not self._workers_started:
                self._start_workers()
        self._jobs.put(dict(video))

    def _start_workers(self) -> None:
        self._workers_started = True
        for number in range(self.WORKER_COUNT):
            threading.Thread(target=self._worker, name=f"thumbnail-worker-{number + 1}", daemon=True).start()

    def _worker(self) -> None:
        while True:
            video = self._jobs.get()
            key = self._cache_key(video)
            with self._lock:
                self._active_workers += 1
                self._counters["max_active_workers"] = max(self._counters["max_active_workers"], self._active_workers)
            try:
                self.generate_now(video)
            except Exception:
                LOGGER.exception("Unexpected thumbnail worker error for item %s", video.get("id"))
            finally:
                with self._lock:
                    self._active_workers -= 1
                    self._pending.discard(key)
                self._jobs.task_done()

    def _record_failure(self, key: str, category: str, reason: str, title: str, detail: str = "", source: str = "") -> None:
        retry_at = time.monotonic() + self.COOLDOWNS.get(category, 3600)
        safe_detail = (detail or "").strip()[-1200:]
        if source:
            safe_detail = safe_detail.replace(source, "<Real-Debrid URL>")
        with self._lock:
            self._failures[key] = (retry_at, category, reason)
            self._counters["failures"] += 1
            self._counters[f"failures_{category}"] += 1
        LOGGER.warning("Thumbnail failed: %s - %s", title, reason)
        if safe_detail:
            LOGGER.debug("FFmpeg detail for %s: %s", title, safe_detail)

    @staticmethod
    def _valid_image(path: Path) -> bool:
        try:
            return path.is_file() and path.stat().st_size > 0
        except OSError:
            return False

    def _load_cache_index(self) -> set[str]:
        if not self.cache_dir.is_dir():
            return set()
        return {path.stem for path in self.cache_dir.glob("*.jpg") if self._valid_image(path)}

    @staticmethod
    def _title(video: dict) -> str:
        return str(video.get("title") or video.get("filename") or f"item {video.get('id', '?')}")

    def _cache_key(self, video: dict) -> str:
        # Keep the established identity so all existing successful cache files remain valid.
        identity = f"{video.get('id', '')}|{video.get('url', '')}"
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.jpg"
