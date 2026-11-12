"""JDeck Server: serves the JDeck page to every device on the network, behind a password.
Runs on the JDownloader computer and talks to JDownloader on 127.0.0.1.
By default it shows a tray icon. --headless runs it without one."""

import argparse
import getpass
import json
import os
import secrets
import signal
import threading
import urllib.error
import urllib.request
import webbrowser

from core import __version__
from core.auth import Sessions, Throttle
from core.edition import Edition
from core.httpd import make_server
from core.runtime import lan_address, show_error, wait_until_stopped
from core.settings import Settings, app_dir, resource_path
from server.handler import CONTROL_HEADER, ServerHandler

DEFAULT_PORT = 23128
QUIT_WAIT_SECONDS = 3  # Open requests may finish for this long when JDeck Server quits.
CONTROL_TOKEN_FILE = "control.token"  # Lets `--stop` on this PC stop the running copy.

SERVER = Edition(
    name="server",
    title="JDeck Server",
    data_folder="JDeck-Server-data",
    defaults={"jd_url": "http://127.0.0.1:3128", "port": DEFAULT_PORT,
              "password_salt": "", "password_hash": ""},
    features={"jdAddressEditable": False, "connectionHelp": False, "signOut": True, "controlPage": True},
)

REQUIRED_RESOURCES = [("web", "index.html"), ("web", "login.html"), ("web", "server.html"),
                      ("web", "common.css"), ("assets", "icon", "jdeck.png")]


class Controller:
    """Owns the running server: start, change port, and a clean stop."""

    def __init__(self, settings, host="0.0.0.0"):
        self.settings = settings
        self.host = host
        self.sessions = Sessions()
        self.throttle = Throttle()
        self.server = None
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.control_token = secrets.token_urlsafe(32)
        self.on_stop = None  # Called once after a stop (the tray removes its icon).
        self._stopping = False

    @property
    def port(self):
        return self.server.server_address[1]

    @property
    def token_path(self):
        return os.path.join(self.settings.data_dir, CONTROL_TOKEN_FILE)

    def local_url(self, path="/"):
        return f"http://127.0.0.1:{self.port}{path}"

    def network_url(self):
        lan = lan_address()
        return f"http://{lan}:{self.port}/" if lan else ""

    def _listen(self, port):
        return make_server(self.host, port, SERVER, self.settings, ServerHandler,
                           controller=self, sessions=self.sessions, throttle=self.throttle)

    def start(self):
        """Start listening on the saved port. Raises OSError when the port is in use."""
        self.server = self._listen(self.settings.get("port"))
        self._write_token()

    def _write_token(self):
        try:
            os.makedirs(self.settings.data_dir, exist_ok=True)
            with open(self.token_path, "w", encoding="utf-8") as f:
                f.write(self.control_token)
        except OSError:
            pass  # Only --stop needs it; the tray and the control page still work.

    def change_port(self, port):
        """Listen on a new port. The old port closes a moment later, after this answer is sent.
        Raises OSError (and keeps the old port) when the new port is in use."""
        with self.lock:
            if port == self.port:
                return
            new = self._listen(port)
            old, self.server = self.server, new
            self.settings.update(port=port)
        threading.Timer(1.0, self._close, args=(old,)).start()

    @staticmethod
    def _close(server):
        server.shutdown()
        server.server_close()

    def request_stop(self):
        """Stop from a request or menu: answer first, then stop in the background."""
        threading.Thread(target=self.stop, daemon=True).start()

    def stop(self):
        with self.lock:
            if self._stopping:
                return
            self._stopping = True
        if self.server:
            self.server.shutdown()                        # 1. Accept no new connections.
            self.server.wait_until_idle(QUIT_WAIT_SECONDS)  # 2. Let open requests finish.
            self.server.server_close()
        try:
            self.settings.save()                          # 3. Save the settings.
        except OSError:
            pass
        try:
            os.remove(self.token_path)
        except OSError:
            pass
        self.stopped.set()
        if self.on_stop:
            self.on_stop()                                # 4. Remove the tray icon.


# ---------------------------------------------------------------- talking to a running copy

