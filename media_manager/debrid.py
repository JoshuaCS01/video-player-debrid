from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import requests


LOGGER = logging.getLogger(__name__)


class DebridError(RuntimeError):
    pass


class RealDebridClient:
    API_ROOT = "https://api.real-debrid.com/rest/1.0"
    VIDEO_EXTENSIONS = {
        ".3gp", ".avi", ".flv", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg",
        ".mpg", ".mts", ".mxf", ".ogv", ".ts", ".vob", ".webm", ".wmv",
    }
    DETAIL_WORKERS = 4

    def __init__(self, token: str | None, cache_seconds: int = 60, details_cache_path: Path | None = None):
        self.token, self.cache_seconds = token, cache_seconds
        self.details_cache_path = Path(details_cache_path) if details_cache_path else None
        self._items: list[dict] = []
        self._groups: list[dict] = []
        self._loaded_at = 0.0
        self._session = requests.Session()
        self._lock = threading.RLock()
        self._details_cache = self._load_details_cache()
        self.last_metrics: dict[str, float | int] = {}

    @property
    def headers(self) -> dict[str, str]:
        if not self.token:
            raise DebridError("REAL_DEBRID_TOKEN is not configured")
        return {"Authorization": f"Bearer {self.token}"}

    def list_torrents(self, force: bool = False) -> list[dict]:
        with self._lock:
            if not force and self._items and time.monotonic() - self._loaded_at < self.cache_seconds:
                return self._items

        total_started = time.perf_counter()
        fetch_started = time.perf_counter()
        try:
            response = self._session.get(f"{self.API_ROOT}/torrents", headers=self.headers, params={"limit": 100}, timeout=(5, 20))
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise DebridError(f"Unable to load Real-Debrid torrents: {exc}") from exc
        if not isinstance(payload, list):
            raise DebridError("Real-Debrid returned an unexpected response")
        fetch_ms = (time.perf_counter() - fetch_started) * 1000

        details_started = time.perf_counter()
        details, cache_hits, fetched = self._details_for(payload)
        details_ms = (time.perf_counter() - details_started) * 1000
        filter_started = time.perf_counter()
        normalized: list[dict] = []
        groups: list[dict] = []
        for torrent in payload:
            detail = details.get(str(torrent.get("id")))
            if detail:
                entries = self._video_entries(detail, len(normalized))
                normalized.extend(entries)
                if len(entries) == 1:
                    groups.append({**entries[0], "kind": "video"})
                elif len(entries) > 1:
                    title = str(detail.get("filename") or "Untitled")
                    groups.append({
                        "id": len(groups),
                        "kind": "group",
                        "filename": title,
                        "torrent_id": detail.get("id"),
                        "video_count": len(entries),
                        "videos": entries,
                        "search_text": title,
                    })
        filter_ms = (time.perf_counter() - filter_started) * 1000

        with self._lock:
            self._items = normalized
            self._groups = groups
            self._loaded_at = time.monotonic()
            self.last_metrics = {
                "torrents": len(payload), "videos": len(normalized), "display_rows": len(groups), "detail_cache_hits": cache_hits,
                "detail_requests": fetched, "fetch_ms": round(fetch_ms, 1), "details_ms": round(details_ms, 1),
                "filter_ms": round(filter_ms, 1), "total_ms": round((time.perf_counter() - total_started) * 1000, 1),
            }
        LOGGER.info(
            "Real-Debrid library: %d torrents -> %d videos (list %.0f ms, details %.0f ms, filter %.0f ms, detail requests %d)",
            len(payload), len(normalized), fetch_ms, details_ms, filter_ms, fetched,
        )
        return normalized

    def list_library(self, force: bool = False) -> list[dict]:
        """Return single videos and parent-torrent groups for presentation."""
        self.list_torrents(force=force)
        with self._lock:
            return self._groups

    def get_group(self, group_id: int) -> dict:
        with self._lock:
            groups = self._groups
        if not groups:
            groups = self.list_library()
        if group_id < 0 or group_id >= len(groups) or groups[group_id].get("kind") != "group":
            raise IndexError(group_id)
        return groups[group_id]

    @classmethod
    def is_video_filename(cls, filename: str) -> bool:
        suffix = PurePosixPath(str(filename).strip().replace("\\", "/")).suffix.lower()
        return suffix in cls.VIDEO_EXTENSIONS

    @classmethod
    def is_rar_filename(cls, filename: str) -> bool:
        suffix = PurePosixPath(str(filename).strip().replace("\\", "/")).suffix.lower()
        return suffix == ".rar" or (len(suffix) == 4 and suffix[1] == "r" and suffix[2:].isdigit())

    @classmethod
    def _video_entries(cls, detail: dict, first_id: int) -> list[dict]:
        files = detail.get("files") or []
        links = detail.get("links") or []
        selected = [file for file in files if int(file.get("selected", 1) or 0) == 1]
        playable = [(index, file) for index, file in enumerate(selected) if cls.is_video_filename(file.get("path", ""))]
        entries = []
        for selected_index, file in playable:
            link = links[selected_index] if selected_index < len(links) else ""
            if not link and len(playable) == 1 and links:
                link = links[0]
            if not link:
                LOGGER.debug("Skipping playable file without a corresponding link in torrent %s", detail.get("id"))
                continue
            path = str(file.get("path") or "").replace("\\", "/")
            entries.append({
                "id": first_id + len(entries),
                "filename": PurePosixPath(path).name or str(detail.get("filename") or "Untitled"),
                "torrent_filename": str(detail.get("filename") or "Untitled"),
                "rd_link": link,
                "torrent_id": detail.get("id"),
                "file_id": file.get("id"),
                "path": path,
                "bookmark_path": path.lstrip("/"),
            })
        return entries

    def _details_for(self, torrents: list[dict]) -> tuple[dict[str, dict], int, int]:
        results: dict[str, dict] = {}
        missing: list[tuple[dict, str]] = []
        cache_hits = 0
        for torrent in torrents:
            torrent_id = str(torrent.get("id"))
            fingerprint = self._fingerprint(torrent)
            if torrent.get("files") is not None:
                results[torrent_id] = torrent
                continue
            cached = self._details_cache.get(torrent_id)
            if cached and cached.get("fingerprint") == fingerprint:
                results[torrent_id] = cached["detail"]
                cache_hits += 1
            else:
                missing.append((torrent, fingerprint))

        changed = False
        if missing:
            with ThreadPoolExecutor(max_workers=self.DETAIL_WORKERS, thread_name_prefix="debrid-detail") as pool:
                futures = {pool.submit(self._fetch_detail, str(torrent.get("id"))): (torrent, fingerprint) for torrent, fingerprint in missing}
                for future in as_completed(futures):
                    torrent, fingerprint = futures[future]
                    torrent_id = str(torrent.get("id"))
                    try:
                        detail = future.result()
                    except DebridError as exc:
                        stale = self._details_cache.get(torrent_id)
                        if stale:
                            results[torrent_id] = stale["detail"]
                            LOGGER.warning("Using cached Real-Debrid details after refresh failed for torrent %s", torrent_id)
                        else:
                            LOGGER.warning("Skipping unverified Real-Debrid torrent %s: %s", torrent_id, exc)
                        continue
                    results[torrent_id] = detail
                    self._details_cache[torrent_id] = {"fingerprint": fingerprint, "detail": detail}
                    changed = True
        if changed:
            self._write_details_cache()
        return results, cache_hits, len(missing)

    def _fetch_detail(self, torrent_id: str) -> dict:
        try:
            with requests.Session() as session:
                response = session.get(f"{self.API_ROOT}/torrents/info/{torrent_id}", headers=self.headers, timeout=(5, 20))
                response.raise_for_status()
                detail = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise DebridError(f"detail request failed: {exc}") from exc
        if not isinstance(detail, dict):
            raise DebridError("detail response was invalid")
        return detail

    @staticmethod
    def _fingerprint(torrent: dict) -> str:
        return "|".join(str(torrent.get(field, "")) for field in ("id", "hash", "status", "ended", "bytes", "progress"))

    def _load_details_cache(self) -> dict[str, dict]:
        if not self.details_cache_path or not self.details_cache_path.is_file():
            return {}
        try:
            data = json.loads(self.details_cache_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            LOGGER.warning("Real-Debrid detail cache could not be read; it will be rebuilt")
            return {}

    def _write_details_cache(self) -> None:
        if not self.details_cache_path:
            return
        self.details_cache_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="debrid-details-", suffix=".json", dir=self.details_cache_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self._details_cache, handle, separators=(",", ":"), ensure_ascii=False)
            os.replace(temporary, self.details_cache_path)
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    def get_torrent(self, item_id: int) -> dict:
        items = self.list_torrents()
        if item_id < 0 or item_id >= len(items):
            raise IndexError(item_id)
        if not items[item_id]["rd_link"]:
            raise DebridError("This video does not contain a playable link")
        return items[item_id]

    def unrestrict(self, link: str) -> str:
        try:
            response = self._session.post(f"{self.API_ROOT}/unrestrict/link", headers=self.headers,
                                          data={"link": link, "remote": 0}, timeout=(5, 20))
            response.raise_for_status()
            download = response.json().get("download")
        except (requests.RequestException, ValueError) as exc:
            raise DebridError(f"Unable to prepare this stream: {exc}") from exc
        if not download:
            raise DebridError("Real-Debrid did not return a download URL")
        return download

    @staticmethod
    def custom_download_url(custom_id: str | None, torrent_filename: str, filename: str | None = None) -> str:
        if not custom_id:
            raise DebridError("REAL_DEBRID_CUSTOM_ID is not configured")
        torrent = quote(torrent_filename, safe="")
        selected = quote(filename or torrent_filename, safe="/")
        return f"https://my.real-debrid.com/{quote(custom_id, safe='')}/torrents/{torrent}/{selected}"
