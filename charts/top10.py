#!/usr/bin/env python3
"""The Top 10 lists on Glider's Movies and Series pages and the Top 15 on Home, by points.

Popularity decides, critics break ties, and the apps skip whatever the playlist doesn't have:

- Chart points: #1 on a chart is 10 points, #10 is 1. Netflix's weekly Top 10 and Apple's
  iTunes chart for Canada count in full, Simkl's weekly trending (what its users watch,
  worldwide) at 80%, and Netflix and Apple for the US at 60%. A title on several charts adds
  them up.
- Critics bonus, up to 4 points: Metacritic for movies and IMDb for shows, both from OMDb (shows
  have no Metacritic score there), counted above 60 out of 100.
- New and acclaimed: titles from the last 60 days scored 80 or more (see trending.py) that are on
  no chart get the bonus alone, so they fill in after the chart titles.

Writes charts-ca.json: "movies" and "shows" (best first, the Top 10s) and "trending" (both kinds
mixed, Home's Top 30). Since November 19, 2026 (version 3) also:

- "netflix": Netflix's own Top 10s, movies and shows, Canada first, then the US, then the world
  (English, then non-English), each title once. The apps' Netflix row leads with them.
- "ranked": a few hundred movies and shows mixed, best first, that every poster row on Movies and
  Series leads with (the apps' TitleOrder). Every place on every free chart counts, not just the
  top 10: place n is worth 100 / (n + 9) points (10 for #1, 5.3 for #10, 1.7 for #50, 0.9 for
  #100), times the chart's weight, added up over the charts. The charts go deeper here: Apple's
  iTunes charts as far as the feed goes (about 75 movies, 100 seasons), Simkl's weekly trending in
  full (100) and its monthly at 40%, and Netflix's world lists at 60%. No OMDb request is made for
  this list: a critics bonus counts only where the score is already known (the Top 10s' scores,
  Simkl's IMDb rating for shows), and the new and acclaimed titles not on any chart come last.
  No paid chart service (Max, November 19, 2026).

Older builds read only movies, shows and trending, which stay as they were. Runs on Tuesdays and
Fridays in the public glider-download repo; the apps read it from raw.githubusercontent.com.
Stdlib and curl only. Usage: OMDB_KEY=... top10.py OUT.json

For a local test without any OMDb key: top10.py --no-omdb OUT.json (no critics bonus, no new and
acclaimed titles, the OMDb cache left alone). Never run it with the apps' key.

Exits without writing when a chart fails, so the apps keep the last good file.
"""
import csv
import io
import json
import os
import re
import subprocess
import sys
import unicodedata
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # trending.py sits next to this file
import omdb as omdb_api  # noqa: E402
import trending  # noqa: E402

NETFLIX = "https://www.netflix.com/tudum/top10/data/all-weeks-countries.tsv"
NETFLIX_WORLD = "https://www.netflix.com/tudum/top10/data/all-weeks-global.tsv"
APPLE = "https://itunes.apple.com/{country}/rss/{feed}/limit=100/json"
SIMKL = "https://data.simkl.in/discover/trending/{kind}/{period}_100.json"
PER_CHART = 10
WEIGHTS = {"Netflix CA": 1.0, "Apple TV CA": 1.0, "Simkl": 0.8, "Netflix US": 0.6, "Apple TV US": 0.6}
# The long list ("ranked") also counts these, which the Top 10s leave out.
DEEP_WEIGHTS = {**WEIGHTS, "Netflix World": 0.6, "Netflix World (non-English)": 0.6, "Simkl month": 0.4}
KEEP_PER_KIND = 30
KEEP_TRENDING = 60  # a playlist missing some still fills Home's 30
KEEP_RANKED = 450


def fetch(url):
    # curl, not urllib: Netflix's server drops the end of the 33 MB file over HTTP/1.1 (curl uses HTTP/2).
    return subprocess.run(
        ["curl", "-sSfL", "--retry", "2", "--retry-delay", "20", "--max-time", "300",
         "-A", "Glider charts (github.com/RivaeStrategy/glider-download)", url],
        check=True, capture_output=True,
    ).stdout


