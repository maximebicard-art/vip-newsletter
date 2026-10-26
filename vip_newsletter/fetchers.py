#!/usr/bin/env python3
"""
VIP Newsletter — Data Fetchers
v4.0: Extracted from monolithic v3.36.
Contains: EventsFetcher, TMDB, TVmaze, Music RSS, and real-data selectors.
v4.0.2: Music RSS drops aggregator "best of" curation posts via
MUSIC_EXCLUDE_SIGNALS (they kept leaking into the New Music section).
"""
import os
import re
import requests
import feedparser
from datetime import datetime, timedelta

try:
    from bs4 import BeautifulSoup
    BS4 = True
except ImportError:
    BS4 = False

from .config import (
    TMDB_API_KEY, TMDB_BASE, MUSIC_RSS_SOURCES, MUSIC_RELEASE_SIGNALS,
    MUSIC_EXCLUDE_SIGNALS, SEMI_STATIC_EVENTS, MAX_ITEMS, _BROWSER_UA,
)


# =============================================================================
# TMDB Films
# =============================================================================
def get_tmdb_films():
    if not TMDB_API_KEY:
        print("   ⚠️ TMDB_API_KEY not set — skipping film data")
        return []

    films = {}
    for endpoint in ["now_playing", "upcoming"]:
        try:
            resp = requests.get(
                f"{TMDB_BASE}/movie/{endpoint}",
                params={"api_key": TMDB_API_KEY, "language": "en-US", "region": "US", "page": 1},
                timeout=10,
            )
            if resp.status_code == 401:
                print("   ❌ TMDB API key invalid")
                return []
            if resp.status_code != 200:
                print(f"   ⚠️ TMDB {endpoint} returned {resp.status_code}")
                continue
            for movie in resp.json().get("results", []):
                title = movie.get("title", "")
                if not title:
                    continue
                if title not in films or endpoint == "now_playing":
                    films[title] = {
                        "title": title,
                        "release_date": movie.get("release_date", ""),
                        "popularity": movie.get("popularity", 0),
                        "vote_count": movie.get("vote_count", 0),
                        "tmdb_id": movie["id"],
                        "status": "Now Playing" if endpoint == "now_playing" else "Upcoming",
                        "cast": [],
                    }
        except Exception as e:
            print(f"   ⚠️ TMDB {endpoint} fetch error: {e}")

    if not films:
        return []

    sorted_films = sorted(films.values(), key=lambda x: x["popularity"], reverse=True)
    for film in sorted_films[:12]:
        try:
            resp = requests.get(
                f"{TMDB_BASE}/movie/{film['tmdb_id']}/credits",
                params={"api_key": TMDB_API_KEY, "language": "en-US"},
                timeout=10,
            )
            if resp.status_code == 200:
                cast = resp.json().get("cast", [])
                film["cast"] = [c["name"] for c in cast[:3]]
        except Exception:
            pass

    print(f"   🎬 TMDB: fetched {len(sorted_films)} films")
    return sorted_films


# =============================================================================
# TVmaze Premieres
# =============================================================================
def get_tvmaze_premieres(days_ahead=14):
    MAJOR_PLATFORMS = {
        "netflix", "hbo", "max", "hulu", "apple tv+", "apple tv",
        "amazon", "prime video", "disney+", "peacock", "paramount+",
        "fx", "starz", "showtime", "amc+",
    }

    premieres = []
    seen_shows = set()
    now = datetime.now()

    for i in range(days_ahead):
        date_str = (now + timedelta(days=i)).strftime("%Y-%m-%d")
        try:
            resp = requests.get(
                f"https://api.tvmaze.com/schedule/web?date={date_str}",
                timeout=8,
            )
            if resp.status_code == 429:
                print("   ⚠️ TVmaze rate limited — stopping early")
                break
            if resp.status_code != 200:
                continue

            for ep in resp.json():
                if ep.get("number") != 1:
                    continue
                show = ep.get("_embedded", {}).get("show") or ep.get("show") or {}
                show_name = show.get("name", "")
                if not show_name or show_name in seen_shows:
                    continue

                web_channel = show.get("webChannel") or {}
                network = show.get("network") or {}
                platform_name = web_channel.get("name", "") or network.get("name", "")
                platform_lower = platform_name.lower()

                if not any(p in platform_lower for p in MAJOR_PLATFORMS):
                    continue

                seen_shows.add(show_name)
                season_num = ep.get("season", 1)
                premieres.append({
                    "title": show_name,
                    "date": date_str,
                    "platform": platform_name,
                    "season": season_num,
                    "is_new_series": season_num == 1,
                    "genres": show.get("genres", []),
                    "rating": (show.get("rating") or {}).get("average") or 0,
                    "summary": re.sub(r"<[^>]+>", "", (show.get("summary") or ""))[:150],
                })
        except Exception as e:
            print(f"   ⚠️ TVmaze error for {date_str}: {e}")

    print(f"   📺 TVmaze: fetched {len(premieres)} TV premieres")
    return premieres


