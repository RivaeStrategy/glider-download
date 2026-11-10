#!/usr/bin/env python3
"""New and acclaimed: the best-reviewed movies and shows of the last 60 days, for top10.py.

Movies released in the US in the last 60 days, by their Metacritic score; new shows with a season
that started in the last 60 days, by their IMDb rating (OMDb has no Metacritic score for shows).
Both are out of 100 (IMDb's 8.4 counts as 84).

Where it comes from: films with a recent release date and an IMDb id from Wikidata (CC0), season
premieres from TVmaze's schedule (CC BY-SA), and the scores and US release dates from OMDb (the
key is the OMDB_KEY secret). One OMDb request per candidate, at most about 300 a week, inside the
key's 1,000 a day.

Run on its own (OMDB_KEY=... trending.py) it prints the list.
"""
import json
import os
import subprocess
import sys
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone

WINDOW_DAYS = 60
MOVIE_CANDIDATES = 160
SHOW_CANDIDATES = 140
MIN_IMDB_VOTES = 500
# Not what people mean by a new show.
SKIPPED_SHOW_TYPES = {"Talk Show", "Sports", "News", "Variety", "Award Show", "Game Show"}
AGENT = "Glider charts (github.com/RivaeStrategy/glider-download)"


def fetch(url, accept="application/json"):
    out = subprocess.run(
        ["curl", "-sSfL", "--retry", "2", "--retry-delay", "10", "--max-time", "120", "-A", AGENT, "-H", f"Accept: {accept}", url],
        check=True, capture_output=True,
    ).stdout
    return json.loads(out)


def omdb(imdb_id, key):
    d = fetch(f"https://www.omdbapi.com/?i={imdb_id}&apikey={key}")
    return d if d.get("Response") == "True" else None


def released(d):
    """OMDb's US release date ("03 Oct 2026"), or None."""
    try:
        return datetime.strptime(d.get("Released", ""), "%d %b %Y").date()
    except ValueError:
        return None


def number(text):
    try:
        return float(str(text).replace(",", ""))
    except ValueError:
        return None


def recent_films(since):
    """English-language films with a release date since [since] and an IMDb id, best known first."""
    query = f"""
    SELECT ?imdb (MAX(?links) AS ?n) WHERE {{
      ?film wdt:P31 wd:Q11424; wdt:P345 ?imdb; wdt:P577 ?date; wdt:P364 wd:Q1860; wikibase:sitelinks ?links.
      FILTER(?date >= "{since.isoformat()}T00:00:00Z"^^xsd:dateTime)
    }} GROUP BY ?imdb ORDER BY DESC(?n) LIMIT {MOVIE_CANDIDATES}"""
    d = fetch("https://query.wikidata.org/sparql?" + urllib.parse.urlencode({"query": query}), "application/sparql-results+json")
    return [b["imdb"]["value"] for b in d["results"]["bindings"]]


def season_premieres(since, today):
    """Shows with an episode 1 of some season since [since], by TVmaze's popularity weight."""
    shows = {}
    day = since
    while day <= today:
        for url in (f"https://api.tvmaze.com/schedule?country=US&date={day}", f"https://api.tvmaze.com/schedule/web?date={day}"):
            for e in fetch(url):
                show = e.get("show") or e.get("_embedded", {}).get("show") or {}
                imdb = (show.get("externals") or {}).get("imdb")
                if e.get("number") != 1 or not imdb or show.get("language") != "English" or show.get("type") in SKIPPED_SHOW_TYPES:
                    continue
                shows.setdefault(imdb, show.get("weight") or 0)
            time.sleep(0.6)  # TVmaze allows 20 requests in 10 seconds
        day += timedelta(days=1)
    return [imdb for imdb, _ in sorted(shows.items(), key=lambda kv: -kv[1])][:SHOW_CANDIDATES]


MIN_SCORE = 80


def recent_acclaimed(key):
    """The movies and shows of the last 60 days scoring MIN_SCORE or more, best first. Shows only
    when the show itself is new (this year or last): a new season of a 1980s show isn't."""
    today = datetime.now(timezone.utc).date()
    since = today - timedelta(days=WINDOW_DAYS)
    items = []

    # Wikidata's dates include festival showings, so look further back and let OMDb's US date decide.
    for imdb in recent_films(today - timedelta(days=2 * WINDOW_DAYS)):
        d = omdb(imdb, key)
        when = d and released(d)
        score = d and number(d.get("Metascore"))
        if not d or d.get("Type") != "movie" or not when or not since <= when <= today or score is None:
            continue
        items.append({"title": d["Title"], "year": d.get("Year", "")[:4] or None, "kind": "movie",
                      "score": int(score), "source": "Metacritic", "released": when.isoformat(), "imdb": imdb})

    for imdb in season_premieres(since, today):
        d = omdb(imdb, key)
        rating = d and number(d.get("imdbRating"))
        votes = d and number(d.get("imdbVotes"))
        first = number((d or {}).get("Year", "")[:4])
        if not d or d.get("Type") != "series" or rating is None or (votes or 0) < MIN_IMDB_VOTES or (first or 0) < today.year - 1:
            continue
        items.append({"title": d["Title"], "year": d.get("Year", "")[:4] or None, "kind": "series",
                      "score": round(rating * 10), "source": "IMDb", "imdb": imdb})

    items = [i for i in items if i["score"] >= MIN_SCORE]
    return sorted(items, key=lambda i: (-i["score"], i["title"]))


if __name__ == "__main__":
    found = recent_acclaimed(os.environ["OMDB_KEY"])
    print(json.dumps(found, ensure_ascii=False, indent=1))
