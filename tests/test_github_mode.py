"""GitHub mode: each run is a fresh process that reopens bot.db.

    python -m tests.test_github_mode
"""
import logging
import os
import shutil
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WEB, DATA = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def page(extra=""):
    return ('<html><body><div class="news">'
            '<a href="/n/concours-2026">Avis de concours 2026</a>' + extra + '</div></body></html>')


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(WEB)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    (WEB / "a.html").write_text(page(), encoding="utf-8")
    (DATA / "sites.yaml").write_text(
        f'sites:\n- {{id: ensa_x, name: ENSA X, url: "{base}/a.html", category: ensa}}\n', encoding="utf-8")
    os.environ.update(DRY_RUN="1", DATA_DIR=str(DATA), SITES_FILE=str(DATA / "sites.yaml"),
                      CHAT_ID="111", ADMIN_ID="222", HEALTHCHECK_URL="")

    from bot.config import load_settings, load_sites
    from bot.monitor import Monitor
    from bot.storage import Storage
    from bot.telegram import Telegram
    import main as entry

    log = logging.getLogger("t")
    updates = []

    def run():
        s = load_settings()
        tg = Telegram("x", dry_run=True)
        tg.get_updates = lambda offset, timeout=0: [u for u in updates if u["update_id"] >= offset]
        st = Storage(s.db_path)
        m = Monitor(s, st, tg)
        m.sites = {x.id: x for x in load_sites(s.sites_file)}
        entry.run_github(s, st, m, log)
        return tg.sent_log

    out = run()
    assert {c for c, _ in out} == {"222"} and "running on GitHub" in out[0][1], out  # admin only
    assert not (DATA / "bot.db-wal").exists() or (DATA / "bot.db-wal").stat().st_size == 0
    print("✓ run 1: baseline, admin informed, database flushed to one file")

    (WEB / "a.html").write_text(page('<a href="/n/resultats">Résultats du concours</a>'), encoding="utf-8")
    updates.append({"update_id": 7, "message": {"chat": {"id": 222}, "from": {"id": 222}, "text": "/status"}})
    out = run()
    to111 = [t for c, t in out if c == "111"]
    to222 = [t for c, t in out if c == "222"]
    assert len(to111) == 1 and "Résultats du concours" in to111[0], out
    assert any("Last check" in t and "GitHub schedule" in t for t in to222), to222
    print("✓ run 2: new announcement sent, /status answered with last check from run 1")

    out = run()
    assert [t for c, t in out if c == "111"] == [] and [t for c, t in out if c == "222"] == [], out
    print("✓ run 3: no duplicate alert, old command not answered twice")

    srv.shutdown()
    shutil.rmtree(WEB)
    shutil.rmtree(DATA)
    print("\nALL GITHUB-MODE TESTS PASSED")


if __name__ == "__main__":
    main()
