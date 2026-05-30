#!/usr/bin/env python3
"""
VIP & Industry News - AUTOMATED NEWSLETTER SYSTEM v3.8
=======================================================
CHANGES FROM v3.7:

EVENTS — FULLY AUTOMATED (no more hardcoded dates)
  EventsFetcher class queries live authoritative sources every run:
  - TVmaze API: award show broadcast dates (Tonys, Emmys, Grammys, VMAs, Met Gala…)
  - Camera Nazionale della Moda Italiana: official Milan FW dates
  - FHCM: official Paris FW + Haute Couture dates
  - Pitti Immagine: Florence/Pitti Uomo dates
  - BFC: London Fashion Week dates
  - CFDA: New York Fashion Week dates
  - FashionUnited events calendar: broader fashion/industry events
  - Film festival official sites: Cannes, Venice, TIFF, Tribeca, Sundance, Telluride
  - TMDB: major upcoming film premieres (popularity ≥ 100)
  Gemini ranks the merged result. All failures are silent with logging.

AMBASSADORS — IMPROVED SOURCE COVERAGE
  Added Hypebeast and Hypebae RSS feeds: both publish ambassador news
  same-day without paywalls. Fixes the Jisoo/Salomon-class misses.

INDUSTRY NEWS — TIGHTER FILTERING
  New weak signals block: collection reviews, runway coverage, red carpet
  attendance, retail logistics, and recurring data trackers/roundups.

PARTNERSHIPS — "x See article" BUG FIXED
  If _extract_partnership() fails, item is silently discarded.
  The broken "x See article" fallback is removed entirely.

CASTINGS — INTERVIEW BLEED FIXED
  New exclude signals catch interview/profile articles that were being
  misclassified as casting news (David Tennant interview, Michelle
  Pfeiffer profile, Rafael Nadal docuseries profile, etc.).

SETUP REQUIREMENTS (unchanged):
- TMDB_API_KEY secret (free at themoviedb.org)
- All existing secrets (GEMINI_API_KEY, BREVO_API_KEY, etc.) unchanged
- New dependency: beautifulsoup4 (added to requirements.txt)
"""

import os
import sys
import json
import re
import feedparser
from datetime import datetime, timedelta
from pathlib import Path

try:
    from bs4 import BeautifulSoup
    BS4 = True
except ImportError:
    BS4 = False
    print("Warning: beautifulsoup4 not installed. Event scraping will use regex fallback.")
    print("Install with: pip install beautifulsoup4")

try:
    import requests
except ImportError:
    print("pip install requests")
    sys.exit(1)

try:
    from jinja2 import Template
    JINJA = True
except ImportError:
    JINJA = False
    print("Warning: jinja2 not installed. Using basic replacement.")

# =============================================================================
# CONFIGURATION
# =============================================================================

RECIPIENT_EMAIL = os.getenv("NEWSLETTER_RECIPIENT", "your-email@tutanota.com")

EMAIL_CONFIG = {
    "provider": os.getenv("EMAIL_PROVIDER", "brevo"),
    "brevo_api_key": os.getenv("BREVO_API_KEY", ""),
    "sendpulse_api_key": os.getenv("SENDPULSE_API_KEY", ""),
    "sendpulse_api_secret": os.getenv("SENDPULSE_API_SECRET", ""),
    "mailjet_api_key": os.getenv("MAILJET_API_KEY", ""),
    "mailjet_api_secret": os.getenv("MAILJET_API_SECRET", ""),
    "sendgrid_api_key": os.getenv("SENDGRID_API_KEY", ""),
    "smtp_server": os.getenv("SMTP_SERVER", "smtp.gmail.com"),
    "smtp_port": int(os.getenv("SMTP_PORT", "587")),
    "smtp_username": os.getenv("SMTP_USERNAME", ""),
    "smtp_password": os.getenv("SMTP_PASSWORD", ""),
    "from_name": os.getenv("FROM_NAME", "VIP & Industry News"),
    "from_email": os.getenv("FROM_EMAIL", "newsletter@yourcompany.com"),
}

# =============================================================================
# VOLUME LIMITS
# =============================================================================
MAX_ITEMS = {
    "events": 15,
    "industryNews": 12,
    "ambassadors": 15,
    "partnerships": 12,
    "castings": 10,
    "filmProjects": 10,
    "tvProjects": 10,
    "inTheaters": 8,    # RE-ENABLED: Real TMDB data (requires TMDB_API_KEY secret)
    "newMusic": 8,      # RE-ENABLED: Real RSS data (no key needed)
    "tvPremieres": 6,   # NEW: Real TVmaze data (no key needed)
}

# =============================================================================
# TMDB CONFIGURATION (free API key at themoviedb.org)
# =============================================================================
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")
TMDB_BASE = "https://api.themoviedb.org/3"

# =============================================================================
# MUSIC RSS SOURCES
# =============================================================================
MUSIC_RSS_SOURCES = [
    {"url": "https://pitchfork.com/rss/news/", "source": "Pitchfork"},
    {"url": "https://www.rollingstone.com/music/music-news/feed/", "source": "Rolling Stone"},
    {"url": "https://www.billboard.com/feed/", "source": "Billboard"},
    {"url": "https://www.nme.com/news/music/feed", "source": "NME"},
    {"url": "https://consequence.net/feed/", "source": "Consequence"},
]

MUSIC_RELEASE_SIGNALS = [
    "new album", "debut album", "releases album", "album out",
    "drops new", "new single", "drops single", "out now",
    "release date", "album drops", "announces album", "new ep",
    "world tour", "arena tour", "stadium tour", "concert tour",
    "announces tour", "tour dates", "residency",
    "new music", "music video", "drops video",
]

# =============================================================================
# GEMINI AI RANKING & CURATION
# =============================================================================

class GeminiRanker:
    def __init__(self, api_key=None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        self.base_url = "https://generativelanguage.googleapis.com/v1beta/models"
        self.model = "gemini-2.5-flash"

    def is_available(self):
        return bool(self.api_key)

    def rank(self, items, category, top_n=10):
        if not self.api_key:
            print(f"   ⚠️ No GEMINI_API_KEY set, using default ordering for {category}")
            return items[:top_n]

        if len(items) <= top_n:
            return items

        prompt = self._build_prompt(items, category, top_n)

        try:
            response = requests.post(
                f"{self.base_url}/{self.model}:generateContent?key={self.api_key}",
                headers={"Content-Type": "application/json"},
                json={
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.1,
                        "responseMimeType": "application/json"
                    }
                },
                timeout=60
            )

            if response.status_code != 200:
                print(f"   ⚠️ Gemini API error {response.status_code}: {response.text[:200]}")
                return items[:top_n]

            result = response.json()

            if 'candidates' not in result or not result['candidates']:
                print(f"   ⚠️ Gemini returned no candidates for {category}")
                return items[:top_n]

            content = result['candidates'][0]['content']['parts'][0]['text']
            parsed = json.loads(content)
            selected_indices = parsed.get('selected', list(range(top_n)))

            valid_indices = [i for i in selected_indices if isinstance(i, int) and 0 <= i < len(items)]

            if not valid_indices:
                print(f"   ⚠️ Gemini returned invalid indices for {category}, using defaults")
                return items[:top_n]

            ranked = [items[i] for i in valid_indices]
            print(f"   🤖 Gemini ranked {category}: kept {len(ranked)}/{len(items)}")
            return ranked[:top_n]

        except json.JSONDecodeError as e:
            print(f"   ⚠️ Gemini returned invalid JSON for {category}: {e}")
            return items[:top_n]
        except Exception as e:
            print(f"   ⚠️ Gemini ranking failed for {category}: {e}")
            return items[:top_n]

    def _build_prompt(self, items, category, top_n):
        category_labels = {
            "events": "Key Award Shows & Events",
            "industryNews": "Industry News",
            "ambassadors": "Ambassador Announcements & Campaign Stars",
            "partnerships": "Brand Partnerships",
            "castings": "Ones to Watch / Key Castings",
            "filmProjects": "Film Projects",
            "tvProjects": "TV Projects",
        }

        label = category_labels.get(category, category)

        prompt = """You are an expert curator for a VIP luxury fashion and entertainment industry newsletter.

Your task: From the articles below, select the """ + str(top_n) + """ MOST relevant and newsworthy items for the section """ + label + """.

SELECTION CRITERIA (in order of priority):
1. **Major luxury brands**: LVMH, Kering, Richemont, Hermès, Chanel, Dior, Gucci, Prada, Louis Vuitton, Cartier, Tiffany, Bulgari, L\'Oréal, Estée Lauder, etc.
2. **A-list talent**: Globally recognized actors, musicians, models, directors
3. **Confirmed deals/announcements**: Avoid rumors, speculation, "might", "could", "reportedly"
4. **Industry significance**: M&A, leadership changes, major strategic moves
5. **Global impact**: Events/news with worldwide relevance, not local/niche

IGNORE:
- Rumors, speculation, unconfirmed reports
- Minor local events or regional news
- Repetitive announcements (same talent/brand multiple times)
- Generic "best dressed" or "red carpet looks" articles
- Articles lacking clear industry significance
- Interviews and profiles (unless major breaking news)
- Product reviews and recaps

INSTRUCTIONS:
- Return ONLY a valid JSON object with this exact format: {"selected": [index1, index2, ...]}
- Indices are 0-based and must be from the provided list
- Order matters: most important first
- Select exactly """ + str(top_n) + """ items (or fewer if list is shorter)

ARTICLES:
"""
        for i, item in enumerate(items):
            title = item.get('headline') or item.get('name') or item.get('talent', '') or item.get('partner1', '')
            source = item.get('source', 'Unknown')
            prompt += "\n[" + str(i) + "] " + title + " (Source: " + source + ")"

        prompt += "\n\nReturn JSON: {\"selected\": [...]}"
        return prompt


