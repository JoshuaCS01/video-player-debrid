from __future__ import annotations

import socket
import threading
from collections.abc import Callable

from werkzeug.serving import make_server


HERESPHERE_COLLECTION_ENV = "home_media_manager.heresphere_collection"
FAVORITES_COLLECTION = "favorites"
LIBRARY_COLLECTION = "library"
_COLLECTIONS = {FAVORITES_COLLECTION, LIBRARY_COLLECTION}


class ListenerApplication:
    """Attach an immutable HereSphere identity to one WSGI listener."""

    def __init__(self, application, collection: str):
        if collection not in _COLLECTIONS:
            raise ValueError(f"Unknown HereSphere collection: {collection}")
        self.application = application
        self.collection = collection

    def __call__(self, environ, start_response):
        environ[HERESPHERE_COLLECTION_ENV] = self.collection
        return self.application(environ, start_response)


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

    def __init__(
        self,
        app,
        host: str,
        port: int,
        on_error: Callable[[Exception], None] | None = None,
        collection: str | None = None,
    ):
        self.application = app
        self.app = ListenerApplication(app, collection) if collection else app
        self.host, self.port, self.on_error = host, port, on_error
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
            self._thread = threading.Thread(target=self._serve, name=f"media-server-{self.port}", daemon=True)
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
            shutdown_error = None
            try:
                server.shutdown()
            except Exception as exc:
                shutdown_error = exc
            finally:
                server.server_close()
            if thread and thread is not threading.current_thread() and thread.is_alive():
                thread.join(timeout=5)
            if thread and thread.is_alive():
                raise RuntimeError(f"Server listener on port {self.port} did not stop")
            self._server = self._thread = None
            if shutdown_error is not None:
                raise shutdown_error


class HereSphereServerGroup:
    """Run Favorites and Library listeners in one process over one Flask app."""

    def __init__(
        self,
        app,
        host: str,
        favorites_port: int,
        library_port: int,
        on_error: Callable[[Exception], None] | None = None,
    ):
        if favorites_port == library_port:
            raise ValueError("Favorites and Library ports must be different")
        self.application = app
        self.favorites = ServerController(
            app, host, favorites_port, on_error, FAVORITES_COLLECTION,
        )
        self.library = ServerController(
            app, host, library_port, on_error, LIBRARY_COLLECTION,
        )

    @property
    def running(self) -> bool:
        return self.favorites.running and self.library.running

    def start(self) -> None:
        self.favorites.start()
        try:
            self.library.start()
        except Exception:
            self.favorites.stop()
            raise

    def stop(self) -> None:
        errors = []
        for server in (self.library, self.favorites):
            try:
                server.stop()
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise errors[0]
