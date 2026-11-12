"""Takes JDeck's README screenshots from the real app, with made-up sample data.

Writes assets/screenshots/<view>-light.png and <view>-dark.png (1040 x 800 px window, saved at 2x):
  downloads  the whole window: running, finished and failed downloads
  add-links  two pasted links, the LinkGrabber and more downloads
  settings   the Settings dialog

How it works:
  1. Copies the app (jdeck_desktop.py, core/, desktop/, web/, assets/icon/) to a temporary folder, so JDeck creates
     its own data folder there. Your real JDeck-Desktop-data/ is never read or changed.
  2. Starts a fake JDownloader on 127.0.0.1 that answers with the sample data below.
     Nothing is sent to the network.
  3. Opens the app's page at the window's size, fills in the view and saves it.

Run it from the project folder:
  python -m pip install PySide6
  python assets/make_screenshots.py [--version-label 1.2.0]

The version label defaults to the newest release tag (1.0.0 if there is none). It is set only in
the temporary copy, never in the source.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "screenshots"
WINDOW = (1040, 800)  # Same as the app window in desktop/app.py.
SCALE = "2"
FAKE_JD_ADDRESS = "http://jd-host.local:3128"  # Shown in Settings. Never contacted.

GB, MB = 1024 ** 3, 1024 ** 2

# ---------------------------------------------------------------- sample data (all made up)

DOWNLOADS = [
    {"uuid": 1, "name": "Big Buck Bunny (4K)", "bytesTotal": int(3.4 * GB), "bytesLoaded": int(2.1 * GB),
     "running": True, "speed": int(18.6 * MB), "eta": 75, "status": "Downloading", "childCount": 1},
    {"uuid": 2, "name": "Debian 13 DVD images", "bytesTotal": int(7.8 * GB), "bytesLoaded": int(2.2 * GB),
     "running": True, "speed": int(9.1 * MB), "eta": 640, "status": "Downloading", "childCount": 2},
    {"uuid": 3, "name": "Field recordings, spring 2025", "bytesTotal": 812 * MB, "bytesLoaded": 812 * MB,
     "finished": True, "status": "Finished", "childCount": 24},
    {"uuid": 4, "name": "Conference talks 2024", "bytesTotal": int(1.1 * GB), "bytesLoaded": 430 * MB,
     "status": "Download failed", "childCount": 6},
    {"uuid": 5, "name": "Lecture notes", "bytesTotal": 240 * MB, "bytesLoaded": 0,
     "status": "Waiting", "childCount": 12},
]
LINKGRABBER = [
    {"uuid": 11, "name": "Sintel (1080p)", "childCount": 2, "bytesTotal": int(1.3 * GB),
     "hosts": ["example.com"]},
    {"uuid": 12, "name": "Photo archive 2023", "childCount": 14, "bytesTotal": int(4.6 * GB),
     "offlineCount": 1, "hosts": ["files.example.net"]},
]
PASTED = ("https://example.com/videos/tears-of-steel-4k.mkv\n"
          "https://files.example.net/share/a7Kq2/holiday-photos.zip")
REPLIES = {
    "/downloadcontroller/getCurrentState": "RUNNING",
    "/downloadcontroller/getSpeedInBps": int(18.6 * MB),
    # The "downloads" view shows three packages and an empty LinkGrabber, so it fits the window.
    "/downloadsV2/queryPackages": [DOWNLOADS[i] for i in (0, 2, 3)],
    "/linkgrabberv2/queryPackages": [],
}
FULL_REPLIES = dict(REPLIES, **{
    "/downloadcontroller/getSpeedInBps": int(27.7 * MB),
    "/downloadsV2/queryPackages": DOWNLOADS,
    "/linkgrabberv2/queryPackages": LINKGRABBER,
})


class FakeJD(BaseHTTPRequestHandler):
    """Answers JDeck's read calls the way JDownloader's local API does."""

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        body = json.dumps({"data": REPLIES.get(path)}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


# ---------------------------------------------------------------- one theme, in its own process

def copy_app(dest: Path, version_label: str) -> None:
    shutil.copy2(ROOT / "jdeck_desktop.py", dest)
    for package in ("core", "desktop"):
        shutil.copytree(ROOT / package, dest / package, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "web", dest / "web")
    shutil.copytree(ROOT / "assets" / "icon", dest / "assets" / "icon")
    (dest / "_version.py").write_text(f'__version__ = "{version_label}"\n', encoding="utf-8")
    data = dest / "JDeck-Desktop-data"
    data.mkdir()
    (data / "settings.json").write_text(json.dumps({"jd_url": FAKE_JD_ADDRESS}), encoding="utf-8")


def capture(theme: str, version_label: str) -> None:
    from PySide6.QtCore import Qt, QTimer, QUrl
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QApplication

    temp = Path(tempfile.mkdtemp(prefix="jdeck-shots-"))
    try:
        copy_app(temp, version_label)
        sys.path.insert(0, str(temp))
        from core import httpd, jd_api, settings as settings_mod  # The temporary copy.
        from desktop.app import DESKTOP

        fake = ThreadingHTTPServer(("127.0.0.1", 0), FakeJD)
        threading.Thread(target=fake.serve_forever, daemon=True).start()
        fake_base = f"http://127.0.0.1:{fake.server_address[1]}"
        real_build = jd_api.build_jd_url
        jd_api.build_jd_url = lambda base, path, params: real_build(fake_base, path, params)

        settings = settings_mod.Settings(str(temp / "JDeck-Desktop-data"), DESKTOP.defaults)
        server = httpd.make_server("127.0.0.1", 0, DESKTOP, settings)
        url = f"http://127.0.0.1:{server.server_address[1]}/"

        app = QApplication(sys.argv[:1])
        app.styleHints().setColorScheme(Qt.ColorScheme.Dark if theme == "dark" else Qt.ColorScheme.Light)
        view = QWebEngineView()
        view.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        view.resize(*WINDOW)
        view.show()
        OUT.mkdir(parents=True, exist_ok=True)

        def js(code):
            view.page().runJavaScript(code)

        def save(name):
            path = OUT / f"{name}-{theme}.png"
            view.grab().save(str(path))
            print(f"Wrote {path.relative_to(ROOT)}")

        steps = [
            (1500, lambda: save("downloads")),
            (100, lambda: REPLIES.update(FULL_REPLIES)),
            (2000, lambda: js(f"""const t = document.getElementById('links');
                t.value = {json.dumps(PASTED)}; t.dispatchEvent(new Event('input'));""")),
            (800, lambda: save("add-links")),
            (100, lambda: js("document.getElementById('open-settings').click();")),
            (900, lambda: save("settings")),
            (100, app.quit),
        ]

        def run_steps(ok):
            delay = 0
            for wait, step in steps:
                delay += wait
                QTimer.singleShot(delay, step)

        view.loadFinished.connect(run_steps)
        view.load(QUrl(url))
        app.exec()
        server.shutdown()
        fake.shutdown()
    finally:
        shutil.rmtree(temp, ignore_errors=True)


# ---------------------------------------------------------------- entry point

def newest_tag() -> str:
    try:
        tags = subprocess.run(["git", "tag", "--list", "v*", "--sort=-v:refname"], cwd=ROOT,
                              capture_output=True, text=True, check=True).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        tags = []
    return tags[0].lstrip("v") if tags else "1.0.0"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version-label", help="version shown next to the title")
    parser.add_argument("--theme", choices=["light", "dark"], help=argparse.SUPPRESS)
    args = parser.parse_args()
    label = args.version_label or newest_tag()
    if args.theme:
        capture(args.theme, label)
        return 0
    # Each theme runs in its own process, because the colour scheme is fixed at start-up.
    env = dict(os.environ, QT_ENABLE_HIGHDPI_SCALING="0", QT_SCALE_FACTOR=SCALE)  # Exactly 2x.
    for theme in ("light", "dark"):
        flag = "preferredColorScheme=" + ("0" if theme == "dark" else "1")
        env["QTWEBENGINE_CHROMIUM_FLAGS"] = f"--blink-settings={flag}"
        result = subprocess.run([sys.executable, __file__, "--theme", theme, "--version-label", label], env=env)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
