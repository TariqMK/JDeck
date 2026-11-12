"""JDownloader local API client, link extraction and address parsing."""

import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

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


class JDError(Exception):
    """A JDownloader call failed. `offline` is True when JDownloader could not be reached.
    `kind` says why: "refused" (the PC answered, but nothing listens on the port),
    "unreachable" (no answer), "http" (JDownloader rejected the call) or "bad-reply"."""

    def __init__(self, message, offline=False, kind="http"):
        super().__init__(message)
        self.offline = offline
        self.kind = kind


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
        if isinstance(getattr(e, "reason", e), ConnectionRefusedError):
            raise JDError("JDownloader does not accept connections at this address. Its local API "
                          "may be off, or open to its own PC only.", offline=True, kind="refused") from e
        raise JDError("Cannot reach JDownloader. Check the address and that its local API is on.",
                      offline=True, kind="unreachable") from e
    try:
        return json.loads(body).get("data") if body else None
    except (ValueError, AttributeError) as e:
        raise JDError("JDownloader sent a reply that could not be read.", kind="bad-reply") from e


def jd_test(base_url):
    """Try one harmless call. Returns {"result": "connected" | <JDError kind>, "message": ...}."""
    try:
        jd_call(base_url, "/jd/version", timeout=5)
    except JDError as e:
        return {"result": e.kind, "message": str(e)}
    return {"result": "connected", "message": "Connected to JDownloader."}


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
