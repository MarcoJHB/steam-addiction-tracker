"""
Steam Addiction Tracker: data collector.

For every game in the configured Steam tags (top sellers per tag), this pulls
the most recent N user reviews and works out how much the reviewers actually
play: average / median / max total playtime, how many are still playing in
the last two weeks, how many are "heavy" players, and so on.

Results are written to docs/data/ where the GitHub Pages dashboard reads them.

Run locally:   python scripts/collect.py
Quick test:    python scripts/collect.py --limit 5 --reviews 100
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
DATA_DIR = ROOT / "docs" / "data"
LATEST_PATH = DATA_DIR / "latest.json"
HISTORY_PATH = DATA_DIR / "history.json"
META_PATH = DATA_DIR / "meta.json"

STORE = "https://store.steampowered.com"
API = "https://api.steampowered.com"

# Steam has moved the public review feed before. Try the store's current
# endpoint first, then the classic documented one.
REVIEW_ENDPOINTS = ["/ajaxappreviews/", "/appreviews/"]

# Playtime bands (hours) for the distribution chart.
BANDS = [(0, 2), (2, 10), (10, 25), (25, 50), (50, 100), (100, 250), (250, 500), (500, 1000), (1000, None)]


def log(msg: str) -> None:
    print(msg, flush=True)


class Steam:
    def __init__(self, delay: float):
        self.delay = delay
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": "steam-addiction-tracker/1.0 (+https://github.com/MarcoJHB/steam-addiction-tracker)",
            "Accept": "application/json,text/javascript,*/*;q=0.1",
            "Accept-Language": "en-US,en;q=0.8",
        })
        # Skip the age gate on mature games.
        self.s.cookies.set("birthtime", "283993201", domain="store.steampowered.com")
        self.s.cookies.set("mature_content", "1", domain="store.steampowered.com")
        self.review_endpoint: str | None = None

    def get_json(self, url: str, params: dict | None = None, tries: int = 4):
        """GET a URL and parse JSON. Returns None if the response isn't JSON."""
        for attempt in range(tries):
            time.sleep(self.delay)
            try:
                r = self.s.get(url, params=params, timeout=30)
            except requests.RequestException as e:
                log(f"    network error ({e.__class__.__name__}), retrying")
                time.sleep(5 * (attempt + 1))
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                wait = 15 * (attempt + 1)
                log(f"    HTTP {r.status_code}, waiting {wait}s")
                time.sleep(wait)
                continue
            if r.status_code != 200:
                return None
            try:
                return r.json()
            except ValueError:
                return None
        return None

    # ---- tags & games ------------------------------------------------------

    def tag_ids(self, entries: list) -> dict[str, str]:
        """
        Turn the config's tag list into {display name: Steam tag id(s)}.

        An entry is either a Steam tag name ("City Builder") or a custom tag
        that requires several Steam tags at once:
            {"name": "Tycoon", "steam_tags": ["Management", "Capitalism"]}
        """
        tags = self.get_json(f"{STORE}/tagdata/populartags/english") or []
        by_name = {t["name"].lower(): t["tagid"] for t in tags}
        out = {}
        for name, parts in self._searches(entries):
            ids = []
            for p in parts:
                tid = by_name.get(p.lower())
                if tid is None:
                    log(f"  ! tag not found on Steam: {p!r} (check spelling in config.json)")
                    break
                ids.append(str(tid))
            else:
                # Steam search treats a list of tag IDs as "all of these".
                # A custom genre with several rules gets one search per rule.
                key = name if name not in out else f"{name} #{sum(k.startswith(name) for k in out) + 1}"
                out[key] = ",".join(ids)
        return out

    @staticmethod
    def _searches(entries: list) -> list[tuple[str, list[str]]]:
        found = []
        for e in entries:
            if isinstance(e, str):
                found.append((e, [e]))
            elif e.get("any_of"):
                found += [(e["name"], rule["steam_tags"]) for rule in e["any_of"]]
            else:
                found.append((e["name"], e["steam_tags"]))
        return found

    def top_games_for_tag(self, tag_id: str, count: int) -> list[tuple[int, str]]:
        games: list[tuple[int, str]] = []
        start = 0
        while len(games) < count:
            data = self.get_json(f"{STORE}/search/results/", {
                "query": "", "start": start, "count": 50, "tags": tag_id,
                "category1": 998,            # games only
                "filter": "topsellers", "infinite": 1, "cc": "us", "l": "english",
            })
            if not data or not data.get("results_html"):
                break
            html = data["results_html"]
            rows = re.findall(r'data-ds-appid="([\d,]+)".*?<span class="title">([^<]*)</span>', html, re.S)
            if not rows:
                break
            for ids, title in rows:
                if "," in ids:               # bundle, skip
                    continue
                games.append((int(ids), unescape(title)))
            start += 50
            if start >= int(data.get("total_count", 0)):
                break
        return games[:count]

    # ---- per-game data -----------------------------------------------------

    def app_meta(self, appid: int) -> dict | None:
        data = self.get_json(f"{STORE}/api/appdetails", {
            "appids": appid, "filters": "basic,release_date", "cc": "us", "l": "english"})
        if not data or not data.get(str(appid), {}).get("success"):
            return None
        d = data[str(appid)]["data"]
        return {
            "name": d.get("name"),
            "type": d.get("type"),
            "is_free": d.get("is_free", False),
            "header_image": d.get("header_image"),
            "release_date": parse_release(d.get("release_date", {}).get("date")),
            "coming_soon": d.get("release_date", {}).get("coming_soon", False),
            "fetched": today(),
        }

    def first_review_date(self, appid: int) -> str | None:
        """Date of the game's first Steam review. For games that spent years in
        Early Access this is far earlier than the official release date, and
        it's the better measure of how long people have been able to play."""
        data = self.get_json(f"{STORE}/appreviewhistogram/{appid}", {"l": "english", "review_score_preference": 0})
        try:
            ts = int(data["results"]["start_date"])
        except (TypeError, KeyError, ValueError):
            return None
        return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d") if ts > 0 else None

    def store_tags(self, appid: int) -> list[str] | None:
        """A game's own Steam tags, in order of how many players applied them
        (from the store's hover card). None if Steam didn't answer."""
        time.sleep(self.delay)
        try:
            r = self.s.get(f"{STORE}/apphover/{appid}", params={"l": "english"}, timeout=30)
        except requests.RequestException:
            return None
        if r.status_code != 200:
            return None
        return [unescape(t) for t in re.findall(r'class="app_tag"[^>]*>([^<]+)<', r.text)][:15]

    def current_players(self, appid: int) -> int | None:
        data = self.get_json(f"{API}/ISteamUserStats/GetNumberOfCurrentPlayers/v1/", {"appid": appid})
        try:
            return int(data["response"]["player_count"])
        except (TypeError, KeyError, ValueError):
            return None

    def recent_reviews(self, appid: int, target: int) -> tuple[list[dict], dict]:
        endpoints = [self.review_endpoint] if self.review_endpoint else REVIEW_ENDPOINTS
        for ep in endpoints:
            reviews, summary, ok = self._page_reviews(ep, appid, target)
            if ok:
                if self.review_endpoint is None:
                    log(f"  using review endpoint {ep}")
                    self.review_endpoint = ep
                return reviews, summary
        return [], {}

    def _page_reviews(self, ep: str, appid: int, target: int):
        reviews: dict[str, dict] = {}
        summary: dict = {}
        cursor = "*"
        seen_cursors = set()
        ok = False
        while len(reviews) < target:
            data = self.get_json(f"{STORE}{ep}{appid}", {
                "json": 1, "filter": "recent", "language": "all",
                "purchase_type": "all", "review_type": "all",
                "num_per_page": 100, "cursor": cursor,
            })
            if not data or data.get("success") != 1:
                break
            ok = True
            if not summary and data.get("query_summary"):
                summary = data["query_summary"]
            batch = data.get("reviews") or []
            new = 0
            for r in batch:
                rid = r.get("recommendationid")
                if rid and rid not in reviews:
                    reviews[rid] = r
                    new += 1
            cursor = data.get("cursor")
            if not batch or new == 0 or not cursor or cursor in seen_cursors:
                break
            seen_cursors.add(cursor)
        newest_first = sorted(reviews.values(), key=lambda r: r.get("timestamp_created", 0), reverse=True)
        return newest_first[:target], summary, ok


