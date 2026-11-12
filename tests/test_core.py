"""Tests for the JDeck core: link parsing, JDownloader URL building, settings, passwords,
the JDownloader client, the update check, and the rules of the local server."""

import json
import os
import socket
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import httpd, jd_api, updates  # noqa: E402
from core import settings as settings_mod  # noqa: E402
from core.edition import Edition  # noqa: E402


class ExtractLinksTests(unittest.TestCase):
    def test_finds_links_in_free_text(self):
        text = "Grab https://example.com/a.zip and also\nhttp://example.org/b.rar, thanks!"
        self.assertEqual(jd_api.extract_links(text),
                         ["https://example.com/a.zip", "http://example.org/b.rar"])

    def test_removes_duplicates_and_keeps_order(self):
        text = "https://x.test/2 https://x.test/1 https://x.test/2"
        self.assertEqual(jd_api.extract_links(text), ["https://x.test/2", "https://x.test/1"])

    def test_strips_trailing_punctuation_and_brackets(self):
        self.assertEqual(jd_api.extract_links("(see https://x.test/file.)"), ["https://x.test/file"])

    def test_keeps_query_strings(self):
        link = "https://www.youtube.com/watch?v=abc123&t=10"
        self.assertEqual(jd_api.extract_links(link), [link])

    def test_ignores_text_without_links(self):
        self.assertEqual(jd_api.extract_links("no links here"), [])
        self.assertEqual(jd_api.extract_links(None), [])


class JDUrlTests(unittest.TestCase):
    def test_each_parameter_is_encoded_json_joined_with_ampersand(self):
        url = jd_api.build_jd_url("http://h:3128/", "/linkgrabberv2/moveToDownloadlist", [[], [5, 6]])
        path, _, query = url.partition("?")
        self.assertEqual(path, "http://h:3128/linkgrabberv2/moveToDownloadlist")
        self.assertEqual([json.loads(urllib.parse.unquote(p)) for p in query.split("&")], [[], [5, 6]])

    def test_ampersands_inside_values_cannot_split_parameters(self):
        url = jd_api.build_jd_url("http://h:3128", "/linkgrabberv2/addLinks",
                                     [{"links": "https://a.test/?x=1&y=2"}])
        query = url.partition("?")[2]
        self.assertNotIn("&", query)
        self.assertEqual(json.loads(urllib.parse.unquote(query))["links"], "https://a.test/?x=1&y=2")

    def test_no_parameters_means_no_query(self):
        self.assertEqual(jd_api.build_jd_url("http://h:3128", "/jd/version", ()), "http://h:3128/jd/version")

    def test_normalise_accepts_short_forms(self):
        self.assertEqual(jd_api.normalise_jd_url("jd-host"), "http://jd-host:3128")
        self.assertEqual(jd_api.normalise_jd_url("jd-host:4000/"), "http://jd-host:4000")
        self.assertEqual(jd_api.normalise_jd_url(" http://nas.local:3128 "), "http://nas.local:3128")

    def test_normalise_rejects_bad_input(self):
        for bad in ("", "ftp://x", "http://"):
            with self.assertRaises(ValueError):
                jd_api.normalise_jd_url(bad)


