# Steam Addiction Tracker

Which Steam games eat the most hours? Every day this pulls the 500 most recent reviews for the best-selling games in a set of Steam tags and ranks them by how long those reviewers have actually played.

**Live page:** https://marcojhb.github.io/steam-addiction-tracker/

## What it measures

For each game, from its most recent reviewers:

| Number | What it is |
| --- | --- |
| Median / average hours | Total Steam playtime for the game, as of today |
| Most by one player | The single biggest playtime in the sample |
| Top 10% | Hours that the top tenth of reviewers have reached |
| Hours after reviewing | Median of (hours now − hours when they reviewed) |
| Still playing | Share who played in the last two weeks |
| Heavy players | Share with 100+ hours (configurable) |
| Addiction score | Median hours × (1 + still-playing share) |

Every number also gets saved daily to `docs/data/history.json`, so the page can show trends.

## Changing what it tracks

Edit `config.json` on GitHub (pencil icon) and commit. The next daily run picks it up.

| Setting | Meaning |
| --- | --- |
| `tags` | Steam tag names, exactly as Steam spells them. A custom tag lists rules, any of which is enough, e.g. Tycoon = Management + Capitalism in the top 10 tags, or Capitalism in the top 5 |
| `genre_top_n` | A game gets a genre only if that tag is in its own top N Steam tags (default 5). Custom tags check the top 10 |
| `excluded_genres` | Games with any of these in their top N tags are left out (4X, RTS, turn-based strategy, incremental, idler, clicker) |
| `genre_overrides` | Set a game's genres by hand, by app ID, e.g. `"3219030": ["Management", "Tycoon"]` for Planet Zoo 2 |
| `games_per_tag` | How many top sellers to take from each tag (overlaps are merged) |
| `reviews_per_game` | How many recent reviews to read per game |
| `min_reviews` | Games with fewer recent reviews than this are left out |
| `heavy_player_hours` | Cut-off for "heavy players" |
| `extra_appids` | Steam app IDs to always include, e.g. `[294100]` |
| `exclude_appids` | App IDs to always leave out |

## Running it

- **Automatically:** every day at 04:23 UTC via GitHub Actions.
- **Right now:** Actions tab → "Refresh data and publish site" → Run workflow. A full run takes roughly 20–40 minutes.
- **On your own machine:** `pip install requests`, then `python scripts/collect.py` (add `--limit 5 --reviews 100` for a quick test). Open `docs/index.html` through any local web server, e.g. `python -m http.server -d docs`.

## Files

```
config.json                     settings
scripts/collect.py              fetches reviews from Steam and computes the numbers
docs/                           the website (GitHub Pages)
docs/data/latest.json           today's numbers, one entry per game
docs/data/history.json          daily snapshots per game
docs/data/meta.json             cached names, release dates and images
.github/workflows/refresh.yml   the daily schedule
```
