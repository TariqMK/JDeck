"""The HTTP server: the page, the JSON API, the Host check and the CSRF header.
Each edition passes its Edition profile. JDeck Server subclasses Handler to add sign-in."""

import json
import socket
import sys
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from core import APP_NAME, __version__
from core.jd_api import (JDError, extract_links, id_list, jd_add_links, jd_call, jd_state, jd_test,
                         normalise_jd_url)
from core.settings import resource_path
from core.updates import RELEASES_PAGE, UpdateError, check_for_update, is_release_url

CSRF_HEADER = "X-JDeck"  # Every POST must carry this header (blocks cross-site requests).
NOT_SET_UP = "Set the JDownloader address in Settings."


def local_host_allowed(host_header, port):
    """The local server only answers requests addressed to itself.
    This blocks DNS-rebinding attacks from websites open in a browser on this PC."""
    return host_header in (f"127.0.0.1:{port}", f"localhost:{port}")


class JDeckHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer that never shares its port.
    On Windows, SO_REUSEADDR lets a second program bind a port that is already in use,
    so a second copy would start without an error. Windows gets SO_EXCLUSIVEADDRUSE instead."""

    daemon_threads = True
    allow_reuse_address = sys.platform != "win32"

    def server_bind(self):
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    # Count open requests, so a clean quit can let them finish first.
    active_requests = 0
    _count_lock = threading.Lock()

    def process_request_thread(self, request, client_address):
        with self._count_lock:
            self.active_requests += 1
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self._count_lock:
                self.active_requests -= 1

    def wait_until_idle(self, timeout):
        """Wait up to `timeout` seconds for open requests to finish. Returns True when none are left."""
        deadline = time.monotonic() + timeout
        while self.active_requests > 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        return self.active_requests == 0


class Handler(BaseHTTPRequestHandler):
    """Serves the page and the JSON API to the app window on 127.0.0.1."""

    server_version = APP_NAME

    def log_message(self, fmt, *args):  # Keep the console quiet.
        pass

    @property
    def settings(self):
        return self.server.settings

    @property
    def edition(self):
        return self.server.edition

    @property
    def is_local(self):
        """True when the request comes from this PC's own window or browser."""
        return True

    # --- helpers

    def send_json(self, data, status=HTTPStatus.OK, headers=None):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, message, status=HTTPStatus.BAD_REQUEST, **extra):
        self.send_json({"error": message, **extra}, status)

    def send_file(self, path, content_type, no_store=False):
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if no_store:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def request_path(self):
        return urllib.parse.urlparse(self.path).path

    def authorised(self):
        if local_host_allowed(self.headers.get("Host", ""), self.server.server_address[1]):
            return True
        self.send_error_json("Forbidden.", HTTPStatus.FORBIDDEN)
        return False

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1_000_000:
            raise ValueError("The request is too large.")
        raw = self.rfile.read(length) if length else b"{}"
        data = json.loads(raw or b"{}")
        if not isinstance(data, dict):
            raise ValueError("The request is not valid.")
        return data

    def jd_base(self):
        """The saved JDownloader address. Raises JDError when none is set yet."""
        base = self.settings.get("jd_url")
        if not base:
            raise JDError(NOT_SET_UP, kind="unset")
        return base

    def meta(self):
        return {"version": __version__, "edition": self.edition.name, "title": self.edition.title,
                "features": self.edition.features, "jdUrl": self.settings.get("jd_url"),
                "isLocal": self.is_local}

    # --- routes

    def do_GET(self):
        if not self.authorised():
            return
        path = self.request_path()
        if self.extra_get(path):
            return
        if path in ("/", "/index.html"):
            self.send_file(resource_path("web", "index.html"), "text/html; charset=utf-8", no_store=True)
        elif path == "/icon.png":
            self.send_file(resource_path("assets", "icon", "jdeck.png"), "image/png")
        elif path == "/common.css":
            self.send_file(resource_path("web", "common.css"), "text/css; charset=utf-8", no_store=True)
        elif path == "/api/state":
            try:
                self.send_json({"ok": True, **self.meta(), **jd_state(self.jd_base())})
            except JDError as e:
                self.send_json({"ok": False, "offline": e.offline, "kind": e.kind,
                                "needsSetup": e.kind == "unset", "error": str(e), **self.meta()})
        elif path == "/api/update-check":
            try:
                self.send_json({"ok": True, **check_for_update()})
            except UpdateError as e:
                self.send_json({"ok": False, "error": str(e)})
        elif path == "/api/settings":
            self.send_json(self.meta())
        else:
            self.send_error_json("Not found.", HTTPStatus.NOT_FOUND)

    def do_POST(self):
        if not self.authorised():
            return
        if self.headers.get(CSRF_HEADER) != "1":
            self.send_error_json("Missing request header.", HTTPStatus.FORBIDDEN)
            return
        path = self.request_path()
        try:
            data = self.read_json()
            if self.extra_post(path, data):
                return
            if path == "/api/add":
                links = extract_links(data.get("text", ""))
                if not links:
                    raise ValueError("No links found. Paste links that start with http://, https:// or ftp://.")
                jd_add_links(self.jd_base(), links, data.get("autostart", True),
                             str(data.get("packageName", "")).strip(),
                             str(data.get("destination", "")).strip())
                self.send_json({"ok": True, "added": len(links)})
            elif path == "/api/control":
                action = data.get("action")
                calls = {
                    "start": ("/downloadcontroller/start",),
                    "stop": ("/downloadcontroller/stop",),
                    "pause": ("/downloadcontroller/pause", True),
                    "resume": ("/downloadcontroller/pause", False),
                }
                if action not in calls:
                    raise ValueError("Unknown action.")
                jd_call(self.jd_base(), *calls[action])
                self.send_json({"ok": True})
            elif path == "/api/linkgrabber/start":
                base = self.jd_base()
                ids = id_list(data.get("packageIds", []))
                if not ids:
                    grabber = jd_call(base, "/linkgrabberv2/queryPackages", {}) or []
                    ids = [p["uuid"] for p in grabber]
                if ids:
                    jd_call(base, "/linkgrabberv2/moveToDownloadlist", [], ids)
                    jd_call(base, "/downloadcontroller/start")
                self.send_json({"ok": True, "moved": len(ids)})
            elif path == "/api/linkgrabber/remove":
                jd_call(self.jd_base(), "/linkgrabberv2/removeLinks", [], id_list(data.get("packageIds")))
                self.send_json({"ok": True})
            elif path == "/api/downloads/remove":
                # Removes entries from JDownloader's list only. Files on disk are not touched.
                jd_call(self.jd_base(), "/downloadsV2/removeLinks", [], id_list(data.get("packageIds")))
                self.send_json({"ok": True})
            elif path == "/api/open-release":
                # Opens the release page in the user's own browser on this PC.
                if not self.is_local:
                    raise PermissionError("Only this PC can open pages on this PC.")
                url = data.get("url")
                if not is_release_url(url):
                    url = RELEASES_PAGE
                import webbrowser
                webbrowser.open(url)
                self.send_json({"ok": True})
            elif path == "/api/test-connection":
                self.require_address_editable()
                self.send_json({"ok": True, **jd_test(normalise_jd_url(data.get("jdUrl")))})
            elif path == "/api/settings":
                self.save_settings(data)
                self.send_json({"ok": True, **self.meta()})
            else:
                self.send_error_json("Not found.", HTTPStatus.NOT_FOUND)
        except JDError as e:
            self.send_error_json(str(e), HTTPStatus.BAD_GATEWAY, offline=e.offline, kind=e.kind)
        except PermissionError as e:
            self.send_error_json(str(e), HTTPStatus.FORBIDDEN)
        except (ValueError, TypeError) as e:
            self.send_error_json(str(e) or "The request is not valid.")

    def extra_get(self, path):
        """Hook for an edition's own GET routes. Return True when the request was answered."""
        return False

    def extra_post(self, path, data):
        """Hook for an edition's own POST routes. Return True when the request was answered."""
        return False

    def require_address_editable(self):
        if not self.edition.features.get("jdAddressEditable"):
            raise PermissionError("The JDownloader address cannot be changed here.")

    def save_settings(self, data):
        if "jdUrl" in data:
            self.require_address_editable()
            self.settings.update(jd_url=normalise_jd_url(data["jdUrl"]))


def make_server(host, port, edition, settings, handler=Handler, **attributes):
    """Start a server in a background thread and return it.
    `attributes` are set on the server before it starts (for example the sign-in sessions)."""
    server = JDeckHTTPServer((host, port), handler)
    server.edition = edition
    server.settings = settings
    for name, value in attributes.items():
        setattr(server, name, value)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
