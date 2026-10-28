"""
Research run for the "most addictive city builders" video.

Builds a pool of Steam's top sellers tagged City Builder or Colony Sim, keeps
games whose top 5 tags include one of those (and none of the excluded genres),
keeps games released in the last N years, then reads each game's most recent
reviews to measure playtime.

Output: research/city-builders-<date>.json (every candidate, with tags and stats).

Run:  python scripts/city_builders.py            (defaults below)
      python scripts/city_builders.py --years 5 --reviews 500 --pool 300
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect  # noqa: E402  (reuses the tracker's Steam client and stats)

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "research"

POOL_TAGS = ["City Builder", "Colony Sim"]
CORE = {"City Builder", "Colony Sim"}
BANNED = {"Incremental", "Idler", "Clicker", "Turn-Based Strategy", "4X", "RTS"}


def hover_tags(steam: collect.Steam, appid: int) -> list[str]:
    """Steam's hover card lists a game's tags in order of how many players applied them."""
    collect.time.sleep(steam.delay)
    try:
        r = steam.s.get(f"{collect.STORE}/apphover/{appid}", params={"l": "english"}, timeout=30)
    except Exception:
        return []
    if r.status_code != 200:
        return []
    return [collect.unescape(t) for t in re.findall(r'class="app_tag"[^>]*>([^<]+)<', r.text)][:10]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=5)
    ap.add_argument("--reviews", type=int, default=500)
    ap.add_argument("--pool", type=int, default=300, help="top sellers per pool tag")
    ap.add_argument("--min-reviews", type=int, default=100)
    ap.add_argument("--extra", default="", help="comma-separated app IDs to add to the pool")
    ap.add_argument("--only-extra", action="store_true", help="skip the top-seller pool, only measure --extra")
    ap.add_argument("--label", default="", help="suffix for the output file name")
    ap.add_argument("--force-extra", action="store_true", help="with --only-extra: measure the games even if they fail the tag rule")
    args = ap.parse_args()

    steam = collect.Steam(delay=0.6)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=365.25 * args.years)).strftime("%Y-%m-%d")
    collect.log(f"Release cutoff: {cutoff}")

    ids = {} if args.only_extra else steam.tag_ids(POOL_TAGS)
    pool: dict[int, str] = {}
    for name, tid in ids.items():
        found = steam.top_games_for_tag(tid, args.pool)
        collect.log(f"  {name}: {len(found)} games")
        for appid, title in found:
            pool.setdefault(appid, title)
    for x in filter(None, args.extra.split(",")):
        pool.setdefault(int(x), f"App {x}")
    collect.log(f"Pool: {len(pool)} unique games\n")

    out, kept = [], 0
    for i, (appid, title) in enumerate(pool.items(), 1):
        tags = hover_tags(steam, appid)
        top5 = tags[:5]
        core = next((t for t in top5 if t in CORE), None)
        banned = [t for t in top5 if t in BANNED]
        rec = {"appid": appid, "name": title, "tags": tags, "core_tag": core or "(hand-picked)",
               "core_pos": top5.index(core) + 1 if core else None, "banned": banned}
        if (not core or banned) and not (args.only_extra and args.force_extra):
            rec["status"] = "excluded: " + (", ".join(banned) + " in top 5 tags" if banned else "no City Builder/Colony Sim in top 5 tags")
            out.append(rec)
            continue

        meta = steam.app_meta(appid) or {}
        rec.update({"name": meta.get("name") or title, "release_date": meta.get("release_date"),
                    "type": meta.get("type"), "coming_soon": meta.get("coming_soon"),
                    "header_image": meta.get("header_image")})
        if meta.get("type") not in (None, "game") or meta.get("coming_soon"):
            rec["status"] = "excluded: not a released game"
            out.append(rec)
            continue
        if not rec["release_date"] or rec["release_date"] < cutoff:
            rec["status"] = f"excluded: released before {cutoff}"
            out.append(rec)
            continue

        rec["first_review"] = steam.first_review_date(appid)
        reviews, summary = steam.recent_reviews(appid, args.reviews)
        if len(reviews) < args.min_reviews:
            rec["status"] = f"excluded: only {len(reviews)} recent reviews"
            out.append(rec)
            continue
        stats = collect.compute_stats(reviews, 100)
        rec.update({"status": "ranked", "total_reviews": summary.get("total_reviews"),
                    "review_score_desc": summary.get("review_score_desc"), **stats})
        players = steam.current_players(appid)
        rec["players_now"] = players
        out.append(rec)
        kept += 1
        collect.log(f"[{i}/{len(pool)}] {rec['name']}: avg {stats['avg']}h, median {stats['median']}h, max {stats['max']}h")

    OUT_DIR.mkdir(exist_ok=True)
    path = OUT_DIR / f"city-builders-{collect.today()}{('-' + args.label) if args.label else ('-extra' if args.only_extra else '')}.json"
    ranked = sorted((r for r in out if r["status"] == "ranked"), key=lambda r: -r["avg"])
    rest = [r for r in out if r["status"] != "ranked"]
    path.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "release_cutoff": cutoff, "reviews_per_game": args.reviews,
        "pool_tags": POOL_TAGS, "core_tags": sorted(CORE), "banned_tags": sorted(BANNED),
        "ranked": ranked, "excluded": rest,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    collect.log(f"\nSaved {path.name}: {kept} ranked, {len(rest)} excluded")
    return 0 if kept else 1


if __name__ == "__main__":
    sys.exit(main())
