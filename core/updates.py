"""Manual update check against the latest GitHub release.
Runs only when the user selects the version. One GET to GitHub, nothing stored."""

import json
import re
import socket
import urllib.error
import urllib.request

from core import APP_NAME, __version__

GITHUB_REPO = "TariqMK/JDeck"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


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
