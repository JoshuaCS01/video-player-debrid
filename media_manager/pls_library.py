from __future__ import annotations

import re
import threading
from pathlib import Path
from urllib.parse import unquote, urlparse


class PLSLibrary:
    """Read an ordered media library from FileN entries in a PLS playlist."""

    _ENTRY_PATTERN = re.compile(r"^(file|title)(\d+)$", re.IGNORECASE)

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._signature: tuple[int, int] | None = None
        self._items: list[dict] = []

    def all(self) -> list[dict]:
        with self._lock:
            file_status = self.path.stat()
            signature = (file_status.st_mtime_ns, file_status.st_size)
            if signature != self._signature:
                self._items = self._parse(self.path)
                self._signature = signature
            return [dict(item) for item in self._items]

    def get(self, item_id: int) -> dict | None:
        return next((item for item in self.all() if item["id"] == item_id), None)

    @classmethod
    def _parse(cls, path: Path) -> list[dict]:
        text = cls._decode(path.read_bytes())
        files: dict[int, str] = {}
        titles: dict[int, str] = {}
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith((";", "#", "[")) or "=" not in line:
                continue
            key, value = (part.strip() for part in line.split("=", 1))
            match = cls._ENTRY_PATTERN.fullmatch(key)
            if not match:
                continue
            entry_type, index = match.group(1).lower(), int(match.group(2))
            if entry_type == "file":
                files[index] = value.strip().strip('"')
            else:
                titles[index] = value

        items: list[dict] = []
        for index in sorted(files):
            raw_source = files[index]
            source, is_remote = cls._normalize_source(raw_source, path.parent)
            local_path = None if is_remote else Path(source)
            available = True if is_remote else local_path.is_file()
            fallback_title = Path(urlparse(source).path).name if is_remote else local_path.name
            items.append({
                "id": index,
                "title": titles.get(index) or unquote(fallback_title) or f"Library item {index}",
                "url": source,
                "source_path": None if is_remote else str(local_path),
                "stars": [],
                "available": available,
                "missing_reason": None if available else "File is missing or unavailable",
            })
        return items

    @staticmethod
    def _decode(payload: bytes) -> str:
        for encoding in ("utf-8-sig", "utf-16", "cp1252"):
            try:
                return payload.decode(encoding)
            except UnicodeDecodeError:
                continue
        return payload.decode("utf-8", errors="replace")

    @staticmethod
    def _normalize_source(value: str, playlist_dir: Path) -> tuple[str, bool]:
        parsed = urlparse(value)
        if parsed.scheme.lower() in {"http", "https"}:
            return value, True
        if parsed.scheme.lower() == "file":
            decoded = unquote(parsed.path)
            if parsed.netloc:
                decoded = f"//{parsed.netloc}{decoded}"
            elif re.match(r"^/[A-Za-z]:/", decoded):
                decoded = decoded[1:]
            return str(Path(decoded)), False
        source = Path(value)
        if not source.is_absolute():
            source = playlist_dir / source
        return str(source), False