# =============================================================================
# REAL DATA FETCHERS
# =============================================================================

def get_tmdb_films():
    """Fetch real film data from TMDB API.
    Returns now_playing + upcoming films with cast, sorted by popularity.
    Requires TMDB_API_KEY environment variable (free at themoviedb.org).
    """
    if not TMDB_API_KEY:
        print("   ⚠️ TMDB_API_KEY not set — skipping film data (set it in GitHub secrets)")
        return []

    films = {}  # title → data, for deduplication across endpoints

    for endpoint in ["now_playing", "upcoming"]:
        try:
            resp = requests.get(
                f"{TMDB_BASE}/movie/{endpoint}",
                params={"api_key": TMDB_API_KEY, "language": "en-US", "region": "US", "page": 1},
                timeout=10,
            )
            if resp.status_code == 401:
                print("   ❌ TMDB API key invalid — check TMDB_API_KEY secret")
                return []
            if resp.status_code != 200:
                print(f"   ⚠️ TMDB {endpoint} returned {resp.status_code}")
                continue

            for movie in resp.json().get("results", []):
                title = movie.get("title", "")
                if not title:
                    continue
                # now_playing takes priority over upcoming for same film
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

    # Sort by popularity; fetch cast for top 12 (to keep API calls reasonable)
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
            pass  # Cast is nice-to-have; don't fail the whole fetch

    print(f"   🎬 TMDB: fetched {len(sorted_films)} films ({sum(1 for f in sorted_films if f['status'] == 'Now Playing')} playing, {sum(1 for f in sorted_films if f['status'] == 'Upcoming')} upcoming)")
    return sorted_films


def get_tvmaze_premieres(days_ahead=14):
    """Fetch upcoming TV/streaming premieres from TVmaze (free, no API key required).
    Returns season premieres and new series from major streaming platforms.
    """
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
                # Only first episodes of a season (season premieres or new series)
                if ep.get("number") != 1:
                    continue

                show = ep.get("_embedded", {}).get("show") or ep.get("show") or {}
                show_name = show.get("name", "")
                if not show_name or show_name in seen_shows:
                    continue

                # Identify platform
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

    print(f"   📺 TVmaze: fetched {len(premieres)} TV premieres across next {days_ahead} days")
    return premieres


def get_music_from_rss():
    """Fetch music release news from established music publications via RSS.
    Filters for release-signal keywords. No API key needed.
    """
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

    print(f"   🎵 Music RSS: fetched {len(items)} release-signal items across {len(MUSIC_RSS_SOURCES)} sources")
    return items


# =============================================================================
# GEMINI SELECTORS (rank REAL data — never generate)
# =============================================================================

def _gemini_select(gemini_ranker, item_lines, category_context, top_n, priority_instructions, fallback_list):
    """Generic helper: send a real item list to Gemini for selection/ranking.
    Returns selected items from fallback_list by index. Falls back gracefully.
    """
    if not gemini_ranker or not gemini_ranker.is_available():
        return fallback_list[:top_n]

    prompt = f"""You are a curator for a VIP luxury entertainment newsletter read by fashion, film, and entertainment executives.

From the CONFIRMED, REAL {category_context} below (live data from trusted APIs), select the {top_n} most relevant for our VIP audience.

{priority_instructions}

RETURN ONLY: JSON object {{"selected": [0, 3, 5, ...]}} — 0-based indices, ordered by relevance. No other text.

{category_context.upper()} (all real, confirmed data):
{chr(10).join(item_lines)}

JSON:"""

    try:
        response = requests.post(
            f"{gemini_ranker.base_url}/{gemini_ranker.model}:generateContent?key={gemini_ranker.api_key}",
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
            },
            timeout=30,
        )
        if response.status_code != 200:
            print(f"   ⚠️ Gemini {category_context} selection error {response.status_code}")
            return fallback_list[:top_n]

        content = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(content)
        indices = [i for i in parsed.get("selected", []) if isinstance(i, int) and 0 <= i < len(fallback_list)]
        selected = [fallback_list[i] for i in indices[:top_n]]
        print(f"   🤖 Gemini selected {len(selected)}/{len(fallback_list)} {category_context} items")
        return selected

    except json.JSONDecodeError as e:
        print(f"   ⚠️ Gemini {category_context} returned invalid JSON: {e}")
        return fallback_list[:top_n]
    except Exception as e:
        print(f"   ⚠️ Gemini {category_context} selection failed: {e}")
        return fallback_list[:top_n]


def get_in_theaters(gemini_ranker=None):
    """Fetch real films from TMDB, then use Gemini to select most VIP-relevant.
    Gemini only ranks — all film data is real, sourced from TMDB's live database.
    """
    max_items = MAX_ITEMS["inTheaters"]
    films = get_tmdb_films()

    if not films:
        return []

    # Build item lines for Gemini (real data only — no invented content)
    item_lines = []
    for i, f in enumerate(films[:25]):
        cast_str = ", ".join(f["cast"][:3]) if f["cast"] else "Cast TBA"
        release = f["release_date"]
        try:
            release = datetime.strptime(release, "%Y-%m-%d").strftime("%b %d")
        except Exception:
            pass
        item_lines.append(
            f"[{i}] {f['title']} | {f['status']} (Release: {release}) | "
            f"Starring: {cast_str} | TMDB Popularity: {f['popularity']:.0f}"
        )

    priority = """PRIORITIZE:
1. Major studio blockbusters with A-list talent (Marvel, DC, prestige drama, auteur films)
2. Films with fashion/luxury/cultural relevance to our VIP audience
3. Wide releases over limited releases
4. Higher TMDB popularity score is a useful signal but not the only criterion"""

    selected_films = _gemini_select(
        gemini_ranker, item_lines, "films", max_items, priority,
        sorted(films, key=lambda x: x["popularity"], reverse=True)
    )

    # Format for newsletter template
    result = []
    for f in selected_films:
        cast_str = ", ".join(f["cast"][:2]) if f["cast"] else ""
        release = f["release_date"]
        try:
            release = datetime.strptime(release, "%Y-%m-%d").strftime("%b %d")
        except Exception:
            pass

        if f["status"] == "Now Playing":
            detail = f"{cast_str} (Now Playing)" if cast_str else "Now Playing"
        else:
            detail = f"{cast_str} — Opening {release}" if cast_str else f"Opening {release}"

        result.append({"talent": f["title"], "project": detail, "link": "", "source": "TMDB"})

    return result


def get_new_music(gemini_ranker=None):
    """Fetch real music release news from RSS, then use Gemini to select most VIP-relevant.
    Gemini only ranks — all data is real headlines from Pitchfork, Billboard, etc.
    """
    max_items = MAX_ITEMS["newMusic"]
    music_items = get_music_from_rss()

    if not music_items:
        return []

    item_lines = []
    for i, m in enumerate(music_items[:30]):
        pub = m["published"].strftime("%b %d") if m.get("published") else "Recent"
        item_lines.append(f"[{i}] {m['title']} (Source: {m['source']}, Published: {pub})")

    priority = """PRIORITIZE:
1. Album/EP releases from globally recognized artists (A-list only)
2. Major single drops from chart-topping artists
3. Stadium/arena tour announcements
4. Music-fashion crossover news (artist x luxury brand, etc.)
SKIP: niche or indie artists, regional news, retrospectives, opinion pieces"""

    selected = _gemini_select(
        gemini_ranker, item_lines, "music releases", max_items, priority, music_items
    )

    return [
        {"talent": m["title"], "project": m["summary"], "link": m["link"], "source": m["source"]}
        for m in selected
    ]


