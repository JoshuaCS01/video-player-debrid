from __future__ import annotations

import ctypes
import logging
import os
import threading
from collections.abc import Callable
from ctypes import wintypes


ERROR_ALREADY_EXISTS = 183
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
ASFW_ANY = -1


class _WindowsKernelObjects:
    def __init__(self):
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel32.CreateEventW.restype = wintypes.HANDLE
        self.kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel32.CreateMutexW.restype = wintypes.HANDLE
        self.kernel32.SetEvent.argtypes = [wintypes.HANDLE]
        self.kernel32.SetEvent.restype = wintypes.BOOL
        self.kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel32.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL
        self.user32.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
        self.user32.AllowSetForegroundWindow.restype = wintypes.BOOL

    def create_event(self, name: str):
        handle = self.kernel32.CreateEventW(None, False, False, name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        return handle

    def create_mutex(self, name: str):
        ctypes.set_last_error(0)
        handle = self.kernel32.CreateMutexW(None, False, name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        return handle, ctypes.get_last_error() == ERROR_ALREADY_EXISTS

    def set_event(self, handle) -> None:
        if not self.kernel32.SetEvent(handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def wait(self, handle, timeout_ms: int) -> int:
        return int(self.kernel32.WaitForSingleObject(handle, timeout_ms))

    def close(self, handle) -> None:
        if handle:
            self.kernel32.CloseHandle(handle)

    def allow_foreground_restore(self) -> None:
        self.user32.AllowSetForegroundWindow(wintypes.DWORD(ASFW_ANY))


class SingleInstance:
    """Own a per-session mutex and receive restore requests through a named event."""

    def __init__(
        self,
        mutex_name: str = r"Local\HomeMediaManager.Singleton.v1",
        event_name: str = r"Local\HomeMediaManager.Restore.v1",
        backend=None,
    ):
        self.mutex_name = mutex_name
        self.event_name = event_name
        self._backend = backend if backend is not None else (_WindowsKernelObjects() if os.name == "nt" else None)
        self._mutex = None
        self._event = None
        self._monitor: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    @property
    def acquired(self) -> bool:
        return self._backend is None or self._mutex is not None

    def acquire(self) -> bool:
        if self._backend is None:
            return True
        with self._lock:
            if self._mutex is not None:
                return True
            event = self._backend.create_event(self.event_name)
            try:
                mutex, already_exists = self._backend.create_mutex(self.mutex_name)
            except Exception:
                self._backend.close(event)
                raise
            if already_exists:
                try:
                    self._backend.allow_foreground_restore()
                    self._backend.set_event(event)
                finally:
                    self._backend.close(mutex)
                    self._backend.close(event)
                return False
            self._event, self._mutex = event, mutex
            return True

    def start_restore_monitor(self, callback: Callable[[], None]) -> None:
        if self._backend is None:
            return
        with self._lock:
            if self._mutex is None or self._event is None:
                raise RuntimeError("Single-instance ownership must be acquired before monitoring")
            if self._monitor and self._monitor.is_alive():
                return
            event = self._event
            self._stop.clear()

            def monitor() -> None:
                while not self._stop.is_set():
                    result = self._backend.wait(event, 250)
                    if self._stop.is_set():
                        return
                    if result == WAIT_OBJECT_0:
                        try:
                            callback()
                        except Exception:
                            logging.exception("Unable to dispatch single-instance restore request")
                    elif result != WAIT_TIMEOUT:
                        logging.error("Single-instance restore wait failed with result %s", result)
                        return

            self._monitor = threading.Thread(
                target=monitor, name="single-instance-restore", daemon=True,
            )
            self._monitor.start()

    def close(self) -> None:
        with self._lock:
            event, mutex, monitor = self._event, self._mutex, self._monitor
            if event is None and mutex is None:
                return
            self._stop.set()
            if event is not None:
                self._backend.set_event(event)
        if monitor and monitor is not threading.current_thread() and monitor.is_alive():
            monitor.join(timeout=2)
        if monitor and monitor.is_alive():
            raise RuntimeError("Single-instance restore monitor did not stop")
        with self._lock:
            self._backend.close(event)
            self._backend.close(mutex)
            self._event = self._mutex = self._monitor = None


def bring_window_to_foreground(window_handle: int) -> None:
    if os.name != "nt" or not window_handle:
        return
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.ShowWindow(wintypes.HWND(window_handle), 9)
    user32.SetForegroundWindow(wintypes.HWND(window_handle))
