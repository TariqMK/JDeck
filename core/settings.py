"""Settings file, password hashing and the paths to the data folder and bundled files."""

import hashlib
import hmac
import json
import os
import secrets
import sys
import threading

# Running from source: the project root (one level above core/).
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def app_dir():
    """Folder that holds the program: next to the .exe when packaged, else the project root."""
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        # macOS: the executable is inside JDeck.app/Contents/MacOS; use the folder beside the .app.
        suffix = os.sep + "Contents" + os.sep + "MacOS"
        if sys.platform == "darwin" and exe_dir.endswith(".app" + suffix):
            return os.path.dirname(exe_dir[: -len(suffix)])
        return exe_dir
    return PROJECT_ROOT


def resource_path(*parts):
    """Path to a bundled file (inside the PyInstaller bundle when packaged)."""
    base = getattr(sys, "_MEIPASS", PROJECT_ROOT)
    return os.path.join(base, *parts)


class Settings:
    """Settings stored as JSON in the data folder next to the program.
    `defaults` (from the Edition) lists every key the edition keeps; other keys in the file are ignored."""

    def __init__(self, data_dir, defaults):
        self.data_dir = data_dir
        self.path = os.path.join(data_dir, "settings.json")
        self.lock = threading.RLock()
        self.defaults = dict(defaults)
        self.values = dict(defaults)
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                stored = json.load(f)
            self.values.update({k: v for k, v in stored.items() if k in self.defaults})

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
        with self.lock:
            return bool(self.values.get("password_hash"))

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
            stored, salt = self.values.get("password_hash"), self.values.get("password_salt", "")
        if not stored:
            return False  # No password means nobody can sign in.
        return hmac.compare_digest(stored, hash_password(password, salt))

    def update(self, **changes):
        with self.lock:
            self.values.update(changes)
            self.save()


def hash_password(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()


def parse_port(value):
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise ValueError("The port must be a number.") from None
    if not 1024 <= port <= 65535:
        raise ValueError("The port must be between 1024 and 65535.")
    return port
