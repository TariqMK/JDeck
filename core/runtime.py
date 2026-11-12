"""Small start-up helpers shared by both editions: error pop-ups, waiting for a stop signal,
and this PC's network address."""

import signal
import socket
import sys
import threading


def show_error(title, message):
    """Show an error even when there is no console window (packaged Windows app)."""
    if sys.stderr:
        print(message, file=sys.stderr)
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)


def wait_until_stopped(message, stop=None):
    """Print `message`, then wait for Ctrl+C, SIGTERM or `stop` (a threading.Event) to be set."""
    stop = stop or threading.Event()
    print(message)
    print("Press Ctrl+C to stop.")
    if threading.current_thread() is threading.main_thread() and hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
    try:
        while not stop.wait(1):  # Short waits so Ctrl+C works on Windows.
            pass
    except KeyboardInterrupt:
        pass


def lan_address():
    """Best guess at this PC's network (LAN) IP address, or ''."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 80))  # TEST-NET address: nothing is sent.
            return s.getsockname()[0]
    except OSError:
        return ""
