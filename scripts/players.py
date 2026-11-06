"""
Steam Addiction Tracker: player-count sampler for the Charts tab.

Every couple of hours this asks Steam how many people are playing each game in
the charts right now and adds it to a running total for the month:

    docs/data/players/2026-11.json
    {"runs": 240, "games": {"3219030": [sum of counts, number of samples, highest count], ...}}

charts.py turns that into an average for any game SteamCharts doesn't track
(it misses some new releases, e.g. Planet Zoo 2).

Run locally:   python scripts/players.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from collect import DATA_DIR, LATEST_PATH, Steam, load_json, log, save_json  # noqa: E402

CHARTS_PATH = DATA_DIR / "charts.json"


def main() -> int:
    appids = {int(g["appid"]) for g in load_json(CHARTS_PATH, {}).get("games", [])}
    appids |= {int(g["appid"]) for g in load_json(LATEST_PATH, {}).get("games", [])}
    if not appids:
        log("No games to sample yet.")
        return 0

    month = datetime.now(timezone.utc).strftime("%Y-%m")
    path = DATA_DIR / "players" / f"{month}.json"
    data = load_json(path, {"runs": 0, "games": {}})

    steam = Steam(delay=0.1)
    got = 0
    for appid in sorted(appids):
        n = steam.current_players(appid)
        if n is None:
            continue
        s = data["games"].setdefault(str(appid), [0, 0, 0])
        s[0] += n
        s[1] += 1
        s[2] = max(s[2], n)
        got += 1

    if got < len(appids) / 2:
        log(f"Only {got}/{len(appids)} answers from Steam, not saving this sample.")
        return 1
    data["runs"] += 1
    data["updated"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
    save_json(path, data, compact=True)
    log(f"Sampled {got}/{len(appids)} games for {month} (run {data['runs']}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