PASSWORD_DEFAULTS = {"jd_url": "", "port": 23128, "password_salt": "", "password_hash": ""}


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "data")

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_without_a_file(self):
        defaults = {"jd_url": ""}
        s = settings_mod.Settings(self.dir, defaults)
        self.assertEqual(s.values, {"jd_url": ""})
        self.assertFalse(s.has_password)
        s.update(jd_url="http://x:3128")
        self.assertEqual(defaults, {"jd_url": ""}, "The edition's defaults must not change")

    def test_no_password_means_nobody_signs_in(self):
        for defaults in ({"jd_url": ""}, PASSWORD_DEFAULTS):
            s = settings_mod.Settings(self.dir, defaults)
            self.assertFalse(s.check_password(""))
            self.assertFalse(s.check_password("anything"))

    def test_password_is_hashed_checked_and_persisted(self):
        s = settings_mod.Settings(self.dir, PASSWORD_DEFAULTS)
        s.set_password("hunter2")
        with open(s.path, encoding="utf-8") as f:
            self.assertNotIn("hunter2", f.read())
        reloaded = settings_mod.Settings(self.dir, PASSWORD_DEFAULTS)
        self.assertTrue(reloaded.check_password("hunter2"))
        self.assertFalse(reloaded.check_password("wrong"))

    def test_empty_password_is_refused(self):
        s = settings_mod.Settings(self.dir, PASSWORD_DEFAULTS)
        s.set_password("x")
        with self.assertRaises(ValueError):
            s.set_password("")
        self.assertTrue(s.check_password("x"))

    def test_unknown_keys_in_file_are_ignored(self):
        os.makedirs(self.dir)
        with open(os.path.join(self.dir, "settings.json"), "w", encoding="utf-8") as f:
            json.dump({"port": 9000, "junk": 1, "share_on_network": True}, f)
        s = settings_mod.Settings(self.dir, {"jd_url": "", "port": 1})
        self.assertEqual(s.get("port"), 9000)
        self.assertNotIn("junk", s.values)
        self.assertNotIn("share_on_network", s.values)


class SmallHelperTests(unittest.TestCase):
    def test_id_list_accepts_numbers_only(self):
        self.assertEqual(jd_api.id_list([1, 2]), [1, 2])
        for bad in (None, "1", [1, "2"]):
            with self.assertRaises(ValueError):
                jd_api.id_list(bad)

    def test_local_host_check_blocks_other_names(self):
        self.assertTrue(httpd.local_host_allowed("127.0.0.1:5000", 5000))
        self.assertTrue(httpd.local_host_allowed("localhost:5000", 5000))
        for bad in ("evil.test:5000", "127.0.0.1:6000", "", "127.0.0.1"):
            self.assertFalse(httpd.local_host_allowed(bad, 5000), bad)

    def test_parse_port(self):
        self.assertEqual(settings_mod.parse_port("8080"), 8080)
        for bad in ("abc", None, 80, 70000):
            with self.assertRaises(ValueError):
                settings_mod.parse_port(bad)


def free_port():
    """A port with nothing listening on it (connections to it are refused)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeJD(BaseHTTPRequestHandler):
    """A stand-in for JDownloader's local API. `reply` picks the answer."""

    reply = "ok"

    def do_GET(self):
        if self.reply == "ok":
            status, body = 200, json.dumps({"data": 12345}).encode()
        elif self.reply == "rejected":
            status, body = 404, json.dumps({"type": "API_COMMAND_NOT_FOUND"}).encode()
        else:
            status, body = 200, b"<html>not json</html>"
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class FakeJDMixin:
    def start_fake_jd(self, reply="ok"):
        handler = type("FakeJDReply", (FakeJD,), {"reply": reply})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}"


class JDClientTests(FakeJDMixin, unittest.TestCase):
    def test_connected(self):
        self.assertEqual(jd_api.jd_test(self.start_fake_jd("ok"))["result"], "connected")

    def test_jdownloader_rejects_the_call(self):
        with self.assertRaises(jd_api.JDError) as ctx:
            jd_api.jd_call(self.start_fake_jd("rejected"), "/jd/version")
        self.assertEqual(ctx.exception.kind, "http")
        self.assertIn("API_COMMAND_NOT_FOUND", str(ctx.exception))
        self.assertFalse(ctx.exception.offline)

    def test_unreadable_reply(self):
        self.assertEqual(jd_api.jd_test(self.start_fake_jd("garbage"))["result"], "bad-reply")

    def test_nothing_listening_is_refused(self):
        r = jd_api.jd_test(f"http://127.0.0.1:{free_port()}")
        self.assertEqual(r["result"], "refused")
        self.assertIn("open to its own PC only", r["message"])


TEST_EDITION = Edition(name="test", title="JDeck", data_folder="test-data", defaults={"jd_url": ""},
                       features={"jdAddressEditable": True, "connectionHelp": True})


