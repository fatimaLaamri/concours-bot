"""Check every site in sites.yaml once and write a report (no Telegram, no database).

    python check_sites.py                 -> data/site_report.csv + summary in the terminal
    python check_sites.py ensa_agadir     -> details for one site (shows the links it found)

Use it to find dead URLs, redirects, sites with an RSS feed, and sites where the
generic extractor finds nothing (those need a `selector` in sites.yaml).
"""
from __future__ import annotations

import csv
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from bot.config import ROOT, load_sites
from bot.extractor import (KeywordMatcher, extract_feed, extract_html, link_path_text,
                           looks_like_feed)
from bot.fetcher import FetchError, fetch

matcher = KeywordMatcher()


def probe(site):
    row = {"id": site.id, "name": site.name, "url": site.url, "category": site.category,
           "status": "", "final_url": "", "ssl": "", "rss": site.rss or "", "links": 0,
           "announcement_links": 0, "error": "", "sample": ""}
    try:
        page = fetch(site.rss or site.url, 25)
    except FetchError as e:
        row.update(status="DOWN", error=str(e))
        return row, []
    row["final_url"] = page.url if page.url.rstrip("/") != site.url.rstrip("/") else ""
    row["ssl"] = "invalid" if page.insecure_ssl else "ok"
    if looks_like_feed(page.content, page.content_type):
        items, feeds = extract_feed(page.content), []
    else:
        items, feeds = extract_html(page.content, page.url, site.selector)
    if feeds and not row["rss"]:
        row["rss"] = feeds[0]
    good = [i for i in items if not site.keywords or matcher.matches(i.title, link_path_text(i.url))]
    row.update(status="OK" if good else "NO_LINKS", links=len(items), announcement_links=len(good),
               sample=" | ".join(i.title[:60] for i in good[:3]))
    return row, good


def main():
    sites = load_sites(ROOT / "sites.yaml")
    if len(sys.argv) > 1:
        wanted = set(sys.argv[1:])
        for site in [s for s in sites if s.id in wanted]:
            row, good = probe(site)
            print(f"\n{site.name} [{row['status']}] {row['error']}")
            if row["final_url"]:
                print(f"  redirects to: {row['final_url']}")
            if row["rss"]:
                print(f"  RSS: {row['rss']}")
            print(f"  {row['links']} links, {row['announcement_links']} look like announcements:")
            for i in good[:40]:
                print(f"   - {i.title[:90]}\n     {i.url}")
        return

    rows = []
    with ThreadPoolExecutor(max_workers=12) as pool:
        futs = [pool.submit(probe, s) for s in sites]
        for n, f in enumerate(as_completed(futs), 1):
            row, _ = f.result()
            rows.append(row)
            print(f"[{n:3}/{len(sites)}] {row['status']:8} {row['id']:28} {row['error']}")

    out = Path(ROOT / "data" / "site_report.csv")
    out.parent.mkdir(exist_ok=True)
    rows.sort(key=lambda r: (r["status"], r["category"], r["id"]))
    with out.open("w", newline="", encoding="utf-8-sig") as fh:  # utf-8-sig: opens fine in Excel
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    by = {}
    for r in rows:
        by[r["status"]] = by.get(r["status"], 0) + 1
    print("\nSummary:", ", ".join(f"{k}: {v}" for k, v in sorted(by.items())))
    print(f"With RSS feed: {sum(1 for r in rows if r['rss'])}")
    print(f"Redirected: {sum(1 for r in rows if r['final_url'])}")
    print(f"Report written to {out}")


if __name__ == "__main__":
    main()
