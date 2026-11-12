"""JDeck Server's request handler: the shared JDeck page and API behind a sign-in page,
plus the control page for the server PC.

Access rules (see ARCHITECTURE.md, section 5.3):
- Every device, the server PC too, must sign in to use the JDeck page and API.
- Only the server PC may set the first password and open the control page (others get 404).
- The sign-in page, its API, the stylesheet and the icon are open to everyone.
- `--stop` on the server PC stops JDeck Server with the token from its data folder."""

import hmac
from http import HTTPStatus

from core import __version__
from core.auth import clear_cookie, session_cookie, session_token
from core.httpd import Handler, local_host_allowed
from core.jd_api import jd_test, normalise_jd_url
from core.settings import parse_port, resource_path
from server import autostart
from server.qr import qr_svg

PUBLIC_GET = {"/login", "/common.css", "/icon.png", "/api/session"}
PUBLIC_POST = {"/api/login", "/api/setup-password", "/api/logout", "/api/server/stop"}
CONTROL_HEADER = "X-JDeck-Control"
NO_PASSWORD_YET = "No password is set yet. Set it on the JDeck Server computer first."


def is_server_pc_only(path):
    return path == "/server" or path.startswith("/api/server/")


class ServerHandler(Handler):

    @property
    def is_local(self):
        """True only for a browser on the server PC itself, addressed as 127.0.0.1 or localhost."""
        return (self.client_address[0] == "127.0.0.1"
                and local_host_allowed(self.headers.get("Host", ""), self.server.server_address[1]))

    @property
    def sessions(self):
        return self.server.sessions

    @property
    def controller(self):
        return self.server.controller

    def token(self):
        return session_token(self.headers.get("Cookie", ""))

    def signed_in(self):
        return self.sessions.valid(self.token())

    def redirect(self, location):
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def authorised(self):
        path = self.request_path()
        if is_server_pc_only(path) and not self.is_local:
            self.send_error_json("Not found.", HTTPStatus.NOT_FOUND)
            return False
        public = PUBLIC_GET if self.command == "GET" else PUBLIC_POST
        if path in public or self.signed_in():
            return True
        if self.command == "GET" and not path.startswith("/api/"):
            self.redirect("/login?next=/server" if path == "/server" else "/login")
        else:
            self.send_error_json("Sign in first.", HTTPStatus.UNAUTHORIZED, signIn=True)
        return False

    # --- routes

    def extra_get(self, path):
        if path == "/login":
            self.send_file(resource_path("web", "login.html"), "text/html; charset=utf-8", no_store=True)
        elif path == "/server":
            self.send_file(resource_path("web", "server.html"), "text/html; charset=utf-8", no_store=True)
        elif path == "/api/session":
            self.send_json({"title": self.edition.title, "signedIn": self.signed_in(),
                            "hasPassword": self.settings.has_password, "isLocal": self.is_local})
        elif path == "/api/server/status":
            self.send_json(self.status())
        elif path == "/api/server/qr.svg":
            url = self.controller.network_url() or self.controller.local_url()
            body = qr_svg(url).encode()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        else:
            return False
        return True

    def extra_post(self, path, data):
        if path == "/api/login":
            self.login(str(data.get("password") or ""))
        elif path == "/api/setup-password":
            self.setup_password(str(data.get("password") or ""))
        elif path == "/api/logout":
            self.sessions.end(self.token())
            self.send_json({"ok": True}, headers={"Set-Cookie": clear_cookie()})
        elif path == "/api/server/stop":
            sent = self.headers.get(CONTROL_HEADER, "")
            if not sent or not hmac.compare_digest(sent, self.controller.control_token):
                raise PermissionError("Not allowed.")
            self.send_json({"ok": True})
            self.controller.request_stop()
        elif path == "/api/server/quit":
            self.send_json({"ok": True})
            self.controller.request_stop()
        elif path == "/api/server/jd-url":
            self.settings.update(jd_url=normalise_jd_url(data.get("jdUrl")))
            self.send_json({"ok": True, **self.status()})
        elif path == "/api/server/test-connection":
            self.send_json({"ok": True, **jd_test(normalise_jd_url(data.get("jdUrl")))})
        elif path == "/api/server/password":
            self.settings.set_password(str(data.get("password") or ""))  # ValueError when empty.
            self.sessions.end_all()  # Every device signs in again; this browser stays signed in.
            self.sign_in_now()
        elif path == "/api/server/port":
            port = parse_port(data.get("port"))
            try:
                self.controller.change_port(port)
            except OSError as e:
                raise ValueError(f"Port {port} is in use by another program. ({e.strerror or e})") from None
            self.send_json({"ok": True, "url": f"http://127.0.0.1:{port}/server"})
        elif path == "/api/server/autostart":
            try:
                autostart.set_enabled(bool(data.get("enabled")))
            except OSError as e:
                raise ValueError(f"Start at login could not be changed: {e}") from None
            self.send_json({"ok": True, **self.status()})
        else:
            return False
        return True

    def status(self):
        return {"title": self.edition.title, "version": __version__, "port": self.controller.port,
                "localUrl": self.controller.local_url(), "networkUrl": self.controller.network_url(),
                "devices": self.sessions.count(), "jdUrl": self.settings.get("jd_url"),
                "startAtLogin": autostart.is_enabled()}

    # --- sign-in

    def sign_in_now(self):
        self.send_json({"ok": True}, headers={"Set-Cookie": session_cookie(self.sessions.create())})

    def login(self, password):
        if not self.settings.has_password:
            self.send_error_json(NO_PASSWORD_YET, HTTPStatus.CONFLICT)
            return
        address = self.client_address[0]
        throttle = self.server.throttle
        wait = throttle.wait_seconds(address)
        if wait:
            self.send_error_json(f"Too many wrong passwords. Try again in {wait} s.",
                                 HTTPStatus.TOO_MANY_REQUESTS, retryAfter=wait)
            return
        if not self.settings.check_password(password):
            throttle.failed(address)
            self.send_error_json("Wrong password.", HTTPStatus.UNAUTHORIZED)
            return
        throttle.succeeded(address)
        self.sign_in_now()

    def setup_password(self, password):
        if not self.is_local:
            raise PermissionError("Set the first password on the JDeck Server computer.")
        if self.settings.has_password:
            self.send_error_json("A password is already set.", HTTPStatus.CONFLICT)
            return
        self.settings.set_password(password)  # ValueError for an empty password.
        self.sessions.end_all()
        self.sign_in_now()