def get_tv_premieres(gemini_ranker=None):
    """Fetch real TV premiere data from TVmaze, then use Gemini to select most VIP-relevant.
    TVmaze is free and requires no API key. Gemini only ranks real schedule data.
    """
    max_items = MAX_ITEMS["tvPremieres"]
    premieres = get_tvmaze_premieres(days_ahead=14)

    if not premieres:
        return []

    item_lines = []
    for i, p in enumerate(premieres[:30]):
        try:
            date_str = datetime.strptime(p["date"], "%Y-%m-%d").strftime("%b %d")
        except Exception:
            date_str = p["date"]
        season_label = "Series Premiere" if p["is_new_series"] else f"Season {p['season']} Premiere"
        genres = ", ".join(p["genres"][:2]) if p["genres"] else "Drama"
        item_lines.append(
            f"[{i}] {p['title']} | {season_label} on {p['platform']} | "
            f"{date_str} | Genres: {genres} | TVmaze Rating: {p['rating']}"
        )

    priority = """PRIORITIZE:
1. Prestige drama, limited series, or high-profile documentary
2. Shows with A-list creators, directors, or cast
3. New series premieres over returning seasons (unless it's a major cultural phenomenon)
4. HBO/Max, Netflix, Apple TV+, Amazon over smaller platforms
SKIP: reality TV, competition shows, daytime programming"""

    fallback = sorted(premieres, key=lambda x: x["rating"], reverse=True)
    selected = _gemini_select(
        gemini_ranker, item_lines, "TV premieres", max_items, priority, fallback
    )

    result = []
    for p in selected:
        try:
            date_str = datetime.strptime(p["date"], "%Y-%m-%d").strftime("%b %d")
        except Exception:
            date_str = p["date"]
        season_label = "Series Premiere" if p["is_new_series"] else f"Season {p['season']} Premiere"
        result.append({
            "talent": p["title"],
            "project": f"{season_label} — {p['platform']}, {date_str}",
            "link": "",
            "source": "TVmaze",
        })

    return result


# =============================================================================
# AUTOMATED EVENTS FETCHER — v3.8
# No hardcoded dates. Every date comes from a live authoritative source.
# All sources fail silently — a failed scrape never crashes the newsletter.
# =============================================================================

class EventsFetcher:
    """
    Queries multiple live authoritative sources for upcoming industry events.

    Sources (in priority order):
      1. TVmaze API           — award show broadcast dates
      2. Official fashion bodies (Camera Nazionale, FHCM, BFC, CFDA, Pitti)
      3. FashionUnited events calendar
      4. Film festival official sites
      5. TMDB upcoming film premieres

    Gemini ranks the merged result. Falls back to chronological order if
    Gemini is unavailable.
    """

    _MONTHS = [
        "january", "february", "march", "april", "may", "june",
        "july", "august", "september", "october", "november", "december",
    ]

    # Award shows → TVmaze search query + display name
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
        ("lacma art film gala",      "LACMA Art+Film Gala"),
        ("british fashion awards",   "British Fashion Awards"),
    ]

    # Official fashion body calendar URLs
    _FASHION_BODIES = [
        {
            "url":    "https://milanofashionweek.cameramoda.it/en/",
            "name":   "Milan Men's Fashion Week",
            "source": "Camera Nazionale",
        },
        {
            "url":    "https://www.fhcm.paris/en/paris-fashion-week/calendar",
            "name":   "Paris Fashion Week",
            "source": "FHCM",
        },
        {
            "url":    "https://www.pittimmagine.com/en/corporate/fairs/uomo.html",
            "name":   "Pitti Uomo",
            "source": "Pitti Immagine",
        },
        {
            "url":    "https://londonfashionweek.co.uk/",
            "name":   "London Fashion Week",
            "source": "BFC",
        },
        {
            "url":    "https://cfda.com/fashion-calendar",
            "name":   "New York Fashion Week",
            "source": "CFDA",
        },
    ]

    # FashionUnited events page + keyword→display-name map
    _FU_URL = "https://fashionunited.com/events/fashion-week"
    _FU_EVENTS = [
        ("new york fashion week",  "New York Fashion Week"),
        ("london fashion week",    "London Fashion Week"),
        ("milan fashion week",     "Milan Fashion Week"),
        ("paris fashion week",     "Paris Fashion Week"),
        ("haute couture",          "Paris Haute Couture Week"),
        ("paris men",              "Paris Men's Fashion Week"),
        ("milan men",              "Milan Men's Fashion Week"),
        ("pitti uomo",             "Pitti Uomo"),
    ]

    # Film festival official homepages
    _FILM_FESTIVALS = [
        ("https://www.festival-cannes.com/en/",       "Cannes Film Festival"),
        ("https://www.labiennale.org/en/cinema",       "Venice Film Festival"),
        ("https://tiff.net/",                          "Toronto International Film Festival (TIFF)"),
        ("https://tribecafilm.com/",                   "Tribeca Film Festival"),
        ("https://www.sundance.org/",                  "Sundance Film Festival"),
        ("https://telluridefilmfestival.org/",         "Telluride Film Festival"),
    ]

    _UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

    def __init__(self, tmdb_api_key=""):
        self.now = datetime.now()
        self.cutoff = self.now - timedelta(days=3)
        self.lookahead = self.now + timedelta(days=120)
        self.tmdb_api_key = tmdb_api_key
        self._seen = set()  # normalised event names already added

    # ── PUBLIC ────────────────────────────────────────────────────────────────

    def fetch_all(self):
        """Return events sorted chronologically, deduped, within date window."""
        events = []

        award = self._fetch_award_shows()
        print(f"   🏆 TVmaze award shows: {len(award)}")
        events.extend(award)

        fashion = self._fetch_fashion_bodies()
        print(f"   👗 Official fashion bodies: {len(fashion)}")
        events.extend(fashion)

        fu = self._fetch_fashionunited()
        print(f"   👗 FashionUnited: {len(fu)}")
        events.extend(fu)

        festivals = self._fetch_film_festivals()
        print(f"   🎥 Film festivals: {len(festivals)}")
        events.extend(festivals)

        if self.tmdb_api_key:
            premieres = self._fetch_tmdb_premieres()
            print(f"   🎬 TMDB film premieres: {len(premieres)}")
            events.extend(premieres)

        events.sort(key=lambda x: x["_sort_dt"])
        for e in events:
            e.pop("_sort_dt", None)

        print(f"   📅 Events total after dedup: {len(events)}")
        return events

    # ── PRIVATE HELPERS ───────────────────────────────────────────────────────

    def _get(self, url, timeout=12):
        """HTTP GET with browser UA. Returns text or None on any error."""
        try:
            r = requests.get(url, timeout=timeout, headers={"User-Agent": self._UA})
            return r.text if r.status_code == 200 else None
        except Exception as e:
            print(f"      ⚠️ GET {url}: {e}")
            return None

    def _extract_dates(self, raw):
        """
        Extract the earliest plausible datetime from raw HTML or text.
        Uses multiple regex patterns to handle the variety of date formats
        found on official fashion/festival websites.
        """
        # Strip HTML tags first
        text = re.sub(r"<[^>]+>", " ", raw).lower()
        text = re.sub(r"\s+", " ", text)

        month_re = (
            r"(?:january|february|march|april|may|june|july|august"
            r"|september|october|november|december)"
        )
        # Patterns: capture (day_start, month, year) in that normalised order
        patterns = [
            # "from 19 to 23 June 2026"
            rf"from\s+(\d{{1,2}})\s+to\s+\d{{1,2}}\s+({month_re})\s+(\d{{4}})",
            # "19–23 June 2026"
            rf"(\d{{1,2}})\s*[-–]\s*\d{{1,2}}\s+({month_re})\s+(\d{{4}})",
            # "June 19–23, 2026"
            rf"({month_re})\s+(\d{{1,2}})\s*[-–]\s*\d{{1,2}},?\s+(\d{{4}})",
            # "June 19 to 23, 2026"
            rf"({month_re})\s+(\d{{1,2}})\s+(?:to|through)\s+\d{{1,2}},?\s+(\d{{4}})",
            # "19 June 2026"
            rf"(\d{{1,2}})\s+({month_re})\s+(\d{{4}})",
            # "June 19, 2026"
            rf"({month_re})\s+(\d{{1,2}}),?\s+(\d{{4}})",
        ]

        found = []
        for pat in patterns:
            for m in re.finditer(pat, text):
                g = m.groups()
                try:
                    # Normalise to (day_str, month_str, year_str)
                    if g[0].isdigit():
                        day_s, month_s, year_s = g[0], g[1], g[2]
                    else:
                        month_s, day_s, year_s = g[0], g[1], g[2]
                    idx = self._MONTHS.index(month_s.strip()) + 1
                    found.append(datetime(int(year_s), idx, int(day_s)))
                except Exception:
                    continue

        # Return dates within the window, earliest first
        valid = sorted(
            {d for d in found if self.cutoff <= d <= self.lookahead}
        )
        return valid

    def _add(self, events, dt, name, source, link=""):
        """Append event if in window and not already seen."""
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

    # ── SOURCE FETCHERS ────────────────────────────────────────────────────────

    def _fetch_award_shows(self):
        """Query TVmaze for next broadcast episode of each major award show."""
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

    def _fetch_fashion_bodies(self):
        """Scrape official fashion body calendar pages for event start dates."""
        events = []
        for body in self._FASHION_BODIES:
            html = self._get(body["url"])
            if not html:
                continue

            # Try BeautifulSoup first for cleaner text, fallback to raw HTML
            if BS4:
                try:
                    text = BeautifulSoup(html, "html.parser").get_text(separator=" ")
                except Exception:
                    text = html
            else:
                text = html

            dates = self._extract_dates(text)
            if dates:
                self._add(events, dates[0], body["name"], body["source"], body["url"])
        return events

    def _fetch_fashionunited(self):
        """Scrape FashionUnited events calendar for fashion week dates."""
        events = []
        html = self._get(self._FU_URL)
        if not html:
            return events

        if BS4:
            try:
                soup = BeautifulSoup(html, "html.parser")
                page_text = soup.get_text(separator=" ")
            except Exception:
                page_text = html
        else:
            page_text = html

        page_lower = page_text.lower()

        for keyword, display in self._FU_EVENTS:
            pos = page_lower.find(keyword)
            if pos == -1:
                continue
            # Search for dates in the 300 characters around the keyword
            snippet = page_lower[max(0, pos - 30): pos + 300]
            dates = self._extract_dates(snippet)
            if dates:
                self._add(events, dates[0], display, "FashionUnited", self._FU_URL)
        return events

    def _fetch_film_festivals(self):
        """Scrape official film festival sites for their event dates."""
        events = []
        for url, name in self._FILM_FESTIVALS:
            html = self._get(url)
            if not html:
                continue

            if BS4:
                try:
                    text = BeautifulSoup(html, "html.parser").get_text(separator=" ")
                except Exception:
                    text = html
            else:
                text = html

            dates = self._extract_dates(text)
            if dates:
                self._add(events, dates[0], name, "Official Site", url)
        return events

    def _fetch_tmdb_premieres(self):
        """Add major upcoming TMDB films as premiere events (popularity ≥ 100)."""
        events = []
        films = get_tmdb_films()
        for film in films:
            if film["status"] != "Upcoming":
                continue
            if film["popularity"] < 100:
                continue
            try:
                dt = datetime.strptime(film["release_date"], "%Y-%m-%d")
            except Exception:
                continue
            self._add(
                events, dt,
                f"{film['title']} (Film Premiere)",
                "TMDB",
            )
        return events


