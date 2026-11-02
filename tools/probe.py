"""Try candidate URLs for sites (run on GitHub so the network is the bot's own).

    python tools/probe.py candidates.json results.json
candidates.json: {"site_id": ["url1", "url2", ...], ...}
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bot.config import Site  # noqa: E402
from bot.extractor import extract_feed, extract_html, looks_like_feed  # noqa: E402
from bot.fetcher import FetchError, fetch  # noqa: E402
from bot.filters import AnnouncementFilter  # noqa: E402

F = AnnouncementFilter()


def probe(job):
    sid, category, url = job
    out = {"id": sid, "url": url}
    try:
        page = fetch(url, 20)
    except FetchError as e:
        return {**out, "ok": False, "error": str(e)}
    out["content_type"] = page.content_type
    out["head"] = page.content[:200].decode("utf-8", "replace")
    if looks_like_feed(page.content, page.content_type):
        items, feeds = extract_feed(page.content), []
    else:
        items, feeds = extract_html(page.content, page.url)
    site = Site(id=sid, name=sid, url=url, category=category)
    good = [i for i in items if F.accept(site, i.title, i.url)]
    return {**out, "ok": True, "final_url": page.url, "links": len(items), "announcements": len(good),
            "rss": feeds[0] if feeds else "", "sample": [i.title[:90] for i in good[:4]]}


def main():
    cands = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    jobs = [(sid, c["category"], u) for sid, c in cands.items() for u in c["urls"]]
    with ThreadPoolExecutor(24) as pool:
        results = list(pool.map(probe, jobs))
    Path(sys.argv[2]).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{sum(r['ok'] for r in results)}/{len(results)} candidate URLs answered")


if __name__ == "__main__":
    main()
