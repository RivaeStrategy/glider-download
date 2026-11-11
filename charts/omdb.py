"""OMDb for the chart job, asked as little as possible.

The key's 1,000 requests a day are shared with the apps' movie pages, so every answer is kept in
omdb-cache.json (committed next to the charts) and asked again only after 30 days, or 7 days when
there was no score yet (new releases get their Metacritic score later). A request is never
retried, a run asks at most MAX_PER_RUN times (so most of the day's 1,000 stay with the apps; the
cache fills over a few runs), and once OMDb says the day's limit is reached the job stops asking.
"""
import json
import os
import subprocess
import urllib.parse
from datetime import date

CACHE = os.environ.get("OMDB_CACHE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "omdb-cache.json"))
KEEP_DAYS = 30
KEEP_MISS_DAYS = 7
MAX_PER_RUN = 250
FIELDS = ("Title", "Year", "Type", "Released", "Metascore", "imdbRating", "imdbVotes")

_cache = None
_limit_reached = False
asked = 0


def _load():
    global _cache
    if _cache is None:
        try:
            with open(CACHE, encoding="utf-8") as f:
                _cache = json.load(f)
        except (OSError, ValueError):
            _cache = {}
    return _cache


def save():
    """Writes what's kept, dropping answers too old to use."""
    cache = _load()
    today = date.today()
    fresh = {k: v for k, v in cache.items() if (today - date.fromisoformat(v["asked"])).days <= KEEP_DAYS}
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(fresh, f, ensure_ascii=False, indent=0, sort_keys=True)
        f.write("\n")


def _usable(entry):
    age = (date.today() - date.fromisoformat(entry["asked"])).days
    scored = entry.get("found") and (entry.get("Metascore") not in (None, "N/A") or entry.get("imdbRating") not in (None, "N/A"))
    return age <= (KEEP_DAYS if scored else KEEP_MISS_DAYS)


def lookup(params, key):
    """OMDb's answer for these parameters (i=… or t=…&type=…), from the cache when it's recent
    enough; None when OMDb doesn't know it, the request fails, or today's limit is reached."""
    global _limit_reached, asked
    cache = _load()
    name = "&".join(f"{k}={params[k]}" for k in sorted(params))
    entry = cache.get(name)
    if entry and _usable(entry):
        return entry if entry.get("found") else None
    if _limit_reached or asked >= MAX_PER_RUN:
        return entry if entry and entry.get("found") else None
    try:
        out = subprocess.run(
            # No -f: at its limit OMDb answers HTTP 401 with a JSON error that has to be read.
            ["curl", "-sS", "--max-time", "30", "-A", "Glider charts (github.com/RivaeStrategy/glider-download)",
             "https://www.omdbapi.com/?" + urllib.parse.urlencode({**params, "apikey": key})],
            check=True, capture_output=True,
        ).stdout
        d = json.loads(out)
    except (subprocess.CalledProcessError, ValueError):
        return None  # not kept: asked again next run
    asked += 1
    error = (d.get("Error") or "").lower()
    if d.get("Response") != "True" and ("limit" in error or "key" in error):
        # The day's limit, or a bad key: nothing more to learn this run, and nothing to keep.
        _limit_reached = True
        print(f"OMDb said \"{d.get('Error')}\"; using saved answers only for the rest of this run")
        return entry if entry and entry.get("found") else None
    found = d.get("Response") == "True"
    entry = {"asked": date.today().isoformat(), "found": found}
    if found:
        entry.update({f: d.get(f) for f in FIELDS})
    cache[name] = entry
    return entry if found else None
