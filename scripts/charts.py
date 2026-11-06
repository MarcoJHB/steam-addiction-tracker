"""
Steam Addiction Tracker: monthly player charts.

Builds the "Charts" tab: the most-played games in each category, ranked by
the average number of people playing at the same time during a calendar month,
with movement against the month before and the same month a year earlier.

Where the numbers come from
  - Which games: Steam's top sellers in each category's tags, plus every game
    already on the main page. Genres come from each game's own top Steam tags,
    with the same rules as the main page (see classify() in collect.py).
  - Player counts: SteamCharts' monthly table for each game ("Avg. Players"
    and "Peak Players" per calendar month), which is built from Steam's own
    hourly player counts.

The ranking itself is done in the browser (docs/charts.js) so any month and
either comparison can be shown. This script just gathers the monthly numbers.

Run locally:   python scripts/charts.py
Quick test:    python scripts/charts.py --limit 10
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime, timezone

import requests

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from collect import (  # noqa: E402
    CONFIG_PATH, DATA_DIR, LATEST_PATH, META_PATH, Steam, classify, days_since,
    load_json, log, save_json, today, unescape,
)

CHARTS_PATH = DATA_DIR / "charts.json"
CHARTS_META_PATH = DATA_DIR / "charts-meta.json"
STEAMCHARTS = "https://steamcharts.com/app/{appid}"

DEFAULTS = {
    "categories": [
        {"name": "City Builders & Colony Sims", "genres": ["City Builder", "Colony Sim"]},
        {"name": "Management & Tycoon", "genres": ["Management", "Tycoon"]},
    ],
    "games_per_tag": 150,
    "chart_size": 40,
    "min_avg_players": 50,
    "months_kept": 25,
    "request_delay_seconds": 1.0,
}


class SteamCharts:
    def __init__(self, delay: float):
        self.delay = delay
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": "steam-addiction-tracker/1.0 (+https://github.com/MarcoJHB/steam-addiction-tracker)",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.8",
        })
        self.ok = 0
        self.failed = 0

    def monthly(self, appid: int) -> dict[str, list[float]] | None:
        """{"2026-10": [avg players, peak players], ...}. {} = SteamCharts has no
        data for this game. None = the request failed."""
        for attempt in range(3):
            time.sleep(self.delay)
            try:
                r = self.s.get(STEAMCHARTS.format(appid=appid), timeout=30)
            except requests.RequestException:
                time.sleep(5 * (attempt + 1))
                continue
            if r.status_code == 404:
                self.ok += 1
                return {}
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(20 * (attempt + 1))
                continue
            if r.status_code != 200:
                break
            rows = parse_monthly(r.text)
            if rows is None:
                break
            self.ok += 1
            return rows
        self.failed += 1
        return None


def parse_monthly(html: str) -> dict[str, list[float]] | None:
    table = re.search(r'<table[^>]*class="[^"]*common-table[^"]*"[^>]*>(.*?)</table>', html, re.S)
    if not table:
        return None                       # not the page we expected (blocked, redesigned...)
    out: dict[str, list[float]] = {}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", table.group(1), re.S):
        cells = [unescape(re.sub(r"<[^>]+>", "", c)) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(cells) < 5:
            continue
        try:
            month = datetime.strptime(cells[0].strip(), "%B %Y").strftime("%Y-%m")
        except ValueError:
            continue                      # "Last 30 Days" row
        avg, peak = number(cells[1]), number(cells[4])
        if avg is not None:
            out[month] = [round(avg, 1), int(peak) if peak is not None else None]
    return out


def number(s: str) -> float | None:
    s = s.replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def month_add(ym: str, n: int) -> str:
    y, m = map(int, ym.split("-"))
    m += n
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return f"{y:04d}-{m:02d}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, help="only process the first N games (for testing)")
    args = ap.parse_args()

    cfg = load_json(CONFIG_PATH, {})
    ccfg = {**DEFAULTS, **cfg.get("charts", {})}
    categories = ccfg["categories"]
    wanted_genres = {g for c in categories for g in c["genres"]}
    exclude = {int(x) for x in cfg.get("exclude_appids", [])}

    steam = Steam(delay=float(cfg.get("request_delay_seconds", 0.7)))
    sc = SteamCharts(delay=float(ccfg["request_delay_seconds"]))

    # Shared cache with the main page, read-only, plus this script's own cache.
    main_meta = load_json(META_PATH, {})
    cache: dict = load_json(CHARTS_META_PATH, {})

    # 1. Candidate games: top sellers in each category's tags + the main page's games.
    log("Resolving tags...")
    tag_entries = [e for e in cfg.get("tags", []) if (e if isinstance(e, str) else e["name"]) in wanted_genres]
    tag_ids = steam.tag_ids(tag_entries)
    pool: dict[int, str | None] = {}
    for name, tid in tag_ids.items():
        found = steam.top_games_for_tag(tid, int(ccfg["games_per_tag"]))
        log(f"  {name}: {len(found)} games")
        for appid, title in found:
            pool.setdefault(appid, title)
    for g in load_json(LATEST_PATH, {}).get("games", []):
        pool.setdefault(int(g["appid"]), g["name"])
    for appid in cfg.get("extra_appids", []):
        pool.setdefault(int(appid), None)
    for appid in exclude:
        pool.pop(appid, None)
    if not pool:
        log("Found no games. Steam search may have changed or be unreachable.")
        return 1

    latest = {int(g["appid"]): g for g in load_json(LATEST_PATH, {}).get("games", [])}
    previous = {g["appid"]: g for g in load_json(CHARTS_PATH, {}).get("games", [])}
    order = list(pool.items())[: args.limit] if args.limit else list(pool.items())
    log(f"Checking {len(order)} games\n")

    # 2. Per game: genres (from its own tags), then SteamCharts monthly numbers.
    games, skipped = [], 0
    for i, (appid, title) in enumerate(order, 1):
        key = str(appid)
        meta = dict(main_meta.get(key) or {}, **(cache.get(key) or {}))
        if not meta.get("name") or "type" not in meta:
            fresh = steam.app_meta(appid)
            if fresh:
                meta.update(fresh)
        if not meta.get("steam_tags") or (days_since(meta.get("tags_fetched")) or 0) > 30:
            tags_now = steam.store_tags(appid)
            if tags_now:
                meta["steam_tags"], meta["tags_fetched"] = tags_now, today()
        cache[key] = {k: meta.get(k) for k in
                      ("name", "type", "is_free", "header_image", "release_date", "coming_soon",
                       "first_review", "steam_tags", "tags_fetched") if k in meta}
        name = meta.get("name") or title or f"App {appid}"

        if meta.get("type") and meta["type"] != "game" or meta.get("coming_soon"):
            skipped += 1
            continue
        genres, dropped = classify(appid, meta.get("steam_tags") or [], cfg)
        cats = [c["name"] for c in categories if set(c["genres"]) & set(genres)]
        if not cats:
            skipped += 1
            continue

        monthly = sc.monthly(appid)
        if i == 15 and sc.ok == 0:
            log("SteamCharts isn't answering (first 15 games all failed). Stopping without saving.")
            return 1
        if monthly is None:
            old = previous.get(appid)
            if old:
                games.append(old)
            log(f"[{i}/{len(order)}] {name}: SteamCharts failed" + (", kept previous numbers" if old else ""))
            continue
        if not monthly:
            log(f"[{i}/{len(order)}] {name}: no SteamCharts data")
            continue

        newest = max(monthly)
        keep = sorted(monthly)[-int(ccfg["months_kept"]) - 1:]
        lg = latest.get(appid, {})
        games.append({
            "appid": appid,
            "name": name,
            "header_image": meta.get("header_image") or f"https://cdn.akamai.steamstatic.com/steam/apps/{appid}/header.jpg",
            "release_date": meta.get("release_date"),
            "genres": genres,
            "categories": cats,
            "steam_tags": (meta.get("steam_tags") or [])[:5],
            "monthly": {m: monthly[m] for m in keep},
            "median_hours": lg.get("median"),
        })
        log(f"[{i}/{len(order)}] {name}: {', '.join(cats)} | {newest}: {monthly[newest][0]:,} avg players")

    if sc.failed > max(10, sc.ok):
        log(f"\nToo many SteamCharts failures ({sc.failed} failed, {sc.ok} ok). Not overwriting.")
        return 1
    if not games:
        log("\nNo games with player data. Not overwriting.")
        return 1

    # The chart month is the last complete calendar month.
    now = datetime.now(timezone.utc)
    chart_month = month_add(now.strftime("%Y-%m"), -1)
    months = sorted({m for g in games for m in g["monthly"] if m <= chart_month}, reverse=True)

    save_json(CHARTS_PATH, {
        "generated_at": now.isoformat(timespec="seconds"),
        "chart_month": chart_month,
        "months": months[:13],
        "chart_size": int(ccfg["chart_size"]),
        "min_avg_players": float(ccfg["min_avg_players"]),
        "genre_top_n": int(cfg.get("genre_top_n", 5)),
        "categories": categories,
        "source": "SteamCharts monthly averages (steamcharts.com)",
        "games": sorted(games, key=lambda g: -(g["monthly"].get(chart_month) or [0])[0]),
    })
    save_json(CHARTS_META_PATH, cache)
    log(f"\nSaved {len(games)} games with player data ({skipped} skipped as off-genre or unreleased). "
        f"Chart month: {chart_month}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
