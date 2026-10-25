"""The watch cycle: check every site, store new announcements, send them, report health."""
from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from .config import Settings, Site, load_sites
from .extractor import (Item, KeywordMatcher, extract_feed, extract_html, link_path_text,
                        looks_like_feed, normalize_text)
from .fetcher import FetchError, fetch
from .storage import Storage
from .telegram import Telegram, esc

log = logging.getLogger(__name__)

CATEGORY_LABEL = {
    "aggregator": ("📰", "Actualité"), "portal": ("🏛", "Portail"), "ensa": ("🛠", "ENSA"),
    "encg": ("💼", "ENCG"), "fst": ("🔬", "FST"), "est": ("🎓", "EST"), "ensam": ("⚙️", "ENSAM"),
    "ens": ("📚", "ENS"), "medicine": ("🩺", "Médecine"), "health": ("🏥", "Santé"),
    "architecture": ("📐", "Architecture"), "military_security": ("🎖", "Militaire_Sécurité"),
    "teaching": ("👩‍🏫", "CRMEF"), "vocational": ("🔧", "Formation_Pro"),
    "religious": ("🕌", "Études_Islamiques"), "university": ("🏫", "Université"),
    "private_university": ("🏢", "Université_Privée"), "faculty": ("🏫", "Faculté"),
    "grande_ecole": ("🎯", "Grande_École"),
}
MASS_FAILURE_RATIO = 0.7


@dataclass
class CycleResult:
    started: float = 0.0
    duration: float = 0.0
    checked: int = 0
    ok: int = 0
    failed: int = 0
    baselined: int = 0
    new_items: int = 0
    sent: int = 0
    floods: list[str] = field(default_factory=list)