# =============================================================================
# Music RSS
# =============================================================================
def get_music_from_rss():
    now = datetime.now()
    cutoff = now - timedelta(days=14)
    items = []
    seen_titles = set()

    for source in MUSIC_RSS_SOURCES:
        try:
            feed = feedparser.parse(source["url"])
            for entry in feed.entries:
                title = entry.get("title", "")
                summary = entry.get("summary", entry.get("description", ""))
                text_lower = f"{title} {summary}".lower()

                if not any(sig in text_lower for sig in MUSIC_RELEASE_SIGNALS):
                    continue
                # v4.0.2: drop aggregator "best of / you should listen"
                # curation posts — they are not actual new releases.
                if any(sig in text_lower for sig in MUSIC_EXCLUDE_SIGNALS):
                    continue
                if title in seen_titles:
                    continue

                pub_date = None
                for field in ["published_parsed", "updated_parsed"]:
                    if hasattr(entry, field) and getattr(entry, field):
                        pub_date = datetime(*getattr(entry, field)[:6])
                        break

                if pub_date and pub_date < cutoff:
                    continue

                seen_titles.add(title)
                items.append({
                    "title": title,
                    "summary": re.sub(r"<[^>]+>", "", summary)[:200],
                    "link": entry.get("link", ""),
                    "source": source["source"],
                    "published": pub_date,
                })
        except Exception as e:
            print(f"   ⚠️ Music RSS {source['source']} error: {e}")

    print(f"   🎵 Music RSS: fetched {len(items)} release-signal items")
    return items


