"""Minimal Telegram Bot API client (sendMessage + getUpdates), with rate limiting."""
from __future__ import annotations

import html
import logging
import threading
import time

import requests

log = logging.getLogger(__name__)
API = "https://api.telegram.org/bot{token}/{method}"


class Telegram:
    def __init__(self, token: str, dry_run: bool = False):
        self.token = token
        self.dry_run = dry_run
        self._lock = threading.Lock()
        self._last_send = 0.0
        self.sent_log: list[tuple[str, str]] = []  # used in dry-run / tests

    def _call(self, method: str, payload: dict, timeout: int = 30) -> dict:
        r = requests.post(API.format(token=self.token, method=method), json=payload, timeout=timeout)
        try:
            data = r.json()
        except ValueError:
            data = {"ok": False, "description": f"HTTP {r.status_code}"}
        return data

    def send(self, chat_id: str, text: str, preview: bool = True) -> bool:
        """Send an HTML message. Returns True on success. Respects Telegram flood limits."""
        if self.dry_run:
            self.sent_log.append((chat_id, text))
            print(f"\n--- [DRY RUN] to {chat_id} ---\n{text}\n")
            return True
        with self._lock:
            for attempt in range(4):
                wait = 1.1 - (time.time() - self._last_send)  # <= ~1 msg/s to one chat
                if wait > 0:
                    time.sleep(wait)
                try:
                    data = self._call("sendMessage", {
                        "chat_id": chat_id, "text": text, "parse_mode": "HTML",
                        "link_preview_options": {"is_disabled": not preview},
                    })
                except requests.RequestException as e:
                    log.warning("Telegram network error: %s", e)
                    time.sleep(3 * (attempt + 1))
                    continue
                self._last_send = time.time()
                if data.get("ok"):
                    return True
                retry = (data.get("parameters") or {}).get("retry_after")
                if retry:
                    log.warning("Telegram rate limit, sleeping %ss", retry)
                    time.sleep(int(retry) + 1)
                    continue
                log.error("Telegram error: %s", data.get("description"))
                return False
        return False

    def get_updates(self, offset: int, timeout: int = 50) -> list[dict]:
        if self.dry_run:
            time.sleep(timeout)
            return []
        try:
            data = self._call("getUpdates", {"offset": offset, "timeout": timeout,
                                             "allowed_updates": ["message", "channel_post"]},
                              timeout=timeout + 15)
        except requests.RequestException as e:
            log.warning("getUpdates failed: %s", e)
            time.sleep(5)
            return []
        if not data.get("ok"):
            log.warning("getUpdates error: %s", data.get("description"))
            time.sleep(10)
            return []
        return data.get("result", [])

    def set_commands(self, commands: list[tuple[str, str]]) -> None:
        if self.dry_run:
            return
        try:
            self._call("setMyCommands", {"commands": [{"command": c, "description": d}
                                                      for c, d in commands]})
        except requests.RequestException:
            pass


def esc(s: str) -> str:
    return html.escape(s or "", quote=False)