def get_curated_events(gemini_ranker=None):
    """
    Build the events calendar from live authoritative sources via EventsFetcher.
    Gemini ranks the result if available; otherwise returns chronological order.
    """
    fetcher = EventsFetcher(tmdb_api_key=TMDB_API_KEY)
    all_events = fetcher.fetch_all()

    if not all_events:
        print("   ⚠️ No events found from any live source — check connectivity")
        return []

    # If we have more than the limit, let Gemini curate
    if gemini_ranker and gemini_ranker.is_available() and len(all_events) > MAX_ITEMS["events"]:
        item_lines = [
            f"[{i}] {e['name']} | {e['date']} | Source: {e['source']}"
            for i, e in enumerate(all_events[:40])
        ]
        priority = """PRIORITIZE:
1. Fashion weeks (Paris, Milan, New York, London) and Haute Couture weeks
2. Major award shows (Oscars, Emmys, Grammys, Tonys, BAFTAs, CFDA, VMAs)
3. Prestige film festivals (Cannes, Venice, TIFF, Tribeca, Sundance)
4. Major luxury industry galas (Met Gala, LACMA Art+Film, British Fashion Awards)
5. Blockbuster film premieres (only if very high-profile)
SKIP: duplicates, minor regional events, repeated entries for the same festival"""

        selected = _gemini_select(
            gemini_ranker, item_lines, "industry events",
            MAX_ITEMS["events"], priority, all_events,
        )
        return selected

    return all_events[:MAX_ITEMS["events"]]


# =============================================================================
# RSS FEED SOURCES
# =============================================================================

RSS_SOURCES = {
    "industry_news": [
        {"url": "https://wwd.com/feed", "source": "WWD"},
        {"url": "https://www.businessoffashion.com/arc/outbound-feeds/rss/", "source": "BoF"},
        {"url": "https://fashionista.com/.rss/excerpt/", "source": "Fashionista"},
        {"url": "https://www.vogue.com/feed/rss", "source": "Vogue"},
        {"url": "https://www.vogue.fr/feed/rss", "source": "Vogue France"},
        {"url": "https://drapersonline.com/feed", "source": "Drapers"},
    ],
    "casting": [
        {"url": "https://deadline.com/feed/", "source": "Deadline"},
        {"url": "https://deadline.com/category/casting/feed/", "source": "Deadline Casting"},
        {"url": "https://variety.com/v/film/feed", "source": "Variety Film"},
        {"url": "https://www.hollywoodreporter.com/feed", "source": "THR"},
    ],
    "tv": [
        {"url": "https://deadline.com/category/tv/feed/", "source": "Deadline TV"},
        {"url": "https://www.hollywoodreporter.com/c/tv/feed", "source": "THR TV"},
        {"url": "https://variety.com/v/tv/feed", "source": "Variety TV"},
    ],
    "film": [
        {"url": "https://deadline.com/category/film/feed/", "source": "Deadline Film"},
        {"url": "https://variety.com/v/film/feed", "source": "Variety Film"},
        {"url": "https://www.hollywoodreporter.com/c/movies/feed", "source": "THR Film"},
    ],
    "ambassadors": [
        {"url": "https://wwd.com/feed", "source": "WWD"},
        {"url": "https://fashionista.com/.rss/excerpt/", "source": "Fashionista"},
        {"url": "https://hypebeast.com/feed", "source": "Hypebeast"},
        {"url": "https://hypebae.com/feed", "source": "Hypebae"},
        {"url": "https://www.popsugar.com/fashion/feed", "source": "POPSUGAR Fashion"},
        {"url": "https://www.popsugar.com/beauty/feed", "source": "POPSUGAR Beauty"},
        {"url": "https://www.vogue.com/feed/rss", "source": "Vogue"},
        {"url": "https://www.vogue.fr/feed/rss", "source": "Vogue France"},
        {"url": "https://www.beautypackaging.com/rss", "source": "Beauty Packaging"},
    ],
    "partnerships": [
        {"url": "https://wwd.com/feed", "source": "WWD"},
        {"url": "https://www.businessoffashion.com/arc/outbound-feeds/rss/", "source": "BoF"},
        {"url": "https://fashionista.com/.rss/excerpt/", "source": "Fashionista"},
        {"url": "https://www.vogue.com/feed/rss", "source": "Vogue"},
    ],
}

CATEGORY_MAP = {
    "industry_news": "industryNews",
    "casting": "castings",
    "tv": "tvProjects",
    "film": "filmProjects",
    "ambassadors": "ambassadors",
    "partnerships": "partnerships",
}

# =============================================================================
# CONTENT CLASSIFIERS - TIGHTENED v3.6.1
# =============================================================================

