"""Tests for JDeck: link parsing, JDownloader URL building, settings, passwords,
and the rules for the app window and optional network sharing."""

import base64
import json
import os
import socket
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import jdeck  # noqa: E402


class ExtractLinksTests(unittest.TestCase):
    def test_finds_links_in_free_text(self):
        text = "Grab https://example.com/a.zip and also\nhttp://example.org/b.rar, thanks!"
        self.assertEqual(jdeck.extract_links(text),
                         ["https://example.com/a.zip", "http://example.org/b.rar"])

    def test_removes_duplicates_and_keeps_order(self):
        text = "https://x.test/2 https://x.test/1 https://x.test/2"
        self.assertEqual(jdeck.extract_links(text), ["https://x.test/2", "https://x.test/1"])

    def test_strips_trailing_punctuation_and_brackets(self):
        self.assertEqual(jdeck.extract_links("(see https://x.test/file.)"), ["https://x.test/file"])

    def test_keeps_query_strings(self):
        link = "https://www.youtube.com/watch?v=abc123&t=10"
        self.assertEqual(jdeck.extract_links(link), [link])

    def test_ignores_text_without_links(self):
        self.assertEqual(jdeck.extract_links("no links here"), [])
        self.assertEqual(jdeck.extract_links(None), [])


class JDUrlTests(unittest.TestCase):
    def test_each_parameter_is_encoded_json_joined_with_ampersand(self):
        url = jdeck.build_jd_url("http://h:3128/", "/linkgrabberv2/moveToDownloadlist", [[], [5, 6]])
        path, _, query = url.partition("?")
        self.assertEqual(path, "http://h:3128/linkgrabberv2/moveToDownloadlist")
        self.assertEqual([json.loads(urllib.parse.unquote(p)) for p in query.split("&")], [[], [5, 6]])

    def test_ampersands_inside_values_cannot_split_parameters(self):
        url = jdeck.build_jd_url("http://h:3128", "/linkgrabberv2/addLinks",
                                     [{"links": "https://a.test/?x=1&y=2"}])
        query = url.partition("?")[2]
        self.assertNotIn("&", query)
        self.assertEqual(json.loads(urllib.parse.unquote(query))["links"], "https://a.test/?x=1&y=2")

    def test_no_parameters_means_no_query(self):
        self.assertEqual(jdeck.build_jd_url("http://h:3128", "/jd/version", ()), "http://h:3128/jd/version")

    def test_normalise_accepts_short_forms(self):
        self.assertEqual(jdeck.normalise_jd_url("jd-host"), "http://jd-host:3128")
        self.assertEqual(jdeck.normalise_jd_url("jd-host:4000/"), "http://jd-host:4000")
        self.assertEqual(jdeck.normalise_jd_url(" http://nas.local:3128 "), "http://nas.local:3128")

    def test_normalise_rejects_bad_input(self):
        for bad in ("", "ftp://x", "http://"):
            with self.assertRaises(ValueError):
                jdeck.normalise_jd_url(bad)


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "data")

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_without_a_file(self):
        s = jdeck.Settings(self.dir)
        self.assertEqual(s.get("port"), 8080)
        self.assertFalse(s.get("share_on_network"), "Network sharing must be off by default")
        self.assertFalse(s.has_password)

    def test_no_password_means_nobody_signs_in(self):
        s = jdeck.Settings(self.dir)
        self.assertFalse(s.check_password(""))
        self.assertFalse(s.check_password("anything"))

    def test_password_is_hashed_checked_and_persisted(self):
        s = jdeck.Settings(self.dir)
        s.set_password("hunter2")
        with open(s.path, encoding="utf-8") as f:
            self.assertNotIn("hunter2", f.read())
        reloaded = jdeck.Settings(self.dir)
        self.assertTrue(reloaded.check_password("hunter2"))
        self.assertFalse(reloaded.check_password("wrong"))

    def test_empty_password_is_refused(self):
        s = jdeck.Settings(self.dir)
        s.set_password("x")
        with self.assertRaises(ValueError):
            s.set_password("")
        self.assertTrue(s.check_password("x"))

    def test_unknown_keys_in_file_are_ignored(self):
        os.makedirs(self.dir)
        with open(os.path.join(self.dir, "settings.json"), "w", encoding="utf-8") as f:
            json.dump({"port": 9000, "junk": 1}, f)
        s = jdeck.Settings(self.dir)
        self.assertEqual(s.get("port"), 9000)
        self.assertNotIn("junk", s.values)