def find_running(port, timeout=2):
    """True when a JDeck Server answers on 127.0.0.1:<port>."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/session", timeout=timeout) as r:
            return json.loads(r.read()).get("title") == SERVER.title
    except (OSError, ValueError, AttributeError):
        return False


def stop_running(settings, timeout=10):
    """`--stop`: ask the running copy on this PC to quit. Returns an exit code."""
    port = settings.get("port")
    try:
        with open(os.path.join(settings.data_dir, CONTROL_TOKEN_FILE), encoding="utf-8") as f:
            token = f.read().strip()
    except OSError:
        print("JDeck Server is not running (no control file).")
        return 1
    request = urllib.request.Request(f"http://127.0.0.1:{port}/api/server/stop", data=b"{}", method="POST",
                                     headers={"X-JDeck": "1", CONTROL_HEADER: token,
                                              "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout):
            pass
    except urllib.error.HTTPError as e:
        print(f"JDeck Server refused to stop (error {e.code}).")
        return 1
    except OSError:
        print(f"JDeck Server is not running on port {port}.")
        return 1
    print("JDeck Server is stopping.")
    return 0


# ---------------------------------------------------------------- start-up

def self_check():
    """Used by the release build to prove the bundle is complete. Exit code 0 means OK."""
    missing = [os.path.join(*p) for p in REQUIRED_RESOURCES if not os.path.isfile(resource_path(*p))]
    for module in ("pystray", "PIL"):
        try:
            __import__(module)
        except Exception as e:
            missing.append(f"{module} module ({e})")
    print(f"{SERVER.title} v{__version__}")
    for m in missing:
        print(f"Missing: {m}")
    return 1 if missing else 0


def set_password_in_terminal(settings):
    password = getpass.getpass("New password: ")
    if not password or password != getpass.getpass("Repeat the password: "):
        print("The passwords are empty or do not match. Nothing was changed.")
        return 1
    settings.set_password(password)
    print("Password saved. If JDeck Server is running, restart it to use the new password.")
    return 0


def start_or_report(controller, open_existing):
    """Start listening. When the port is busy, open the running copy (if it is one) or show an error.
    Returns None when started, else the exit code."""
    port = controller.settings.get("port")
    try:
        controller.start()
        return None
    except OSError as e:
        if find_running(port):
            print(f"JDeck Server is already running on port {port}.")
            if open_existing:
                webbrowser.open(f"http://127.0.0.1:{port}/server")
            return 0
        show_error(SERVER.title, f"Port {port} is in use by another program. Choose another port in "
                                 f"JDeck-Server-data/settings.json, or close that program.\n\n({e.strerror or e})")
        return 1


def run_headless(controller):
    code = start_or_report(controller, open_existing=False)
    if code is not None:
        return code
    print(f"JDownloader: {controller.settings.get('jd_url')}")
    if not controller.settings.has_password:
        print(f"No password is set yet. On this computer, open {controller.local_url('/server')} "
              "to set it, or stop JDeck Server and run it with --set-password.")
    try:
        wait_until_stopped(f"{SERVER.title} is running. On other devices, open "
                           f"{controller.network_url() or controller.local_url()}", stop=controller.stopped)
    finally:
        controller.stop()
    print("JDeck Server stopped.")
    return 0


def run_with_tray(controller):
    code = start_or_report(controller, open_existing=True)
    if code is not None:
        return code
    if not controller.settings.has_password:
        webbrowser.open(controller.local_url("/server"))  # First run: set the password.
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: controller.request_stop())
    from server.tray import run_tray
    try:
        run_tray(controller)  # Returns after Quit.
    except KeyboardInterrupt:
        pass
    except Exception as e:  # No tray on this system (for example no desktop session).
        print(f"The tray icon could not start ({e}). Running without it.")
        try:
            wait_until_stopped(f"{SERVER.title} is running at {controller.local_url()}", stop=controller.stopped)
        finally:
            controller.stop()
        return 0
    controller.stop()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="jdeck-server",
                                     description="JDeck Server: JDownloader 2 from any device on your network.")
    parser.add_argument("--headless", action="store_true",
                        help="run without a tray icon (for computers without a desktop)")
    parser.add_argument("--set-password", action="store_true",
                        help="set the password in the terminal, then exit")
    parser.add_argument("--stop", action="store_true", help="stop the JDeck Server running on this computer")
    parser.add_argument("--self-check", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--version", action="version", version=f"{SERVER.title} v{__version__}")
    args = parser.parse_args(argv)
    if args.self_check:
        return self_check()

    data_dir = os.path.join(app_dir(), SERVER.data_folder)
    try:
        settings = Settings(data_dir, SERVER.defaults)
        if not os.path.exists(settings.path):
            settings.save()
    except OSError as e:
        show_error(SERVER.title, f"Cannot write the settings folder:\n{data_dir}\n\n{e}\n\n"
                                 "Move JDeck Server to a folder you can write to.")
        return 1

    if args.set_password:
        return set_password_in_terminal(settings)
    if args.stop:
        return stop_running(settings)
    controller = Controller(settings)
    return run_headless(controller) if args.headless else run_with_tray(controller)