CLASSIFIERS = {
    "ambassadors": {
        "keywords": [
            "brand ambassador", "named ambassador", "new face of", "face of",
            "signs with", "signs deal", "tapped by", "tapped for", "campaign star",
            "new campaign", "global ambassador", "house ambassador",
            "beauty ambassador", "fashion ambassador", "named face",
            "new spokesperson", "new muse", "brand muse", "newest ambassador",
            "latest ambassador", "joins as ambassador", "as the face",
            "as new face", "for campaign", "fronts campaign", "stars in campaign",
            "new deal with", "partners with", "collaborates with",
            "teams up with", "joins forces with", "becomes ambassador",
            "is ambassador", "named the face", "tapped as", "tapped to",
        ],
        "exclude": [
            "rumored", "speculated", "might", "could", "reportedly considering",
            "allegedly", "reportedly in talks", "eying", "circling",
            "nominations", "nominees", "awards", "wins", "won", "accepts",
            "interview", "profile", "q&a", "talks about", "reflects on",
            "review", "recap", "best dressed", "makeup", "hair",
            "festival", "premiere", "screening", "gala", "ceremony",
            "talent agency", "signs with agency", "management", "representation",
            "agent", "caa", "wme", "uta", "icm", "vmg",
        ],
        "must_have": ["ambassador", "face of", "campaign", "muse", "spokesperson",
                      "signs with", "tapped", "named", "becomes"],
    },
    "partnerships": {
        "keywords": [
            "collaboration", "capsule collection", "limited edition", "drop",
            "exclusive collection", "debut collection", "联名", "collab",
            "special collection", "co-branded", "co-designed",
            "unveils collection with", "launches collection with",
            "partnership with", "teams up with", "joins forces with",
            "x ", "× ", " x ", " × ",
        ],
        "exclude": [
            "rumored", "speculated", "might", "could", "allegedly",
            "reportedly considering", "eying", "circling",
            "interview", "profile", "q&a", "review", "recap",
            "nominations", "nominees", "awards", "wins", "won",
            "cast in", "joins cast", "to star", "signs with", "deal",
            "must read", "weekly drop watch", "school drop-offs",
            "how to", "guide to", "best ", "top ", "worst ",
            "saison", "films qui", "decisions", "mode printemps",
        ],
        "must_have": ["collection", "collaboration", "partnership", "capsule",
                      "limited edition", "exclusive", "drop", "co-branded",
                      "teams up", "joins forces"],
    },
    "castings": {
        "keywords": [
            "cast in", "joins cast", "set to star", "lands role", "to play",
            "in talks", "signed on", "boarded", "attached to", "to portray",
            "cast as", "set to lead", "toplines", "in final talks",
            "replacing", "exits", "departs", "steps down", "new host",
            "to direct", "to produce", "writing ", "directing ",
            "returns for", "returning to", "back for", "reprises role",
            "to star in", "to appear in", "to feature in", "to cameo in",
            "joins ", "added to", "boards ", "set to join",
        ],
        "exclude": [
            "rumored", "speculated", "might", "could", "allegedly",
            "reportedly in talks", "eying", "circling",
            "review", "recap", "best dressed", "worst dressed",
            "makeup", "hair", "beauty", "skincare", "fashion trend",
            "outfit", "dress", "wore", "style", "look",
            "interview", "profile", "q&a", "talks about",
            "joins magazine", "joins publication", "editor in chief",
            "creative director", "appointed ceo", "named ceo",
            # v3.6.1: Broader editorial hire detection
            "joins as editor", "joins as writer", "joins as contributor",
            "joins column", "joins team", "hired as", "named editor",
            "appointed editor", "steps into", "takes over as",
            # v3.8: Interview/profile bleed-prevention
            "talking with", "on taking", "on working with", "in conversation",
            "opens up about", "speaks with", "reflects on his", "reflects on her",
            "on his career", "on her career", "discusses his", "discusses her",
            "the story of", "how the", "unexpected turns",
        ],
        "must_have": ["cast", "star", "role", "play", "joins", "direct", "produce"],
    },
    "filmProjects": {
        "keywords": [
            "movie", "film", "feature", "biopic", "sequel", "prequel",
            "franchise", "adaptation", "remake", "reboot", "spinoff",
            "in production", "wraps production", "starts filming",
            "begins shoot", "wraps shoot", "in theaters", "release date",
            "greenlit", "picked up", "ordered", "in development",
        ],
        "must_have": ["film", "movie", "feature", "biopic", "sequel", "prequel",
                      "franchise", "adaptation", "remake", "reboot", "spinoff",
                      "production", "filming", "shoot", "theaters", "release",
                      "greenlit", "development"],
        "exclude": [
            "review", "recap", "critics", "rating", "box office prediction",
            "interview", "profile", "q&a", "talks about", "reflects on",
            "makeup", "hair", "beauty look", "outfit", "dress", "wore",
            "festival", "premiere", "screening", "gala", "ceremony",
            "cast in", "joins cast", "to star", "lands role", "to play",
            # NEW v3.6.1: Exclude acquisition/market deals
            "closes deal", "acquires", "lands rights", "picks up rights",
            "distribution deal", "market deal", "cannes market",
        ],
    },
    "tvProjects": {
        "keywords": [
            "series", "season", "episode", "show", "tv", "television",
            "streaming", "netflix", "hbo", "hulu", "apple tv", "amazon",
            "peacock", "paramount", "disney+", "starz", "fx", "amc",
            "renewed", "ordered", "greenlit", "picked up", "in development",
            "pilot", "limited series", "anthology",
        ],
        "must_have": ["renewed", "ordered", "greenlit", "picked up", "pilot",
                      "series", "season", "development", "netflix", "hbo",
                      "hulu", "apple tv", "amazon", "peacock", "paramount",
                      "disney+", "starz", "fx"],
        "exclude": [
            "review", "recap", "critics", "rating",
            "interview", "profile", "q&a", "talks about", "reflects on",
            "makeup", "hair", "beauty look", "outfit", "dress", "wore",
            "cast in", "joins cast", "to star", "lands role", "to play",
            "dies", "dead", "death", "passed away",
            # NEW v3.6.1: Exclude awards and endings
            "awards", "winners", "full list", "nominations", "nominees",
            "bids farewell", "concludes", "final episode", "series finale",
            "ending", "canceled", "cancelled",
        ],
    },
}


# =============================================================================
# CORE CLASSES
# =============================================================================

