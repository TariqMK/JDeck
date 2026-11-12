"""Tests for JDeck Desktop: its Edition profile and the one-time move from the old JDeck-data folder."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.settings import Settings  # noqa: E402
from desktop.app import DESKTOP, OLD_DATA_FOLDER, migrate_old_settings  # noqa: E402


class EditionTests(unittest.TestCase):
    def test_desktop_keeps_only_the_jdownloader_address(self):
        self.assertEqual(DESKTOP.defaults, {"jd_url": ""})
        self.assertEqual(DESKTOP.data_folder, "JDeck-Desktop-data")

    def test_desktop_features(self):
        self.assertTrue(DESKTOP.features["jdAddressEditable"])
        self.assertTrue(DESKTOP.features["connectionHelp"])
        self.assertFalse(DESKTOP.features["signOut"])
        self.assertFalse(DESKTOP.features["controlPage"])


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name
        self.old_file = os.path.join(self.base, OLD_DATA_FOLDER, "settings.json")
        self.settings = Settings(os.path.join(self.base, DESKTOP.data_folder), DESKTOP.defaults)

    def tearDown(self):
        self.tmp.cleanup()

    def write_old(self, content):
        os.makedirs(os.path.dirname(self.old_file))
        with open(self.old_file, "w", encoding="utf-8") as f:
            f.write(content if isinstance(content, str) else json.dumps(content))

    def test_copies_only_the_address(self):
        self.write_old({"jd_url": "jd-host.local:3128", "share_on_network": True, "port": 8080,
                        "password_salt": "aa", "password_hash": "bb"})
        self.assertTrue(migrate_old_settings(self.base, self.settings))
        with open(self.settings.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"jd_url": "http://jd-host.local:3128"})

    def test_old_folder_is_not_changed(self):
        self.write_old({"jd_url": "http://jd-host.local:3128", "password_hash": "bb"})
        with open(self.old_file, "rb") as f:
            before = f.read()
        mtime = os.path.getmtime(self.old_file)
        migrate_old_settings(self.base, self.settings)
        with open(self.old_file, "rb") as f:
            self.assertEqual(f.read(), before)
        self.assertEqual(os.path.getmtime(self.old_file), mtime)

    def test_runs_only_once(self):
        self.settings.update(jd_url="http://mine:3128")
        self.write_old({"jd_url": "http://old:3128"})
        self.assertFalse(migrate_old_settings(self.base, self.settings))
        self.assertEqual(Settings(self.settings.data_dir, DESKTOP.defaults).get("jd_url"), "http://mine:3128")

    def test_nothing_to_move(self):
        self.assertFalse(migrate_old_settings(self.base, self.settings))
        self.assertFalse(os.path.exists(self.settings.path))

    def test_unreadable_old_file_is_skipped(self):
        for content in ("not json", ["a list"], {"jd_url": ""}, {"jd_url": 5}, {"other": 1}):
            with self.subTest(content=content):
                if os.path.exists(self.old_file):
                    os.remove(self.old_file)
                    os.rmdir(os.path.dirname(self.old_file))
                self.write_old(content)
                self.assertFalse(migrate_old_settings(self.base, self.settings))
                self.assertFalse(os.path.exists(self.settings.path))


if __name__ == "__main__":
    unittest.main()
