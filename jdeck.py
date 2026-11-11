"""JDeck: a desktop app for JDownloader 2. Send links and watch downloads through
JDownloader's local API (no cloud). Optionally lets phones and other devices on
your network use it too. Run `python jdeck.py`, or `python jdeck.py --headless`."""

import argparse
import base64
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from _version import __version__

APP_NAME = "JDeck"
DATA_FOLDER_NAME = "JDeck-data"
CSRF_HEADER = "X-JDeck"  # Every POST must carry this header (blocks cross-site requests).
GITHUB_REPO = "TariqMK/JDeck"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

DEFAULT_SETTINGS = {
    "jd_url": "http://127.0.0.1:3128",
    "share_on_network": False,
    "port": 8080,
    "password_salt": "",
    "password_hash": "",
}

URL_PATTERN = re.compile(r"(?:https?|ftp)://[^\s<>\"'`]+", re.IGNORECASE)
TRAILING_PUNCTUATION = ".,;:!?)]}>'\""

PACKAGE_FIELDS = {
    "bytesLoaded": True, "bytesTotal": True, "childCount": True, "enabled": True,
    "eta": True, "finished": True, "hosts": True, "running": True, "saveTo": True,
    "speed": True, "status": True,
}
LINKGRABBER_FIELDS = {
    "bytesTotal": True, "childCount": True, "hosts": True, "saveTo": True,
    "availableOnlineCount": True, "availableOfflineCount": True, "status": True,
}

CLOSE_WARNING = ("Other devices are connected to JDeck through your network.\n\n"
                 "If you close JDeck, they lose access. Close JDeck?")


# ---------------------------------------------------------------- paths & settings

def app_dir():
    """Folder that holds the program: next to the .exe when packaged, else the project root."""
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        # macOS: the executable is inside JDeck.app/Contents/MacOS; use the folder beside the .app.
        suffix = os.sep + "Contents" + os.sep + "MacOS"
        if sys.platform == "darwin" and exe_dir.endswith(".app" + suffix):
            return os.path.dirname(exe_dir[: -len(suffix)])
        return exe_dir
    return os.path.dirname(os.path.abspath(__file__))


def resource_path(*parts):
    """Path to a bundled file (inside the PyInstaller bundle when packaged)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


class Settings:
    """Settings stored as JSON in the data folder next to the program."""

    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.path = os.path.join(data_dir, "settings.json")
        self.lock = threading.RLock()
        self.values = dict(DEFAULT_SETTINGS)
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                stored = json.load(f)
            self.values.update({k: v for k, v in stored.items() if k in DEFAULT_SETTINGS})

    def save(self):
        with self.lock:
            os.makedirs(self.data_dir, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.values, f, indent=2)
            os.replace(tmp, self.path)

    def get(self, key):
        with self.lock:
            return self.values[key]

    @property
    def has_password(self):
        return bool(self.get("password_hash"))

    def set_password(self, password):
        if not password:
            raise ValueError("The password cannot be empty.")
        with self.lock:
            salt = secrets.token_hex(16)
            self.values["password_salt"] = salt
            self.values["password_hash"] = hash_password(password, salt)
            self.save()

    def check_password(self, password):
        with self.lock:
            stored, salt = self.values["password_hash"], self.values["password_salt"]
        if not stored:
            return False  # No password means nobody can sign in from the network.
        return hmac.compare_digest(stored, hash_password(password, salt))

    def update(self, **changes):
        with self.lock:
            self.values.update(changes)
            self.save()


def hash_password(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()


# ---------------------------------------------------------------- link handling

def extract_links(text):
    """Find every http/https/ftp link in pasted text, in order, without duplicates."""
    found = []
    seen = set()
    for match in URL_PATTERN.findall(text or ""):
        link = match.rstrip(TRAILING_PUNCTUATION)
        if link and link not in seen:
            seen.add(link)
            found.append(link)
    return found


def normalise_jd_url(value):
    """Accept 'jd-host', 'jd-host:3128' or a full URL; return 'http://host:port'."""
    value = (value or "").strip()
    if not value:
        raise ValueError("Enter the JDownloader address.")
    if "://" not in value:
        value = "http://" + value
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("The JDownloader address is not valid.")
    port = parsed.port or 3128
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    return f"{parsed.scheme}://{host}:{port}"


def local_host_allowed(host_header, port):
    """The app window's server only answers requests addressed to itself.
    This blocks DNS-rebinding attacks from websites open in a browser on this PC."""
    return host_header in (f"127.0.0.1:{port}", f"localhost:{port}")


def parse_port(value):
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise ValueError("The port must be a number.") from None
    if not 1024 <= port <= 65535:
        raise ValueError("The port must be between 1024 and 65535.")
    return port


# ---------------------------------------------------------------- JDownloader client

class JDError(Exception):
    """A JDownloader call failed. `offline` is True when JDownloader could not be reached."""

    def __init__(self, message, offline=False):
        super().__init__(message)
        self.offline = offline


def build_jd_url(base_url, path, params):
    """JD's local API takes each parameter as a JSON value, joined with '&'."""
    query = "&".join(
        urllib.parse.quote(json.dumps(p, separators=(",", ":")), safe="") for p in params
    )
    return base_url.rstrip("/") + path + ("?" + query if query else "")