# ---- stats -------------------------------------------------------------------

def compute_stats(reviews: list[dict], heavy_hours: float) -> dict:
    forever = [r["author"].get("playtime_forever", 0) / 60 for r in reviews]
    at_review = [r["author"].get("playtime_at_review", 0) / 60 for r in reviews]
    last2w = [r["author"].get("playtime_last_two_weeks", 0) / 60 for r in reviews]
    n = len(forever)
    if n == 0:
        return {"sample": 0}

    srt = sorted(forever)
    median = statistics.median(srt)
    active = sum(1 for h in last2w if h > 0) / n
    stamps = [r.get("timestamp_created", 0) for r in reviews if r.get("timestamp_created")]
    span_days = (max(stamps) - min(stamps)) / 86400 if len(stamps) > 1 else None

    buckets = []
    for lo, hi in BANDS:
        c = sum(1 for h in forever if h >= lo and (hi is None or h < hi))
        buckets.append({"label": f"{lo}+" if hi is None else f"{lo}-{hi}", "count": c})

    return {
        "sample": n,
        "avg": round(sum(forever) / n, 1),
        "median": round(median, 1),
        "p90": round(percentile(srt, 0.9), 1),
        "max": round(srt[-1], 1),
        "avg_at_review": round(sum(at_review) / n, 1),
        "median_at_review": round(statistics.median(at_review), 1),
        # Hours played after writing the review: people who kept going.
        "median_after_review": round(statistics.median([f - a for f, a in zip(forever, at_review)]), 1),
        "avg_last_2w": round(sum(last2w) / n, 1),
        "active_pct": round(active * 100, 1),
        "heavy_pct": round(sum(1 for h in forever if h >= heavy_hours) / n * 100, 1),
        "recommended_pct": round(sum(1 for r in reviews if r.get("voted_up")) / n * 100, 1),
        "sample_span_days": round(span_days, 1) if span_days is not None else None,
        # Headline metric. Median is used instead of average so a few people
        # who left the game idling for 20,000 hours can't skew it, and it's
        # boosted by how many reviewers are still playing right now.
        "score": round(median * (1 + active), 1),
        "distribution": buckets,
    }