def key(title):
    """"Grey’s Anatomy" and "Grey's Anatomy" are the same title."""
    t = "".join(" " if unicodedata.category(c)[0] in "PS" else c for c in unicodedata.normalize("NFKD", title))
    t = t.encode("ascii", "ignore").decode().lower().replace("&", " and ")
    return " ".join(re.findall(r"[a-z0-9]+", t))


def netflix():
    """The newest week's Canada and US lists: (week, {chart: {"movie": [...], "series": [...]}})."""
    rows = [r for r in csv.DictReader(io.StringIO(fetch(NETFLIX).decode("utf-8")), delimiter="\t")
            if r["country_iso2"] in ("CA", "US")]
    week = max(r["week"] for r in rows)

    def ranked(country, category):
        picked = sorted((r for r in rows if r["week"] == week and r["country_iso2"] == country and r["category"] == category),
                        key=lambda r: int(r["weekly_rank"]))
        out, seen = [], set()
        for r in picked:  # a show with two seasons in the list counts once, at its best
            if key(r["show_title"]) not in seen:
                seen.add(key(r["show_title"]))
                out.append({"title": r["show_title"].strip(), "year": None})
        return out

    return week, {f"Netflix {c}": {"movie": ranked(c, "Films"), "series": ranked(c, "TV")} for c in ("CA", "US")}


def netflix_world():
    """The newest week's world lists: {chart: {"movie": [...], "series": [...]}}, English and not."""
    rows = list(csv.DictReader(io.StringIO(fetch(NETFLIX_WORLD).decode("utf-8")), delimiter="\t"))
    week = max(r["week"] for r in rows)

    def ranked(category):
        picked = sorted((r for r in rows if r["week"] == week and r["category"] == category), key=lambda r: int(r["weekly_rank"]))
        out, seen = [], set()
        for r in picked:
            if key(r["show_title"]) not in seen:
                seen.add(key(r["show_title"]))
                out.append({"title": r["show_title"].strip(), "year": None})
        return out

    return {
        "Netflix World": {"movie": ranked("Films (English)"), "series": ranked("TV (English)")},
        "Netflix World (non-English)": {"movie": ranked("Films (Non-English)"), "series": ranked("TV (Non-English)")},
    }


YEAR_IN_TITLE = re.compile(r"\s*\((\d{4})\)\s*$")
SEASON = re.compile(r",\s*(Season|Series|Saison)\s+\d+.*$", re.IGNORECASE)
BOX_SET = re.compile(r"[,:]\s*The Complete (Series|Collection)\s*$", re.IGNORECASE)


def apple(country, feed, shows):
    entries = json.loads(fetch(APPLE.format(country=country, feed=feed)))["feed"].get("entry", [])
    out, seen = [], set()
    for e in entries:
        name = e["im:name"]["label"].strip()
        # The Canadian store lists French editions too ("…, Saison 1"); the English one is there as well.
        if re.search(r",\s*Saison\s+\d", name, re.IGNORECASE):
            continue
        year = (e.get("im:releaseDate", {}).get("label") or "")[:4] or None
        if shows:
            name, year = BOX_SET.sub("", SEASON.sub("", name)).strip(), None  # a season's date isn't the show's year
        else:
            m = YEAR_IN_TITLE.search(name)
            if m:
                name, year = name[: m.start()].strip(), m.group(1)
        if key(name) in seen:
            continue
        seen.add(key(name))
        out.append({"title": name, "year": year})
    return out


def simkl(kind, period="week"):
    """Simkl's trending for the week (or month), in its order, with the IMDb rating it carries."""
    out = []
    for e in json.loads(fetch(SIMKL.format(kind="movies" if kind == "movie" else "tv", period=period))):
        when = e.get("release_date") or ""
        imdb = (e.get("ratings") or {}).get("imdb") or {}
        out.append({"title": e["title"], "year": when[-4:] if len(when) >= 4 else None,
                    "imdbRating": imdb.get("rating"), "imdbVotes": imdb.get("votes")})
    return out


