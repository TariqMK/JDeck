"""Sign-in sessions and the wrong-password delay. Used by JDeck Server only.
Everything is kept in memory: a restart signs out every device."""

import secrets
import threading
import time
from http.cookies import CookieError, SimpleCookie

SESSION_COOKIE = "jdeck_session"
SESSION_IDLE_SECONDS = 30 * 24 * 3600  # A session ends after 30 days without use.
MAX_FAILURES = 5                       # Wrong passwords from one address before the delay.
DELAY_SECONDS = 30                     # The delay after MAX_FAILURES wrong passwords.


class Sessions:
    """Random session tokens and when each was last used."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.last_used = {}

    def create(self):
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.last_used[token] = self.clock()
        return token

    def valid(self, token):
        """True for a known, unexpired token. Using a token keeps it alive."""
        if not token:
            return False
        now = self.clock()
        with self.lock:
            last = self.last_used.get(token)
            if last is None:
                return False
            if now - last > SESSION_IDLE_SECONDS:
                del self.last_used[token]
                return False
            self.last_used[token] = now
            return True

    def end(self, token):
        with self.lock:
            self.last_used.pop(token, None)

    def end_all(self):
        with self.lock:
            self.last_used.clear()

    def count(self):
        now = self.clock()
        with self.lock:
            return sum(1 for last in self.last_used.values() if now - last <= SESSION_IDLE_SECONDS)


class Throttle:
    """After MAX_FAILURES wrong passwords from one address, that address waits DELAY_SECONDS."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.failures = {}      # address -> wrong passwords in a row
        self.blocked_until = {}  # address -> time

    def wait_seconds(self, address):
        """Seconds this address must still wait. 0 means it may try."""
        with self.lock:
            until = self.blocked_until.get(address, 0)
        return max(0, int(until - self.clock() + 0.999))

    def failed(self, address):
        with self.lock:
            count = self.failures.get(address, 0) + 1
            if count >= MAX_FAILURES:
                self.blocked_until[address] = self.clock() + DELAY_SECONDS
                count = 0
            self.failures[address] = count

    def succeeded(self, address):
        with self.lock:
            self.failures.pop(address, None)
            self.blocked_until.pop(address, None)


def session_token(cookie_header):
    """The session token from a Cookie header, or ''."""
    try:
        morsel = SimpleCookie(cookie_header or "").get(SESSION_COOKIE)
    except CookieError:
        return ""
    return morsel.value if morsel else ""


def session_cookie(token):
    """Set-Cookie value. No Secure flag: JDeck Server uses plain HTTP on the home network."""
    return f"{SESSION_COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_IDLE_SECONDS}"


def clear_cookie():
    return f"{SESSION_COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"