def jd_call(base_url, path, *params, timeout=8):
    url = build_jd_url(base_url, path, params)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read()).get("type", "")
        except Exception:
            pass
        raise JDError(f"JDownloader refused the request ({detail or e.code}).") from e
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
        raise JDError("Cannot reach JDownloader. Check the address and that its local API is on.",
                      offline=True) from e
    try:
        return json.loads(body).get("data") if body else None
    except ValueError as e:
        raise JDError("JDownloader sent a reply that could not be read.") from e


def jd_state(base_url):
    """Everything the page shows, in one call."""
    state = jd_call(base_url, "/downloadcontroller/getCurrentState")
    speed = jd_call(base_url, "/downloadcontroller/getSpeedInBps")
    downloads = jd_call(base_url, "/downloadsV2/queryPackages", PACKAGE_FIELDS) or []
    grabber = jd_call(base_url, "/linkgrabberv2/queryPackages", LINKGRABBER_FIELDS) or []
    return {"state": state, "speed": speed, "downloads": downloads, "linkgrabber": grabber}


def jd_add_links(base_url, links, autostart, package_name="", destination=""):
    query = {"links": "\n".join(links), "autostart": bool(autostart)}
    if package_name:
        query["packageName"] = package_name
    if destination:
        query["destinationFolder"] = destination
    return jd_call(base_url, "/linkgrabberv2/addLinks", query)


def id_list(value):
    if not isinstance(value, list) or not all(isinstance(v, int) for v in value):
        raise ValueError("Package IDs must be a list of numbers.")
    return value


# ---------------------------------------------------------------- update check
# Runs only when the user selects the version. One GET to GitHub, nothing stored.

class UpdateError(Exception):
    """The update check failed. The message is written for the user."""