class Monitor:
    github_mode = False  # True when run by GitHub Actions (one cycle per run)

    def __init__(self, settings: Settings, storage: Storage, telegram: Telegram):
        self.s = settings
        self.db = storage
        self.tg = telegram
        self.matcher = KeywordMatcher(settings.extra_keywords)
        self.tz = ZoneInfo(settings.timezone)
        self.wake = threading.Event()
        self.last: CycleResult | None = None
        self.next_run: float = 0.0
        self._cycle_lock = threading.Lock()
        self.sites: dict[str, Site] = {}

    # ------------------------------------------------------------------ helpers
    @property
    def paused(self) -> bool:
        return self.db.get("paused") == "1"

    def now_local(self) -> datetime:
        return datetime.now(self.tz)

    def notify_admin(self, text: str) -> None:
        for admin in self.s.admin_ids:
            self.tg.send(admin, text, preview=False)

    # ------------------------------------------------------------------ one site
    def collect(self, site: Site) -> tuple[list[Item], bool, str | None]:
        """Fetch a site and return (items, insecure_ssl, feed_url_used)."""
        target = site.rss or site.url
        page = fetch(target, self.s.request_timeout)
        if looks_like_feed(page.content, page.content_type):
            return extract_feed(page.content), page.insecure_ssl, target

        items, feeds = extract_html(page.content, page.url, site.selector)
        feed_used = None
        if feeds and not site.selector:
            try:
                fp = fetch(feeds[0], self.s.request_timeout)
                if looks_like_feed(fp.content, fp.content_type):
                    feed_items = extract_feed(fp.content)
                    by_key = {i.key: i for i in items}
                    for fi in feed_items:  # feed titles are cleaner: they win
                        by_key[fi.key] = fi
                    items = list(by_key.values())
                    feed_used = feeds[0]
            except FetchError:
                pass
        return items, page.insecure_ssl, feed_used

    def check_site(self, site: Site) -> tuple[str, int]:
        """Returns (status, new_count). status in ok / baseline / flood / fail."""
        state = self.db.site_state(site.id)
        try:
            items, insecure, feed = self.collect(site)
        except FetchError as e:
            streak = self.db.mark_fail(site.id, str(e))
            log.info("FAIL %-28s %s (streak %d)", site.id, e, streak)
            return "fail", 0
        except Exception as e:  # never let one weird page kill the cycle
            streak = self.db.mark_fail(site.id, f"Parse error: {e}")
            log.exception("Unexpected error on %s", site.id)
            return "fail", 0

        if site.keywords:
            items = [i for i in items if self.matcher.matches(i.title, link_path_text(i.url))]

        known = self.db.known_keys(site.id)
        # Some sites (e.g. Laravel ones) put a code in each link that changes on every page
        # load, so a known title with a new link is not a new announcement.
        known_titles = {normalize_text(t) for t in self.db.known_titles(site.id)}
        new, seen_now = [], set()
        for i in items:
            t = normalize_text(i.title)
            if i.key in known or (len(t) >= 12 and (t in known_titles or t in seen_now)):
                continue
            seen_now.add(t)
            new.append(i)
        rows = [(i.key, i.url, i.title) for i in new]
        self.db.mark_ok(site.id, len(items), feed, insecure)

        if not state["baselined"]:
            # First successful visit: remember everything silently, so we don't spam old news.
            self.db.add_items(site.id, rows, notified=2)
            self.db.set_baselined(site.id)
            log.info("BASE %-28s %d existing links saved", site.id, len(rows))
            return "baseline", 0

        if len(new) > self.s.max_new_per_site:
            # Probably a redesign / new menu: don't flood the channel.
            self.db.add_items(site.id, rows, notified=2)
            log.warning("FLOOD %-27s %d new links stored silently", site.id, len(new))
            return "flood", len(new)

        if new:
            self.db.add_items(site.id, rows, notified=0)
            log.info("NEW  %-28s %d", site.id, len(new))
        return "ok", len(new)

    # ------------------------------------------------------------------ messages
    def format_item(self, site: Site | None, row) -> str:
        cat = site.category if site else "other"
        emoji, tag = CATEGORY_LABEL.get(cat, ("🔔", "Annonce"))
        name = site.name if site else row["site_id"]
        when = datetime.fromtimestamp(row["first_seen"], self.tz).strftime("%d/%m/%Y %H:%M")
        lines = [
            f"{emoji} <b>{esc(name)}</b>",
            "",
            f"📌 {esc(row['title'])}",
            "",
            f"🔗 <a href=\"{esc(row['url'])}\">فتح الإعلان / Ouvrir l'annonce</a>",
        ]
        if site and site.also:
            lines.append(f"🏫 {esc(' • '.join(site.also[:4]))}")
        lines.append(f"\n#{tag} · 🕒 {when}")
        return "\n".join(lines)

    def send_pending(self) -> int:
        sent = 0
        for row in self.db.pending(limit=200):
            if self.db.sent_elsewhere(row["url_key"], row["site_id"]):
                self.db.mark_silent(row["site_id"], row["url_key"])
                continue
            site = self.sites.get(row["site_id"])
            text = self.format_item(site, row)
            results = [self.tg.send(chat, text, preview=self.s.link_preview) for chat in self.s.chat_ids]
            if any(results):
                self.db.mark_sent(row["site_id"], row["url_key"])
                sent += 1
            else:
                break  # Telegram problem: keep the rest pending for next cycle
        return sent

    # ------------------------------------------------------------------ cycle
    def run_cycle(self) -> CycleResult:
        with self._cycle_lock:
            res = CycleResult(started=time.time())
            try:
                sites = load_sites(self.s.sites_file)
            except Exception as e:
                log.error("Cannot read sites file: %s", e)
                self.notify_admin(f"⚠️ sites.yaml error: <code>{esc(str(e))[:300]}</code>")
                sites = list(self.sites.values())
            self.sites = {s.id: s for s in sites}
            enabled = [s for s in sites if s.enabled]

            with ThreadPoolExecutor(max_workers=self.s.max_workers) as pool:
                futures = {pool.submit(self.check_site, s): s for s in enabled}
                for fut in as_completed(futures):
                    status, n = fut.result()
                    res.checked += 1
                    if status == "fail":
                        res.failed += 1
                        continue
                    res.ok += 1
                    if status == "baseline":
                        res.baselined += 1
                    elif status == "flood":
                        res.floods.append(f"{futures[fut].name} ({n})")
                    else:
                        res.new_items += n

            res.sent = self.send_pending()
            res.duration = time.time() - res.started
            self.last = res
            self.db.set("last_cycle", str(res.started))
            self.db.set("last_cycle_json", json.dumps({k: getattr(res, k) for k in (
                "started", "duration", "checked", "ok", "failed", "new_items", "sent")}))
            log.info("Cycle done in %.0fs: %d ok, %d failed, %d new, %d sent, %d baselined",
                     res.duration, res.ok, res.failed, res.new_items, res.sent, res.baselined)

            if res.floods:
                self.notify_admin("ℹ️ Sites with many new links at once (probably a redesign), "
                                  "saved without sending:\n• " + "\n• ".join(map(esc, res.floods)))
            self._mass_failure_check(res)
            self._ping_healthcheck(res)
            return res

    def _mass_failure_check(self, res: CycleResult) -> None:
        if res.checked >= 10 and res.failed / res.checked >= MASS_FAILURE_RATIO:
            last = float(self.db.get("last_mass_alert", "0"))
            if time.time() - last > 6 * 3600:
                self.db.set("last_mass_alert", str(time.time()))
                self.notify_admin(f"🚨 {res.failed}/{res.checked} sites failed this cycle. "
                                  "The server's internet connection may be down or blocked.")

    def _ping_healthcheck(self, res: CycleResult) -> None:
        if not self.s.healthcheck_url:
            return
        url = self.s.healthcheck_url
        if res.checked and res.failed == res.checked:
            url = url.rstrip("/") + "/fail"
        try:
            requests.get(url, timeout=10)
        except requests.RequestException:
            pass

    # ------------------------------------------------------------------ reports
    def failing_sites(self, min_streak: int = 1) -> list[tuple[Site, object]]:
        out = []
        for row in self.db.all_site_states():
            site = self.sites.get(row["site_id"])
            if site and site.enabled and row["fail_streak"] >= min_streak:
                out.append((site, row))
        out.sort(key=lambda x: -x[1]["fail_streak"])
        return out

    def empty_sites(self) -> list[Site]:
        """Sites that answer but where we found no announcement links at all."""
        out = []
        for row in self.db.all_site_states():
            site = self.sites.get(row["site_id"])
            if site and site.enabled and row["fail_streak"] == 0 and row["last_ok"] and row["last_count"] == 0:
                out.append(site)
        return out

    def health_report(self) -> str:
        st = self.db.stats()
        failing = self.failing_sites(min_streak=3)
        empty = self.empty_sites()
        enabled = sum(1 for s in self.sites.values() if s.enabled)
        lines = [
            "📊 <b>Daily report</b>",
            f"Sites watched: {enabled}",
            f"Announcements sent: {st['sent_24h']} (24h) · {st['sent_7d']} (7 days) · {st['sent_total']} total",
            f"Unreachable for 3+ checks: {len(failing)}",
            f"Reachable but no announcement links found: {len(empty)}",
        ]
        if failing:
            lines.append("\n<b>Unreachable</b> (use /failing for all):")
            for site, row in failing[:15]:
                lines.append(f"• {esc(site.name)} — {esc(row['last_error'] or '')}")
        if self.paused:
            lines.append("\n⏸ Bot is PAUSED (/resume)")
        return "\n".join(lines)

    def maybe_daily_report(self) -> None:
        if self.s.report_hour < 0:
            return
        now = self.now_local()
        today = now.strftime("%Y-%m-%d")
        if now.hour >= self.s.report_hour and self.db.get("last_report") != today:
            self.db.set("last_report", today)
            self.notify_admin(self.health_report())

    # ------------------------------------------------------------------ loop
    def loop(self, stop: threading.Event) -> None:
        interval = self.s.interval_minutes * 60
        while not stop.is_set():
            if not self.paused:
                try:
                    self.run_cycle()
                except Exception:
                    log.exception("Cycle crashed")
                try:
                    self.maybe_daily_report()
                except Exception:
                    log.exception("Report failed")
            self.next_run = time.time() + interval
            self.wake.wait(timeout=interval)
            self.wake.clear()
