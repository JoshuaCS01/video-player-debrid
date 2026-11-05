from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path
from typing import Protocol


class CredentialStore(Protocol):
    def load(self) -> dict[str, str]: ...
    def save(self, values: dict[str, str]) -> None: ...


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


class DpapiCredentialStore:
    """Persist a small credential payload encrypted for the current Windows user."""

    _DESCRIPTION = "Home Media Manager credentials"

    def __init__(self, path: Path):
        if os.name != "nt":
            raise RuntimeError("Windows DPAPI is unavailable on this platform")
        self.path = Path(path)
        self.crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.crypt32.CryptProtectData.argtypes = [
            ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.POINTER(_DataBlob),
            wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(_DataBlob),
        ]
        self.crypt32.CryptProtectData.restype = wintypes.BOOL
        self.crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(_DataBlob), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_DataBlob),
            wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(_DataBlob),
        ]
        self.crypt32.CryptUnprotectData.restype = wintypes.BOOL
        self.kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
        self.kernel32.LocalFree.restype = wintypes.HLOCAL

    @staticmethod
    def _blob(payload: bytes):
        buffer = ctypes.create_string_buffer(payload)
        return _DataBlob(len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer

    def _protect(self, payload: bytes) -> bytes:
        source, keepalive = self._blob(payload)
        output = _DataBlob()
        if not self.crypt32.CryptProtectData(
            ctypes.byref(source), self._DESCRIPTION, None, None, None, 0, ctypes.byref(output),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            self.kernel32.LocalFree(ctypes.cast(output.pbData, wintypes.HLOCAL))

    def _unprotect(self, payload: bytes) -> bytes:
        source, keepalive = self._blob(payload)
        output = _DataBlob()
        description = wintypes.LPWSTR()
        if not self.crypt32.CryptUnprotectData(
            ctypes.byref(source), ctypes.byref(description), None, None, None, 0, ctypes.byref(output),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            if description:
                self.kernel32.LocalFree(ctypes.cast(description, wintypes.HLOCAL))
            self.kernel32.LocalFree(ctypes.cast(output.pbData, wintypes.HLOCAL))

    def load(self) -> dict[str, str]:
        import json

        if not self.path.exists():
            return {}
        payload = json.loads(self._unprotect(self.path.read_bytes()).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Credential payload is invalid")
        return {str(key): str(value) for key, value in payload.items() if value is not None}

    def save(self, values: dict[str, str]) -> None:
        import json
        import os as os_module
        import tempfile

        self.path.parent.mkdir(parents=True, exist_ok=True)
        encrypted = self._protect(json.dumps(values, ensure_ascii=False).encode("utf-8"))
        descriptor, temporary = tempfile.mkstemp(prefix="credentials-", suffix=".tmp", dir=self.path.parent)
        try:
            with os_module.fdopen(descriptor, "wb") as handle:
                handle.write(encrypted)
            os_module.replace(temporary, self.path)
        except Exception:
            try:
                os_module.unlink(temporary)
            except OSError:
                pass
            raise


class MemoryCredentialStore:
    """Injectable non-persistent store used by tests and future non-Windows frontends."""

    def __init__(self, values: dict[str, str] | None = None):
        self.values = dict(values or {})

    def load(self) -> dict[str, str]:
        return dict(self.values)

    def save(self, values: dict[str, str]) -> None:
        self.values = dict(values)