class NewsletterScraper:
    def __init__(self, days_back=7):
        self.days_back = days_back
        self.cutoff_date = datetime.now() - timedelta(days=days_back)
        self.raw_items = []
        self.categorized = {
            "events": [],
            "industryNews": [],
            "ambassadors": [],
            "partnerships": [],
            "castings": [],
            "filmProjects": [],
            "tvProjects": [],
            "inTheaters": [],
            "newMusic": [],
            "tvPremieres": [],
        }
        self.gemini = GeminiRanker()
        self._seen_links = set()
        self._seen_titles = set()  # NEW v3.6.1: Global title dedup

    def fetch_all_feeds(self):
        print("\n" + "="*60)
        print("📡 FETCHING RSS FEEDS")
        print("="*60)
        total = 0
        failed = []

        for rss_category, sources in RSS_SOURCES.items():
            target_category = CATEGORY_MAP.get(rss_category, rss_category)
            for source in sources:
                try:
                    print(f"   → {source['source']} ({rss_category} → {target_category})")
                    feed = feedparser.parse(source['url'])

                    if hasattr(feed, 'bozo_exception') and feed.bozo_exception:
                        print(f"      ⚠️ Parse warning: {feed.bozo_exception}")

                    for entry in feed.entries:
                        pub_date = self._parse_date(entry)
                        if pub_date and pub_date < self.cutoff_date:
                            continue

                        item = {
                            "title": entry.get('title', ''),
                            "summary": entry.get('summary', entry.get('description', '')),
                            "link": entry.get('link', ''),
                            "published": pub_date,
                            "source": source['source'],
                            "category_hint": target_category,
                        }
                        self.raw_items.append(item)
                        total += 1

                except Exception as e:
                    failed.append(f"{source['source']}: {e}")
                    print(f"      ❌ Error: {e}")

        print(f"\n✅ Fetched {total} items from {sum(len(s) for s in RSS_SOURCES.values())} feeds")
        if failed:
            print(f"   ⚠️ {len(failed)} feeds failed (see above)")
        return self

    def _parse_date(self, entry):
        for field in ['published_parsed', 'updated_parsed', 'created_parsed']:
            if hasattr(entry, field) and getattr(entry, field):
                dt = getattr(entry, field)
                return datetime(*dt[:6])
        return datetime.now()

    def categorize_content(self):
        print("\n" + "="*60)
        print("🧠 CATEGORIZING CONTENT")
        print("="*60)

        # ── REAL-DATA-POWERED SECTIONS ───────────────────────────────
        # Events: hardcoded industry calendar + TMDB film premieres (no Gemini generation)
        curated_events = get_curated_events(self.gemini)
        self.categorized["events"] = curated_events
        print(f"   📅 Loaded {len(curated_events)} events")

        # In Theaters: real TMDB data → Gemini selects (no generation)
        in_theaters = get_in_theaters(self.gemini)
        self.categorized["inTheaters"] = in_theaters
        print(f"   🎬 Loaded {len(in_theaters)} films in theaters")

        # New Music: real RSS headlines → Gemini selects (no generation)
        new_music = get_new_music(self.gemini)
        self.categorized["newMusic"] = new_music
        print(f"   🎵 Loaded {len(new_music)} music items")

        # TV Premieres: real TVmaze data → Gemini selects (no generation)
        tv_premieres = get_tv_premieres(self.gemini)
        self.categorized["tvPremieres"] = tv_premieres
        print(f"   📺 Loaded {len(tv_premieres)} TV premieres")

        # ── RSS-BASED SECTIONS ────────────────────────────────────────
        for item in self.raw_items:
            text = f"{item['title']} {item['summary']}".lower()
            assigned = False
            hint = item.get('category_hint', '')

            link = item.get('link', '')
            title = item.get('title', '')

            # NEW v3.6.1: Global title dedup
            if title and title in self._seen_titles:
                continue

            if link and link in self._seen_links:
                continue

            if hint in CLASSIFIERS:
                rules = CLASSIFIERS[hint]
                if self._passes_classifier(text, rules):
                    formatted = self._format_item(item, hint)
                    if formatted and not self._is_duplicate(formatted, hint):
                        self.categorized[hint].append(formatted)
                        if link:
                            self._seen_links.add(link)
                        if title:
                            self._seen_titles.add(title)
                        assigned = True

            if not assigned:
                for category, rules in CLASSIFIERS.items():
                    if category == hint:
                        continue
                    if self._passes_classifier(text, rules):
                        formatted = self._format_item(item, category)
                        if formatted and not self._is_duplicate(formatted, category):
                            self.categorized[category].append(formatted)
                            if link:
                                self._seen_links.add(link)
                            if title:
                                self._seen_titles.add(title)
                            assigned = True
                            break

            if not assigned:
                formatted = self._format_industry_news(item)
                if formatted and not self._is_duplicate(formatted, "industryNews"):
                    self.categorized["industryNews"].append(formatted)

        print("\n📊 CATEGORIZATION RESULTS (before limits):")
        for cat, items in self.categorized.items():
            print(f"   • {cat}: {len(items)} items")

        self._apply_limits()

        print("\n📊 FINAL RESULTS (after limits + Gemini ranking):")
        for cat, items in self.categorized.items():
            print(f"   • {cat}: {len(items)} items")

        return self

    def _passes_classifier(self, text, rules):
        keywords = rules.get('keywords', [])
        exclude = rules.get('exclude', [])
        must_have = rules.get('must_have', [])

        if any(exc.lower() in text for exc in exclude):
            return False

        if must_have and not any(mh.lower() in text for mh in must_have):
            return False

        score = sum(1 for kw in keywords if kw.lower() in text)
        return score >= 1

    def _format_industry_news(self, item):
        text_lower = (f"{item['title']} {item['summary']}").lower()

        strong_signals = [
            "acquires", "merger", "acquisition", "ipo", "earnings", "revenue",
            "appointed", "ceo", "creative director", "steps down", "resigns",
            "launches", "expansion", "store opening", "flagship", "partnership",
            "sustainability", "carbon neutral", "diversity", "inclusion",
            "lvmh", "kering", "richemont", "hermès", "chanel", "dior",
            "l'oréal", "estée lauder", "shiseido", "coty",
            "sales", "growth", "profit", "billion", "million", "market",
            "trend", "forecast", "report", "study", "survey",
            "digital", "e-commerce", "metaverse", "nft", "ai",
            "supply chain", "manufacturing", "retail", "wholesale",
            "quarterly", "annual", "fiscal", "revenue", "profit",
            "merger", "acquisition", "investment", "valuation",
            "bankruptcy", "restructuring", "layoffs", "hiring",
        ]

        # ENHANCED v3.6.1: More comprehensive weak signals
        weak_signals = [
            "boot", "shoe", "sneaker", "bag", "dress", "outfit",
            "makeup", "lipstick", "skincare", "fragrance", "perfume",
            "lookbook", "runway", "trend", "style",
            "shop for", "to shop", "shopping", "buy now", "on sale",
            "discount", "markdown", "deal", "steal", "bargain",
            "gift guide", "holiday gift", "wishlist", "must-have",
            "pack a", "wear this", "outfit idea", "get the look",
            "beginner's guide", "how to", "tutorial", "tips for",
            "products to", "new launches", " launches to",
            # v3.6.1: product signals
            "first look", "rare ", "coming back", "reissue", "restock",
            "sneaker release", "drop date", "colorway", "collab release",
            "beauty launch", "skincare launch", "fragrance launch",
            "red carpet", "best dressed", "worst dressed", "wore ", "wears ",
            "accused of", "scandal", "lawsuit", "allegations", "abuse",
            # v3.8: collection/runway coverage
            "croisière", "cruise collection", "resort 20", "collection croisière",
            "collection review", "runway review", "season review",
            "front row", "première rang", "au premier rang",
            "attends the", "attend the", "steps out in",
            # v3.8: retail logistics (Walmart-class irrelevance)
            "supply chain", "consolidation network", "distribution center",
            "first-mile", "prepaid", "supplier access", "logistics",
            # v3.8: recurring data columns
            "trend tracker", "data tracker", "beauty tracker", "spate tracking",
        ]

        # NEW v3.6.1: Exclude round-up articles
        roundup_signals = [
            "must read", "must-read", "weekly round-up", "daily round-up",
            "morning digest", "evening digest", "news roundup", "headlines",
            "what to know", "what you missed", "catch up", "this week in",
        ]
        if any(rp in text_lower for rp in roundup_signals):
            return None

        tier1_sources = ["WWD", "BoF", "Vogue", "Vogue France", "Drapers", "Business of Fashion"]
        is_tier1 = item.get('source') in tier1_sources

        relevance_score = sum(1 for signal in strong_signals if signal in text_lower)
        weakness_score = sum(1 for signal in weak_signals if signal in text_lower)

        if (relevance_score >= 2 or (is_tier1 and relevance_score >= 1)) and weakness_score < 2:
            return self._format_item(item, "industryNews")
        return None

    def _apply_limits(self):
        for category, max_count in MAX_ITEMS.items():
            items = self.categorized[category]

            if category in ("events", "inTheaters", "newMusic", "tvPremieres"):
                self.categorized[category] = items[:max_count]
                continue

            if len(items) > max_count:
                self.categorized[category] = self.gemini.rank(items, category, max_count)
            elif len(items) > 1 and self.gemini.is_available():
                self.categorized[category] = self.gemini.rank(items, category, len(items))
            else:
                self.categorized[category] = items[:max_count]

    def _format_item(self, item, category):
        title = item['title'].strip()
        summary = re.sub(r'<[^>]+>', '', item['summary'])
        summary = summary[:200] + "..." if len(summary) > 200 else summary
        link = item.get('link', '')
        source = item.get('source', '')

        if category == "events":
            pub_date = item.get('published')
            if pub_date:
                date_str = pub_date.strftime('%b %d') if isinstance(pub_date, datetime) else "TBA"
            else:
                date_str = "TBA"
            return {"date": date_str, "name": title, "link": link, "source": source}

        elif category == "industryNews":
            return {"headline": title, "detail": summary, "link": link, "source": source}

        elif category == "ambassadors":
            result = self._extract_ambassador(title, link, source)
            if result:
                return result
            # v3.8: discard if extraction fails — don't show "brand: See article"
            # The full headline is still usable, so fall back to title-only format
            return {"talent": title, "brand": "", "link": link, "source": source}

        elif category == "partnerships":
            result = self._extract_partnership(title, link, source)
            if result:
                return result
            return None  # v3.8: discard failed extraction instead of "x See article"

        elif category in ["castings", "filmProjects", "tvProjects", "inTheaters", "newMusic"]:
            return {"talent": title, "project": summary[:120] + "..." if len(summary) > 120 else summary, "link": link, "source": source}

        return None

    def _extract_ambassador(self, title, link, source):
        patterns = [
            r'^([A-Z][a-zA-Z\s\.\'\-]+?)(?:\s+(?:for|is|becomes|named|tapped as|tapped by|signs with|joins|fronts|stars in|is the new face of|is the face of))\s+([A-Z][a-zA-Z\s&\'\-]+?)(?:\s+(?:as|brand|ambassador|face|muse|spokesperson|campaign|global|house|beauty|fashion|\'s|$))',
            r'^([A-Z][a-zA-Z\s&\'\-]+?)(?:\s+(?:taps|names|signs|welcomes|casts|chooses|picks|appoints))\s+([A-Z][a-zA-Z\s\.\'\-]+?)(?:\s+(?:as|for|to|its|global|house|brand|beauty|fashion|ambassador|face|muse|spokesperson|$))',
            r'^([A-Z][a-zA-Z\s\.\'\-]+?)(?:\s+(?:teams up with|collaborates with|partners with))\s+([A-Z][a-zA-Z\s&\'\-]+?)(?:\s+(?:for|on|to|as|\'s|$))',
        ]

        for pattern in patterns:
            match = re.search(pattern, title, re.IGNORECASE)
            if match:
                g1, g2 = match.group(1).strip(), match.group(2).strip()

                brand_words = [
                    'lvmh', 'kering', 'l\'oréal', 'estée lauder', 'chanel', 'dior',
                    'gucci', 'prada', 'lv', 'louis vuitton', 'hermès', 'cartier',
                    'tiffany', 'bulgari', 'lancome', 'ysl', 'givenchy', 'fendi',
                    'celine', 'balenciaga', 'bottega veneta', 'valentino',
                    'armani', 'burberry', 'versace', 'dolce', 'gabbana',
                    'tom ford', 'marc jacobs', 'coach', 'michael kors',
                    'ralph lauren', 'tory burch', 'kate spade', 'jimmy choo',
                    'nike', 'adidas', 'puma', 'reebok', 'asics', 'new balance',
                    'lululemon', 'alo yoga', 'skims', 'fenty', 'kylie cosmetics',
                    'charlotte tilbury', 'mac', 'nars', 'pat mcgrath',
                    'la mer', 'la prairie', 'clinique', 'origins',
                    'jo malone', 'diptyque', 'byredo', 'le labo',
                    'omega', 'rolex', 'patek philippe', 'audemars piguet',
                    'tag heuer', 'breitling', 'iwc', 'jaeger-lecoultre',
                ]

                g1_lower = g1.lower()
                g2_lower = g2.lower()

                g1_has_brand = any(b in g1_lower for b in brand_words)
                g2_has_brand = any(b in g2_lower for b in brand_words)

                g1_words = len(g1.split())
                g2_words = len(g2.split())

                if g1_has_brand and not g2_has_brand:
                    return {"talent": g2, "brand": g1, "link": link, "source": source}
                elif g2_has_brand and not g1_has_brand:
                    return {"talent": g1, "brand": g2, "link": link, "source": source}
                elif g1_words <= 2 and g2_words > 2:
                    return {"talent": g1, "brand": g2, "link": link, "source": source}
                elif g2_words <= 2 and g1_words > 2:
                    return {"talent": g2, "brand": g1, "link": link, "source": source}
                else:
                    return {"talent": g1, "brand": g2, "link": link, "source": source}

        return None

    def _extract_partnership(self, title, link, source):
        # ENHANCED v3.6.1: More comprehensive skip patterns
        skip_patterns = [
            r'^how\s', r'^must\s+read', r'^weekly\s+drop', r'^school\s+',
            r'^pourquoi', r'^mode\s+', r'^pedro\s+almod', r'^american\s+horror',
            r'^\d+\s+new\s+', r'^\d+\s+best\s+', r'^a\s+beginner',
            r'^maximize\s+your', r'^a\s+river\s+runs',
            # NEW v3.6.1: More false positive patterns
            r'^in\s+pictures', r'^first\s+look', r'^bfc\s+unveils',
            r'^how\s+this', r'^why\s+', r'^what\s+', r'^the\s+best',
            r'^\d+\s+things', r'^\d+\s+ways',
        ]
        for sp in skip_patterns:
            if re.search(sp, title, re.IGNORECASE):
                return None

        separators = [
            ' x ', ' X ', ' × ', ' × ', ' × ',
            ' Collaborates With ', ' Partners With ', ' Teams Up With ',
            ' Joins Forces With ', ' and ', ' & ',
        ]

        for sep in separators:
            if sep in title:
                parts = title.split(sep, 1)
                p1 = parts[0].strip()
                p2 = parts[1].strip()

                p2 = re.sub(r'\s+(?:for|on|to|launch|collection|collaboration|partnership|drop|capsule|limited edition).*$', '', p2, flags=re.IGNORECASE)
                p1 = re.sub(r'^(?:New|Exclusive|First Look|Inside|Weekly Drop Watch:|In pictures:)\s+', '', p1, flags=re.IGNORECASE)

                if len(p1) > 2 and len(p2) > 2 and len(p1) < 60 and len(p2) < 60:
                    return {"partner1": p1, "partner2": p2, "link": link, "source": source}

        and_match = re.search(r'^([A-Z][a-zA-Z\s&\'\-]+?)\s+(?:and|&)\s+([A-Z][a-zA-Z\s&\'\-]+?)(?:\s|$)', title)
        if and_match:
            return {"partner1": and_match.group(1).strip(), "partner2": and_match.group(2).strip(), "link": link, "source": source}

        return None

    def _is_duplicate(self, item, category):
        # Check within the target category
        existing = self.categorized.get(category, [])
        for e in existing:
            if item.get('headline') and e.get('headline') == item.get('headline'):
                return True
            if item.get('talent') and e.get('talent') == item.get('talent'):
                return True
            if item.get('name') and e.get('name') == item.get('name'):
                return True
            if item.get('partner1') and e.get('partner1') == item.get('partner1'):
                return True

        # Cross-category check for castings/film/tv
        if category in ["castings", "filmProjects", "tvProjects"]:
            cross_cats = ["castings", "filmProjects", "tvProjects"]
            cross_cats.remove(category)
            for cross_cat in cross_cats:
                for e in self.categorized.get(cross_cat, []):
                    # ENHANCED v3.6.1: Better cross-category dedup
                    item_title = item.get('talent', '').lower()
                    e_title = e.get('talent', '').lower()
                    if item_title and e_title:
                        # Exact match or substantial overlap
                        if item_title == e_title:
                            return True
                        # Check if one is contained in the other (for long titles)
                        if len(item_title) > 20 and len(e_title) > 20:
                            if item_title in e_title or e_title in item_title:
                                return True

        return False

    def get_newsletter_data(self):
        now = datetime.now()
        return {
            "MONTH": now.strftime('%B'),
            "YEAR": str(now.year),
            "SENDER_NAME": os.getenv("SENDER_NAME", "Your Name"),
            "SENDER_TITLE": os.getenv("SENDER_TITLE", "VIP & Media Relations"),
            "COMPANY_NAME": os.getenv("COMPANY_NAME", "Your Company"),
            "COMPANY_ADDRESS": os.getenv("COMPANY_ADDRESS", "Your Address"),
            "SENDER_EMAIL": os.getenv("SENDER_EMAIL", "you@company.com"),
            "SENDER_PHONE": os.getenv("SENDER_PHONE", ""),
            "COMPANY_URL": os.getenv("COMPANY_URL", ""),
            "CALENDAR_LINK": os.getenv("CALENDAR_LINK", "#"),
            "PREHEADER": os.getenv("PREHEADER", f"VIP & Industry News — {now.strftime('%B %Y')} edition"),
            **self.categorized
        }


