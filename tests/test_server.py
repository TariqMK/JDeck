"""Tests for JDeck Server: sessions, the wrong-password delay, and who may do what."""

import http.client
import json
import os
import plistlib
import socket
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import auth  # noqa: E402
from core.settings import Settings  # noqa: E402
from server import app as server_app  # noqa: E402
from server import autostart, qr  # noqa: E402
from server.app import SERVER, Controller  # noqa: E402


def start_server(settings, host="0.0.0.0"):
    controller = Controller(settings, host=host)
    controller.start()
    return controller


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.sessions = auth.Sessions(clock=self.clock)

    def test_new_token_is_valid_and_random(self):
        a, b = self.sessions.create(), self.sessions.create()
        self.assertNotEqual(a, b)
        self.assertGreaterEqual(len(a), 40)
        self.assertTrue(self.sessions.valid(a))
        self.assertFalse(self.sessions.valid("made-up"))
        self.assertFalse(self.sessions.valid(""))

    def test_session_ends_after_30_idle_days_but_use_keeps_it(self):
        token = self.sessions.create()
        self.clock.now += auth.SESSION_IDLE_SECONDS - 1
        self.assertTrue(self.sessions.valid(token))
        self.clock.now += auth.SESSION_IDLE_SECONDS - 1
        self.assertTrue(self.sessions.valid(token), "Each use restarts the 30 days")
        self.clock.now += auth.SESSION_IDLE_SECONDS + 1
        self.assertFalse(self.sessions.valid(token))
        self.assertEqual(self.sessions.count(), 0)

    def test_end_and_end_all(self):
        a, b = self.sessions.create(), self.sessions.create()
        self.sessions.end(a)
        self.assertFalse(self.sessions.valid(a))
        self.assertTrue(self.sessions.valid(b))
        self.sessions.end_all()
        self.assertFalse(self.sessions.valid(b))


class ThrottleTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.throttle = auth.Throttle(clock=self.clock)

    def test_delay_after_five_wrong_passwords(self):
        for _ in range(auth.MAX_FAILURES - 1):
            self.throttle.failed("10.0.0.5")
        self.assertEqual(self.throttle.wait_seconds("10.0.0.5"), 0)
        self.throttle.failed("10.0.0.5")
        self.assertEqual(self.throttle.wait_seconds("10.0.0.5"), auth.DELAY_SECONDS)
        self.assertEqual(self.throttle.wait_seconds("10.0.0.6"), 0, "Other addresses are not affected")
        self.clock.now += auth.DELAY_SECONDS
        self.assertEqual(self.throttle.wait_seconds("10.0.0.5"), 0)

    def test_success_resets_the_count(self):
        for _ in range(auth.MAX_FAILURES - 1):
            self.throttle.failed("a")
        self.throttle.succeeded("a")
        self.throttle.failed("a")
        self.assertEqual(self.throttle.wait_seconds("a"), 0)


class CookieTests(unittest.TestCase):
    def test_cookie_flags(self):
        value = auth.session_cookie("abc")
        self.assertTrue(value.startswith("jdeck_session=abc;"))
        for flag in ("HttpOnly", "SameSite=Strict", "Path=/", f"Max-Age={auth.SESSION_IDLE_SECONDS}"):
            self.assertIn(flag, value)
        self.assertNotIn("Secure", value)
        self.assertIn("Max-Age=0", auth.clear_cookie())

    def test_read_token_from_cookie_header(self):
        self.assertEqual(auth.session_token("a=1; jdeck_session=tok-123; b=2"), "tok-123")
        self.assertEqual(auth.session_token(""), "")
        self.assertEqual(auth.session_token("other=1"), "")
        self.assertEqual(auth.session_token("\x00bad;;=="), "")


