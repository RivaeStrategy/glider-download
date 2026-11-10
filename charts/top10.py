#!/usr/bin/env python3
"""Top 10 in Canada for Glider's Home: Netflix's weekly Top 10 and Apple's iTunes store charts,
zipped together (Netflix #1, Apple #1, Netflix #2, Apple #2, ...), one list for movies and one
for shows. A title on both lists shows once, where it comes first.

Runs in the public glider-download repo (GitHub Actions, free there) and writes charts-ca.json,
which the apps read from raw.githubusercontent.com. Stdlib and curl only. Usage: top10.py OUT.json

Exits without writing when a source fails, so the apps keep the last good file.
"""
import csv
import io
import json
import re
import sys
import subprocess
import unicodedata
from datetime import datetime, timezone

NETFLIX = "https://www.netflix.com/tudum/top10/data/all-weeks-countries.tsv"
APPLE = "https://itunes.apple.com/ca/rss/{feed}/limit=25/json"
COUNTRY = "Canada"
PER_SOURCE = 10


def fetch(url):
    # curl, not urllib: Netflix's server drops the end of the 33 MB file over HTTP/1.1 (curl uses HTTP/2).
    return subprocess.run(
        ["curl", "-sSfL", "--retry", "2", "--retry-delay", "20", "--max-time", "300",
         "-A", "Glider charts (github.com/RivaeStrategy/glider-download)", url],
        check=True, capture_output=True,
    ).stdout


def key(title):
    """"Grey’s Anatomy" and "Grey's Anatomy" are the same title."""
    t = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower().replace("&", " and ")
    return " ".join(re.findall(r"[a-z0-9]+", t))


def netflix():
    """The newest week's Canada lists, rank order: (week, movies, shows)."""
    rows = [r for r in csv.DictReader(io.StringIO(fetch(NETFLIX).decode("utf-8")), delimiter="\t")
            if r["country_name"] == COUNTRY]
    week = max(r["week"] for r in rows)

    def ranked(category):
        picked = sorted((r for r in rows if r["week"] == week and r["category"] == category), key=lambda r: int(r["weekly_rank"]))
        return [{"title": r["show_title"].strip(), "year": None} for r in picked]

    return week, ranked("Films"), ranked("TV")


YEAR_IN_TITLE = re.compile(r"\s*\((\d{4})\)\s*$")
SEASON = re.compile(r",\s*(Season|Series|Saison)\s+\d+.*$", re.IGNORECASE)


def apple(feed, shows):
    entries = json.loads(fetch(APPLE.format(feed=feed)))["feed"].get("entry", [])
    out, seen = [], set()
    for e in entries:
        name = e["im:name"]["label"].strip()
        # The Canadian store lists French editions too ("…, Saison 1"); the English one is there as well.
        if re.search(r",\s*Saison\s+\d", name, re.IGNORECASE):
            continue
        year = (e.get("im:releaseDate", {}).get("label") or "")[:4] or None
        if shows:
            name, year = SEASON.sub("", name).strip(), None  # a season's date isn't the show's year
        else:
            m = YEAR_IN_TITLE.search(name)
            if m:
                name, year = name[: m.start()].strip(), m.group(1)
        if key(name) in seen:
            continue
        seen.add(key(name))
        out.append({"title": name, "year": year})
    return out


def zip_lists(first, second):
    """Netflix #1, Apple #1, Netflix #2, ...; a title already in the list is skipped."""
    out, seen = [], set()
    for i in range(PER_SOURCE):
        for source, items in (("Netflix", first), ("Apple TV", second)):
            if i < len(items) and key(items[i]["title"]) not in seen:
                seen.add(key(items[i]["title"]))
                out.append({**items[i], "source": source, "rank": i + 1})
    return out


def main(path):
    week, nf_movies, nf_shows = netflix()
    ap_movies, ap_shows = apple("topmovies", shows=False), apple("toptvseasons", shows=True)
    if not (nf_movies and nf_shows and ap_movies and ap_shows):
        sys.exit("a list came back empty; keeping the last file")
    charts = {
        "version": 1,
        "country": "CA",
        "netflixWeek": week,
        "movies": zip_lists(nf_movies, ap_movies),
        "shows": zip_lists(nf_shows, ap_shows),
    }
    try:
        with open(path, encoding="utf-8") as f:
            old = json.load(f)
        old.pop("updated", None)
        if old == charts:
            print("no change")
            return
    except (OSError, ValueError):
        pass
    charts["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(charts, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"week {week}: {len(charts['movies'])} movies, {len(charts['shows'])} shows")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "charts-ca.json")