class EmailSender:
    def __init__(self, config):
        self.config = config
        self.provider = config.get("provider", "brevo")

    def send(self, to_email, subject, html_content, text_content=None):
        print(f"\n📧 Sending via {self.provider}...")

        if self.provider == "brevo":
            return self._send_brevo(to_email, subject, html_content, text_content)
        elif self.provider == "sendpulse":
            return self._send_sendpulse(to_email, subject, html_content)
        elif self.provider == "mailjet":
            return self._send_mailjet(to_email, subject, html_content, text_content)
        elif self.provider == "sendgrid":
            return self._send_sendgrid(to_email, subject, html_content, text_content)
        elif self.provider == "smtp":
            return self._send_smtp(to_email, subject, html_content, text_content)
        else:
            print("❌ Unknown provider")
            return False

    def _send_brevo(self, to_email, subject, html, text=None):
        api_key = self.config.get("brevo_api_key")
        if not api_key:
            print("❌ BREVO_API_KEY not set")
            return False

        url = "https://api.brevo.com/v3/smtp/email"
        headers = {
            "accept": "application/json",
            "api-key": api_key,
            "content-type": "application/json"
        }
        payload = {
            "sender": {"name": self.config["from_name"], "email": self.config["from_email"]},
            "to": [{"email": to_email}],
            "subject": subject,
            "htmlContent": html,
        }
        # NEW v3.6.1: Add plain text version
        if text:
            payload["textContent"] = text

        try:
            response = requests.post(url, json=payload, headers=headers)
            if response.status_code in [200, 201, 202]:
                print("✅ Sent successfully via Brevo!")
                return True
            else:
                print(f"❌ Brevo error: {response.status_code} - {response.text[:200]}")
                return False
        except Exception as e:
            print(f"❌ Error: {e}")
            return False

    def _send_sendpulse(self, to_email, subject, html):
        print("ℹ️ SendPulse: Requires OAuth2 token setup. See docs.")
        return False

    def _send_mailjet(self, to_email, subject, html, text=None):
        api_key = self.config.get("mailjet_api_key")
        api_secret = self.config.get("mailjet_api_secret")
        if not api_key or not api_secret:
            print("❌ MAILJET_API_KEY or MAILJET_API_SECRET not set")
            return False

        url = "https://api.mailjet.com/v3.1/send"
        auth = (api_key, api_secret)
        payload = {
            "Messages": [{
                "From": {"Email": self.config["from_email"], "Name": self.config["from_name"]},
                "To": [{"Email": to_email}],
                "Subject": subject,
                "HTMLPart": html,
            }]
        }
        if text:
            payload["Messages"][0]["TextPart"] = text

        try:
            response = requests.post(url, json=payload, auth=auth)
            if response.status_code == 200:
                print("✅ Sent successfully via Mailjet!")
                return True
            else:
                print(f"❌ Mailjet error: {response.status_code} - {response.text[:200]}")
                return False
        except Exception as e:
            print(f"❌ Error: {e}")
            return False

    def _send_sendgrid(self, to_email, subject, html, text=None):
        api_key = self.config.get("sendgrid_api_key")
        if not api_key:
            print("❌ SENDGRID_API_KEY not set")
            return False

        url = "https://api.sendgrid.com/v3/mail/send"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "personalizations": [{"to": [{"email": to_email}]}],
            "from": {"email": self.config["from_email"], "name": self.config["from_name"]},
            "subject": subject,
            "content": [{"type": "text/html", "value": html}]
        }
        if text:
            payload["content"].append({"type": "text/plain", "value": text})

        try:
            response = requests.post(url, json=payload, headers=headers)
            if response.status_code == 202:
                print("✅ Sent successfully via SendGrid!")
                return True
            else:
                print(f"❌ SendGrid error: {response.status_code} - {response.text[:200]}")
                return False
        except Exception as e:
            print(f"❌ Error: {e}")
            return False

    def _send_smtp(self, to_email, subject, html, text=None):
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = f"{self.config['from_name']} <{self.config['from_email']}>"
        msg['To'] = to_email
        msg.attach(MIMEText(html, 'html'))
        if text:
            msg.attach(MIMEText(text, 'plain'))

        try:
            server = smtplib.SMTP(self.config['smtp_server'], self.config['smtp_port'])
            server.starttls()
            server.login(self.config['smtp_username'], self.config['smtp_password'])
            server.sendmail(self.config['from_email'], [to_email], msg.as_string())
            server.quit()
            print("✅ Sent successfully via SMTP!")
            return True
        except Exception as e:
            print(f"❌ SMTP error: {e}")
            return False