# =============================================================================
# Events Fetcher
# =============================================================================
class EventsFetcher:
    _MONTHS = [
        "january", "february", "march", "april", "may", "june",
        "july", "august", "september", "october", "november", "december",
    ]

    _AWARD_SHOWS = [
        ("tony awards",              "Tony Awards"),
        ("the grammy awards",        "Grammy Awards"),
        ("emmy awards",              "Emmy Awards"),
        ("golden globe awards",      "Golden Globe Awards"),
        ("bafta film awards",        "BAFTA Film Awards"),
        ("mtv video music awards",   "MTV VMAs"),
        ("american music awards",    "American Music Awards"),
        ("cfda fashion awards",      "CFDA Fashion Awards"),
        ("met gala",                 "Met Gala"),
    ]

    _WIKI_FESTIVALS = [
        ("2026 Cannes Film Festival",                    "Cannes Film Festival"),
        ("2026 Venice International Film Festival",      "Venice Film Festival"),
        ("2026 Toronto International Film Festival",     "Toronto International Film Festival (TIFF)"),
        ("2026 Tribeca Film Festival",                   "Tribeca Film Festival"),
        ("2026 Sundance Film Festival",                  "Sundance Film Festival"),
        ("Telluride Film Festival 2026",                 "Telluride Film Festival"),
        ("2026 Berlin International Film Festival",      "Berlinale"),
    ]

    _FASHION_BODIES = [
        {
            "url":      "https://milanofashionweek.cameramoda.it/en/",
            "name":     "Milan Men's Fashion Week",
            "source":   "Camera Nazionale",
            "keywords": ["fashion week", "june", "moda", "spring", "summer"],
        },
        {
            "url":      "https://www.fhcm.paris/en/paris-fashion-week/calendar",
            "name":     "Paris Men's Fashion Week",
            "source":   "FHCM",
            "keywords": ["men", "homme", "menswear", "june", "juin", "ss27"],
        },
        {
            "url":      "https://www.fhcm.paris/en/paris-fashion-week/calendar",
            "name":     "Paris Haute Couture Week",
            "source":   "FHCM",
            "keywords": ["haute couture", "couture", "july", "juillet"],
        },
        {
            "url":      "https://www.fhcm.paris/en/paris-fashion-week/calendar",
            "name":     "Paris Fashion Week",
            "source":   "FHCM",
            "keywords": ["women", "femme", "womenswear", "september", "septembre"],
        },
        {
            "url":      "https://londonfashionweek.co.uk/",
            "name":     "London Fashion Week",
            "source":   "BFC",
            "keywords": ["london fashion week", "september", "spring summer 2027", "ss27"],
        },
        {
            "url":      "https://cfda.com/fashion-calendar",
            "name":     "New York Fashion Week",
            "source":   "CFDA",
            "keywords": ["new york fashion week", "nyfw", "september"],
        },
    ]

    _UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

    def __init__(self, tmdb_api_key=""):
        self.now = datetime.now()
        self.cutoff = self.now - timedelta(days=3)
        self.lookahead = self.now + timedelta(days=270)
        self.tmdb_api_key = tmdb_api_key
        self._seen = set()

    def fetch_all(self):
        events = []
        award = self._fetch_award_shows()
        print(f"   🏆 TVmaze award shows: {len(award)}")
        events.extend(award)

        fashion = self._fetch_fashion_weeks()
        print(f"   👗 Fashion weeks: {len(fashion)}")
        events.extend(fashion)

        festivals = self._fetch_film_festivals_wiki()
        print(f"   🎥 Film festivals (Wikipedia): {len(festivals)}")
        events.extend(festivals)

        semi = self._fetch_semi_static()
        print(f"   📌 Semi-static annual events: {len(semi)}")
        events.extend(semi)

        if self.tmdb_api_key:
            premieres = self._fetch_tmdb_premieres()
            print(f"   🎬 TMDB film premieres: {len(premieres)}")
            events.extend(premieres)

        events.sort(key=lambda x: x["_sort_dt"])
        for e in events:
            e.pop("_sort_dt", None)

        print(f"   📅 Events total: {len(events)}")
        return events

    def _get(self, url, timeout=12):
        try:
            r = requests.get(url, timeout=timeout, headers={"User-Agent": self._UA})
            return r.text if r.status_code == 200 else None
        except Exception as e:
            print(f"      ⚠️ GET {url[:60]}: {e}")
            return None

    def _extract_dates(self, raw):
        text = re.sub(r"<[^>]+>", " ", raw).lower()
        text = re.sub(r"\s+", " ", text)

        month_re = (
            r"(?:january|february|march|april|may|june|july|august"
            r"|september|october|november|december)"
        )
        patterns = [
            rf"from\s+(\d{{1,2}})\s+to\s+\d{{1,2}}\s+({month_re})\s+(\d{{4}})",
            rf"(\d{{1,2}})\s*[-–]\s*\d{{1,2}}\s+({month_re})\s+(\d{{4}})",
            rf"({month_re})\s+(\d{{1,2}})\s*[-–]\s*\d{{1,2}},?\s+(\d{{4}})",
            rf"({month_re})\s+(\d{{1,2}})\s+(?:to|through)\s+\d{{1,2}},?\s+(\d{{4}})",
            rf"(\d{{1,2}})\s+({month_re})\s+(\d{{4}})",
            rf"({month_re})\s+(\d{{1,2}}),?\s+(\d{{4}})",
        ]

        found = []
        for pat in patterns:
            for m in re.finditer(pat, text):
                g = m.groups()
                try:
                    if g[0].isdigit():
                        day_s, month_s, year_s = g[0], g[1], g[2]
                    else:
                        month_s, day_s, year_s = g[0], g[1], g[2]
                    idx = self._MONTHS.index(month_s.strip()) + 1
                    found.append(datetime(int(year_s), idx, int(day_s)))
                except Exception:
                    continue

        return sorted({d for d in found if self.cutoff <= d <= self.lookahead})

    def _add(self, events, dt, name, source, link=""):
        if not (self.cutoff <= dt <= self.lookahead):
            return
        key = re.sub(r"\s+", " ", name.lower().strip())
        if key in self._seen:
            return
        self._seen.add(key)
        events.append({
            "date": dt.strftime("%b %d"),
            "name": name,
            "link": link,
            "source": source,
            "_sort_dt": dt,
        })

    def _fetch_award_shows(self):
        events = []
        for query, display in self._AWARD_SHOWS:
            try:
                r = requests.get(
                    "https://api.tvmaze.com/singlesearch/shows",
                    params={"q": query}, timeout=8,
                )
                if r.status_code != 200:
                    continue
                show_id = r.json().get("id")
                if not show_id:
                    continue

                r2 = requests.get(
                    f"https://api.tvmaze.com/shows/{show_id}",
                    params={"embed": "nextepisode"}, timeout=8,
                )
                if r2.status_code != 200:
                    continue

                data = r2.json()
                ep = (data.get("_embedded") or {}).get("nextepisode")
                if ep and ep.get("airdate"):
                    dt = datetime.strptime(ep["airdate"], "%Y-%m-%d")
                    site = data.get("officialSite") or data.get("url", "")
                    self._add(events, dt, display, "TVmaze", site)
            except Exception as e:
                print(f"      ⚠️ TVmaze '{display}': {e}")
        return events

    def _fetch_fashion_weeks(self):
        events = []
        fetched_urls = {}

        for src in self._FASHION_BODIES:
            url = src["url"]
            if url not in fetched_urls:
                html = self._get(url)
                if not html:
                    fetched_urls[url] = ""
                    continue
                if BS4:
                    try:
                        fetched_urls[url] = BeautifulSoup(html, "html.parser").get_text(" ")
                    except Exception:
                        fetched_urls[url] = html
                else:
                    fetched_urls[url] = html

            text = fetched_urls.get(url, "")
            if not text:
                continue

            text_lower = text.lower()
            dates_found = set()

            for keyword in src["keywords"]:
                pos = 0
                hit = 0
                while hit < 4:
                    pos = text_lower.find(keyword, pos)
                    if pos == -1:
                        break
                    snippet = text[max(0, pos - 80): min(len(text), pos + 300)]
                    for dt in self._extract_dates(snippet):
                        dates_found.add(dt)
                    pos += len(keyword)
                    hit += 1

            for dt in sorted(dates_found):
                self._add(events, dt, src["name"], src["source"], url)
                break

        return events

    def _fetch_film_festivals_wiki(self):
        events = []
        year = str(self.now.year)

        for search_q, display_name in self._WIKI_FESTIVALS:
            try:
                r = requests.get(
                    "https://en.wikipedia.org/w/api.php",
                    params={
                        "action": "query", "list": "search",
                        "srsearch": search_q, "format": "json", "srlimit": 5,
                    },
                    timeout=10,
                )
                if r.status_code != 200:
                    continue
                results = r.json().get("query", {}).get("search", [])
                if not results:
                    continue
                best = next(
                    (res for res in results if year in res.get("title", "")),
                    results[0],
                )
                r2 = requests.get(
                    "https://en.wikipedia.org/w/api.php",
                    params={
                        "action": "query", "titles": best["title"],
                        "prop": "extracts", "exintro": True,
                        "explaintext": True, "format": "json",
                    },
                    timeout=10,
                )
                if r2.status_code != 200:
                    continue
                pages = r2.json().get("query", {}).get("pages", {})
                for page in pages.values():
                    extract = page.get("extract", "")
                    if not extract:
                        continue
                    dates = self._extract_dates(extract)
                    for dt in dates:
                        self._add(events, dt, display_name, "Wikipedia")
                        break
                    break
            except Exception as e:
                print(f"      ⚠️ Wikipedia '{display_name}': {e}")
        return events

    def _fetch_semi_static(self):
        events = []
        for name, date_str in SEMI_STATIC_EVENTS.items():
            if not date_str:
                continue
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d")
                self._add(events, dt, name, "Annual Calendar")
            except Exception:
                continue
        return events

    def _fetch_tmdb_premieres(self):
        events = []
        films = get_tmdb_films()
        for film in films:
            if film["status"] != "Upcoming" or film["popularity"] < 100:
                continue
            try:
                dt = datetime.strptime(film["release_date"], "%Y-%m-%d")
            except Exception:
                continue
            self._add(events, dt, f"{film['title']} (Film Premiere)", "TMDB")
        return events


