"""HTTP fetching tuned for slow / badly-configured Moroccan school websites."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import requests
import urllib3

log = logging.getLogger(__name__)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-MA,fr;q=0.9,ar;q=0.8,en;q=0.7",
}
MAX_BYTES = 6 * 1024 * 1024


@dataclass
class Page:
    url: str            # final URL after redirects
    status: int
    content: bytes
    content_type: str
    insecure_ssl: bool  # True if we had to skip certificate verification


class FetchError(Exception):
    pass


_session = requests.Session()
_session.headers.update(HEADERS)
adapter = requests.adapters.HTTPAdapter(pool_connections=50, pool_maxsize=50, max_retries=1)
_session.mount("http://", adapter)
_session.mount("https://", adapter)


def _get(url: str, timeout: int, verify: bool) -> requests.Response:
    r = _session.get(url, timeout=(10, timeout), verify=verify, stream=True, allow_redirects=True)
    chunks, size = [], 0
    for chunk in r.iter_content(64 * 1024):
        chunks.append(chunk)
        size += len(chunk)
        if size > MAX_BYTES:
            break
    r._content = b"".join(chunks)  # noqa: SLF001 - keep body with size limit
    return r


def fetch(url: str, timeout: int = 25) -> Page:
    insecure = False
    try:
        r = _get(url, timeout, verify=True)
    except requests.exceptions.SSLError:
        # Many school sites have expired / incomplete certificates. These are public
        # pages we only read, so retry without verification and remember it.
        insecure = True
        try:
            r = _get(url, timeout, verify=False)
        except requests.RequestException as e:
            raise FetchError(_short(e)) from e
    except requests.RequestException as e:
        raise FetchError(_short(e)) from e

    if r.status_code >= 400:
        raise FetchError(f"HTTP {r.status_code}")
    return Page(url=r.url, status=r.status_code, content=r.content,
                content_type=r.headers.get("Content-Type", ""), insecure_ssl=insecure)


def _short(e: Exception) -> str:
    name = type(e).__name__
    msg = str(e)
    for key in ("Name or service not known", "nodename nor servname", "getaddrinfo failed",
                "Failed to resolve"):
        if key in msg:
            return "DNS: domain not found"
    if "timed out" in msg.lower() or isinstance(e, requests.Timeout):
        return "Timeout"
    if "Connection refused" in msg:
        return "Connection refused"
    if "Max retries exceeded" in msg or isinstance(e, requests.ConnectionError):
        return "Connection error"
    return f"{name}: {msg[:120]}"
