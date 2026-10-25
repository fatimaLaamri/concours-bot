"""End-to-end test with local fake school websites and Telegram in dry-run mode.

    python -m tests.test_end_to_end
"""
import os
import shutil
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = Path(tempfile.mkdtemp())
DATA = Path(tempfile.mkdtemp())

PAGE_A = """<html><head><meta charset="utf-8"><title>ENSA Test</title></head><body>
<nav><a href="/">Accueil</a><a href="/contact">Contact</a></nav>
<div class="news">
 <a href="/actualites/concours-acces-1ere-annee-2026">Avis de concours d'accès en 1ère année 2026</a>
 <a href="/actualites/journee-portes-ouvertes">Journée sportive des étudiants</a>
 {extra}
</div>
<div class="item"><h3>إعلان عن مباراة ولوج سلك المهندس</h3><a href="/news/42">اقرأ المزيد</a></div>
<a href="https://facebook.com/ensa">Facebook</a>
<footer><a href="/mentions">Mentions légales concours</a></footer>
</body></html>"""

FEED_B = """<?xml version="1.0"?><rss version="2.0"><channel><title>Tawjih test</title>
<item><title>Concours ENCG 2026 : inscription TAFEM</title><link>http://127.0.0.1:{port}/p/encg-tafem</link></item>
{extra}
</channel></rss>"""


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def write(name, text):
    (WEB / name).write_text(text, encoding="utf-8")


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(WEB)))
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    write("a.html", PAGE_A.format(extra=""))
    write("b.xml", FEED_B.format(port=port, extra=""))
    (DATA / "sites.yaml").write_text(f"""sites:
- {{id: ensa_test, name: ENSA Test, url: "{base}/a.html", category: ensa}}
- {{id: agg_test, name: Tawjih Test, url: "{base}/b.xml", category: aggregator, also: [Autre école]}}
- {{id: dead_test, name: Dead Site, url: "{base}/missing.html", category: faculty}}
- {{id: off_test, name: Disabled, url: "{base}/a.html", category: faculty, enabled: false}}
""", encoding="utf-8")

    os.environ.update(DRY_RUN="1", DATA_DIR=str(DATA), SITES_FILE=str(DATA / "sites.yaml"),
                      CHAT_ID="111,333", ADMIN_ID="222,444", MAX_NEW_PER_SITE="3", HEALTHCHECK_URL="")
    from bot.config import load_settings
    from bot.monitor import Monitor
    from bot.storage import Storage
    from bot.telegram import Telegram
    from bot.commands import CommandHandler

    s = load_settings()
    tg = Telegram("x", dry_run=True)
    m = Monitor(s, Storage(s.db_path), tg)

    def to(chat):
        return [t for c, t in tg.sent_log if c == chat]

    # 1) first cycle = baseline, nothing sent to the channel
    r = m.run_cycle()
    assert r.checked == 3 and r.failed == 1 and r.baselined == 2, r
    assert to("111") == [], to("111")
    keys = m.db.known_keys("ensa_test")
    assert any("concours-acces" in k for k in keys), keys
    assert not any("portes-ouvertes" in k for k in keys), "non-keyword link should be filtered"
    assert not any("mentions" in k for k in keys), "footer links ignored"
    assert any(k.endswith("news/42") for k in keys), "'lire la suite' link should use block heading"
    print("✓ baseline: existing links saved silently, filters ok")

    # 2) new announcements appear
    write("a.html", PAGE_A.format(extra='<a href="/actualites/resultats-concours">Résultats du concours écrit</a>'
                                        '<a href="/actualites/fete">Fête de fin d\'année</a>'))
    write("b.xml", FEED_B.format(port=port, extra=f"<item><title>نتائج مباراة ولوج كلية الطب</title>"
                                                  f"<link>{base}/p/fmp-results</link></item>"))
    tg.sent_log.clear()
    r = m.run_cycle()
    sent = to("111")
    assert len(sent) == 2, sent
    assert len(to("333")) == 2, "second recipient gets the same announcements"
    assert any("Résultats du concours écrit" in t for t in sent)
    assert any("نتائج مباراة" in t and "#Actualité" in t and "Autre école" in t for t in sent)
    assert not any("Fête" in t for t in sent)
    print("✓ new announcements sent (FR + AR), non-announcement ignored")

    # 3) same content again -> no duplicates
    tg.sent_log.clear()
    m.run_cycle()
    assert to("111") == [], to("111")
    print("✓ no duplicates on next check")

    # 4) flood protection
    many = "".join(f'<a href="/actualites/concours-{i}">Concours numéro {i}</a>' for i in range(10))
    write("a.html", PAGE_A.format(extra=many))
    tg.sent_log.clear()
    r = m.run_cycle()
    assert to("111") == [], to("111")
    assert any("redesign" in t for t in to("222")), tg.sent_log
    print("✓ flood protection: 10 new links stored silently, admin informed")

    # 5) commands
    h = CommandHandler(m)
    tg.sent_log.clear()
    h.handle({"chat": {"id": 222}, "from": {"id": 222}, "text": "/status"})
    h.handle({"chat": {"id": 222}, "from": {"id": 222}, "text": "/failing"})
    h.handle({"chat": {"id": 999}, "from": {"id": 999}, "text": "/status"})
    h.handle({"chat": {"id": 222}, "from": {"id": 222}, "text": "/site ensa_test"})
    h.handle({"chat": {"id": 222}, "from": {"id": 222}, "text": "/last"})
    h.handle({"chat": {"id": 222}, "from": {"id": 222}, "text": "/report"})
    out = "\n".join(to("222"))
    assert "Running" in out and "dead_test" in out and "HTTP 404" in out and "Daily report" in out
    assert "private" in "\n".join(to("999"))
    print("✓ admin commands work, strangers get only their id")

    # 6) search / latest (second admin can use commands too)
    tg.sent_log.clear()
    h.handle({"chat": {"id": 444}, "from": {"id": 444}, "text": "/search concours"})
    h.handle({"chat": {"id": 444}, "from": {"id": 444}, "text": "/search مباراه الطب"})
    h.handle({"chat": {"id": 444}, "from": {"id": 444}, "text": "/search zzzz"})
    h.handle({"chat": {"id": 444}, "from": {"id": 444}, "text": "/latest"})
    h.handle({"chat": {"id": 444}, "from": {"id": 444}, "text": "/latest_agg_test"})
    h.handle({"chat": {"id": 444}, "from": {"id": 444}, "text": "/latest test"})
    h.handle({"chat": {"id": 444}, "from": {"id": 444}, "text": "/latest dead"})
    out = to("444")
    assert "concours" in out[0].lower() and "Fête" not in out[0], out[0]
    assert "نتائج مباراة" in out[1], out[1]
    assert "No saved announcement" in out[2]
    assert "/latest_agg_test" in out[3] and "/latest_ensa_test" not in out[3], out[3]
    assert "Tawjih Test" in out[4] and "fmp-results" in out[4], out[4]
    assert "Several sites match" in out[5], out[5]
    assert "unreachable" in out[6], out[6]
    print("✓ /search (FR + AR, accent-insensitive) and /latest work")

    # 7) pause
    h.handle({"chat": {"id": 222}, "from": {"id": 222}, "text": "/pause"})
    assert m.paused
    h.handle({"chat": {"id": 222}, "from": {"id": 222}, "text": "/resume"})
    assert not m.paused
    print("✓ pause / resume")

    srv.shutdown()
    shutil.rmtree(WEB)
    shutil.rmtree(DATA)
    print("\nALL TESTS PASSED")


if __name__ == "__main__":
    main()