class ServerTests(FakeJDMixin, unittest.TestCase):
    """Runs the real local server (no JDownloader needed) and checks what it allows."""

    edition = TEST_EDITION

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = settings_mod.Settings(os.path.join(self.tmp.name, "data"), self.edition.defaults)
        self.local = httpd.make_server("127.0.0.1", 0, self.edition, self.settings)
        self.local_url = f"http://127.0.0.1:{self.local.server_address[1]}"

    def tearDown(self):
        self.local.shutdown()
        self.local.server_close()
        self.tmp.cleanup()

    def request(self, path, body=None, headers=None):
        headers = dict(headers or {})
        if body is not None:
            headers.setdefault("X-JDeck", "1")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.local_url + path,
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            with e:
                raw = e.read()
            return e.code, (json.loads(raw) if raw else {})

    def test_settings_describe_the_edition(self):
        status, data = self.request("/api/settings")
        self.assertEqual(status, 200)
        self.assertTrue(data["isLocal"])
        self.assertEqual(data["edition"], self.edition.name)
        self.assertEqual(data["features"], self.edition.features)
        self.assertNotIn("sharing", data)

    def test_rejects_other_host_names(self):
        status, _ = self.request("/api/settings", headers={"Host": "evil.test"})
        self.assertEqual(status, 403)

    def test_post_without_jdeck_header_is_rejected(self):
        status, _ = self.request("/api/settings", body={}, headers={"X-JDeck": "0"})
        self.assertEqual(status, 403)

    def test_no_address_means_setup_is_needed(self):
        status, data = self.request("/api/state")
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertTrue(data["needsSetup"])
        status, data = self.request("/api/add", body={"text": "https://example.com/a.zip"})
        self.assertEqual(status, 502)
        self.assertEqual((data["kind"], data["error"]), ("unset", httpd.NOT_SET_UP))

    def test_saving_the_address_normalises_it(self):
        status, data = self.request("/api/settings", body={"jdUrl": "jd-host.local"})
        self.assertEqual(status, 200, data)
        self.assertEqual(self.settings.get("jd_url"), "http://jd-host.local:3128")
        status, _ = self.request("/api/settings", body={"jdUrl": "ftp://x"})
        self.assertEqual(status, 400)
        self.assertEqual(self.settings.get("jd_url"), "http://jd-host.local:3128")

    def test_test_connection_does_not_save(self):
        status, data = self.request("/api/test-connection", body={"jdUrl": self.start_fake_jd()})
        self.assertEqual((status, data["result"]), (200, "connected"))
        self.assertEqual(self.settings.get("jd_url"), "")
        status, _ = self.request("/api/test-connection", body={"jdUrl": ""})
        self.assertEqual(status, 400)

    def test_state_with_a_reachable_jdownloader(self):
        self.settings.update(jd_url=self.start_fake_jd())
        status, data = self.request("/api/state")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"], data)


class LockedAddressTests(ServerTests):
    """An edition that does not allow the address to be changed from the page."""

    edition = Edition(name="locked", title="JDeck", data_folder="x", defaults={"jd_url": ""},
                      features={"jdAddressEditable": False})

    def test_address_cannot_be_changed_or_tested(self):
        for path in ("/api/settings", "/api/test-connection"):
            status, _ = self.request(path, body={"jdUrl": "jd-host.local"})
            self.assertEqual(status, 403, path)
        self.assertEqual(self.settings.get("jd_url"), "")

    # The base tests that change the address do not apply to this edition.
    test_saving_the_address_normalises_it = None
    test_test_connection_does_not_save = None


