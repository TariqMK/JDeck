"""JDeck Desktop: the JDeck page in its own window. Connects straight to JDownloader on the
network. Nothing is open to the network: the page is served on 127.0.0.1 for this window only."""

import argparse
import json
import os
import sys

from core import __version__
from core.edition import Edition
from core.httpd import make_server
from core.jd_api import normalise_jd_url
from core.runtime import show_error, wait_until_stopped
from core.settings import Settings, app_dir, resource_path

DESKTOP = Edition(
    name="desktop",
    title="JDeck",
    data_folder="JDeck-Desktop-data",
    defaults={"jd_url": ""},
    features={"jdAddressEditable": True, "connectionHelp": True, "signOut": False, "controlPage": False},
)

OLD_DATA_FOLDER = "JDeck-data"  # Used by JDeck v1.0.0, before the Desktop and Server editions.
REQUIRED_RESOURCES = [("web", "index.html"), ("web", "common.css"), ("assets", "icon", "jdeck.png")]


def migrate_old_settings(base_dir, settings):
    """First start only: copy the JDownloader address from the old JDeck-data folder.
    Only reads the old folder. Sharing settings and the password are not copied."""
    if os.path.exists(settings.path):
        return False
    try:
        with open(os.path.join(base_dir, OLD_DATA_FOLDER, "settings.json"), encoding="utf-8") as f:
            jd_url = normalise_jd_url(json.load(f).get("jd_url"))
    except (OSError, ValueError, AttributeError, TypeError):
        return False
    settings.update(jd_url=jd_url)
    return True


def self_check():
    """Used by the release build to prove the bundle is complete. Exit code 0 means OK."""
    missing = [os.path.join(*p) for p in REQUIRED_RESOURCES if not os.path.isfile(resource_path(*p))]
    try:
        import webview  # noqa: F401
    except Exception as e:
        missing.append(f"webview module ({e})")
    print(f"{DESKTOP.title} v{__version__}")
    for m in missing:
        print(f"Missing: {m}")
    return 1 if missing else 0


def run_window(settings, data_dir):
    local = make_server("127.0.0.1", 0, DESKTOP, settings)
    url = f"http://127.0.0.1:{local.server_address[1]}/"
    try:
        import webview
        webview.create_window(DESKTOP.title, url, width=1040, height=800, min_size=(420, 560),
                              background_color="#161715")
        options = {}
        if sys.platform.startswith("linux"):
            # Only GTK uses this; Windows crashes on a PNG here. Windows and macOS
            # take the window icon from the .exe / .app instead.
            options["icon"] = resource_path("assets", "icon", "jdeck.png")
        webview.start(private_mode=True, storage_path=os.path.join(data_dir, "webview"), **options)
    except Exception as e:
        # No window available (for example a Linux PC without WebKitGTK): use the browser.
        import webbrowser
        print(f"The app window could not open ({e}). Opening JDeck in your browser instead.")
        webbrowser.open(url)
        wait_until_stopped(f"JDeck is at {url}")
    finally:
        local.shutdown()
        local.server_close()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="jdeck-desktop",
                                     description="JDeck Desktop: a desktop app for JDownloader 2.")
    parser.add_argument("--self-check", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--version", action="version", version=f"{DESKTOP.title} v{__version__}")
    args = parser.parse_args(argv)
    if args.self_check:
        return self_check()

    base_dir = app_dir()
    data_dir = os.path.join(base_dir, DESKTOP.data_folder)
    try:
        settings = Settings(data_dir, DESKTOP.defaults)
        if not migrate_old_settings(base_dir, settings) and not os.path.exists(settings.path):
            settings.save()
    except OSError as e:
        show_error(DESKTOP.title, f"Cannot write the settings folder:\n{data_dir}\n\n{e}\n\n"
                   "Move JDeck to a folder you can write to, such as Documents.")
        return 1
    return run_window(settings, data_dir)