def parse_version(text):
    """'v1.2.3' or '1.2.3' -> (1, 2, 3). Anything else (such as a dev build) -> None."""
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(text or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def fetch_latest_release(timeout=10):
    request = urllib.request.Request(LATEST_RELEASE_API, headers={
        "User-Agent": f"{APP_NAME}/{__version__}",
        "Accept": "application/vnd.github+json",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError(f"No {APP_NAME} releases are published yet.") from e
        if e.code in (403, 429):
            raise UpdateError("GitHub limits how often it can be checked. Try again in an hour.") from e
        raise UpdateError(f"GitHub answered with error {e.code}. Try again later.") from e
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
        reason = getattr(e, "reason", None) or e
        raise UpdateError(f"Can't reach GitHub: {reason}. Check the internet connection.") from e
    try:
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ValueError
        return data
    except ValueError as e:
        raise UpdateError(f"GitHub sent an answer {APP_NAME} can't read. Try again later.") from e


def check_for_update(current=__version__, fetch=fetch_latest_release):
    """Compare this build with GitHub's latest release. Returns a dict for the page."""
    release = fetch()
    tag = str(release.get("tag_name") or "")
    latest = parse_version(tag)
    if latest is None:
        raise UpdateError(f"GitHub sent an answer {APP_NAME} can't read. Try again later.")
    url = release.get("html_url")
    if not isinstance(url, str) or not url.startswith("https://"):
        url = RELEASES_PAGE
    notes = release.get("body")
    mine = parse_version(current)
    if mine is None:
        status = "dev"
    elif latest > mine:
        status = "newer"
    else:
        status = "current"
    return {"status": status, "current": "v" + current.lstrip("v"),
            "latest": "v{}.{}.{}".format(*latest), "url": url,
            "notes": notes if isinstance(notes, str) else ""}


def is_release_url(url):
    return isinstance(url, str) and (url == RELEASES_PAGE or url.startswith(RELEASES_PAGE + "/"))


# ---------------------------------------------------------------- network sharing

def lan_addresses():
    """Best guess at this PC's LAN IP."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 80))  # TEST-NET address: nothing is sent.
            return [s.getsockname()[0]]
    except OSError:
        return []


class Sharing:
    """The optional server that lets other devices on the network use JDeck."""

    def __init__(self, settings):
        self.settings = settings
        self.server = None
        self.error = ""
        self.lock = threading.Lock()
        self.on_change = None  # Called with True/False when sharing starts or stops.

    @property
    def running(self):
        return self.server is not None

    def address(self):
        if not self.running:
            return ""
        lan = lan_addresses()
        return f"http://{lan[0] if lan else 'this-pc'}:{self.server.server_address[1]}"

    def start(self):
        with self.lock:
            self._stop()
            self.error = ""
            port = self.settings.get("port")
            try:
                self.server = make_server("0.0.0.0", port, "network", self.settings, self)
            except OSError as e:
                self.server = None
                self.error = f"Port {port} is in use by another program. Choose another port. ({e.strerror or e})"
        self._notify()

    def stop(self):
        with self.lock:
            self._stop()
            self.error = ""
        self._notify()

    def _stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None

    def _notify(self):
        if self.on_change:
            self.on_change(self.running)

    def info(self):
        return {"enabled": self.settings.get("share_on_network"), "running": self.running,
                "address": self.address(), "error": self.error}


# ---------------------------------------------------------------- web server

class Handler(BaseHTTPRequestHandler):
    """Serves the page and JSON API. `self.server.mode` is "local" (the app window,
    127.0.0.1 only, no password) or "network" (other devices, password required)."""

    server_version = "JDeck"

    def log_message(self, fmt, *args):  # Keep the console quiet.
        pass

    @property
    def settings(self):
        return self.server.settings

    @property
    def sharing(self):
        return self.server.sharing

    @property
    def is_local(self):
        return self.server.mode == "local"

    # --- helpers

    def send_json(self, data, status=HTTPStatus.OK):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, message, status=HTTPStatus.BAD_REQUEST, **extra):
        self.send_json({"error": message, **extra}, status)

    def authorised(self):
        if self.is_local:
            if local_host_allowed(self.headers.get("Host", ""), self.server.server_address[1]):
                return True
            self.send_error_json("Forbidden.", HTTPStatus.FORBIDDEN)
            return False
        header = self.headers.get("Authorization", "")
        if header.startswith("Basic "):
            try:
                _, _, password = base64.b64decode(header[6:]).decode().partition(":")
            except Exception:
                password = None
            if password is not None and self.settings.check_password(password):
                return True
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", f'Basic realm="{APP_NAME}", charset="UTF-8"')
        self.send_header("Content-Length", "0")
        self.end_headers()
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

    def meta(self):
        return {"version": __version__, "jdUrl": self.settings.get("jd_url"),
                "isLocal": self.is_local, "sharing": self.sharing.info()}

    # --- routes

    def do_GET(self):
        if not self.authorised():
            return
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            with open(resource_path("web", "index.html"), "rb") as f:
                body = f.read()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif path == "/icon.png":
            with open(resource_path("assets", "icon", "jdeck.png"), "rb") as f:
                body = f.read()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/state":
            base = self.settings.get("jd_url")
            try:
                self.send_json({"ok": True, **self.meta(), **jd_state(base)})
            except JDError as e:
                self.send_json({"ok": False, "offline": e.offline, "error": str(e), **self.meta()})
        elif path == "/api/update-check":
            try:
                self.send_json({"ok": True, **check_for_update()})
            except UpdateError as e:
                self.send_json({"ok": False, "error": str(e)})
        elif path == "/api/settings":
            self.send_json({**self.meta(), "port": self.settings.get("port"),
                            "hasPassword": self.settings.has_password})
        else:
            self.send_error_json("Not found.", HTTPStatus.NOT_FOUND)

    def do_POST(self):
        if not self.authorised():
            return
        if self.headers.get(CSRF_HEADER) != "1":
            self.send_error_json("Missing request header.", HTTPStatus.FORBIDDEN)
            return
        path = urllib.parse.urlparse(self.path).path
        base = self.settings.get("jd_url")
        try:
            data = self.read_json()
            if path == "/api/add":
                links = extract_links(data.get("text", ""))
                if not links:
                    raise ValueError("No links found. Paste links that start with http://, https:// or ftp://.")
                jd_add_links(base, links, data.get("autostart", True),
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
                jd_call(base, *calls[action])
                self.send_json({"ok": True})
            elif path == "/api/linkgrabber/start":
                ids = id_list(data.get("packageIds", []))
                if not ids:
                    grabber = jd_call(base, "/linkgrabberv2/queryPackages", {}) or []
                    ids = [p["uuid"] for p in grabber]
                if ids:
                    jd_call(base, "/linkgrabberv2/moveToDownloadlist", [], ids)
                    jd_call(base, "/downloadcontroller/start")
                self.send_json({"ok": True, "moved": len(ids)})
            elif path == "/api/linkgrabber/remove":
                jd_call(base, "/linkgrabberv2/removeLinks", [], id_list(data.get("packageIds")))
                self.send_json({"ok": True})
            elif path == "/api/downloads/remove":
                # Removes entries from JDownloader's list only. Files on disk are not touched.
                jd_call(base, "/downloadsV2/removeLinks", [], id_list(data.get("packageIds")))
                self.send_json({"ok": True})
            elif path == "/api/open-release":
                # Opens the release page in the user's own browser on this PC (window only).
                if not self.is_local:
                    raise PermissionError("Only the JDeck window can open pages on this computer.")
                url = data.get("url")
                if not is_release_url(url):
                    url = RELEASES_PAGE
                import webbrowser
                webbrowser.open(url)
                self.send_json({"ok": True})
            elif path == "/api/settings":
                self.save_settings(data)
                self.send_json({"ok": True, "sharing": self.sharing.info()})
            else:
                self.send_error_json("Not found.", HTTPStatus.NOT_FOUND)
        except JDError as e:
            self.send_error_json(str(e), HTTPStatus.BAD_GATEWAY, offline=e.offline)
        except PermissionError as e:
            self.send_error_json(str(e), HTTPStatus.FORBIDDEN)
        except (ValueError, TypeError) as e:
            self.send_error_json(str(e) or "The request is not valid.")

    def save_settings(self, data):
        sharing_keys = {"shareOnNetwork", "password", "port"}
        if not self.is_local and sharing_keys & data.keys():
            raise PermissionError("Network sharing can only be changed in the JDeck window.")

        changes = {}
        if "jdUrl" in data:
            changes["jd_url"] = normalise_jd_url(data["jdUrl"])
        if "port" in data:
            changes["port"] = parse_port(data["port"])
        password = str(data.get("password") or "")
        share = data.get("shareOnNetwork", self.settings.get("share_on_network"))
        if share and not password and not self.settings.has_password:
            raise ValueError("Set a password before you turn on network sharing.")

        if password:
            self.settings.set_password(password)
        port_changed = "port" in changes and changes["port"] != self.settings.get("port")
        was_on = self.settings.get("share_on_network")
        changes["share_on_network"] = bool(share)
        self.settings.update(**changes)

        if share and (not was_on or port_changed or not self.sharing.running):
            self.sharing.start()
        elif not share and was_on:
            self.sharing.stop()


def make_server(host, port, mode, settings, sharing):
    """Start a server in a background thread and return it."""
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.mode = mode
    server.settings = settings
    server.sharing = sharing
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# ---------------------------------------------------------------- start-up

REQUIRED_RESOURCES = [("web", "index.html"), ("assets", "icon", "jdeck.png")]


def self_check():
    """Used by the release build to prove the bundle is complete. Exit code 0 means OK."""
    missing = [os.path.join(*p) for p in REQUIRED_RESOURCES if not os.path.isfile(resource_path(*p))]
    try:
        import webview  # noqa: F401
    except Exception as e:
        missing.append(f"webview module ({e})")
    print(f"{APP_NAME} v{__version__}")
    for m in missing:
        print(f"Missing: {m}")
    return 1 if missing else 0


def show_error(message):
    """Show an error even when there is no console window (packaged Windows app)."""
    if sys.stderr:
        print(message, file=sys.stderr)
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x10)


def wait_for_ctrl_c(message):
    print(message)
    print("Press Ctrl+C to stop.")
    stop = threading.Event()
    try:
        while not stop.wait(1):  # Short waits so Ctrl+C works on Windows.
            pass
    except KeyboardInterrupt:
        pass


def run_headless(settings, sharing):
    """No window: only the network page. For a server or a PC without a desktop."""
    if not settings.has_password:
        print("Network sharing needs a password. Other devices will use it to sign in.")
        password = getpass.getpass("New password: ")
        if not password or password != getpass.getpass("Repeat the password: "):
            print("The passwords are empty or do not match.")
            return 1
        settings.set_password(password)
    settings.update(share_on_network=True)
    sharing.start()
    if not sharing.running:
        print(sharing.error)
        return 1
    print(f"JDownloader: {settings.get('jd_url')}")
    wait_for_ctrl_c(f"Open {sharing.address()} on any device on your network.")
    sharing.stop()
    return 0


def run_window(settings, sharing, data_dir):
    local = make_server("127.0.0.1", 0, "local", settings, sharing)
    url = f"http://127.0.0.1:{local.server_address[1]}/"
    try:
        import webview
        window = webview.create_window(APP_NAME, url, width=1040, height=800, min_size=(420, 560),
                                       background_color="#161715")
        window.confirm_close = sharing.running
        sharing.on_change = lambda running: setattr(window, "confirm_close", running)
        options = {}
        if sys.platform.startswith("linux"):
            # Only GTK uses this; Windows crashes on a PNG here. Windows and macOS
            # take the window icon from the .exe / .app instead.
            options["icon"] = resource_path("assets", "icon", "jdeck.png")
        webview.start(private_mode=True, storage_path=os.path.join(data_dir, "webview"),
                      localization={"global.quitConfirmation": CLOSE_WARNING}, **options)
    except Exception as e:
        # No window available (for example a Linux PC without WebKitGTK): use the browser.
        import webbrowser
        print(f"The app window could not open ({e}). Opening JDeck in your browser instead.")
        webbrowser.open(url)
        wait_for_ctrl_c(f"JDeck is at {url}")
    finally:
        sharing.on_change = None
        sharing.stop()
        local.shutdown()
        local.server_close()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="jdeck", description=__doc__.splitlines()[0])
    parser.add_argument("--headless", action="store_true",
                        help="no window: share the page on your network only (asks for a password)")
    parser.add_argument("--self-check", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--version", action="version", version=f"{APP_NAME} v{__version__}")
    args = parser.parse_args(argv)
    if args.self_check:
        return self_check()

    data_dir = os.path.join(app_dir(), DATA_FOLDER_NAME)
    try:
        settings = Settings(data_dir)
        if not os.path.exists(settings.path):
            settings.save()
    except OSError as e:
        show_error(f"Cannot write the settings folder:\n{data_dir}\n\n{e}\n\n"
                   "Move JDeck to a folder you can write to, such as Documents.")
        return 1

    sharing = Sharing(settings)
    if args.headless:
        return run_headless(settings, sharing)
    if settings.get("share_on_network") and settings.has_password:
        sharing.start()  # A failure is shown in the window's Settings.
    return run_window(settings, sharing, data_dir)


if __name__ == "__main__":
    sys.exit(main())
