"""Concours notification bot — entry point.

    python main.py           run forever (checks every INTERVAL_MINUTES)
    python main.py --once    run one check cycle and exit (useful for testing / cron)
    python main.py --github  one GitHub Actions run: answer commands, check sites, exit
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from logging.handlers import RotatingFileHandler

from bot.commands import CommandHandler
from bot.config import load_settings, load_sites
from bot.monitor import Monitor
from bot.storage import Storage
from bot.telegram import Telegram


def setup_logging(settings) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(settings.log_level)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)
    fileh = RotatingFileHandler(settings.data_dir / "bot.log", maxBytes=5_000_000, backupCount=3,
                                encoding="utf-8")
    fileh.setFormatter(fmt)
    root.addHandler(fileh)
    logging.getLogger("urllib3").setLevel(logging.ERROR)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="run one cycle and exit")
    ap.add_argument("--github", action="store_true", help="one scheduled run (GitHub Actions)")
    args = ap.parse_args()

    settings = load_settings()
    setup_logging(settings)
    log = logging.getLogger("main")

    sites = load_sites(settings.sites_file)
    log.info("Loaded %d sites (%d enabled)", len(sites), sum(s.enabled for s in sites))

    storage = Storage(settings.db_path)
    tg = Telegram(settings.bot_token, dry_run=settings.dry_run)
    monitor = Monitor(settings, storage, tg)
    monitor.sites = {s.id: s for s in sites}

    if args.github:
        run_github(settings, storage, monitor, log)
        return

    if args.once:
        if not settings.chat_id and not settings.dry_run:
            raise SystemExit("CHAT_ID is empty: start the bot normally first to get your id, or use DRY_RUN=1")
        monitor.run_cycle()
        return

    stop = threading.Event()

    def shutdown(*_):
        log.info("Stopping…")
        stop.set()
        monitor.wake.set()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    commands = CommandHandler(monitor)
    threading.Thread(target=commands.poll_forever, args=(stop,), daemon=True, name="commands").start()

    if not settings.chat_id and not settings.dry_run:
        log.warning("SETUP MODE: CHAT_ID is empty. Send /start to your bot in Telegram; it will reply "
                    "with your id. Put it in .env as CHAT_ID, then restart.")
        while not stop.wait(3600):
            pass
        return

    first_start = storage.get("started_once") is None
    storage.set("started_once", "1")
    monitor.notify_admin(
        "🚀 Bot started" + (" for the first time.\nThe first check saves what is already on the sites "
                            "(no alerts). From the next check you'll get only NEW announcements."
                            if first_start else ".")
        + "\n/help for commands")

    monitor.loop(stop)


def run_github(settings, storage, monitor, log) -> None:
    """One GitHub Actions run: answer commands, check all sites, answer again, exit."""
    monitor.github_mode = True
    commands = CommandHandler(monitor)
    n = commands.poll_once()
    log.info("Answered %d commands", n)
    if not settings.chat_id:
        storage.close()
        log.warning("SETUP MODE: CHAT_ID secret is empty. Send /start to the bot; it replies with "
                    "your id. Add it as the CHAT_ID secret.")
        return
    if storage.get("started_once") is None:
        storage.set("started_once", "1")
        monitor.notify_admin("🚀 Bot is now running on GitHub, checking every ~30 min.\n"
                             "This first check saves what is already on the sites (no alerts). "
                             "From the next check you'll get only NEW announcements.\n/help for commands")
    try:
        if not monitor.paused:
            monitor.run_cycle()
            monitor.maybe_daily_report()
        commands.poll_once()  # answer anything that arrived during the check
    finally:
        storage.close()


if __name__ == "__main__":
    main()