def _sort_events_chronologically(events):
    now = datetime.now()
    def parse_date(e):
        date_str = e.get("date", "")
        for year in [now.year, now.year + 1]:
            try:
                dt = datetime.strptime(f"{date_str} {year}", "%b %d %Y")
                if dt >= now - timedelta(days=3):
                    return dt
            except Exception:
                continue
        return datetime(2099, 1, 1)
    return sorted(events, key=parse_date)


def get_curated_events(gemini_ranker=None):
    from .ranking import _gemini_select
    fetcher = EventsFetcher(tmdb_api_key=TMDB_API_KEY)
    all_events = fetcher.fetch_all()

    if not all_events:
        print("   ⚠️ No events found from any live source")
        return []

    if gemini_ranker and gemini_ranker.is_available() and len(all_events) > MAX_ITEMS["events"]:
        item_lines = [
            f"[{i}] {e['name']} | {e['date']} | Source: {e['source']}"
            for i, e in enumerate(all_events[:40])
        ]
        priority = """PRIORITIZE:
1. Fashion weeks (Paris Men's, Paris Women's, Paris Haute Couture, Milan, NY, London, Copenhagen)
2. Major award shows (Oscars, Emmys, Grammys, Tonys, BAFTAs, CFDAs, VMAs, Golden Globes)
3. Prestige film festivals (Cannes, Venice, TIFF, Tribeca, Sundance, Telluride)
4. Major luxury industry galas (Met Gala, LACMA Art+Film, British Fashion Awards)
5. Blockbuster film premieres (very high-profile only)
SKIP: duplicates, minor events, any event appearing twice for the same occasion"""

        selected = _gemini_select(
            gemini_ranker, item_lines, "industry events",
            MAX_ITEMS["events"], priority, all_events,
        )
    else:
        selected = all_events[:MAX_ITEMS["events"]]

    return _sort_events_chronologically(selected)
