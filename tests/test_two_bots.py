"""Two bots: the friend's main bot and the owner's own bot.

    python -m tests.test_two_bots
"""
import os
import tempfile
from pathlib import Path
from unittest import mock

from bot.fetcher import Page

D = Path(tempfile.mkdtemp())
PAGE = ['<a href="/a">Avis de concours d’accès 2026 cycle ingénieur</a>']


def fake_fetch(url, timeout=25):
    html = "<html><body><div>" + "".join(PAGE) + "</div></body></html>"
    return Page(url=url, status=200, content=html.encode(), content_type="text/html", insecure_ssl=False)


def main():
    (D / "sites.yaml").write_text('sites:\n- {id: ensa_x, name: ENSA X, url: "http://x.ma/", category: ensa}\n')
    os.environ.update(DRY_RUN="1", DATA_DIR=str(D), SITES_FILE=str(D / "sites.yaml"),
                      BOT_TOKEN="111:friend", CHAT_ID="500", ADMIN_ID="500",
                      BOT_TOKEN_2="222:owner", CHAT_ID_2="900", ADMIN_ID_2="")
    from bot.commands import CommandHandler
    from bot.config import load_settings
    from bot.monitor import Monitor
    from bot.storage import Storage
    from bot.telegram import Telegram

    s = load_settings()
    tg1 = Telegram(s.bot_token, dry_run=True)
    m = Monitor(s, Storage(s.db_path), tg1)
    tg2, bot2 = m.bots[1]
    assert bot2.chat_ids == ["900"] and bot2.admin_ids == ["900"], bot2

    with mock.patch("bot.monitor.fetch", fake_fetch):
        m.run_cycle()
        PAGE.append('<a href="/b">Résultats du concours d’accès 2026 cycle ingénieur</a>')
        m.run_cycle()
    one = [t for c, t in tg1.sent_log if c == "500" and "Résultats" in t]
    two = [t for c, t in tg2.sent_log if c == "900" and "Résultats" in t]
    assert len(one) == 1 and len(two) == 1, (tg1.sent_log, tg2.sent_log)
    print("✓ a new announcement goes to the friend's bot AND the owner's bot")

    h1, h2 = CommandHandler(m, tg1, m.bots[0][1]), CommandHandler(m, tg2, bot2)
    tg1.sent_log.clear(); tg2.sent_log.clear()
    h2.handle({"chat": {"id": 900}, "from": {"id": 900}, "text": "/status"})
    h1.handle({"chat": {"id": 900}, "from": {"id": 900}, "text": "/status"})
    assert "Running" in tg2.sent_log[0][1], tg2.sent_log
    assert "private" in tg1.sent_log[0][1], tg1.sent_log
    assert h1._offset_key != h2._offset_key
    print("✓ each bot has its own admins and its own Telegram read position")
    print("\nALL TWO-BOT TESTS PASSED")


if __name__ == "__main__":
    main()
