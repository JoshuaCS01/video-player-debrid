from __future__ import annotations

import socket
import threading
from collections.abc import Callable

from werkzeug.serving import make_server


def lan_address() -> str:
    """Return the most useful LAN address without sending network traffic."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 80))
        return sock.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        sock.close()


class ServerController:
    """Own a stoppable Werkzeug server on a background thread."""

    def __init__(self, app, host: str, port: int, on_error: Callable[[Exception], None] | None = None):
        self.app, self.host, self.port, self.on_error = app, host, port, on_error
        self._server = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        with self._lock:
            if self.running:
                return
            try:
                self._server = make_server(self.host, self.port, self.app, threaded=True)
            except Exception as exc:
                if self.on_error:
                    self.on_error(exc)
                raise
            self._thread = threading.Thread(target=self._serve, name="media-server", daemon=True)
            self._thread.start()

    def _serve(self) -> None:
        try:
            assert self._server is not None
            self._server.serve_forever()
        except Exception as exc:
            if self.on_error:
                self.on_error(exc)

    def stop(self) -> None:
        with self._lock:
            server, thread = self._server, self._thread
            if server is None:
                return
            server.shutdown()
            server.server_close()
            if thread and thread is not threading.current_thread():
                thread.join(timeout=5)
            self._server = self._thread = None
