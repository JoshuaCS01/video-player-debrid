from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any


class PlaylistStore:
    """Thread-safe JSON playlist storage with atomic writes."""

    def __init__(self, path: Path):
        self.path, self._lock = Path(path), threading.RLock()

    def all(self) -> list[dict[str, Any]]:
        with self._lock, self.path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, list):
            raise ValueError("playlist.json must contain a JSON array")
        return data

    def count(self) -> int:
        try:
            return len(self.all())
        except (OSError, ValueError):
            return 0

    def get(self, item_id: int) -> dict[str, Any] | None:
        return next((item for item in self.all() if item.get("id") == item_id), None)

    def add_bookmark(self, title: str, url: str) -> bool:
        with self._lock:
            items = self.all()
            if any(item.get("url") == url for item in items):
                return False
            new_id = max((int(item.get("id", 0)) for item in items), default=0) + 1
            items.append({"id": new_id, "title": title, "url": url, "stars": []})
            self._write(items)
            return True

    def remove(self, item_id: int) -> dict[str, Any] | None:
        """Remove one saved item by ID while preserving all remaining order."""
        with self._lock:
            items = self.all()
            removed = next((item for item in items if item.get("id") == item_id), None)
            if removed is None:
                return None
            self._write([item for item in items if item.get("id") != item_id])
            return removed

    def _write(self, items: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="playlist-", suffix=".json", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(items, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
            os.replace(temporary, self.path)
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