def load_template(template_path="newsletter_template.html"):
    with open(template_path, 'r', encoding='utf-8') as f:
        return f.read()


def render_newsletter(data, template):
    if JINJA:
        t = Template(template)
        return t.render(**data)
    else:
        html = template
        for key, value in data.items():
            if isinstance(value, str):
                html = html.replace(f'{{{{{key}}}}}', value)
        return html


def generate_plain_text(data):
    """Generate plain text version for multipart emails."""
    lines = [
        f"VIP & Industry News | {data['MONTH']} {data['YEAR']}",
        "=" * 50,
        "",
        "KEY EVENTS AHEAD:",
        "-" * 30,
    ]
    for event in data.get('events', []):
        lines.append(f"{event.get('date', 'TBA')} — {event.get('name', '')}")

    lines.extend(["", "INDUSTRY NEWS:", "-" * 30])
    for item in data.get('industryNews', []):
        lines.append(f"• {item.get('headline', '')}")

    lines.extend(["", "AMBASSADORS:", "-" * 30])
    for item in data.get('ambassadors', []):
        lines.append(f"• {item.get('talent', '')} for {item.get('brand', '')}")

    lines.extend(["", "PARTNERSHIPS:", "-" * 30])
    for item in data.get('partnerships', []):
        lines.append(f"• {item.get('partner1', '')} x {item.get('partner2', '')}")

    lines.extend(["", "CASTINGS:", "-" * 30])
    for item in data.get('castings', []):
        lines.append(f"• {item.get('talent', '')}")

    lines.extend(["", "FILM PROJECTS:", "-" * 30])
    for item in data.get('filmProjects', []):
        lines.append(f"• {item.get('talent', '')}")

    lines.extend(["", "TV PROJECTS:", "-" * 30])
    for item in data.get('tvProjects', []):
        lines.append(f"• {item.get('talent', '')}")

    lines.extend(["", "TV PREMIERES:", "-" * 30])
    for item in data.get('tvPremieres', []):
        lines.append(f"• {item.get('talent', '')} — {item.get('project', '')}")

    lines.extend(["", "IN THEATERS:", "-" * 30])
    for item in data.get('inTheaters', []):
        lines.append(f"• {item.get('talent', '')} — {item.get('project', '')}")

    lines.extend(["", "NEW MUSIC:", "-" * 30])
    for item in data.get('newMusic', []):
        lines.append(f"• {item.get('talent', '')}")

    lines.extend(["", "=" * 50, f"Best,\n{data.get('SENDER_NAME', '')}", data.get('COMPANY_NAME', '')])

    return "\n".join(lines)


def save_newsletter(html, filename=None):
    if not filename:
        now = datetime.now()
        filename = f"newsletter_{now.strftime('%B_%Y').lower()}.html"

    with open(filename, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"\n💾 Saved: {filename}")
    return filename


def main():
    print("=" * 60)
    print("VIP & INDUSTRY NEWS - AUTOMATED NEWSLETTER SYSTEM v3.7")
    print("=" * 60)
    print(f"📅 Running: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    scraper = NewsletterScraper(days_back=7)
    scraper.fetch_all_feeds().categorize_content()
    data = scraper.get_newsletter_data()

    try:
        template = load_template()
    except FileNotFoundError:
        print("\n❌ newsletter_template.html not found!")
        sys.exit(1)

    html = render_newsletter(data, template)
    plain_text = generate_plain_text(data)

    save_newsletter(html)

    # Save plain text version too
    txt_filename = f"newsletter_{datetime.now().strftime('%B_%Y').lower()}.txt"
    with open(txt_filename, 'w', encoding='utf-8') as f:
        f.write(plain_text)
    print(f"💾 Saved: {txt_filename}")

    if os.getenv("SEND_EMAIL", "0") == "1":
        subject = f"VIP & Industry News | {data['MONTH']} {data['YEAR']}"
        sender = EmailSender(EMAIL_CONFIG)
        success = sender.send(RECIPIENT_EMAIL, subject, html, plain_text)

        if not success:
            print("\n⚠️ Email sending failed. HTML saved locally.")
    else:
        print("\n📌 Email sending skipped (set SEND_EMAIL=1 to enable)")
        print(f"📌 Recipient: {RECIPIENT_EMAIL}")

    print("\n✨ Done!")


if __name__ == "__main__":
    main()