def omdb_score(title, year, kind, omdb_key):
    """Metacritic for a movie, IMDb (out of 100) for a show, or None. Kept between runs (omdb.py).
    None without a key (a local test)."""
    if not omdb_key:
        return None
    q = {"t": title, "type": kind}
    if year:
        q["y"] = year
    d = omdb_api.lookup(q, omdb_key)
    if not d:
        return None
    raw = d.get("Metascore") if kind == "movie" else d.get("imdbRating")
    try:
        return round(float(raw)) if kind == "movie" else round(float(raw) * 10)
    except (TypeError, ValueError):
        return None


def bonus(score):
    return round(max(0.0, (score - 60) / 40) * 4, 2) if score else 0.0


def rank(charts, acclaimed, kind, omdb_key):
    """Every title of one kind with its points, best first."""
    titles = {}
    for chart, lists in charts.items():
        for i, item in enumerate(lists[kind][:PER_CHART]):
            t = titles.setdefault(key(item["title"]), {"title": item["title"], "year": item.get("year"), "kind": kind,
                                                        "chartPoints": 0.0, "charts": []})
            t["chartPoints"] += (PER_CHART + 1 - (i + 1)) * WEIGHTS[chart]
            t["charts"].append(f"{chart} #{i + 1}")
            t["year"] = t["year"] or item.get("year")
            # Simkl carries the IMDb rating, which is a show's score: no OMDb request for it.
            if kind == "series" and item.get("imdbRating") and (item.get("imdbVotes") or 0) >= trending.MIN_IMDB_VOTES:
                t.setdefault("score", round(item["imdbRating"] * 10))
    for item in acclaimed:
        if item["kind"] == kind and key(item["title"]) not in titles:
            titles[key(item["title"])] = {"title": item["title"], "year": item["year"], "kind": kind,
                                          "chartPoints": 0.0, "charts": [], "score": item["score"]}
    for t in titles.values():
        if "score" not in t:
            t["score"] = omdb_score(t["title"], t["year"], kind, omdb_key)
        t["points"] = round(t["chartPoints"] + bonus(t["score"]), 2)
    return sorted(titles.values(), key=lambda t: (-t["points"], t["title"]))


def deep_points(place):
    """What place n (1 is the top) on a chart is worth in the long list."""
    return 100 / (place + 9)


def ranked_list(charts, known, acclaimed):
    """Every title on any chart, at any depth, best first; then the acclaimed ones on none. [known]
    holds the scores the Top 10s already found ({(kind, key): score}): no new OMDb request."""
    titles = {}
    for chart, lists in charts.items():
        weight = DEEP_WEIGHTS[chart]
        for kind in ("movie", "series"):
            for i, item in enumerate(lists[kind]):
                k = (kind, key(item["title"]))
                if not k[1]:
                    continue
                t = titles.setdefault(k, {"title": item["title"], "year": item.get("year"), "kind": kind, "points": 0.0})
                t["points"] += deep_points(i + 1) * weight
                t["year"] = t["year"] or item.get("year")
                if kind == "series" and item.get("imdbRating") and (item.get("imdbVotes") or 0) >= trending.MIN_IMDB_VOTES:
                    t.setdefault("score", round(item["imdbRating"] * 10))
    for k, t in titles.items():
        t["points"] += bonus(t.get("score") or known.get(k))
    out = sorted(titles.values(), key=lambda t: (-t["points"], t["title"]))
    for item in acclaimed:
        if (item["kind"], key(item["title"])) not in titles:
            titles[(item["kind"], key(item["title"]))] = item
            out.append(item)
    return [{"title": t["title"], "year": t["year"], "kind": t["kind"]} for t in out[:KEEP_RANKED]]