class SmallHelperTests(unittest.TestCase):
    def test_id_list_accepts_numbers_only(self):
        self.assertEqual(jdeck.id_list([1, 2]), [1, 2])
        for bad in (None, "1", [1, "2"]):
            with self.assertRaises(ValueError):
                jdeck.id_list(bad)

    def test_local_host_check_blocks_other_names(self):
        self.assertTrue(jdeck.local_host_allowed("127.0.0.1:5000", 5000))
        self.assertTrue(jdeck.local_host_allowed("localhost:5000", 5000))
        for bad in ("evil.test:5000", "127.0.0.1:6000", "", "127.0.0.1"):
            self.assertFalse(jdeck.local_host_allowed(bad, 5000), bad)

    def test_parse_port(self):
        self.assertEqual(jdeck.parse_port("8080"), 8080)
        for bad in ("abc", None, 80, 70000):
            with self.assertRaises(ValueError):
                jdeck.parse_port(bad)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServerTests(unittest.TestCase):
    """Runs the real servers (no JDownloader needed) and checks who may do what."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = jdeck.Settings(os.path.join(self.tmp.name, "data"))
        self.settings.update(port=free_port())
        self.sharing = jdeck.Sharing(self.settings)
        self.local = jdeck.make_server("127.0.0.1", 0, "local", self.settings, self.sharing)
        self.local_url = f"http://127.0.0.1:{self.local.server_address[1]}"

    def tearDown(self):
        self.sharing.stop()
        self.local.shutdown()
        self.local.server_close()
        self.tmp.cleanup()

    def request(self, url, body=None, headers=None, password=None):
        headers = dict(headers or {})
        if body is not None:
            headers.setdefault("X-JDeck", "1")
            headers["Content-Type"] = "application/json"
        if password is not None:
            headers["Authorization"] = "Basic " + base64.b64encode(f"any:{password}".encode()).decode()
        req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                     headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            with e:
                raw = e.read()
            return e.code, (json.loads(raw) if raw else {})

    def test_window_page_is_served_without_password(self):
        status, data = self.request(self.local_url + "/api/settings")
        self.assertEqual(status, 200)
        self.assertTrue(data["isLocal"])
        self.assertFalse(data["sharing"]["enabled"])

    def test_window_server_rejects_other_host_names(self):
        status, _ = self.request(self.local_url + "/api/settings", headers={"Host": "evil.test"})
        self.assertEqual(status, 403)

    def test_post_without_jdeck_header_is_rejected(self):
        status, _ = self.request(self.local_url + "/api/settings", body={}, headers={"X-JDeck": "0"})
        self.assertEqual(status, 403)

    def test_sharing_needs_a_password(self):
        status, data = self.request(self.local_url + "/api/settings", body={"shareOnNetwork": True})
        self.assertEqual(status, 400)
        self.assertIn("password", data["error"])
        self.assertFalse(self.sharing.running)

    def test_sharing_starts_requires_password_and_stops(self):
        port = self.settings.get("port")
        status, data = self.request(self.local_url + "/api/settings",
                                    body={"shareOnNetwork": True, "password": "pw1", "port": port})
        self.assertEqual(status, 200, data)
        self.assertTrue(self.sharing.running)
        net = f"http://127.0.0.1:{port}"
        self.assertEqual(self.request(net + "/api/settings")[0], 401)
        self.assertEqual(self.request(net + "/api/settings", password="wrong")[0], 401)
        status, data = self.request(net + "/api/settings", password="pw1")
        self.assertEqual(status, 200)
        self.assertFalse(data["isLocal"])

        # Other devices cannot change sharing, the password or the port.
        status, _ = self.request(net + "/api/settings", body={"shareOnNetwork": False}, password="pw1")
        self.assertEqual(status, 403)
        self.assertTrue(self.sharing.running)

        status, _ = self.request(self.local_url + "/api/settings", body={"shareOnNetwork": False})
        self.assertEqual(status, 200)
        self.assertFalse(self.sharing.running)
        self.assertFalse(self.settings.get("share_on_network"))

    def test_open_release_is_window_only(self):
        self.settings.set_password("pw")
        self.settings.update(share_on_network=True)
        self.sharing.start()
        port = self.settings.get("port")
        status, _ = self.request(f"http://127.0.0.1:{port}/api/open-release",
                                 body={"url": jdeck.RELEASES_PAGE}, password="pw")
        self.assertEqual(status, 403)

    def test_port_in_use_is_reported(self):
        with socket.socket() as blocker:
            blocker.bind(("0.0.0.0", 0))
            blocker.listen()
            busy = blocker.getsockname()[1]
            status, data = self.request(self.local_url + "/api/settings",
                                        body={"shareOnNetwork": True, "password": "pw", "port": busy})
        self.assertEqual(status, 200)
        self.assertFalse(data["sharing"]["running"])
        self.assertIn(str(busy), data["sharing"]["error"])


class UpdateCheckTests(unittest.TestCase):
    """The manual update check, with the GitHub call stubbed (no network)."""

    SAMPLE = {"tag_name": "v1.4.0", "html_url": "https://github.com/TariqMK/JDeck/releases/tag/v1.4.0",
              "body": "- Faster refresh.", "draft": False, "prerelease": False}

    def check(self, current, release=None):
        return jdeck.check_for_update(current, fetch=lambda: dict(release or self.SAMPLE))

    def test_parse_version(self):
        self.assertEqual(jdeck.parse_version("v1.2.3"), (1, 2, 3))
        self.assertEqual(jdeck.parse_version("1.2.3"), (1, 2, 3))
        self.assertIsNone(jdeck.parse_version("0.0.0.dev0"))
        self.assertIsNone(jdeck.parse_version("v1.2"))
        self.assertIsNone(jdeck.parse_version(None))

    def test_compares_as_numbers(self):
        self.assertGreater(jdeck.parse_version("0.10.0"), jdeck.parse_version("0.9.0"))
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
            self.assertEqual(r["url"], jdeck.RELEASES_PAGE)

    def test_odd_tag_is_unreadable(self):
        with self.assertRaisesRegex(jdeck.UpdateError, "can't read"):
            self.check("1.0.0", {**self.SAMPLE, "tag_name": "latest"})

    def test_release_url_check(self):
        self.assertTrue(jdeck.is_release_url(jdeck.RELEASES_PAGE))
        self.assertTrue(jdeck.is_release_url(self.SAMPLE["html_url"]))
        self.assertFalse(jdeck.is_release_url("https://github.com/TariqMK/JDeck/releasesX"))
        self.assertFalse(jdeck.is_release_url("https://evil.test/"))
        self.assertFalse(jdeck.is_release_url(None))

    def fetch_with(self, error):
        def fake_urlopen(request, timeout=None):
            self.request = request
            self.timeout = timeout
            raise error
        original = urllib.request.urlopen
        urllib.request.urlopen = fake_urlopen
        try:
            with self.assertRaises(jdeck.UpdateError) as ctx:
                jdeck.fetch_latest_release()
        finally:
            urllib.request.urlopen = original
        return str(ctx.exception)

    def http_error(self, code):
        return urllib.error.HTTPError(jdeck.LATEST_RELEASE_API, code, "x", {}, None)

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
        self.assertEqual(self.request.full_url, jdeck.LATEST_RELEASE_API)
        self.assertEqual(self.request.get_method(), "GET")
        self.assertIsNone(self.request.data)
        headers = {k.lower(): v for k, v in self.request.header_items()}
        self.assertEqual(set(headers), {"user-agent", "accept"})
        self.assertTrue(headers["user-agent"].startswith("JDeck/"))
        self.assertEqual(self.timeout, 10)


if __name__ == "__main__":
    unittest.main()
