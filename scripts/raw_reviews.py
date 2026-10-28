"""
Export the raw recent reviews for a list of games, so the stats can be checked
by hand (e.g. in Excel).

Run:  python scripts/raw_reviews.py --appids 294100,427520 --label my-list
Output: research/raw/<date>-<label>.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "research" / "raw"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--appids", required=True)
    ap.add_argument("--reviews", type=int, default=500)
    ap.add_argument("--label", default="raw")
    args = ap.parse_args()

    steam = collect.Steam(delay=0.6)
    games = []
    for appid in [int(x) for x in args.appids.split(",") if x.strip()]:
        meta = steam.app_meta(appid) or {}
        reviews, summary = steam.recent_reviews(appid, args.reviews)
        rows = []
        for r in reviews:
            a = r.get("author", {})
            rows.append({
                "id": r.get("recommendationid"),
                "created": datetime.fromtimestamp(r.get("timestamp_created", 0), timezone.utc).strftime("%Y-%m-%d %H:%M"),
                "recommended": bool(r.get("voted_up")),
                "minutes_total": a.get("playtime_forever", 0),
                "minutes_at_review": a.get("playtime_at_review", 0),
                "minutes_last_2_weeks": a.get("playtime_last_two_weeks", 0),
                "language": r.get("language"),
                "steam_purchase": r.get("steam_purchase"),
                "received_for_free": r.get("received_for_free"),
                "early_access_review": r.get("written_during_early_access"),
            })
        games.append({"appid": appid, "name": meta.get("name"), "release_date": meta.get("release_date"),
                      "total_reviews": summary.get("total_reviews"), "review_score_desc": summary.get("review_score_desc"),
                      "players_now": steam.current_players(appid), "reviews": rows})
        collect.log(f"{meta.get('name')}: {len(rows)} reviews")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{collect.today()}-{args.label}.json"
    path.write_text(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                "reviews_per_game": args.reviews, "games": games}, ensure_ascii=False) + "\n", encoding="utf-8")
    collect.log(f"Saved {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
