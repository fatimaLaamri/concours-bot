"""Admin commands over Telegram (long polling, runs in its own thread)."""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

from .monitor import Monitor
from .extractor import link_path_text, normalize_text
from .telegram import esc

log = logging.getLogger(__name__)

COMMANDS = [
    ("search", "Search saved announcements: /search ENSA"),
    ("latest", "Latest announcements of a site: /latest tawjihnet"),
    ("status", "Bot status and last check"),
    ("check", "Check all sites now"),
    ("last", "Last 10 announcements sent"),
    ("failing", "Sites that can't be reached"),
    ("empty", "Sites where no announcement links are found"),
    ("site", "Details of one site: /site ensa_agadir"),
    ("stats", "Counters"),
    ("report", "Send the daily report now"),
    ("pause", "Stop checking"),
    ("resume", "Start checking again"),
    ("test", "Send a test message to the announcements chat"),
    ("help", "List commands"),
]


def _ago(ts: float | None, tz) -> str:
    if not ts:
        return "never"
    mins = int((time.time() - ts) / 60)
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{mins} min ago"
    if mins < 1440:
        return f"{mins // 60} h ago"
    return datetime.fromtimestamp(ts, tz).strftime("%d/%m %H:%M")


class CommandHandler:
    def __init__(self, monitor: Monitor):
        self.m = monitor
        self.tg = monitor.tg
        self.db = monitor.db

    @property
    def _offset_key(self) -> str:
        """Telegram's read position belongs to one bot: keep it per bot, so switching
        BOT_TOKEN to another bot doesn't make the new bot ignore its messages."""
        return "tg_offset:" + (self.tg.token or "").split(":")[0]

    def reply(self, chat_id, text: str) -> None:
        self.tg.send(str(chat_id), text, preview=False)

    def authorized(self, msg: dict) -> bool:
        ids = {str(msg.get("chat", {}).get("id")), str((msg.get("from") or {}).get("id"))}
        return any(a in ids for a in self.m.s.admin_ids)

    def handle(self, msg: dict) -> None:
        text = (msg.get("text") or "").strip()
        if not text.startswith("/"):
            return
        chat_id = msg["chat"]["id"]
        cmd, _, arg = text.partition(" ")
        cmd = cmd[1:].split("@")[0].lower()
        arg = arg.strip()
        if cmd.startswith("latest_"):  # tappable shortcut: /latest_tawjihnet
            cmd, arg = "latest", cmd[len("latest_"):]

        if cmd == "start" or not self.authorized(msg):
            uid = (msg.get("from") or {}).get("id")
            if self.authorized(msg):
                self.reply(chat_id, "✅ Bot is running. /help for commands.")
            else:
                self.reply(chat_id, f"👋 This bot is private.\nYour chat id: <code>{chat_id}</code>\n"
                                    f"Your user id: <code>{uid}</code>")
            return

        handler = getattr(self, f"cmd_{cmd}", None)
        if handler is None:
            self.reply(chat_id, "Unknown command. /help")
            return
        try:
            handler(chat_id, arg)
        except Exception as e:
            log.exception("Command %s failed", cmd)
            self.reply(chat_id, f"⚠️ Error: <code>{esc(str(e))[:300]}</code>")

    # ---------------------------------------------------------------- commands
    def cmd_help(self, chat_id, _):
        self.reply(chat_id, "\n".join(f"/{c} — {d}" for c, d in COMMANDS))

    def cmd_status(self, chat_id, _):
        last = self.m.last
        if last is None and self.db.get("last_cycle_json"):
            import json
            from types import SimpleNamespace
            last = SimpleNamespace(**json.loads(self.db.get("last_cycle_json")))
        tz = self.m.tz
        enabled = sum(1 for s in self.m.sites.values() if s.enabled)
        lines = ["⏸ <b>PAUSED</b>" if self.m.paused else "▶️ <b>Running</b>",
                 f"Sites watched: {enabled}",
                 f"Check every: {self.m.s.interval_minutes} min"]
        if last:
            lines += [f"Last check: {_ago(last.started, tz)} ({last.duration:.0f}s)",
                      f"Result: {last.ok} ok · {last.failed} unreachable · {last.new_items} new · "
                      f"{last.sent} sent"]
        else:
            lines.append("First check is running…")
        if self.m.github_mode and not self.m.paused:
            lines.append(f"Next check: within ~{self.m.s.interval_minutes} min (GitHub schedule)")
        elif self.m.next_run and not self.m.paused:
            mins = max(0, int((self.m.next_run - time.time()) / 60))
            lines.append(f"Next check: in {mins} min")
        lines.append(f"Waiting to send: {len(self.db.pending())}")
        self.reply(chat_id, "\n".join(lines))

    def cmd_check(self, chat_id, _):
        if self.m.paused:
            self.reply(chat_id, "Bot is paused. /resume first.")
            return
        if self.m.github_mode:
            self.reply(chat_id, "🔄 Checking all sites now (results in a minute or two)…")
            return  # this GitHub run checks right after reading commands
        self.reply(chat_id, "🔄 Checking all sites now…")
        self.m.wake.set()

    def cmd_last(self, chat_id, _):
        rows = self.db.recent_sent(10)
        if not rows:
            self.reply(chat_id, "Nothing sent yet.")
            return
        out = []
        for r in rows:
            site = self.m.sites.get(r["site_id"])
            out.append(f"• <b>{esc(site.name if site else r['site_id'])}</b>: "
                       f"<a href=\"{esc(r['url'])}\">{esc(r['title'][:90])}</a>")
        self.reply(chat_id, "\n".join(out))

    def cmd_failing(self, chat_id, _):
        failing = self.m.failing_sites(min_streak=1)
        if not failing:
            self.reply(chat_id, "✅ All sites reachable on the last check.")
            return
        lines = [f"❌ <b>{len(failing)} unreachable sites</b> (failed checks in a row):"]
        for site, row in failing[:60]:
            lines.append(f"• <code>{esc(site.id)}</code> ×{row['fail_streak']} — {esc(row['last_error'] or '')}")
        self._reply_long(chat_id, lines)

    def cmd_empty(self, chat_id, _):
        empty = self.m.empty_sites()
        if not empty:
            self.reply(chat_id, "✅ Every reachable site has announcement links.")
            return
        lines = [f"🔍 <b>{len(empty)} sites</b> reachable but no announcement links found "
                 "(may need a <code>selector</code> or <code>rss</code> in sites.yaml):"]
        lines += [f"• <code>{esc(s.id)}</code> {esc(s.url)}" for s in empty[:60]]
        self._reply_long(chat_id, lines)

    def cmd_site(self, chat_id, arg):
        site = self.m.sites.get(arg)
        if not site:
            matches = [s.id for s in self.m.sites.values() if arg and arg.lower() in s.id.lower()]
            hint = ("\nDid you mean: " + ", ".join(matches[:10])) if matches else ""
            self.reply(chat_id, f"Usage: /site &lt;id&gt;{esc(hint)}")
            return
        row = self.db.site_state(site.id)
        tz = self.m.tz
        lines = [f"<b>{esc(site.name)}</b> (<code>{esc(site.id)}</code>)",
                 f"URL: {esc(site.url)}",
                 f"Enabled: {'yes' if site.enabled else 'no'} · Category: {esc(site.category)}",
                 f"Last OK: {_ago(row['last_ok'], tz)}",
                 f"Fail streak: {row['fail_streak']}" + (f" — {esc(row['last_error'])}" if row['fail_streak'] else ""),
                 f"Announcement links found: {row['last_count'] if row['last_count'] is not None else '-'}",
                 f"RSS: {esc(site.rss or row['rss_url'] or 'none')}"]
        if row["insecure_ssl"]:
            lines.append("⚠️ Site has an invalid SSL certificate")
        self.reply(chat_id, "\n".join(lines))

    # ---------------------------------------------------------------- search
    IMPORTANT_CATEGORIES = ("aggregator", "portal")

    def _item_line(self, r) -> str:
        site = self.m.sites.get(r["site_id"])
        when = datetime.fromtimestamp(r["first_seen"], self.m.tz).strftime("%d/%m")
        name = esc(site.name if site else r["site_id"])
        return (f"• <a href=\"{esc(r['url'])}\">{esc(r['title'][:110])}</a>\n"
                f"   <i>{name} · {when}</i>")

    def cmd_search(self, chat_id, arg):
        if not arg:
            self.reply(chat_id, "🔎 Search everything the bot has found so far.\nExamples:\n"
                                "/search ENSA\n/search ENCG TAFEM\n/search مباراة الطب\n"
                                "/search résultats\nAll words must match (accents don't matter).")
            return
        words = [normalize_text(w) for w in arg.split() if w.strip()]
        hits = []
        for r in self.db.all_items():
            site = self.m.sites.get(r["site_id"])
            hay = normalize_text(" ".join([r["title"], link_path_text(r["url"]), r["site_id"],
                                           site.name if site else ""]))
            if all(w in hay for w in words):
                hits.append(r)
                if len(hits) >= 15:
                    break
        if not hits:
            self.reply(chat_id, f"No saved announcement matches <b>{esc(arg)}</b>.\n"
                                "Tip: try one word, or a different spelling (Arabic / French).")
            return
        lines = [f"🔎 <b>{esc(arg)}</b>: {len(hits)} most recent results"
                 + (" (showing first 15)" if len(hits) == 15 else "")]
        lines += [self._item_line(r) for r in hits]
        self._reply_long(chat_id, lines)

    def _find_site(self, term: str):
        t = normalize_text(term)
        if t in self.m.sites:
            return self.m.sites[t], []
        matches = [s for s in self.m.sites.values()
                   if t and (t in s.id.lower() or t in normalize_text(s.name))]
        exact = [s for s in matches if s.id.lower() == t]
        if exact:
            return exact[0], []
        if len(matches) == 1:
            return matches[0], []
        return None, matches

    def cmd_latest(self, chat_id, arg):
        if not arg:
            important = [s for s in self.m.sites.values()
                         if s.enabled and s.category in self.IMPORTANT_CATEGORIES]
            lines = ["📰 <b>Latest announcements</b>: tap a site", ""]
            lines += [f"/latest_{s.id} — {esc(s.name)}" for s in important]
            lines += ["", "Any other site: /latest ensa agadir, /latest encg, /latest fmp…"]
            self._reply_long(chat_id, lines)
            return
        site, choices = self._find_site(arg)
        if site is None:
            if not choices:
                self.reply(chat_id, f"No site matches <b>{esc(arg)}</b>. Try /latest to see the list.")
                return
            lines = [f"Several sites match <b>{esc(arg)}</b>, tap one:"]
            lines += [f"/latest_{s.id} — {esc(s.name)}" for s in choices[:40]]
            self._reply_long(chat_id, lines)
            return
        rows = self.db.latest_for_site(site.id, 10)
        if not rows:
            state = self.db.site_state(site.id)
            why = (f"the site is unreachable ({esc(state['last_error'] or '')})" if state["fail_streak"]
                   else "it hasn't been checked yet, or no announcement links were found there")
            self.reply(chat_id, f"Nothing saved for <b>{esc(site.name)}</b> yet: {why}.")
            return
        lines = [f"📰 <b>{esc(site.name)}</b>: last {len(rows)} announcements", ""]
        lines += [self._item_line(r) for r in rows]
        self._reply_long(chat_id, lines)

    def cmd_stats(self, chat_id, _):
        st = self.db.stats()
        self.reply(chat_id, "\n".join(f"{k}: {v}" for k, v in st.items()))

    def cmd_report(self, chat_id, _):
        self.reply(chat_id, self.m.health_report())

    def cmd_pause(self, chat_id, _):
        self.db.set("paused", "1")
        self.reply(chat_id, "⏸ Paused. /resume to continue.")

    def cmd_resume(self, chat_id, _):
        self.db.set("paused", "0")
        self.m.wake.set()
        self.reply(chat_id, "▶️ Resumed, checking now.")

    def cmd_test(self, chat_id, _):
        lines = []
        for target in self.m.s.chat_ids:
            ok = self.tg.send(target, "✅ Test message from the concours bot.", preview=False)
            lines.append(f"{'✅' if ok else '❌'} {esc(target)}")
        if not all(l.startswith("✅") for l in lines):
            lines.append("For ❌: that person must press Start in the bot first; for a channel, "
                         "the bot must be its admin.")
        self.reply(chat_id, "\n".join(lines))

    def _reply_long(self, chat_id, lines: list[str]) -> None:
        chunk = ""
        for line in lines:
            if len(chunk) + len(line) > 3800:
                self.reply(chat_id, chunk)
                chunk = ""
            chunk += line + "\n"
        if chunk:
            self.reply(chat_id, chunk)

    # ---------------------------------------------------------------- polling
    def poll_once(self) -> int:
        """Answer every command received since the last run (used by GitHub Actions)."""
        offset = int(self.db.get(self._offset_key, "0") or 0)
        handled = 0
        for _ in range(20):
            updates = self.tg.get_updates(offset, timeout=0)
            if not updates:
                break
            for upd in updates:
                offset = upd["update_id"] + 1
                self.db.set(self._offset_key, str(offset))
                msg = upd.get("message") or upd.get("channel_post")
                if msg:
                    self.handle(msg)
                    handled += 1
        return handled

    def poll_forever(self, stop: threading.Event) -> None:
        self.tg.set_commands(COMMANDS)
        offset = int(self.db.get(self._offset_key, "0") or 0)
        while not stop.is_set():
            updates = self.tg.get_updates(offset)
            for upd in updates:
                offset = upd["update_id"] + 1
                self.db.set(self._offset_key, str(offset))
                msg = upd.get("message") or upd.get("channel_post")
                if msg:
                    self.handle(msg)