def classify(appid: int, steam_tags: list[str], cfg: dict) -> tuple[list[str], str | None]:
    """
    Work out which of the site's genres a game belongs to, from its OWN top
    Steam tags (not from whichever search happened to find it).

    - A plain genre ("City Builder") applies when that tag is in the game's
      top N tags (genre_top_n, default 5).
    - A custom genre ({"name": "Tycoon", "any_of": [{"steam_tags": [...], "top_n": 10}]})
      applies when, for any one of its rules, all those Steam tags are in the
      game's top `top_n` tags.
    - excluded_genres (4X, RTS...) in the top N drops the game entirely:
      further down the list they only count as a minor mechanic.
    - genre_overrides in config.json replace all of this for a given app ID.
    Returns (genres, reason it was dropped or None).
    """
    override = cfg.get("genre_overrides", {}).get(str(appid))
    if override:
        return list(override), None
    n = int(cfg.get("genre_top_n", 5))
    top = steam_tags[:n]
    banned = [t for t in cfg.get("excluded_genres", []) if t in top]
    if banned:
        return [], f"{' + '.join(banned)} in its top {n} Steam tags"
    genres = []
    for entry in cfg.get("tags", []):
        if isinstance(entry, str):
            if entry in top:
                genres.append(entry)
        else:
            # A custom genre matches if any of its rules does. A rule = Steam tags
            # that must ALL be in the game's top `top_n` tags.
            rules = entry.get("any_of") or [{"steam_tags": entry["steam_tags"], "top_n": entry.get("top_n", 10)}]
            for rule in rules:
                within = steam_tags[: int(rule.get("top_n", 10))]
                if all(t in within for t in rule["steam_tags"]):
                    genres.append(entry["name"])
                    break
    if not genres:
        return [], f"none of the site's genres in its top {n} Steam tags"
    return genres, None