def netflix_lists(charts):
    """Netflix's own lists, Canada, the US, then the world, each title once: {"movies": [...], "shows": [...]}."""
    order = ["Netflix CA", "Netflix US", "Netflix World", "Netflix World (non-English)"]
    out = {}
    for kind, name in (("movie", "movies"), ("series", "shows")):
        seen, items = set(), []
        for chart in order:
            for item in charts[chart][kind]:
                if key(item["title"]) not in seen:
                    seen.add(key(item["title"]))
                    items.append({"title": item["title"], "year": item.get("year")})
        out[name] = items
    return out


def entry(t):
    """What the file keeps per title. "source" and "rank" (its best chart) are for builds before
    November 10, which still print "#1 on Netflix" under the poster."""
    best = t["charts"][0] if t["charts"] else "Critics #1"
    source, _, place = best.rpartition(" #")
    return {"title": t["title"], "year": t["year"], "kind": t["kind"], "points": t["points"],
            "score": t["score"], "charts": t["charts"], "source": source.replace(" CA", ""), "rank": int(place)}


def main(path, use_omdb=True):
    omdb_key = os.environ.get("OMDB_KEY") if use_omdb else None
    if use_omdb and not omdb_key:
        sys.exit("OMDB_KEY is not set (for a local test without OMDb: top10.py --no-omdb OUT.json)")
    week, charts = netflix()
    for country in ("ca", "us"):
        charts[f"Apple TV {country.upper()}"] = {"movie": apple(country, "topmovies", False), "series": apple(country, "toptvseasons", True)}
    charts["Simkl"] = {"movie": simkl("movie"), "series": simkl("series")}
    empty = [f"{c} {k}" for c, lists in charts.items() for k, items in lists.items() if not items]
    if empty:
        sys.exit(f"empty chart: {', '.join(empty)}; keeping the last file")
    # The long list's extra charts are nice to have: the file still goes out without them.
    deep = dict(charts)
    try:
        deep.update(netflix_world())
    except Exception as e:
        print(f"Netflix world lists skipped: {e}")
    try:
        deep["Simkl month"] = {"movie": simkl("movie", "month"), "series": simkl("series", "month")}
    except Exception as e:
        print(f"Simkl month skipped: {e}")
    acclaimed = []
    if omdb_key:
        try:
            acclaimed = trending.recent_acclaimed(omdb_key)
        except Exception as e:  # the extra list is nice to have; the charts still go out without it
            print(f"new and acclaimed skipped: {e}")

    movies = rank(charts, acclaimed, "movie", omdb_key)
    shows = rank(charts, acclaimed, "series", omdb_key)
    both = sorted(movies + shows, key=lambda t: (-t["points"], t["title"]))
    known = {(t["kind"], key(t["title"])): t["score"] for t in movies + shows if t.get("score")}
    ranked = ranked_list(deep, known, acclaimed)
    netflix_own = netflix_lists({c: deep.get(c, {"movie": [], "series": []}) for c in
                                 ("Netflix CA", "Netflix US", "Netflix World", "Netflix World (non-English)")})
    out = {
        "version": 3,
        "country": "CA",
        "netflixWeek": week,
        "movies": [entry(t) for t in movies[:KEEP_PER_KIND]],
        "shows": [entry(t) for t in shows[:KEEP_PER_KIND]],
        "trending": [entry(t) for t in both[:KEEP_TRENDING]],
        "netflix": netflix_own,
        "ranked": ranked,
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
        f.write("\n")
    if omdb_key:
        omdb_api.save()
        print(f"OMDb: {omdb_api.asked} requests this run")
    print(f"week {week}: {len(out['movies'])} movies, {len(out['shows'])} shows, {len(out['trending'])} trending, "
          f"{len(ranked)} ranked, Netflix {len(netflix_own['movies'])} movies and {len(netflix_own['shows'])} shows")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--no-omdb"]
    main(args[0] if args else "charts-ca.json", use_omdb="--no-omdb" not in sys.argv[1:])