class UpdateCheckTests(unittest.TestCase):
    """The manual update check, with the GitHub call stubbed (no network)."""

    SAMPLE = {"tag_name": "v1.4.0", "html_url": "https://github.com/TariqMK/JDeck/releases/tag/v1.4.0",
              "body": "- Faster refresh.", "draft": False, "prerelease": False}

    def check(self, current, release=None):
        return updates.check_for_update(current, fetch=lambda: dict(release or self.SAMPLE))

    def test_parse_version(self):
        self.assertEqual(updates.parse_version("v1.2.3"), (1, 2, 3))
        self.assertEqual(updates.parse_version("1.2.3"), (1, 2, 3))
        self.assertIsNone(updates.parse_version("0.0.0.dev0"))
        self.assertIsNone(updates.parse_version("v1.2"))
        self.assertIsNone(updates.parse_version(None))

    def test_compares_as_numbers(self):
        self.assertGreater(updates.parse_version("0.10.0"), updates.parse_version("0.9.0"))
        r = self.check("0.9.0", {**self.SAMPLE, "tag_name": "v0.10.0"})
        self.assertEqual(r["status"], "newer")
        self.assertEqual(r["latest"], "v0.10.0")

    def test_newer_release(self):
        r = self.check("1.3.9")
        self.assertEqual((r["status"], r["current"], r["latest"]), ("newer", "v1.3.9", "v1.4.0"))
        self.assertEqual(r["url"], self.SAMPLE["html_url"])
        self.assertEqual(r["notes"], "- Faster refresh.")

    def test_up_to_date(self):
        self.assertEqual(self.check("1.4.0")["status"], "current")
        self.assertEqual(self.check("1.5.0")["status"], "current")

    def test_development_build(self):
        r = self.check("0.0.0.dev0")
        self.assertEqual((r["status"], r["current"]), ("dev", "v0.0.0.dev0"))

    def test_missing_or_insecure_url_falls_back_to_releases_page(self):
        for url in (None, "", "http://github.com/x", "javascript:alert(1)"):
            r = self.check("1.0.0", {**self.SAMPLE, "html_url": url})
            self.assertEqual(r["url"], updates.RELEASES_PAGE)

    def test_odd_tag_is_unreadable(self):
        with self.assertRaisesRegex(updates.UpdateError, "can't read"):
            self.check("1.0.0", {**self.SAMPLE, "tag_name": "latest"})

    def test_release_url_check(self):
        self.assertTrue(updates.is_release_url(updates.RELEASES_PAGE))
        self.assertTrue(updates.is_release_url(self.SAMPLE["html_url"]))
        self.assertFalse(updates.is_release_url("https://github.com/TariqMK/JDeck/releasesX"))
        self.assertFalse(updates.is_release_url("https://evil.test/"))
        self.assertFalse(updates.is_release_url(None))

    def fetch_with(self, error):
        def fake_urlopen(request, timeout=None):
            self.request = request
            self.timeout = timeout
            raise error
        original = urllib.request.urlopen
        urllib.request.urlopen = fake_urlopen
        try:
            with self.assertRaises(updates.UpdateError) as ctx:
                updates.fetch_latest_release()
        finally:
            urllib.request.urlopen = original
        return str(ctx.exception)

    def http_error(self, code):
        return urllib.error.HTTPError(updates.LATEST_RELEASE_API, code, "x", {}, None)

    def test_error_messages(self):
        self.assertEqual(self.fetch_with(self.http_error(404)), "No JDeck releases are published yet.")
        for code in (403, 429):
            self.assertIn("Try again in an hour", self.fetch_with(self.http_error(code)))
        self.assertEqual(self.fetch_with(self.http_error(500)),
                         "GitHub answered with error 500. Try again later.")
        msg = self.fetch_with(urllib.error.URLError("no route"))
        self.assertEqual(msg, "Can't reach GitHub: no route. Check the internet connection.")
        self.assertIn("timed out", self.fetch_with(socket.timeout("timed out")))

    def test_request_sends_only_user_agent_and_accept(self):
        self.fetch_with(self.http_error(404))
        self.assertEqual(self.request.full_url, updates.LATEST_RELEASE_API)
        self.assertEqual(self.request.get_method(), "GET")
        self.assertIsNone(self.request.data)
        headers = {k.lower(): v for k, v in self.request.header_items()}
        self.assertEqual(set(headers), {"user-agent", "accept"})
        self.assertTrue(headers["user-agent"].startswith("JDeck/"))
        self.assertEqual(self.timeout, 10)


if __name__ == "__main__":
    unittest.main()