def add_age_adjustment(games: list[dict], peers: int = 30) -> None:
    """
    Older games naturally show more hours: their reviewers have had longer to
    play. Dividing by age doesn't fix that (a 10-year-old game's recent
    reviewers didn't all play it for 10 years), so instead each game is
    compared with the games closest to it in release date.

    age_x = this game's addiction score / median score of its nearest peers.
    1.0 = typical for a game of its age, 2.0 = twice as much as its peers.
    """
    import math

    def days(g):
        d = g.get("days_playable")
        return d if d is not None else g.get("days_since_release")

    def age_key(g):
        # Log scale: the gap between 1 and 6 months matters far more than
        # between 8 and 9 years. +30 days keeps launch-week games stable.
        return math.log(days(g) + 30)

    pool = [g for g in games if days(g) is not None and g.get("score")]
    k = min(peers, len(pool) - 1)
    for g in games:
        g["age_x"] = g["age_peer_score"] = g["age_peer_years"] = None
    if k < 5:
        return
    for g in pool:
        me = age_key(g)
        nearest = sorted((p for p in pool if p is not g), key=lambda p: abs(age_key(p) - me))[:k]
        peer_score = statistics.median(p["score"] for p in nearest)
        if peer_score <= 0:
            continue
        years = sorted(int((p.get("playable_since") or p["release_date"])[:4])
                       for p in nearest if p.get("playable_since") or p.get("release_date"))
        g["age_x"] = round(g["score"] / peer_score, 2)
        g["age_peer_score"] = round(peer_score, 1)
        g["age_peer_years"] = [years[0], years[-1]] if years else None


def percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * q
    f, c = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


# ---- helpers -----------------------------------------------------------------

def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def unescape(s: str) -> str:
    import html
    return html.unescape(s).strip()