class EditionTests(unittest.TestCase):
    def test_server_defaults(self):
        self.assertEqual(SERVER.defaults["jd_url"], "http://127.0.0.1:3128")
        self.assertEqual(SERVER.defaults["port"], 23128)
        self.assertEqual(SERVER.data_folder, "JDeck-Server-data")
        self.assertFalse(SERVER.features["jdAddressEditable"])
        self.assertTrue(SERVER.features["signOut"])
        self.assertTrue(SERVER.features["controlPage"])
        self.assertFalse(SERVER.features["connectionHelp"])


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServerAccessTests(unittest.TestCase):
    """Runs the real JDeck Server on 127.0.0.1 (no JDownloader needed).
    A request with the Host 'jdeck-server.lan:<port>' counts as a network device."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(os.path.join(self.tmp.name, "data"), SERVER.defaults)
        self.settings.update(port=free_port())
        self.controller = start_server(self.settings, host="127.0.0.1")
        self.port = self.controller.port

    def tearDown(self):
        self.controller.stop()
        self.tmp.cleanup()

    def request(self, method, path, body=None, cookie=None, network=False, csrf=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": f"jdeck-server.lan:{self.port}" if network else f"127.0.0.1:{self.port}"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
            if csrf:
                headers["X-JDeck"] = "1"
        if cookie:
            headers["Cookie"] = f"{auth.SESSION_COOKIE}={cookie}"
        conn.request(method, path, body=data, headers=headers)
        r = conn.getresponse()
        raw = r.read()
        conn.close()
        try:
            payload = json.loads(raw) if raw else {}
        except ValueError:
            payload = raw
        return r.status, payload, r.headers

    def sign_in(self, password="pw", network=False):
        status, data, headers = self.request("POST", "/api/login", {"password": password}, network=network)
        self.assertEqual(status, 200, data)
        return auth.session_token(headers["Set-Cookie"])

    def test_public_pages_need_no_sign_in(self):
        for path in ("/login", "/common.css", "/icon.png", "/api/session"):
            status, _, _ = self.request("GET", path, network=True)
            self.assertEqual(status, 200, path)

    def test_everything_else_needs_sign_in(self):
        status, _, headers = self.request("GET", "/")
        self.assertEqual((status, headers["Location"]), (303, "/login"))
        status, data, _ = self.request("GET", "/api/state")
        self.assertEqual(status, 401)
        self.assertTrue(data["signIn"])
        status, _, _ = self.request("POST", "/api/add", {"text": "https://example.com/a"})
        self.assertEqual(status, 401)
        status, _, _ = self.request("GET", "/api/state", cookie="made-up-token")
        self.assertEqual(status, 401)

    def test_first_password_only_from_the_server_pc(self):
        _, s, _ = self.request("GET", "/api/session", network=True)
        self.assertEqual((s["hasPassword"], s["isLocal"], s["signedIn"]), (False, False, False))
        status, _, _ = self.request("POST", "/api/setup-password", {"password": "pw"}, network=True)
        self.assertEqual(status, 403)
        status, _, _ = self.request("POST", "/api/login", {"password": "pw"}, network=True)
        self.assertEqual(status, 409)
        self.assertFalse(self.settings.has_password)

        _, s, _ = self.request("GET", "/api/session")
        self.assertTrue(s["isLocal"])
        status, _, _ = self.request("POST", "/api/setup-password", {"password": ""})
        self.assertEqual(status, 400)
        status, _, headers = self.request("POST", "/api/setup-password", {"password": "pw"})
        self.assertEqual(status, 200)
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        token = auth.session_token(headers["Set-Cookie"])
        status, data, _ = self.request("GET", "/api/settings", cookie=token)
        self.assertEqual((status, data["edition"]), (200, "server"))

        status, _, _ = self.request("POST", "/api/setup-password", {"password": "other"})
        self.assertEqual(status, 409, "The first password cannot be replaced this way")
        self.assertTrue(self.settings.check_password("pw"))

    def test_sign_in_use_and_sign_out(self):
        self.settings.set_password("pw")
        status, data, _ = self.request("POST", "/api/login", {"password": "wrong"}, network=True)
        self.assertEqual((status, data["error"]), (401, "Wrong password."))
        token = self.sign_in(network=True)
        status, data, _ = self.request("GET", "/api/settings", cookie=token, network=True)
        self.assertEqual(status, 200)
        self.assertFalse(data["isLocal"])
        _, s, _ = self.request("GET", "/api/session", cookie=token, network=True)
        self.assertTrue(s["signedIn"])

        status, _, headers = self.request("POST", "/api/logout", {}, cookie=token, network=True)
        self.assertEqual(status, 200)
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        status, _, _ = self.request("GET", "/api/settings", cookie=token, network=True)
        self.assertEqual(status, 401)

    def test_wrong_password_delay(self):
        self.settings.set_password("pw")
        for _ in range(auth.MAX_FAILURES):
            self.request("POST", "/api/login", {"password": "wrong"}, network=True)
        status, data, _ = self.request("POST", "/api/login", {"password": "pw"}, network=True)
        self.assertEqual(status, 429)
        self.assertIn("Try again in", data["error"])
        self.assertGreater(data["retryAfter"], 0)

    def test_login_needs_the_jdeck_header(self):
        self.settings.set_password("pw")
        status, _, _ = self.request("POST", "/api/login", {"password": "pw"}, csrf=False)
        self.assertEqual(status, 403)

    def test_signed_in_devices_cannot_change_the_address_or_open_pages(self):
        self.settings.set_password("pw")
        token = self.sign_in(network=True)
        for path, body in (("/api/settings", {"jdUrl": "evil.test"}),
                           ("/api/test-connection", {"jdUrl": "evil.test"}),
                           ("/api/open-release", {})):
            status, _, _ = self.request("POST", path, body, cookie=token, network=True)
            self.assertEqual(status, 403, path)
        self.assertEqual(self.settings.get("jd_url"), "http://127.0.0.1:3128")

    def test_state_tells_the_page_what_to_show(self):
        self.settings.set_password("pw")
        self.settings.update(jd_url=f"http://127.0.0.1:{free_port()}")  # Nothing there: offline.
        for network in (False, True):
            token = self.sign_in(network=network)
            status, s, _ = self.request("GET", "/api/state", cookie=token, network=network)
            self.assertEqual(status, 200)
            self.assertEqual(s["features"], SERVER.features)
            self.assertEqual(s["isLocal"], not network, "The server PC link shows only on the server PC")
            self.assertEqual((s["ok"], s["offline"], s["kind"]), (False, True, "refused"))

    def test_page_is_served_after_sign_in(self):
        self.settings.set_password("pw")
        token = self.sign_in()
        status, body, _ = self.request("GET", "/", cookie=token)
        self.assertEqual(status, 200)
        self.assertIn(b"/common.css", body)


class StartUpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(os.path.join(self.tmp.name, "data"), SERVER.defaults)

    def tearDown(self):
        self.tmp.cleanup()

    def test_set_password_in_terminal(self):
        with mock.patch.object(server_app.getpass, "getpass", side_effect=["pw", "pw"]), \
                mock.patch("builtins.print"):
            self.assertEqual(server_app.set_password_in_terminal(self.settings), 0)
        self.assertTrue(self.settings.check_password("pw"))

    def test_set_password_in_terminal_refuses_a_mismatch(self):
        with mock.patch.object(server_app.getpass, "getpass", side_effect=["pw", "other"]), \
                mock.patch("builtins.print"):
            self.assertEqual(server_app.set_password_in_terminal(self.settings), 1)
        self.assertFalse(self.settings.has_password)

    def test_second_copy_cannot_take_the_same_port(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.settings.update(port=s.getsockname()[1])
        first = start_server(self.settings)
        self.addCleanup(first.stop)
        with self.assertRaises(OSError):
            start_server(self.settings)

    def test_port_in_use_is_reported(self):
        with socket.socket() as blocker:
            blocker.bind(("127.0.0.1", 0))
            blocker.listen()
            self.settings.update(port=blocker.getsockname()[1])
            with self.assertRaises(OSError):
                start_server(self.settings, host="127.0.0.1")


class ControlPageTests(ServerAccessTests):
    """The control page and its API: server PC only, sign-in required."""

    def setUp(self):
        super().setUp()
        self.settings.set_password("pw")
        self.token = self.sign_in()

    # The base class tests start without a password; they run once there, not again here.
    test_first_password_only_from_the_server_pc = None
    test_sign_in_use_and_sign_out = None
    test_wrong_password_delay = None
    test_login_needs_the_jdeck_header = None
    test_signed_in_devices_cannot_change_the_address_or_open_pages = None
    test_page_is_served_after_sign_in = None
    test_public_pages_need_no_sign_in = None
    test_everything_else_needs_sign_in = None

    def test_network_devices_get_not_found(self):
        token = self.sign_in(network=True)
        for method, path, body in (("GET", "/server", None), ("GET", "/api/server/status", None),
                                   ("GET", "/api/server/qr.svg", None), ("POST", "/api/server/quit", {}),
                                   ("POST", "/api/server/password", {"password": "x"}),
                                   ("POST", "/api/server/stop", {})):
            status, _, _ = self.request(method, path, body, cookie=token, network=True)
            self.assertEqual(status, 404, path)
        self.assertFalse(self.controller.stopped.is_set())

    def test_signed_out_server_pc_goes_to_sign_in_and_back(self):
        status, _, headers = self.request("GET", "/server")
        self.assertEqual((status, headers["Location"]), (303, "/login?next=/server"))
        status, body, _ = self.request("GET", "/server", cookie=self.token)
        self.assertEqual(status, 200)
        self.assertIn(b"JDeck Server", body)

    def test_status_and_qr_code(self):
        status, s, _ = self.request("GET", "/api/server/status", cookie=self.token)
        self.assertEqual(status, 200)
        self.assertEqual((s["port"], s["devices"], s["jdUrl"]), (self.port, 1, "http://127.0.0.1:3128"))
        self.assertIn("startAtLogin", s)
        status, svg, headers = self.request("GET", "/api/server/qr.svg", cookie=self.token)
        self.assertEqual((status, headers["Content-Type"]), (200, "image/svg+xml"))
        self.assertTrue(svg.startswith(b"<svg"))

    def test_change_jdownloader_address(self):
        status, s, _ = self.request("POST", "/api/server/jd-url", {"jdUrl": "127.0.0.1:4000"}, cookie=self.token)
        self.assertEqual((status, s["jdUrl"]), (200, "http://127.0.0.1:4000"))
        status, _, _ = self.request("POST", "/api/server/jd-url", {"jdUrl": "ftp://x"}, cookie=self.token)
        self.assertEqual(status, 400)
        status, r, _ = self.request("POST", "/api/server/test-connection", {"jdUrl": f"127.0.0.1:{free_port()}"},
                                    cookie=self.token)
        self.assertEqual((status, r["result"]), (200, "refused"))

    def test_password_change_signs_out_other_devices(self):
        other = self.sign_in(network=True)
        status, _, headers = self.request("POST", "/api/server/password", {"password": "new"}, cookie=self.token)
        self.assertEqual(status, 200)
        mine = auth.session_token(headers["Set-Cookie"])
        self.assertEqual(self.request("GET", "/api/settings", cookie=other, network=True)[0], 401)
        self.assertEqual(self.request("GET", "/api/settings", cookie=self.token)[0], 401)
        self.assertEqual(self.request("GET", "/api/settings", cookie=mine)[0], 200)
        self.assertTrue(self.settings.check_password("new"))
        status, _, _ = self.request("POST", "/api/server/password", {"password": ""}, cookie=mine)
        self.assertEqual(status, 400)

    def test_change_port(self):
        new_port = free_port()
        status, r, _ = self.request("POST", "/api/server/port", {"port": new_port}, cookie=self.token)
        self.assertEqual((status, r["url"]), (200, f"http://127.0.0.1:{new_port}/server"))
        self.assertEqual(self.settings.get("port"), new_port)
        old_port, self.port = self.port, new_port
        status, _, _ = self.request("GET", "/api/server/status", cookie=self.token)
        self.assertEqual(status, 200, "The session works on the new port")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and port_open(old_port):
            time.sleep(0.1)
        self.assertFalse(port_open(old_port), "The old port closes")

    def test_change_to_a_busy_port_keeps_the_old_one(self):
        with socket.socket() as blocker:
            blocker.bind(("127.0.0.1", 0))
            blocker.listen()
            busy = blocker.getsockname()[1]
            status, data, _ = self.request("POST", "/api/server/port", {"port": busy}, cookie=self.token)
        self.assertEqual(status, 400)
        self.assertIn("in use", data["error"])
        self.assertEqual(self.settings.get("port"), self.port)
        self.assertEqual(self.request("GET", "/api/server/status", cookie=self.token)[0], 200)

    def test_start_at_login_switch(self):
        calls = []
        with mock.patch("server.autostart.set_enabled", side_effect=lambda on: calls.append(on)), \
                mock.patch("server.autostart.is_enabled", side_effect=lambda: bool(calls and calls[-1])):
            status, r, _ = self.request("POST", "/api/server/autostart", {"enabled": True}, cookie=self.token)
        self.assertEqual((status, calls, r["startAtLogin"]), (200, [True], True))
        with mock.patch("server.autostart.set_enabled", side_effect=OSError("denied")):
            status, r, _ = self.request("POST", "/api/server/autostart", {"enabled": True}, cookie=self.token)
        self.assertEqual(status, 400)
        self.assertIn("denied", r["error"])

    def test_quit_from_the_control_page(self):
        token_file = self.controller.token_path
        self.assertTrue(os.path.isfile(token_file))
        status, _, _ = self.request("POST", "/api/server/quit", {}, cookie=self.token)
        self.assertEqual(status, 200)
        self.assertTrue(self.controller.stopped.wait(10))
        self.assertFalse(port_open(self.port))
        self.assertFalse(os.path.exists(token_file))

    def test_stop_needs_the_control_token(self):
        status, _, _ = self.request("POST", "/api/server/stop", {})
        self.assertEqual(status, 403)
        self.assertFalse(self.controller.stopped.is_set())
        with mock.patch("builtins.print"):
            self.assertEqual(server_app.stop_running(self.settings), 0)
        self.assertTrue(self.controller.stopped.wait(10))
        with mock.patch("builtins.print"):
            self.assertEqual(server_app.stop_running(self.settings), 1, "Nothing left to stop")


def port_open(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


class SingleInstanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(os.path.join(self.tmp.name, "data"), SERVER.defaults)
        self.settings.update(port=free_port())

    def tearDown(self):
        self.tmp.cleanup()

    def test_second_copy_opens_the_first_and_exits(self):
        first = start_server(self.settings, host="127.0.0.1")
        self.addCleanup(first.stop)
        second = Controller(self.settings, host="127.0.0.1")
        with mock.patch.object(server_app.webbrowser, "open") as opened, mock.patch("builtins.print"):
            self.assertEqual(server_app.start_or_report(second, open_existing=True), 0)
        opened.assert_called_once_with(f"http://127.0.0.1:{self.settings.get('port')}/server")

    def test_other_program_on_the_port_is_an_error(self):
        with socket.socket() as blocker:
            blocker.bind(("127.0.0.1", 0))
            blocker.listen()
            self.settings.update(port=blocker.getsockname()[1])
            controller = Controller(self.settings, host="127.0.0.1")
            with mock.patch.object(server_app, "show_error") as error:
                self.assertEqual(server_app.start_or_report(controller, open_existing=True), 1)
        self.assertIn("in use by another program", error.call_args[0][1])


class AutostartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_linux_desktop_file(self):
        command = ["/opt/My Apps/JDeck-Server"]
        autostart.set_enabled(True, command=command, platform="linux", home=self.home)
        path = autostart.entry_path(platform="linux", home=self.home)
        self.assertTrue(path.endswith(os.path.join(".config", "autostart", "jdeck-server.desktop")))
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn('Exec="/opt/My Apps/JDeck-Server"\n', text)
        self.assertTrue(autostart.is_enabled(platform="linux", home=self.home))
        autostart.set_enabled(False, platform="linux", home=self.home)
        self.assertFalse(os.path.exists(path))
        autostart.set_enabled(False, platform="linux", home=self.home)  # Already off: no error.

    def test_macos_launch_agent(self):
        command = ["/Applications/JDeck Server.app/Contents/MacOS/JDeck-Server"]
        autostart.set_enabled(True, command=command, platform="darwin", home=self.home)
        path = autostart.entry_path(platform="darwin", home=self.home)
        with open(path, "rb") as f:
            plist = plistlib.load(f)
        self.assertEqual(plist["ProgramArguments"], command)
        self.assertTrue(plist["RunAtLoad"])
        self.assertEqual(plist["Label"], "io.github.jdeck.server")

    def test_launch_command_from_source(self):
        cmd = autostart.launch_command()
        self.assertTrue(cmd[-1].endswith("jdeck_server.py"))
        self.assertTrue(os.path.isfile(cmd[-1]))

    @unittest.skipUnless(sys.platform == "win32", "Windows shortcut")
    def test_windows_startup_shortcut(self):
        appdata = os.path.join(self.home, "Roaming")
        autostart.set_enabled(True, command=[sys.executable, "C:\\Some Folder\\jdeck_server.py"],
                              platform="win32", appdata=appdata)
        path = autostart.entry_path(platform="win32", appdata=appdata)
        self.assertTrue(path.endswith(os.path.join("Startup", "JDeck Server.lnk")))
        self.assertTrue(os.path.isfile(path))
        autostart.set_enabled(False, platform="win32", appdata=appdata)
        self.assertFalse(os.path.exists(path))


class QRTests(unittest.TestCase):
    def test_sizes_grow_with_the_text(self):
        self.assertEqual(len(qr.qr_matrix("x")), 21)                            # Version 1
        self.assertEqual(len(qr.qr_matrix("http://192.168.1.20:23128/")), 25)   # Version 2
        self.assertEqual(len(qr.qr_matrix("a" * 213)), 57)                      # Version 10
        with self.assertRaises(ValueError):
            qr.qr_matrix("a" * 214)

    def test_finder_patterns_and_quiet_zone(self):
        m = qr.qr_matrix("http://192.168.1.20:23128/")
        n = len(m)
        for x0, y0 in ((0, 0), (n - 7, 0), (0, n - 7)):
            self.assertTrue(all(m[y0][x0 + i] and m[y0 + 6][x0 + i] for i in range(7)))
            self.assertFalse(m[y0 + 1][x0 + 1])
            self.assertTrue(m[y0 + 3][x0 + 3])
        self.assertIn('viewBox="0 0 29 29"', qr.qr_svg("x"))  # 21 modules + 2 x 4 quiet zone

    def test_reed_solomon_known_value(self):
        # ISO/IEC 18004 example "01234567" (version 1-M): the published 10 EC codewords.
        data = [0x10, 0x20, 0x0C, 0x56, 0x61, 0x80, 0xEC, 0x11, 0xEC, 0x11, 0xEC, 0x11, 0xEC, 0x11, 0xEC, 0x11]
        ecc = qr._rs_remainder(data, qr._rs_divisor(10))
        self.assertEqual(ecc, [0xA5, 0x24, 0xD4, 0xC1, 0xED, 0x36, 0xC7, 0x87, 0x2C, 0x55])


if __name__ == "__main__":
    unittest.main()