def parse_release(s: str | None) -> str | None:
    if not s:
        return None
    for fmt in ("%d %b, %Y", "%b %d, %Y", "%d %B, %Y", "%B %d, %Y", "%b %Y", "%B %Y", "%Y"):
        try:
            return datetime.strptime(s.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def days_since(date_str: str | None) -> int | None:
    if not date_str:
        return None
    d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return max((datetime.now(timezone.utc) - d).days, 0)


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


def save_json(path: Path, data, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    else:
        text = json.dumps(data, ensure_ascii=False, indent=1)
    path.write_text(text + "\n", encoding="utf-8")


# ---- main --------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, help="only process the first N games (for testing)")
    ap.add_argument("--reviews", type=int, help="override reviews_per_game")
    args = ap.parse_args()

    cfg = load_json(CONFIG_PATH, {})
    target = args.reviews or int(cfg.get("reviews_per_game", 500))
    heavy = float(cfg.get("heavy_player_hours", 100))
    min_reviews = int(cfg.get("min_reviews", 50))
    exclude = {int(x) for x in cfg.get("exclude_appids", [])}
    extra = {int(x) for x in cfg.get("extra_appids", [])}

    steam = Steam(delay=float(cfg.get("request_delay_seconds", 0.7)))
    meta_cache: dict = load_json(META_PATH, {})
    history: dict = load_json(HISTORY_PATH, {})
    previous = {str(g["appid"]): g for g in load_json(LATEST_PATH, {}).get("games", [])}

    # 1. Which games?
    log("Resolving tags...")
    tag_ids = steam.tag_ids(cfg.get("tags", []))
    if not tag_ids:
        log("No valid tags, stopping.")
        return 1
    games: dict[int, dict] = {}
    for name, tid in tag_ids.items():
        found = steam.top_games_for_tag(tid, int(cfg.get("games_per_tag", 50)))
        log(f"  {name}: {len(found)} games")
        for appid, title in found:
            g = games.setdefault(appid, {"name": title, "tags": []})
            genre = name.split(" #")[0]
            if genre not in g["tags"]:
                g["tags"].append(genre)
    for appid in cfg.get("extra_appids", []):
        games.setdefault(int(appid), {"name": None, "tags": ["Extra"]})
    for appid in exclude:
        games.pop(appid, None)
    if not games:
        log("Found no games. Steam search may have changed or be unreachable.")
        return 1

    order = list(games.items())
    if args.limit:
        order = order[: args.limit]
    log(f"Processing {len(order)} unique games, {target} reviews each\n")

    # 2. Per-game data.
    results, failed = [], 0
    for i, (appid, g) in enumerate(order, 1):
        key = str(appid)
        meta = meta_cache.get(key)
        if not meta or not meta.get("release_date"):
            fresh = steam.app_meta(appid)
            if fresh:
                meta = meta_cache[key] = fresh
        meta = meta or {}
        if meta and "first_review" not in meta:
            meta["first_review"] = steam.first_review_date(appid)
            meta_cache[key] = meta
        name = meta.get("name") or g["name"] or f"App {appid}"
        playable_since = min(filter(None, [meta.get("release_date"), meta.get("first_review")]), default=None)

        if meta.get("type") and meta["type"] != "game":
            log(f"[{i}/{len(order)}] {name}: skipped (not a game)")
            continue
        if meta.get("coming_soon"):
            log(f"[{i}/{len(order)}] {name}: skipped (not released)")
            continue

        # Genres from the game's own tags (refreshed every couple of weeks).
        if not meta.get("steam_tags") or (days_since(meta.get("tags_fetched")) or 0) > 14:
            tags_now = steam.store_tags(appid)
            if tags_now:
                meta["steam_tags"], meta["tags_fetched"] = tags_now, today()
                meta_cache[key] = meta
        steam_tags = meta.get("steam_tags") or []
        if steam_tags:
            genres, dropped = classify(appid, steam_tags, cfg)
        else:
            genres, dropped = g["tags"], None      # Steam didn't answer: fall back to the search tags
        if appid in extra and not genres:
            genres, dropped = ["Extra"], None
        if dropped:
            log(f"[{i}/{len(order)}] {name}: skipped ({dropped})")
            continue

        reviews, summary = steam.recent_reviews(appid, target)
        if len(reviews) < min_reviews:
            if key in previous and not reviews:
                # Steam hiccup: keep yesterday's numbers rather than dropping the game.
                old = dict(previous[key], stale=True, tags=genres)
                results.append(old)
                failed += 1
                log(f"[{i}/{len(order)}] {name}: fetch failed, kept previous data")
            else:
                log(f"[{i}/{len(order)}] {name}: only {len(reviews)} reviews, skipped")
            continue

        stats = compute_stats(reviews, heavy)
        players = steam.current_players(appid)
        entry = {
            "appid": appid,
            "name": name,
            "tags": genres,
            "steam_tags": steam_tags[:5],
            "header_image": meta.get("header_image"),
            "release_date": meta.get("release_date"),
            "days_since_release": days_since(meta.get("release_date")),
            # Earlier than release_date for games that were in Early Access.
            "playable_since": playable_since,
            "days_playable": days_since(playable_since),
            "is_free": meta.get("is_free", False),
            "players_now": players,
            "total_reviews": summary.get("total_reviews"),
            "review_score_desc": summary.get("review_score_desc"),
            **stats,
        }
        results.append(entry)

        h = history.setdefault(key, [])
        h[:] = [p for p in h if p.get("d") != today()]
        h.append({"d": today(), "score": stats["score"], "avg": stats["avg"], "median": stats["median"],
                  "max": stats["max"], "active": stats["active_pct"], "players": players})
        log(f"[{i}/{len(order)}] {name}: median {stats['median']}h, avg {stats['avg']}h, "
            f"max {stats['max']}h, active {stats['active_pct']}%")

    if not results:
        log("\nNo results at all, not overwriting existing data.")
        return 1
    if failed > len(results) / 2:
        log(f"\nMore than half the games failed ({failed}), not overwriting existing data.")
        return 1

    # 3. Level the playing field between old and new games.
    add_age_adjustment(results, int(cfg.get("age_peers", 30)))
    for e in results:
        h = history.get(str(e["appid"]))
        if h and h[-1].get("d") == today():
            h[-1]["age_x"] = e.get("age_x")

    # 4. Save.
    keep = int(cfg.get("history_days", 365))
    for k in history:
        history[k] = history[k][-keep:]
    results.sort(key=lambda e: e.get("score", 0), reverse=True)
    save_json(LATEST_PATH, {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reviews_per_game": target,
        "heavy_player_hours": heavy,
        "age_peers": int(cfg.get("age_peers", 30)),
        "tags": [e if isinstance(e, str) else e["name"] for e in cfg.get("tags", [])],
        "review_endpoint": steam.review_endpoint,
        "games": results,
    })
    save_json(HISTORY_PATH, history, compact=True)
    save_json(META_PATH, meta_cache)
    log(f"\nSaved {len(results)} games ({failed} kept from previous run).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
