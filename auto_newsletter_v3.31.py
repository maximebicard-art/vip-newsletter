#!/usr/bin/env python3
"""
VIP & Industry News - AUTOMATED NEWSLETTER SYSTEM v3.31
========================================================
CHANGES FROM v3.30 — industry-news throughput fixes
  Diagnosed from the Jul 27 run (industryNews: 32 before limits -> 10 after;
  tier reported "4 hard + 6 soft from 16 hard / 16 soft available").

  FIX 1 — _apply_industry_news_tier fill order was INVERTED.
    HARD_QUOTA=4 behaved as a CEILING, not a floor: hard[:4] + soft[:6] filled
    all 10 slots, so the leftover-hard backfill never ran and 12 of 16 hard
    items (including the Chanel CFO appointment) were silently dropped. Now
    hard news fills FIRST; only SOFT_RESERVE (default 3) slots are held for
    soft/variety, and unused soft slots fall back to hard.

  FIX 2 — fashion-week CALENDAR news had no route in.
    Schedule / line-up announcements (Paris & Milan provisional calendars)
    scored 0 strong signals in _format_industry_news and were discarded. Added
    CALENDAR_SIGNALS (accepted on one hit for tier-1 trade sources) and exempted
    calendar items from is_runway_review so "...Spring 2027..." is not mistaken
    for a show review and capped under RUNWAY_CAP.

  FIX 3 — resilient feed fetch + visibility.
    FashionNetwork threw "not well-formed (invalid token)" on every fetch. Feeds
    are now fetched via requests (UA header) and stripped of illegal XML control
    chars before parsing; per-feed kept/seen counts are logged so a broken feed
    fails loudly instead of silently yielding nothing.

  FIX 4 — debug dump.
    With DEBUG_NEWSLETTER=1 (default on), prints per-source ingest counts and the
    industryNews headlines BEFORE limits, so cuts are auditable from the log.

  NOTE (tunable, unchanged): MAX_ITEMS["industryNews"]=10 with 32 classified is a
    tight funnel; raise it for more industry coverage per issue.
  NOTE: the FR edition (fr.fashionnetwork.com) is still not read — the US feed
    carried English equivalents of the missed items. Adding the FR feed needs
    fuzzy de-dup (current de-dup is exact title/link only); left out here.

CHANGES FROM v3.29 — Eileen Gu routing

THE BUG
  "EXCLUSIVE: Eileen Gu Signs as Giorgio Armani Global Makeup Ambassador"
  appeared in Industry News instead of Ambassador Announcements.

  Root cause: RSS_SOURCES registers "industry_news" BEFORE "ambassadors", so
  WWD and WWD Beauty items arrive tagged hint="industryNews". The v3.22
  campaign reroute only ran for hint in (partnerships, ambassadors, "") — an
  industryNews-hinted item was never offered to it. The reroute would have
  fired correctly; it was simply never consulted.

  (This also explains why Alisha Boe DID reach Ambassadors: her headline fails
  the industry-news classifier, so she fell through to the ambassadors path.
  Eileen Gu's headline passes industry news, so she was captured there first.)

THE FIX — looks_like_explicit_ambassador()
  industryNews-hinted items are now offered to the reroute, but through a
  STRICTER test than the general one. It requires an explicit ambassador /
  face-of / muse / spokesperson noun, AND rejects anything carrying an
  executive-role marker (as SVP/CEO/chief, promotes, to lead Americas,
  named inaugural, executive to lead).
  Verified against every item in the Jul 27 issue: 6/6 genuine ambassador
  signings move, 12/12 industry-news items stay put — including the tricky
  ones (Manolo Blahnik "Taps Chloe Executive", Skims "Promotes ... to Chief
  Communications Officer", LVMH "Promotes Yann Musquin").

POLISH — _clean_headline()
  Strips wire-service prefixes (EXCLUSIVE:, BREAKING:, FIRST LOOK:) from
  displayed headlines across all sections.

Also confirmed this run: the v3.29 ALO fix landed (broken "for Eyewear With
Star-Studded" fragment is gone, clean headline shown, duplicate collapsed).
Chloe/Mattel/Aerin Lauder items still present in this issue are already
blocked by v3.29, which had not yet been deployed when it ran.
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
# v3.31: Resilient feed fetching
# Some feeds (notably FashionNetwork) periodically emit XML containing illegal
# control characters that trip feedparser ("not well-formed (invalid token)"),
# which can truncate or empty the entry list. We fetch the bytes ourselves,
# strip illegal XML 1.0 control chars, then parse — with a feedparser-native
# fallback. This function never raises.
# =============================================================================
_XML_ILLEGAL_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')

def fetch_feed(url):
    """Return (feed, note). note is None on a clean fetch, else a short string."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; VIPNewsletterBot/3.31)"}
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        encoding = resp.encoding or resp.apparent_encoding or "utf-8"
        text = resp.content.decode(encoding, errors="replace")
        text = _XML_ILLEGAL_RE.sub("", text)
        feed = feedparser.parse(text)
        if getattr(feed, "bozo", 0) and not feed.entries:
            # sanitized parse still yielded nothing — try feedparser's own fetch
            fb = feedparser.parse(url)
            if fb.entries:
                return fb, "sanitized parse empty; used feedparser fallback"
            return feed, f"parse issue: {getattr(feed, 'bozo_exception', 'unknown')}"
        return feed, None
    except Exception as e:
        try:
            return feedparser.parse(url), f"requests failed ({e}); used feedparser fallback"
        except Exception as e2:
            return None, f"fetch failed: {e2}"

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
    "events": 30,  # v3.16: raised to 30 for richer World Cup / sports / music festival calendar
    "industryNews": 10,
    "ambassadors": 10,
    "partnerships": 12,
    "castings": 10,
    "filmProjects": 10,
    "tvProjects": 10,
    "inTheaters": 8,
    "newMusic": 8,
    "tvPremieres": 6,
}

# =============================================================================
# SOURCE → CATEGORY RESTRICTIONS  v3.11
# Each source may ONLY contribute to the listed categories.
# None means unrestricted (source feeds all categories via general logic).
# This is enforced before every classifier attempt, not just at fallback.
# =============================================================================
SOURCE_CATEGORY_RESTRICTIONS = {
    # Fashion/beauty sources — only ambassadors and partnerships
    "Hypebeast":          {"ambassadors", "partnerships"},
    "Hypebae":            {"ambassadors", "partnerships"},
    "POPSUGAR Fashion":   {"ambassadors", "partnerships"},
    "POPSUGAR Beauty":    {"ambassadors", "partnerships"},
    "Beauty Packaging":   {"ambassadors"},
    # v3.12: Contain Vogue France and Fashionista
    "Vogue France":       {"ambassadors", "partnerships"},
    "Fashionista":        {"ambassadors", "partnerships"},  # v3.16: industryNews removed — fallback path was still reaching it
    # v3.13: Vogue (US/international) had the same fallthrough issue
    "Vogue":              {"ambassadors", "partnerships"},  # v3.23: industryNews removed — nail/Airbnb/wedding lifestyle filler was leaking into industry news via fallback
    "WWD Beauty":         {"ambassadors", "partnerships", "industryNews"},  # v3.18
    # v3.14: New sources — each restricted to their intended categories
    "FashionWeekDaily":   {"ambassadors", "partnerships", "industryNews"},
    "The Impression":     {"ambassadors", "partnerships"},
    "British Vogue":      {"ambassadors", "partnerships", "industryNews"},
    "Vogue Italia":       {"ambassadors", "partnerships"},
    "Vogue España":       {"ambassadors", "partnerships"},
    "People":             {"ambassadors"},
    "Nylon":              {"industryNews", "castings"},
    "Complex Style":      {"partnerships", "ambassadors", "industryNews"},
    "THR Music":          {"industryNews"},
    # Entertainment sources — never ambassadors, partnerships, or industry news
    "Deadline":           {"castings", "filmProjects", "tvProjects"},
    "Deadline Casting":   {"castings", "filmProjects", "tvProjects"},
    "Deadline TV":        {"tvProjects", "castings"},
    "Deadline Film":      {"filmProjects", "castings"},
    "Variety Film":       {"filmProjects", "castings"},
    "Variety TV":         {"tvProjects", "castings"},
    "THR":                {"castings", "filmProjects", "tvProjects"},
    "THR TV":             {"tvProjects", "castings"},
    "THR Film":           {"filmProjects", "castings"},
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
# SEMI-STATIC ANNUAL EVENTS
# Update once per year when officially announced. Empty string = skip.
# These cover events with no reliable API AND events whose scrapers are
# unreliable (FHCM requires JS; Wikipedia API blocked on some runners).
# =============================================================================
SEMI_STATIC_EVENTS = {
    # ── SPORTS & MUSIC CALENDAR (v3.14 additions) ─────────────────────────────
    "Gotham TV Awards":              "2026-06-01",
    "Monaco Grand Prix":             "2026-06-05",
    "Governor's Ball":               "2026-06-05",
    "NBA Finals":                    "2026-06-04",
    "BET Awards":                    "2026-06-14",
    "Bonnaroo":                      "2026-06-11",
    "FIFA World Cup 2026":           "2026-06-11",  # Biggest luxury/music brand activation of 2026
    "Wimbledon":                     "2026-06-30",
    "FIFA World Cup Final":          "2026-07-19",
    # ── FASHION WEEKS ────────────────────────────────────────────────────────
    "Pitti Uomo":                    "2026-06-10",
    "Milan Men's Fashion Week":      "2026-06-19",
    "London Men's Fashion Week":     "2026-06-20",  # v3.14: re-added per KCD calendar
    "Paris Men's Fashion Week":      "2026-06-23",
    "Los Angeles Men's Fashion Week":"2026-06-24",  # v3.14: added per KCD calendar
    "Paris Haute Couture Week":      "2026-07-04",
    "Copenhagen Fashion Week":       "2026-08-03",
    "New York Fashion Week":         "2026-09-08",
    "London Fashion Week":           "2026-09-17",
    "Milan Fashion Week":            "2026-09-22",
    "Paris Fashion Week":            "2026-09-28",
    # ── FILM FESTIVALS ───────────────────────────────────────────────────────
    "Cannes Film Festival":          "2026-05-12",   # past — auto-filtered
    "Venice Film Festival":          "2026-08-27",
    "Toronto International Film Festival (TIFF)": "2026-09-03",
    "Tribeca Film Festival":         "2026-06-04",
    "Telluride Film Festival":       "2026-08-29",
    # ── LUXURY & INDUSTRY GALAS ──────────────────────────────────────────────
    "CFDA Fashion Awards":           "2026-06-09",
    "LACMA Art+Film Gala":           "2026-11-07",
    "British Fashion Awards":        "2026-12-07",
}

# =============================================================================
# LUXURY BRANDS — used for mid-market source filtering in Industry News
# =============================================================================
LUXURY_BRANDS = {
    "lvmh", "kering", "richemont", "hermès", "hermes", "chanel", "dior",
    "gucci", "prada", "louis vuitton", "cartier", "tiffany", "bulgari",
    "ysl", "saint laurent", "balenciaga", "bottega veneta", "valentino",
    "celine", "fendi", "givenchy", "loewe", "berluti", "rimowa",
    "loro piana", "emilio pucci", "moncler", "burberry", "versace",
    "armani", "dolce gabbana", "tod's", "ferragamo", "brunello cucinelli",
    "estée lauder", "estee lauder", "lancome", "jo malone", "la mer",
    "shiseido", "l'oréal", "loreal", "sephora", "tapestry", "capri holdings",
    "pvh", "ralph lauren", "amer sports", "on running", "salomon",
    "casablanca", "jacquemus", "marine serre", "courrèges", "courreges",
    "ami paris", "ami", "lemaire", "the row", "khaite", "toteme", "totême",
    "ladurée", "laduree", "zegna", "ermenegildo zegna", "thom browne",
    "marni", "jil sander", "acne studios", "isabel marant", "chloé", "chloe",
    "carven", "schiaparelli", "alaïa", "alaia", "balmain", "lanvin",
}

# =============================================================================
# v3.21 — HARD-NEWS TIER + PRESTIGE SCORING
# =============================================================================

# Phrases that mark a "hard" industry-news item: executive moves, M&A,
# corporate/financial events, store openings. These get the top reserved slots.
HARD_NEWS_SIGNALS = [
    # Executive / leadership moves
    "ceo", "chief executive", "deputy ceo", "co-ceo", "coo", "cfo",
    "creative director", "design director", "artistic director",
    "appointed", "appoints", "names as", "names a new", "named director",
    "steps down", "resigns", "joins as", "to lead", "to head",
    "president", "chairman", "chairwoman", "managing director",
    "people moves", "power moves", "daily media", "executive moves",
    "new hire", "names chief", "name chief", "named chief",
    # M&A / corporate / financial
    "acquires", "acquisition", "merger", "to merge", "buys", "acquired by",
    "ipo", "spin off", "spins off", "spinoff", "divests", "divestment",
    "stake in", "majority stake", "takeover", "bankruptcy", "chapter 11",
    "earnings", "revenue", "quarterly results", "fiscal", "valuation",
    "restructuring", "layoffs", "fundraise", "investment round",
    # Retail expansion
    "new store", "opens store", "opens its", "boutique", "flagship",
    "first store", "largest store", "new location", "opens flagship",
    "newest store", "third store", "second store", "store is", "opens a",
    # Exec appointment verbs (catch "Taps X as SVP")
    "taps", "as svp", "as evp", "as chief", "as vp of",
    # v3.27: M&A verb variants + house milestones (Chanel/Charvet was soft!)
    "acquiring", "to acquire", "buys", "acquires stake", "is acquiring",
    "sets date for", "first haute couture", "first couture",
    "met en vente", "vente aux enchères", "cède ses",
    # v3.29: promotions and C-suite titles
    "promotes", "promoted to", "elevates", "elevated to",
    "chief communications", "chief marketing", "chief brand", "chief creative",
    "cmo", "cco", "named inaugural", "to lead americas",
    # v3.25: appointments, exits, returns
    # v3.29: promotions and C-suite titles
    "promotes", "promoted to", "elevates", "elevated to",
    "chief communications", "chief marketing", "chief brand", "chief creative",
    "cmo", "cco", "named inaugural", "to lead americas",
    "confirms exit", "confirms departure", "exit of", "departure of",
    "new communications director", "new creative director", "global communications",
    "has a new", "to return to", "returns to", "leaves after", "after 13-year",
    # v3.23: Bankruptcy / insolvency (EN + FR) — Coperni receivership was missed
    "receivership", "redressement judiciaire", "liquidation judiciaire",
    "insolvency", "insolvent", "administration", "chapter 11", "chapter 7",
    "files for bankruptcy", "placed in receivership", "court protection",
    "creditor protection", "winding up", "en faillite", "cessation de paiement",
    # v3.23: Brand takeovers / activations / pop-ups / store events as hard-ish
    "takes over", "taking over", "pop-up store", "opens summer", "opens pop",
    "flagship store", "to open flagship", "opens a pop", "store takeover",
    "confirms show venue", "show venue", "permanent exhibition", "exhibition space",
    "content series", "new content", "milestone with capsule",
]

def classify_news_hardness(text_lower):
    """Return 'hard' if the item is an exec move / M&A / corporate event,
    else 'soft' (campaigns, events, exhibitions, lifestyle)."""
    for sig in HARD_NEWS_SIGNALS:
        if sig in text_lower:
            return "hard"
    return "soft"

def _phrase_in(text_lower, phrase):
    """Word-boundary containment test.

    v3.28: substring matching has caused a recurring class of bug in this
    codebase — "ami" matched inside "gAMIng", "icon" inside "XT-ICONS", "app"
    inside "chAPPell", "feature" inside "features". Every brand/entity match
    now goes through this helper instead of a bare `in` test.
    """
    import re as _re
    return _re.search(r"(?<![a-z0-9])" + _re.escape(phrase) + r"(?![a-z0-9])",
                      text_lower) is not None

def _any_phrase_in(text_lower, phrases):
    return any(_phrase_in(text_lower, p) for p in phrases)

def partnership_prestige(text_lower):
    """Score a partnership 0-3 by brand prestige for ranking.
    3 = luxury house, 2 = recognised mid-market, 0 = streetwear/unknown.
    v3.28: word-boundary matched — "ami" was matching inside "gaming"."""
    if _any_phrase_in(text_lower, LUXURY_BRANDS):
        return 3
    mid_market = [
        "nike", "adidas", "new balance", "puma", "reebok", "uniqlo", "gap",
        "levi's", "levis", "free people", "anthropologie", "h&m", "zara",
        "calvin klein", "tommy hilfiger", "lacoste", "diesel", "guess",
        "coach", "michael kors", "kate spade", "marc jacobs",
        # v3.26: automotive/sport/beauty houses that co-brand with fashion
        "alpine", "ferrari", "porsche", "bmw", "mercedes", "savage x fenty",
        "fenty", "rimowa", "moncler", "canada goose", "north face",
    ]
    if _any_phrase_in(text_lower, mid_market):
        return 2
    return 0

# v3.22 — Person-vs-brand routing for the ambassador/partnership boundary.
# A headline pairing a PERSON with a BRAND ("True Religion Linked Up With Zara
# Larsson") is an ambassadorship, not a brand-to-brand collaboration. We detect
# the celebrity-campaign shape so these items route to ambassadors.

# Verb phrases that, when joining a brand and a person, signal a campaign/
# ambassadorship rather than a product collaboration.
AMBASSADOR_VERB_PHRASES = [
    "linked up with", "links up with", "teams up with", "partners with",
    "taps", "recruited", "recruits", "enlists", "enlisted", "fronts",
    "stars in", "is the new face", "new face of", "campaign with",
    "campaign starring", "named the face", "named ambassador",
    # v3.25: removed bare "named","names","welcomes","celebrates","unveils",
    # "debuts","announces","collaborates with" — too generic, fired on product
    # launches and collection news (Salomon Unveils Collection, etc.)
]

# Markers that strongly indicate one side of a headline is a person (campaign
# star / musician / athlete) rather than a brand. v3.25: matched with WORD
# BOUNDARIES — substring matching caused "icon" to hit "XT-ICONS", "star" to
# hit "starter", "model" to hit "modeling", etc.
PERSON_MARKERS = [
    "pop princess", "pop star", "popstar", "singer", "rapper", "musician",
    "actor", "actress", "supermodel", "athlete",
    "olympian", "footballer", "songwriter", "performer", "it girl", "muse",
]

def _has_person_marker(text_lower):
    """Word-boundary match for person markers to avoid substring false hits."""
    import re as _re
    for m in PERSON_MARKERS:
        if _re.search(r'(?<![a-z])' + _re.escape(m) + r'(?![a-z])', text_lower):
            return True
    return False


# =============================================================================
# v3.28 — NEAR-DUPLICATE DETECTION
# Different outlets covering the same story with different headlines were each
# taking a slot (Johnny Depp Comic-Con x3, Batman Joker x2, Mugler campaign x2,
# Hailey Bieber/Miu Miu x2, Dungeon Crawler Carl x2, Zosia Mamet x2). That cost
# ~8 slots in one issue and pushed out real news. These helpers detect that the
# two headlines describe the SAME story.
# =============================================================================

_DEDUP_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "for", "with", "from", "into", "onto",
    "his", "her", "its", "their", "this", "that", "these", "those", "is", "are",
    "was", "were", "be", "been", "as", "at", "by", "in", "of", "on", "to", "up",
    "new", "latest", "first", "says", "said", "will", "has", "have", "had",
    "exclusive", "must", "read", "summary", "more", "than", "who", "what",
    "how", "why", "when", "where", "amid", "ahead", "after", "before", "over",
}

# Topic words: shared subject matter (used with a shared entity to spot same-story)
_DEDUP_TOPIC_WORDS = {
    "campaign", "collection", "ambassador", "collab", "collaboration",
    "capsule", "launch", "show", "trailer", "season", "cast", "casts",
    "joins", "appearance", "debut", "premiere", "partnership", "boutique",
    "acquires", "acquisition", "appointed", "names", "voice", "role",
    "leak", "leaks", "bootleg", "piracy",  # v3.29
}

# v3.29: generic domain vocabulary that must NOT count as a distinguishing
# entity. "star" + "season" caused two unrelated castings to merge
# ("BMF Star Da'Vinchi Joins Chicago Fire" vs "Star Trek: Starfleet Academy
# Season 2 Adds..."), which is why the castings section collapsed to 3 items.
_DEDUP_GENERIC = {
    "star", "stars", "season", "series", "show", "shows", "film", "films",
    "movie", "movies", "actor", "actress", "brand", "brands", "fashion",
    "beauty", "luxury", "style", "collection", "campaign", "video", "prime",
    "netflix", "hulu", "hbo", "amazon", "apple", "disney", "paramount",
    "exclusive", "official", "global", "world", "york", "paris", "london",
    "milan", "week", "spring", "summer", "fall", "winter", "autumn",
    "regular", "recurring", "joins", "adds", "sets", "part", "years",
}

def _dedup_stem(word):
    """v3.29: light suffix stripping so 'leaked'/'leaks' and 'names'/'named'
    collapse to the same token. Deliberately conservative — no real stemmer."""
    for suf in ("ing", "ed", "es", "s"):
        if len(word) > len(suf) + 3 and word.endswith(suf):
            return word[: -len(suf)]
    return word

def _dedup_tokens(text):
    """Significant lowercase tokens: punctuation stripped, stopwords dropped,
    light stemming applied (v3.29)."""
    import re as _re
    if not text:
        return set()
    t = _re.sub(r"[^a-z0-9\s-]", " ", text.lower())
    toks = {w.strip("-") for w in t.split() if len(w.strip("-")) >= 3}
    return {_dedup_stem(w) for w in toks if w not in _DEDUP_STOPWORDS}

def is_near_duplicate(text_a, text_b):
    """True if two headlines almost certainly describe the same story.

    Four rules, tuned against real duplicate clusters from live issues:
      1. Jaccard overlap >= 0.45 (broad rewording of the same headline)
      2. Containment >= 0.75 (one headline is essentially inside the other —
         catches short structured items like "Hailey Bieber for Miu Miu" vs a
         longer roundup headline mentioning the same story)
      3. Shared entity + shared topic word (two "Mugler ... campaign" stories in
         the same week are the same story)
      4. >= 3 shared long entity tokens (Johnny Depp / Ebenezer / Scrooge)
    """
    ta, tb = _dedup_tokens(text_a), _dedup_tokens(text_b)
    if not ta or not tb:
        return False
    inter = ta & tb
    if not inter:
        return False

    jaccard = len(inter) / len(ta | tb)
    containment = len(inter) / min(len(ta), len(tb))

    if jaccard >= 0.45:
        return True
    if containment >= 0.75 and min(len(ta), len(tb)) >= 2:
        return True

    shared_entities = {
        w for w in inter
        if w not in _DEDUP_TOPIC_WORDS
        and w not in _DEDUP_GENERIC
        and len(w) >= 4
    }
    shared_topics = inter & _DEDUP_TOPIC_WORDS
    if shared_entities and shared_topics and len(inter) >= 2:
        return True

    long_entities = {w for w in shared_entities if len(w) >= 5}
    if len(long_entities) >= 3:
        return True

    return False

def _dedup_text(item):
    """Best available text for comparing an item against another."""
    parts = [
        item.get("headline") or "",
        item.get("talent") or "",
        item.get("brand") or "",
        item.get("name") or "",
        item.get("project") or "",
    ]
    return " ".join(p for p in parts if p).strip()



# v3.30 — Explicit-ambassador test for industryNews-hinted items.
# WWD / WWD Beauty appear in the industry_news RSS list BEFORE the ambassadors
# list, so their items arrive hinted "industryNews" and were never offered to
# the campaign reroute. "EXCLUSIVE: Eileen Gu Signs as Giorgio Armani Global
# Makeup Ambassador" therefore landed in Industry News instead of Ambassadors.
# This test is deliberately STRICTER than looks_like_ambassador_campaign(): it
# requires an explicit ambassador/face/muse noun, so executive appointments
# ("Taps Chloe Executive to Lead Americas", "Promotes X to Chief Comms Officer")
# stay in Industry News where they belong.

_EXPLICIT_AMBASSADOR_NOUNS = [
    "brand ambassador", "global ambassador", "house ambassador",
    "global makeup ambassador", "beauty ambassador", "fragrance ambassador",
    "as ambassador", "an ambassador", "new ambassador", "first ambassador",
    "named the face", "new face of", "is the face of", "the new face",
    "brand muse", "house muse", "new muse", "spokesperson for",
    "viva glam", "campaign star",
]

# Executive/corporate roles: if present, this is industry news, not an
# ambassadorship — even when the word "ambassador" appears elsewhere.
_EXEC_ROLE_MARKERS = [
    "as svp", "as evp", "as vp", "as ceo", "as cfo", "as coo", "as cmo",
    "as president", "as chief", "as director of", "as head of",
    "as general manager", "as managing director", "to lead americas",
    "to lead emea", "to lead apac", "promotes", "promoted to",
    "chief communications", "chief marketing", "named inaugural",
    "executive to lead", "leadership roles",
]


def _clean_headline(title):
    """v3.30: strip wire-service prefixes from displayed headlines.
    'EXCLUSIVE: Eileen Gu Signs as...' reads better as 'Eileen Gu Signs as...'"""
    import re as _re
    if not title:
        return title
    return _re.sub(
        r"^\s*(?:EXCLUSIVE|EXCLUSIF|BREAKING|UPDATED|FIRST LOOK|SCOOP)\s*[:\-–]\s*",
        "", title, flags=_re.IGNORECASE
    ).strip()


def looks_like_explicit_ambassador(text_lower):
    """Strict test: an unambiguous brand-ambassador announcement."""
    if any(e in text_lower for e in _EXEC_ROLE_MARKERS):
        return False
    return any(n in text_lower for n in _EXPLICIT_AMBASSADOR_NOUNS)


def looks_like_ambassador_campaign(text_lower):
    """Return True if the headline looks like a brand pairing with a PERSON
    for a campaign/ambassadorship (route to ambassadors), rather than a
    brand-to-brand product collaboration (keep in partnerships).

    v3.24: Two ways to qualify:
    (a) a collaboration verb + an explicit person-marker word, OR
    (b) a strong endorsement/campaign phrase that is itself person-anchored
        ("stars in ... campaign", "unveils X's signature", "links up with",
        "first creative partner", "named the face of", etc.)
    Plus a HARD BLOCK list so tech/music/product/event items from Hypebeast
    never reroute (Nothing phone, playlists, watch launches, Hypebeast Cup)."""

    # Hard block — these are never ambassador campaigns even if they contain a
    # collaboration verb or "unveils". Catches the junk that polluted the section.
    AMBASSADOR_HARD_BLOCK = [
        "everything we loved", "everything we added", "playlist this week",
        "music this week", "new single", "drops new", "shares new single",
        "ram costs", "cmf phone", "shelves next", "supply chain",
        "watch with a", "presage", "dial", "chronograph", "timepiece",
        "hypebeast cup", "lands in singapore", "month-long program",
        "sneaker archive", "boot test", "gaming", "video game",
        "radio player", "streams curated", "mixtape", "print t-shirts",
        "t-shirts spotlight", "best world cup capsule",
        # v3.24.1: product/tech launches that used "unveils"
        "scent scanner", "ai-powered", "ai powered", "scanner on",
        "gadget", "wearable", "headphones", "earbuds",
        "smartphone", "phone as", "raises $", "million from",
        "for our next vintage",  # broken Vestiaire fragment
        # v3.25: runway shows, seasonal collections, product drops — not ambassadors
        "ss27", "ss26", "fw27", "fw26", "aw27", "aw26", "spring 2027",
        "fall 2027", "men's fashion week", "fashion week ss", "fashion week fw",
        "bridal collection", "ballet era", "rewrites the", "our favorite shows",
        "euro-summer", "everywhere right now", "xt-icons", "collection for ss",
        "punk identity", "first-ever bridal", "got you covered",
        # v3.26: gifts/watches are not ambassadorships
        "gifts", "bespoke watch", "tracks one million", "million hours",
        "gifted", "presented with a watch",
        # v3.29: brand product/collection launches are not ambassadorships
        "launches its first", "launches its own", "first-ever sunglasses",
        "first-ever collection", "unveils its first", "expands beyond",
        "enters the", "is an ode to", "ode to nature",
        # v3.28: roundup/digest headlines are not single ambassador items
        "must read:", "must-read:", "daily media:", "power moves:",
        "top stories", "what to know", "roundup",
        # v3.27: show attendance and interviews are not ambassadorships
        "attend ", "attends ", "attended ", "arrive at", "arrives at",
        "will accept is his own", "ordinary people suck",
        "revisits its roots", "trades neutrals", "turns up the heat with its",
        "fever dream",
    ]
    if any(b in text_lower for b in AMBASSADOR_HARD_BLOCK):
        return False

    # (b) Strong person-anchored endorsement phrases — qualify on their own.
    # v3.24.1: removed bare "unveils" and "signature sneaker"/"signature
    # collection" — they fired on every product launch (Jo Malone Scent Scanner).
    # "unveils" now only counts via the verb+person path below.
    STRONG_CAMPAIGN_PHRASES = [
        "stars in", "fronts", "is the new face", "new face of", "the face of",
        "named the face", "named ambassador", "global ambassador",
        "brand ambassador", "house ambassador", "becomes the face",
        "first creative partner", "creative partner in residence",
        "joins as", "campaign starring",
        # v3.28: "Taps Director/Photographer/Actor X for ... Campaign"
        "taps director", "taps rising star", "taps actor", "taps actress",
        "taps photographer", "taps model", "taps singer", "taps olympian",
        "as brand ambassador", "signs as", "as global makeup",
        "fronts the campaign", "is the face", "tapped as",
        # standalone endorsement nouns — safe because the hard-block above
        # already removed tech/music/product/event junk
        "ambassador", "viva glam", "brand muse", "new muse", "spokesperson",
    ]
    # v3.27: "campaign" is no longer a standalone trigger — it pulled in brand
    # campaign/product items with no person (NORMFORM, Miu Miu, UGG collections).
    # It now requires person evidence: a pronoun or a person-marker.
    import re as _re2
    has_campaign_person = (
        "campaign" in text_lower and (
            _re2.search(r"\b(his|her|she|he)\b", text_lower) is not None
            or _has_person_marker(text_lower)
        )
    )
    has_strong = any(p in text_lower for p in STRONG_CAMPAIGN_PHRASES)

    # v3.24.1: signature-product endorsements — "unveils X's signature sneaker",
    # "X's First-Ever Signature" — these are athlete/celebrity endorsements.
    # Requires the possessive "'s ... signature" shape so it doesn't fire on
    # generic product launches.
    import re as _re
    has_signature = bool(_re.search(r"'s\b[^.]{0,40}\bsignature\b", text_lower)) or \
                    ("signature" in text_lower and "first-ever" in text_lower) or \
                    ("signature" in text_lower and "first ever" in text_lower)

    # (a) verb + explicit person-marker
    has_verb = any(v in text_lower for v in AMBASSADOR_VERB_PHRASES)
    has_person = _has_person_marker(text_lower)

    if has_strong:
        return True
    if has_campaign_person:
        return True
    if has_signature:
        return True
    if has_verb and has_person:
        return True
    return False



# =============================================================================
# GEMINI AI RANKING & CURATION
# =============================================================================

class GeminiRanker:
    def __init__(self, api_key=None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        self.base_url = "https://generativelanguage.googleapis.com/v1beta/models"
        # v3.21: gemini-1.5-flash-latest was SHUT DOWN by Google — all 1.5 models
        # return 404 as of 2026, which surfaced as "no response" and killed all
        # ranking. Current free-tier options: gemini-2.5-flash, gemini-2.5-flash-lite,
        # gemini-3.5-flash. Flash-Lite chosen for higher RPM on many sequential calls.
        # Override with GEMINI_MODEL env var if needed.
        self.model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
        self._last_call_time = 0  # epoch seconds of last API call

    def is_available(self):
        return bool(self.api_key)

    def _call_gemini(self, prompt, retry=2):
        """Make a Gemini API call with rate limiting and retry on 429."""
        import time

        # Enforce minimum 4-second gap between calls (~15 RPM max)
        gap = time.time() - self._last_call_time
        if gap < 4:
            time.sleep(4 - gap)

        for attempt in range(retry + 1):
            self._last_call_time = time.time()
            try:
                response = requests.post(
                    # v3.21: x-goog-api-key header instead of ?key= query param.
                    # Google's current security guidance; ?key= keys without API
                    # restrictions are blocked starting June 19, 2026.
                    f"{self.base_url}/{self.model}:generateContent",
                    headers={
                        "Content-Type": "application/json",
                        "x-goog-api-key": self.api_key,
                    },
                    json={
                        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                        "generationConfig": {
                            "temperature": 0.1,
                            "responseMimeType": "application/json"
                        }
                    },
                    timeout=60
                )
                if response.status_code == 429:
                    wait = 30 * (attempt + 1)
                    print(f"   ⚠️ Gemini 429 (quota), waiting {wait}s before retry {attempt + 1}/{retry}...")
                    time.sleep(wait)
                    continue
                if response.status_code == 404:
                    # Model not found — log clearly rather than silent "no response"
                    print(f"   ❌ Gemini 404: model '{self.model}' not found. "
                          f"Set GEMINI_MODEL to a current model (e.g. gemini-2.5-flash).")
                    return response
                return response
            except Exception as e:
                if attempt < retry:
                    time.sleep(10)
                    continue
                print(f"   ⚠️ Gemini request exception: {e}")
                raise
        return None  # all retries exhausted

    # v3.17: Source quality tiers for fallback ordering when Gemini fails.
    # Ensures WWD/BoF/FashionNetwork items surface before Hypebeast/Hypebae
    # when the ranking API returns an error.
    _SOURCE_TIER = {
        "WWD": 1, "BoF": 1, "FashionNetwork": 1, "FashionWeekDaily": 1,
        "British Vogue": 1, "Vogue Italia": 1, "Vogue España": 1,
        "The Impression": 2, "Drapers": 2, "Complex Style": 2,
        "THR Music": 2, "Nylon": 2, "People": 2,
        "Vogue": 3, "Vogue France": 3, "Fashionista": 3,
        "Hypebeast": 4, "Hypebae": 4, "POPSUGAR Fashion": 4, "POPSUGAR Beauty": 4,
        "Beauty Packaging": 4,
    }

    def _quality_sort(self, items):
        """Sort items by source quality tier, then by original order within tier."""
        return sorted(
            enumerate(items),
            key=lambda x: (self._SOURCE_TIER.get(x[1].get("source", ""), 3), x[0])
        )

    def rank(self, items, category, top_n=10):
        if not self.api_key:
            print(f"   ⚠️ No GEMINI_API_KEY set, using quality-sorted default for {category}")
            return [item for _, item in self._quality_sort(items)][:top_n]

        if len(items) <= top_n:
            return items

        prompt = self._build_prompt(items, category, top_n)

        try:
            response = self._call_gemini(prompt)

            if response is None or response.status_code != 200:
                code = response.status_code if response else "no response"
                print(f"   ⚠️ Gemini API error {code} — using quality-sorted fallback for {category}")
                return [item for _, item in self._quality_sort(items)][:top_n]

            result = response.json()

            if 'candidates' not in result or not result['candidates']:
                print(f"   ⚠️ Gemini no candidates for {category} — using quality-sorted fallback")
                return [item for _, item in self._quality_sort(items)][:top_n]

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
    Uses gemini_ranker._call_gemini for consistent rate limiting and retry.
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
        response = gemini_ranker._call_gemini(prompt)

        if response is None or response.status_code != 200:
            code = response.status_code if response else "no response"
            print(f"   ⚠️ Gemini {category_context} selection error {code}")
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
# AUTOMATED EVENTS FETCHER — v3.9
# Sources: TVmaze (award shows), Wikipedia API (film festivals),
#          official fashion body pages with proximity extraction (fashion weeks),
#          SEMI_STATIC_EVENTS dict (CFDA/Pitti/LACMA/BFA), TMDB (film premieres)
# =============================================================================

class EventsFetcher:
    """
    Queries multiple live authoritative sources for upcoming industry events.
    All sources fail silently — a failed fetch never crashes the newsletter.

    Key v3.9 changes vs v3.8:
    - _add() now enforces the date window (critical bug fix)
    - Lookahead extended to 270 days (captures full awards cycle)
    - Film festivals use Wikipedia API instead of homepage scraping
    - Fashion weeks use proximity-based extraction (no more May 30 for LFW)
    - FashionUnited scraper removed (was a false-positive source)
    - SEMI_STATIC_EVENTS provides reliable dates for non-API events
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
    ]

    # Wikipedia search queries for film festivals
    _WIKI_FESTIVALS = [
        ("2026 Cannes Film Festival",                    "Cannes Film Festival"),
        ("2026 Venice International Film Festival",      "Venice Film Festival"),
        ("2026 Toronto International Film Festival",     "Toronto International Film Festival (TIFF)"),
        ("2026 Tribeca Film Festival",                   "Tribeca Film Festival"),
        ("2026 Sundance Film Festival",                  "Sundance Film Festival"),
        ("Telluride Film Festival 2026",                 "Telluride Film Festival"),
        ("2026 Berlin International Film Festival",      "Berlinale"),
    ]

    # Official fashion body pages with season-specific proximity keywords.
    # Dates are only accepted if they appear within 300 chars of one of these keywords.
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
        self.lookahead = self.now + timedelta(days=270)  # 9 months — captures full awards cycle
        self.tmdb_api_key = tmdb_api_key
        self._seen = set()

    # ── PUBLIC ────────────────────────────────────────────────────────────────

    def fetch_all(self):
        """Return events sorted chronologically within the date window."""
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

    # ── PRIVATE HELPERS ───────────────────────────────────────────────────────

    def _get(self, url, timeout=12):
        """HTTP GET with browser UA. Returns text or None."""
        try:
            r = requests.get(url, timeout=timeout, headers={"User-Agent": self._UA})
            return r.text if r.status_code == 200 else None
        except Exception as e:
            print(f"      ⚠️ GET {url[:60]}: {e}")
            return None

    def _extract_dates(self, raw):
        """Extract datetimes from HTML or text. Returns sorted list within window."""
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
        """Add event only if within date window and not already seen.
        v3.9 FIX: this check was missing in v3.8, causing out-of-window dates to appear.
        """
        if not (self.cutoff <= dt <= self.lookahead):  # CRITICAL FIX
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

    # ── SOURCE FETCHERS ────────────────────────────────────────────────────────

    def _fetch_award_shows(self):
        """Query TVmaze for next broadcast date of each major award show."""
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
        """Fetch fashion week dates using proximity-based extraction.
        Only accepts dates that appear near season-specific keywords,
        preventing false positives from unrelated page content (e.g. press deadlines).
        """
        events = []
        fetched_urls = {}  # Cache: url → page text (FHCM is fetched 3 times otherwise)

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
                break  # Only the earliest valid date per source entry

        return events

    def _fetch_film_festivals_wiki(self):
        """Fetch film festival dates from the Wikipedia API.
        Wikipedia intro text reliably contains "held from X to Y, YEAR" in the
        first paragraph — far more stable than scraping festival homepages which
        contain many unrelated dates (deadlines, sub-programs, etc.).
        """
        events = []
        year = str(self.now.year)

        for search_q, display_name in self._WIKI_FESTIVALS:
            try:
                # Step 1: Search Wikipedia
                r = requests.get(
                    "https://en.wikipedia.org/w/api.php",
                    params={
                        "action": "query",
                        "list": "search",
                        "srsearch": search_q,
                        "format": "json",
                        "srlimit": 5,
                    },
                    timeout=10,
                )
                if r.status_code != 200:
                    continue

                results = r.json().get("query", {}).get("search", [])
                if not results:
                    continue

                # Prefer result with current year in title
                best = next(
                    (res for res in results if year in res.get("title", "")),
                    results[0],
                )

                # Step 2: Fetch intro extract (plain text — no HTML to parse)
                r2 = requests.get(
                    "https://en.wikipedia.org/w/api.php",
                    params={
                        "action": "query",
                        "titles": best["title"],
                        "prop": "extracts",
                        "exintro": True,
                        "explaintext": True,
                        "format": "json",
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
                        break  # First valid date only
                    break

            except Exception as e:
                print(f"      ⚠️ Wikipedia '{display_name}': {e}")

        return events

    def _fetch_semi_static(self):
        """Return manually-confirmed dates for events with no reliable API.
        Update SEMI_STATIC_EVENTS once per year when dates are officially announced.
        Past dates are filtered by _add(). Empty strings are skipped.
        """
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
        """Add major upcoming TMDB films as premiere events (popularity ≥ 100)."""
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
    """Re-sort events by calendar date after Gemini reorders them by relevance."""
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
    """Build the events calendar from live authoritative sources."""
    fetcher = EventsFetcher(tmdb_api_key=TMDB_API_KEY)
    all_events = fetcher.fetch_all()

    if not all_events:
        print("   ⚠️ No events found from any live source — check connectivity")
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

    # v3.11: Re-sort chronologically — Gemini returns by relevance, not date
    return _sort_events_chronologically(selected)


# =============================================================================
# RSS FEED SOURCES
# =============================================================================

RSS_SOURCES = {
    "industry_news": [
        {"url": "https://wwd.com/feed", "source": "WWD"},
        {"url": "https://wwd.com/beauty-industry-news/feed", "source": "WWD Beauty"},  # v3.18
        {"url": "https://www.businessoffashion.com/arc/outbound-feeds/rss/", "source": "BoF"},
        # v3.15: Fashionista removed — jobs board pollutes industry news
        # v3.15: Vogue removed — webinars/travel guides/collection reviews
        {"url": "https://drapersonline.com/feed", "source": "Drapers"},
        {"url": "https://fashionweekdaily.com/feed/", "source": "FashionWeekDaily"},
        {"url": "https://www.hollywoodreporter.com/t/music/feed/", "source": "THR Music"},
        {"url": "https://nylon.com/entertainment/feed/", "source": "Nylon"},
        {"url": "https://complex.com/style/rss", "source": "Complex Style"},
        # v3.15: FashionNetwork — major European fashion trade source
        {"url": "https://us.fashionnetwork.com/rss/", "source": "FashionNetwork"},
    ],
    "casting": [
        {"url": "https://deadline.com/feed/", "source": "Deadline"},
        {"url": "https://deadline.com/category/casting/feed/", "source": "Deadline Casting"},
        {"url": "https://variety.com/v/film/feed", "source": "Variety Film"},
        {"url": "https://www.hollywoodreporter.com/feed", "source": "THR"},
        # v3.14: rising talent coverage
        {"url": "https://nylon.com/entertainment/feed/", "source": "Nylon"},
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
        {"url": "https://wwd.com/beauty-industry-news/feed", "source": "WWD Beauty"},  # v3.18: celebrity beauty ambassadors
        {"url": "https://fashionista.com/.rss/excerpt/", "source": "Fashionista"},
        {"url": "https://hypebeast.com/feed", "source": "Hypebeast"},
        {"url": "https://hypebae.com/feed", "source": "Hypebae"},
        {"url": "https://www.popsugar.com/fashion/feed", "source": "POPSUGAR Fashion"},
        {"url": "https://www.popsugar.com/beauty/feed", "source": "POPSUGAR Beauty"},
        {"url": "https://www.vogue.com/feed/rss", "source": "Vogue"},
        {"url": "https://www.vogue.fr/feed/rss", "source": "Vogue France"},
        {"url": "https://www.beautypackaging.com/rss", "source": "Beauty Packaging"},
        # v3.14: new ambassador coverage sources
        {"url": "https://fashionweekdaily.com/feed/", "source": "FashionWeekDaily"},
        {"url": "https://theimpression.com/feed/", "source": "The Impression"},
        {"url": "https://www.vogue.co.uk/feed/rss", "source": "British Vogue"},
        {"url": "https://www.vogue.it/rss/", "source": "Vogue Italia"},
        {"url": "https://people.com/tag/celebrity-style/feed/", "source": "People"},
        # v3.15: FashionNetwork for European ambassador coverage
        {"url": "https://us.fashionnetwork.com/rss/", "source": "FashionNetwork"},
    ],
    "partnerships": [
        {"url": "https://wwd.com/feed", "source": "WWD"},
        {"url": "https://www.businessoffashion.com/arc/outbound-feeds/rss/", "source": "BoF"},
        {"url": "https://fashionista.com/.rss/excerpt/", "source": "Fashionista"},
        {"url": "https://www.vogue.com/feed/rss", "source": "Vogue"},
        # v3.14: European and sports-fashion collab coverage
        {"url": "https://fashionweekdaily.com/feed/", "source": "FashionWeekDaily"},
        {"url": "https://theimpression.com/feed/", "source": "The Impression"},
        {"url": "https://www.vogue.co.uk/feed/rss", "source": "British Vogue"},
        {"url": "https://complex.com/style/rss", "source": "Complex Style"},
        # v3.15: FashionNetwork for European partnership coverage
        {"url": "https://us.fashionnetwork.com/rss/", "source": "FashionNetwork"},
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
            # v3.21: standalone terms so "Is X's New Viva Glam Ambassador" scores
            "ambassador", "the face of", "new face", "viva glam",
            "is the face", "joins as global", "as global brand",
        ],
        "exclude": [
            "rumored", "speculated", "might", "could", "reportedly considering",
            "allegedly", "reportedly in talks", "eying", "circling",
            "nominations", "nominees", "awards", "wins", "won", "accepts",
            "interview", "profile", "q&a", "talks about", "reflects on",
            "review", "recap", "best dressed", "makeup", "hair",
            "festival", "premiere", "screening", "gala", "ceremony",
            # Talent agency signings — not brand ambassador deals
            "talent agency", "signs with agency", "management", "representation",
            "agent", "caa", "wme", "uta", "icm", "vmg",
            "kensington grey", "anonymous content", "paradigm", "endeavor",
            "signs with talent", "talent management", "signs with rep",
            "new representation", "joins roster",
            # v3.21: Executive appointments are industry news, NOT ambassadors.
            # "Taps X as SVP/CEO/Director" must route to industry news, not here.
            "as svp", "as evp", "as vp", "as ceo", "as cfo", "as coo",
            "as president", "as chief", "as director", "as head of",
            "as general manager", "as managing director", "as global head",
            "as creative director", "as design director", "as senior vice",
            "deputy ceo", "names ceo", "appointed ceo", "joins as chief",
            "fragrance cluster", "to drive growth", "to lead", "names chief",
            # v3.28: roundup/digest headlines
            "must read:", "must-read:", "daily media:", "power moves:",
            # v3.27: brand collection launches with campaign imagery are not
            # ambassadorships (UGG Dusty Orchid, Miu Miu denim, Kulani Kinis)
            "trades neutrals", "revisits its roots", "turns up the heat with its",
            "denim selection", "with the dusty",
            "its first-ever", "fever dream",
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
            # v3.20.1: phrases that also appear in must_have must score here too
            "partner on", "partners with", "partner with", "collaborates with",
            "collaborated on", "collaborated with", "collaborate on",  # v3.26
            "collab with", "links up with", "link up with", "limited-edition",
            "return to fashion with", "with free people", "capsule",
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
            # v3.12: Low-relevance sports/skateboarding collaborations
            "skateboarding", "skate", "afa world cup", "world cup skateboarding",
            "snowboarding", "surfboarding",
            # v3.13: Netflix/streaming content guides misclassified as partnerships
            "coming to netflix", "new on netflix", "upcoming shows",
            "streaming guide", "new shows on", "new series on",
            "what's streaming", "everything coming to", "new movies on",
            # v3.20: Non-partnership content that leaks from Hypebeast
            "journey from", "explores the", "everything we loved",
            "music this week", "is touring", "enters gaming", "video game",
            "anthology", "album", "prints", "exhibition", "documentary",
            "celebrates the dialogue", "quietly celebrates",
            "rooted in legacy", "reimagines the", "abstract artist",
            "punk rocker", "suburbia", "dream house", "furniture designs",
        ],
        # v3.20: must_have requires a genuine collaboration phrase. Broadened in
        # v3.20.1 to catch real WWD/Fashionista headline formats like
        # "X With Free People", "X, Y Partner on", "X Partner on Capsule".
        "must_have": ["capsule collection", "limited-edition", "limited edition",
                      "collaboration with", "collaborates with", "collab with",
                      "teams up with", "joins forces with", "partnership with",
                      "partners with", "partner on", "partner with",
                      "co-branded", "co-designed", "return to fashion with",
                      "makes his return", "makes her return", " with free people",
                      "capsule", " x ", "teams up", "links up with", "link up with",
                      "collaborated on", "collaborated with"],  # v3.26
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
            # v3.9: Distribution deals
            "buys rights", "acquires rights", "picks up rights",
            "distribution rights", "north american rights", "scoops up",
            "paramount buys", "netflix buys", "buys starring",
            # v3.9: Film festival industry programs
            "joins panel", "joins jury", "joins program", "joins industry",
            "docfest", "film festival program", "industry program",
            # v3.9: Where-to-watch guides
            "where to watch", "how to watch", "when to watch",
            "streaming on", "now streaming", "available on",
            # v3.11: Production features, fan speculation, music playlists
            "artisans brought", "brought to life", "how they made",
            "easter eggs", "fans' belief", "heighten fans",
            "added to our", "new music friday", "music friday drops",
            "we added to our playlist",
            # v3.13: Persistent low-relevance shows that keep appearing
            "virgin river", "baywatch",
            # v3.16: Love island was only in filmProjects exclude, not castings
            "love island",
            # v3.16: TV ratings / viewership data articles
            "tv ratings", "viewing record", "premiere week viewing",
            "multi-year high", "million minutes", "billion minutes",
            # v3.16: Awards race / nominations (not a casting announcement)
            "nominated on sunday", "egot", "fastest streamer to",
            "poised to become",
            # v3.13: Premiere date announcements are not castings
            "sets series premiere", "premiere date on", "sets premiere date",
            # v3.20: Corporate / guild / political news — not castings or talent projects
            "dga deal", "dga board", "directors guild", "amptp", "guild deal",
            "kennedy center", "trump", "justice department", "justice dept",
            "regulatory", "antitrust", "attorneys general", "lawsuit",
            "takeover", "warner bros. discovery", "warner bros. merger",
            "paramount skydance", "paramount-warner", "paramount buys",
            "merger greenlit", "billion merger", "billion takeover",
            "merger cleared", "greenlit by", "skydance",
            "health plan", "residual", "four-year deal", "4-year deal",
            "tax incentive", "pr executive", "launches agenda", "veteran pr",
            "most watched shows", "ratings for the season",
        ],
        "must_have": ["cast", "star", "role", "play", "joins", "direct", "produce"],
    },
    "filmProjects": {
        "keywords": [
            "movie", "film", "feature film", "biopic", "sequel", "prequel",
            "franchise", "adaptation", "remake", "reboot", "spinoff",
            "in production", "wraps production", "starts filming",
            "begins shoot", "wraps shoot", "in theaters", "release date",
            "greenlit", "picked up", "ordered", "in development",
        ],
        "must_have": ["film", "movie", "feature film", "biopic", "sequel", "prequel",
                      "franchise", "adaptation", "remake", "reboot", "spinoff",
                      "production", "filming", "shoot", "theaters", "release",
                      "greenlit", "development"],
        "exclude": [
            "review", "recap", "critics", "rating", "box office prediction",
            "interview", "profile", "q&a", "talks about", "reflects on",
            "makeup", "hair", "beauty look", "outfit", "dress", "wore",
            "festival", "premiere", "screening", "gala", "ceremony",
            "cast in", "joins cast", "to star", "lands role", "to play",
            # Acquisition/market deals
            "closes deal", "acquires", "lands rights", "picks up rights",
            "distribution deal", "market deal", "cannes market",
            # v3.12: Obituaries and tributes
            "dies at", "passed away", "in memoriam", "pays tribute",
            "deeply saddened", "mourning the death", "former wife",
            # v3.12: Editorial lists and retrospectives
            "5 best", "10 best", "best movies", "best performances",
            "best films", "retrospective", "ranked", "greatest of all time",
            # v3.12: Logistics, policy, retail noise
            "warehouse", "film tax credits", "governor candidates",
            "uncapped credits", "film credits bill", "supply chain",
            # v3.12: Reality TV / non-VIP content
            "love island", "baywatch",
            # v3.13: Interview/profile articles misclassified as film projects
            "looking forward to", "is looking forward", "is terrified",
            "refused to use", "produced by conference", "comparing the",
            "could do what", "low-budget horror", "horror boom",
            "visited london", "dug through", "archive", "war museum",
            # v3.20: Corporate / guild / political news — not castings or talent projects
            "dga deal", "dga board", "directors guild", "amptp", "guild deal",
            "kennedy center", "trump", "justice department", "justice dept",
            "regulatory", "antitrust", "attorneys general", "lawsuit",
            "takeover", "warner bros. discovery", "warner bros. merger",
            "paramount skydance", "paramount-warner", "paramount buys",
            "merger greenlit", "billion merger", "billion takeover",
            "merger cleared", "greenlit by", "skydance",
            "health plan", "residual", "four-year deal", "4-year deal",
            "tax incentive", "pr executive", "launches agenda", "veteran pr",
            "most watched shows", "ratings for the season",
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
            # v3.6.1: Awards and endings
            "awards", "winners", "full list", "nominations", "nominees",
            "bids farewell", "concludes", "final episode", "series finale",
            "ending", "canceled", "cancelled",
            # v3.12: Streaming guides and obituary tributes
            "where to watch", "where to stream", "how to watch",
            # v3.16: TV ratings / viewership data articles not relevant as TV projects
            "tv ratings", "viewing record", "multi-year high", "million minutes",
            "love island",
            "now streaming on", "available to stream",
            "pays tribute", "in memoriam", "deeply saddened",
            # v3.20: Corporate / guild / political news — not castings or talent projects
            "dga deal", "dga board", "directors guild", "amptp", "guild deal",
            "kennedy center", "trump", "justice department", "justice dept",
            "regulatory", "antitrust", "attorneys general", "lawsuit",
            "takeover", "warner bros. discovery", "warner bros. merger",
            "paramount skydance", "paramount-warner", "paramount buys",
            "merger greenlit", "billion merger", "billion takeover",
            "merger cleared", "greenlit by", "skydance",
            "health plan", "residual", "four-year deal", "4-year deal",
            "tax incentive", "pr executive", "launches agenda", "veteran pr",
            "most watched shows", "ratings for the season",
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

        self._ingest_counts = {}  # v3.31: per (source → category) kept/seen for debug dump
        for rss_category, sources in RSS_SOURCES.items():
            target_category = CATEGORY_MAP.get(rss_category, rss_category)
            for source in sources:
                try:
                    feed, note = fetch_feed(source['url'])
                    if feed is None:
                        failed.append(f"{source['source']}: {note}")
                        print(f"   → {source['source']} ({rss_category} → {target_category})  ❌ {note}")
                        continue

                    seen = len(feed.entries)
                    kept = 0
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
                        kept += 1

                    self._ingest_counts[f"{source['source']} → {target_category}"] = (kept, seen)
                    zero = "  ⚠️ 0 entries returned" if seen == 0 else ""
                    note_str = f"  ⚠️ {note}" if note else ""
                    print(f"   → {source['source']} ({rss_category} → {target_category})  "
                          f"{kept}/{seen} kept{zero}{note_str}")

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
            source = item.get('source', '')

            # v3.22: Person-vs-brand routing. A headline pairing a brand with a
            # PERSON for a campaign ("True Religion Linked Up With Zara Larsson")
            # is an ambassadorship, not a brand-to-brand collab. When detected,
            # route directly to ambassadors (authoritative — the ambassador
            # classifier's rigid keyword scoring would otherwise reject the
            # campaign phrasing). Source must be allowed to contribute ambassadors.
            # v3.22.1: Person-vs-brand campaign reroute. Fire whenever the item
            # looks like a brand+person campaign AND it's a fashion/beauty item
            # (hint is ambassadors or partnerships, or unset). Previously this
            # only checked hint=="partnerships", but Hypebae items are hinted
            # "ambassadors" first (that feed is listed before partnerships), so
            # the True Religion x Zara Larsson item slipped through to partnerships
            # via the classifier fallback. Now hint-independent for fashion items.
            _campaign_reroute = False
            if hint in ("partnerships", "ambassadors", "") and looks_like_ambassador_campaign(text):
                _amb_allowed = SOURCE_CATEGORY_RESTRICTIONS.get(source, None)
                if _amb_allowed is None or "ambassadors" in _amb_allowed:
                    _campaign_reroute = True
            # v3.30: industryNews-hinted items (WWD / WWD Beauty, whose feeds are
            # registered under industry_news first) get a STRICTER test so genuine
            # ambassador signings reach Ambassadors while exec moves do not.
            elif hint == "industryNews" and looks_like_explicit_ambassador(text):
                _amb_allowed = SOURCE_CATEGORY_RESTRICTIONS.get(source, None)
                if _amb_allowed is None or "ambassadors" in _amb_allowed:
                    _campaign_reroute = True

            # v3.11: Source containment — check allowed categories for this source
            allowed_cats = SOURCE_CATEGORY_RESTRICTIONS.get(source, None)
            # None means unrestricted; a set means only those categories are allowed

            if title and title in self._seen_titles:
                continue
            if link and link in self._seen_links:
                continue

            # v3.22: Apply the campaign reroute before normal classification
            if _campaign_reroute:
                formatted = self._format_item(item, "ambassadors")
                if formatted and not self._is_duplicate(formatted, "ambassadors"):
                    self.categorized["ambassadors"].append(formatted)
                    if link: self._seen_links.add(link)
                    if title: self._seen_titles.add(title)
                    continue

            if hint in CLASSIFIERS:
                rules = CLASSIFIERS[hint]
                # Only attempt if source is allowed to contribute to this category
                if (allowed_cats is None or hint in allowed_cats):
                    if self._passes_classifier(text, rules):
                        formatted = self._format_item(item, hint)
                        if formatted and not self._is_duplicate(formatted, hint):
                            self.categorized[hint].append(formatted)
                            if link: self._seen_links.add(link)
                            if title: self._seen_titles.add(title)
                            assigned = True

            if not assigned:
                for category, rules in CLASSIFIERS.items():
                    if category == hint:
                        continue
                    # v3.11: Skip if source is not allowed for this category
                    if allowed_cats is not None and category not in allowed_cats:
                        continue
                    if self._passes_classifier(text, rules):
                        formatted = self._format_item(item, category)
                        if formatted and not self._is_duplicate(formatted, category):
                            self.categorized[category].append(formatted)
                            if link: self._seen_links.add(link)
                            if title: self._seen_titles.add(title)
                            assigned = True
                            break

            if not assigned:
                # v3.11: Specialist sources are discarded rather than pushed to industry news
                SPECIALIST_SOURCES = {
                    "Hypebeast", "Hypebae",
                    "Deadline", "Deadline Casting", "Deadline TV", "Deadline Film",
                    "Variety Film", "Variety TV",
                    "THR", "THR TV", "THR Film",
                }
                if source in SPECIALIST_SOURCES:
                    continue

                # v3.15 CRITICAL FIX: SOURCE_CATEGORY_RESTRICTIONS was not enforced
                # at the fallback stage — restricted sources were bypassing containment
                # and landing in industry_news (Vogue France bijoux, Vogue webinars, etc.)
                fallback_allowed = SOURCE_CATEGORY_RESTRICTIONS.get(source)
                if fallback_allowed is not None and "industryNews" not in fallback_allowed:
                    continue

                formatted = self._format_industry_news(item)
                if formatted and not self._is_duplicate(formatted, "industryNews"):
                    self.categorized["industryNews"].append(formatted)

        print("\n📊 CATEGORIZATION RESULTS (before limits):")
        for cat, items in self.categorized.items():
            print(f"   • {cat}: {len(items)} items")

        # v3.31: DEBUG dump — makes ingestion and cuts auditable from the run log.
        if os.getenv("DEBUG_NEWSLETTER", "1") == "1":
            print("\n🔎 DEBUG: per-feed kept/seen (after date cutoff):")
            for key, (kept, seen) in sorted(getattr(self, "_ingest_counts", {}).items()):
                mark = "  ⚠️ 0 kept" if kept == 0 else ""
                print(f"      {key}: {kept}/{seen}{mark}")
            inews = self.categorized.get("industryNews", [])
            print(f"\n🔎 DEBUG: industryNews headlines BEFORE limits ({len(inews)}):")
            for it in inews:
                tag = "CAL " if it.get("is_calendar") else it.get("hardness", "?")
                print(f"      [{tag:4}] {it.get('source','')}: {it.get('headline','')[:110]}")

        self._apply_limits()

        print("\n📊 FINAL RESULTS (after limits + Gemini ranking):")
        for cat, items in self.categorized.items():
            print(f"   • {cat}: {len(items)} items")

        return self

    def _passes_classifier(self, text, rules):
        """
        v3.12: Exclude list uses word-boundary regex matching via lookbehind/
        lookahead to prevent short keywords firing inside longer words
        (e.g. "feature" in "features", "film" in "fulfilled").
        Keywords and must_have keep substring matching for intentional breadth
        (e.g. "series" matching "series premiere").
        Exclude check runs FIRST to short-circuit as early as possible.
        """
        keywords  = rules.get('keywords', [])
        exclude   = rules.get('exclude', [])
        must_have = rules.get('must_have', [])

        # Exclude: word-boundary match — rejects if ANY exclude phrase found
        for exc in exclude:
            pattern = r'(?<![a-z])' + re.escape(exc.lower()) + r'(?![a-z])'
            if re.search(pattern, text):
                return False

        # Must-have: substring match — rejects if NONE present
        if must_have and not any(mh.lower() in text for mh in must_have):
            return False

        # Keyword scoring: substring match
        score = sum(1 for kw in keywords if kw.lower() in text)
        return score >= 1

    def _format_industry_news(self, item):
        text_lower = (f"{item['title']} {item['summary']}").lower()

        strong_signals = [
            "acquires", "merger", "acquisition", "ipo", "earnings", "revenue",
            "appointed", "names as", "names a new", "ceo", "creative director",
            "steps down", "resigns", "president", "chairman", "chairwoman",
            "launches", "expansion", "store opening", "flagship", "partnership",
            "sustainability", "carbon neutral", "diversity", "inclusion",
            "lvmh", "kering", "richemont", "hermès", "chanel", "dior",
            "l'oréal", "estée lauder", "shiseido", "coty",
            "sales", "growth", "profit", "billion", "million", "market",
            "forecast", "report", "study", "survey",
            # v3.14: World Cup, sports crossovers, Cannes industry news
            "world cup", "formula 1", "f1 race", "grand prix", "halftime show",
            "cannes jury", "palme d'or", "trophée chopard", "nba finals", "wimbledon",
            "digital", "e-commerce", "metaverse", "nft", "ai",
            "manufacturing", "retail", "wholesale",
            "quarterly", "annual", "fiscal",
            "investment", "valuation", "bankruptcy", "restructuring", "layoffs", "hiring",
            # v3.19: Corporate moves — spin-offs, deputy/division roles
            "spin off", "spins off", "spinoff", "spun off", "divests", "divestment",
            "deputy ceo", "deputy chief", "coo", "cfo", "co-ceo", "managing director",
            "design director", "named design", "names design", "head of design",
            "joins as", "appoints", "to lead", "to head", "named director",
            # v3.19: Retail expansion
            "new store", "opens store", "opens its", "boutique", "opens flagship",
            "store opens", "unveils store", "first store", "largest store",
            "place des victoires", "madison avenue", "new location",
            # v3.19: People-moves roundup signals (Drapers/BoF/FWD style)
            "people moves", "power moves", "daily media", "executive moves",
            "creative director cheat",
            # v3.23: Bankruptcy / insolvency (EN + FR)
            "receivership", "redressement judiciaire", "liquidation judiciaire",
            "insolvency", "insolvent", "court protection", "creditor protection",
            "en faillite", "cessation de paiement", "winding up",
            # v3.23: Brand activations, pop-ups, takeovers, exhibitions — these are
            # the European luxury brand-marketing moves the daily briefings flag
            "takes over", "taking over", "pop-up store", "pop up store",
            "opens summer", "opens a pop", "store takeover", "takeover of",
            "show venue", "confirms show", "permanent exhibition", "exhibition space",
            "exhibition", "retrospective at", "archives",  # v3.27: museum/exhibition news
            "content series", "milestone with", "woolmark prize", "opens up to",
            "switches gears", "new gig", "creative director at large",
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
            # v3.8: retail logistics
            "supply chain", "consolidation network", "distribution center",
            "first-mile", "prepaid", "supplier access", "logistics",
            # v3.8: recurring data columns
            "trend tracker", "data tracker", "beauty tracker", "spate tracking",
            # v3.9: French language product signals
            "crayons à lèvres", "maquillage", "make-up pour", "trésors make-up",
            "avant-première pour vogue", "prochains trésors", "à lèvres",
            "prochaines créations", "nouvelle création",
            # v3.9: music content misclassified as industry news
            "star-studded", "inspired album", "film's netflix debut",
            "drops album", "album drops", "announces album",
            # v3.9: sports-fashion without luxury brand
            "french open fashion", "roland-garros", "wimbledon",
            # v3.9: where-to-watch guides
            "where to watch", "how to watch", "now streaming",
            # v3.13: Job listing articles — "hiring" is a strong signal but bare
            # job postings are not industry news for a VIP newsletter
            "job offer", "now hiring", "is hiring a ", "open position",
            "position available", "apply now", "applications open",
            "join our team", "career opportunity", "send your cv",
            "we are looking for", "job listing", "job posting",
            # v3.16: Celebrity shoe / event attendance (WWD red carpet noise)
            "cap-toe", "pumps at", "luncheon", "steps out in", "opts for a",
            "mule trend", "metallic gold pair",
            # v3.16: Logistics / trucking (WWD supply chain noise)
            "slowing trucks", "fuel costs are", "conserving diesel", "reduces speeds",
            # v3.16: Education institution layoffs (not luxury industry)
            "the new school", "reduces staff by", "school has laid off", "enrollment down",
            # v3.16: Artist interview signals
            "moves on from the", "i said everything i have",
            "dyes her hair", "ditches her", "hair for summer",
            "summer trends", "spring trends", "street style",
            "most-requested", "leveled up", "levels up",
            # v3.10: streaming guides / content calendars
            "coming to netflix", "leaving netflix", "everything coming to",
            "everything leaving", "what's coming to", "what's leaving",
            "new on netflix", "new on hbo", "new on hulu",
            "new on prime", "new on disney",
            # v3.10: French interview signals
            "se confie", "se livre", "parle de", "nous révèle", "nous raconte",
            "son rapport", "sa vie", "son parcours",
            # v3.10: art/cultural events not relevant to luxury industry
            "photography exhibition", "art exhibition", "dual exhibition",
            "unveils dual", "photo exhibition", "gallery opens",
        ]

        # v3.15: HARD EXCLUDES — these are absolute blocks regardless of other scores
        # Must be checked BEFORE the weak/strong signal scoring loop
        hard_excludes = [
            "register now", "live webinar", "webinar", "sign up for our",
            "is hiring a ", "is hiring an ", "we are hiring",
            "apply at:", "apply here:", "submit your",
            # v3.20: reality TV and body-image/weight content (unwanted + sensitive)
            "love island", "plus-size model", "celebrity wasting",
            "ozempic", "weight loss", "wasting syndrome", "slim-downs",
            # v3.29: celebrity footwear/outfit style pieces
            "peep-toe", "mules", "pumps after", "wore black", "glossy heels",
            "in patent", "heels after", "pointed dior",
            # v3.27: political/celebrity gossip
            "presidential compliment", "trump praised", "rose garden",
            "— on her looks", "on her looks",
            # v3.26: celebrity red-carpet / wedding gossip — not industry news
            "rehearsal dinner", "wedding:", "'s wedding", "glitters in diamonds",
            "guest list rumors", "dress predictions", "reception details",
            "guests arrive", "spotted en route", "down low", "wedding weekend",
            "engagement ring", "bachelorette", "honeymoon",
            # v3.23: Vogue lifestyle filler that pads industry news
            "best airbnbs", "airbnb", "nail designs", "nail art", "manicure",
            "sunscreens for", "best sunscreen", "oily skin", "cozy cabins",
            "celebrated their wedding", "wedding with sunset", "their wedding",
            "best hotels", "where to stay", "travel guide", "best beaches",
            "natural nail", "skincare routine", "summer trends",
        ]
        for he in hard_excludes:
            if he in text_lower:
                return None
        # Articles from these sources that mention no luxury brand AND are not
        # M&A/regulatory are likely mass-market retail and should be excluded.
        MID_MARKET_SOURCES = {"Drapers"}  # v3.15: WWD removed — it's a primary luxury trade source
        REGULATORY_SIGNALS = {"acquires", "merger", "acquisition", "ipo", "fined",
                               "fine", "regulation", "lawsuit", "ruling", "sanction"}

        if item.get("source") in MID_MARKET_SOURCES:
            has_luxury = _any_phrase_in(text_lower, LUXURY_BRANDS)
            has_regulatory = any(sig in text_lower for sig in REGULATORY_SIGNALS)
            if not has_luxury and not has_regulatory:
                return None

        # NEW v3.6.1: Exclude round-up articles
        roundup_signals = [
            "must read", "must-read", "weekly round-up", "daily round-up",
            "morning digest", "evening digest", "news roundup", "headlines",
            "what to know", "what you missed", "catch up", "this week in",
        ]
        # v3.19: But KEEP executive-move roundups — they are high-value industry news
        exec_roundup_exceptions = [
            "people moves", "power moves", "daily media", "executive moves",
            "creative director", "named ceo", "appoints", "design director",
        ]
        is_exec_roundup = any(e in text_lower for e in exec_roundup_exceptions)
        if any(rp in text_lower for rp in roundup_signals) and not is_exec_roundup:
            return None

        # v3.23: tier1 sources expanded to include the European trade sources.
        # Vogue removed (it's no longer in industry_news RSS and is lifestyle-heavy).
        tier1_sources = ["WWD", "WWD Beauty", "BoF", "Business of Fashion",
                         "FashionNetwork", "FashionWeekDaily", "Drapers"]
        is_tier1 = item.get('source') in tier1_sources

        # v3.23: CRITICAL signals pass on a single hit regardless of source tier.
        # Bankruptcy, major appointments, M&A, and store openings are too important
        # to require two signals (Coperni receivership was missed for this reason).
        CRITICAL_SIGNALS = [
            "receivership", "redressement judiciaire", "liquidation judiciaire",
            "insolvency", "bankruptcy", "en faillite", "chapter 11",
            "acquires", "acquisition", "merger", "ipo",
            "acquiring", "to acquire", "buys", "is acquiring",
            "sets date for", "first haute couture", "first couture",
            "named ceo", "names as", "appointed", "creative director",
            "steps down", "spin off", "spins off",
            "opens flagship", "new store", "takes over", "pop-up store",
            # v3.25: appointment/exit/return phrasings that were missed
            # v3.29: promotions and C-suite titles (headline alone must suffice)
            "promotes", "promoted to", "elevates", "elevated to",
            "chief communications", "chief marketing", "chief brand",
            "chief creative", "cmo", "cco", "named inaugural", "to lead americas",
            "confirms exit", "confirms departure", "exit of", "departure of",
            "to return to", "returns to", "has a new", "new global",
            "new communications director", "new creative director",
            "global communications", "to lead", "to head", "named to lead",
            "steps down as", "leaves after", "after 13-year", "year run",
            "joins london", "join the official",
        ]
        has_critical = any(c in text_lower for c in CRITICAL_SIGNALS)

        # v3.31: Fashion-week CALENDAR / schedule announcements had no strong-signal
        # vocabulary and were discarded (Paris & Milan provisional calendars named
        # only Valentino/Margiela/Prada, which aren't strong signals). For tier-1
        # trade sources these now pass on a single hit. They are also flagged so
        # is_runway_review() won't treat them as show reviews and cap them.
        CALENDAR_SIGNALS = [
            "fashion week", "official calendar", "provisional calendar",
            "provisional schedule", "official schedule", "show schedule",
            "runway calendar", "show calendar", "womenswear calendar",
            "menswear calendar", "couture calendar", "fashion week schedule",
            "fashion week lineup", "fashion week line-up", "provisional lineup",
            "unveils calendar", "unveils schedule", "unveils its calendar",
            "reveals calendar", "reveals schedule", "reveals lineup",
            "opens the week", "closes the week", "opens fashion week",
            "back on the calendar", "return to the calendar", "returns to the calendar",
            "official lineup", "sets the calendar", "line-up unveiled",
        ]
        has_calendar = any(sig in text_lower for sig in CALENDAR_SIGNALS)

        relevance_score = sum(1 for signal in strong_signals if signal in text_lower)
        weakness_score = sum(1 for signal in weak_signals if signal in text_lower)

        if has_critical and weakness_score < 3:
            return self._format_item(item, "industryNews")
        # v3.31: calendar announcements from tier-1 trade sources pass on one hit
        if has_calendar and is_tier1 and weakness_score < 3:
            it = self._format_item(item, "industryNews")
            if it is not None:
                it["is_calendar"] = True   # exempt from runway cap downstream
            return it
        if (relevance_score >= 2 or (is_tier1 and relevance_score >= 1)) and weakness_score < 2:
            return self._format_item(item, "industryNews")
        return None

    def _apply_limits(self):
        for category, max_count in MAX_ITEMS.items():
            items = self.categorized[category]

            if category in ("events", "inTheaters", "newMusic", "tvPremieres"):
                self.categorized[category] = items[:max_count]
                continue

            # v3.21: Industry news hard-news tier — reserve top slots for
            # appointments / M&A / corporate events.
            if category == "industryNews":
                self.categorized[category] = self._apply_industry_news_tier(items, max_count)
                continue

            # v3.21: Partnerships — sort by prestige before ranking so luxury
            # collabs are preferred over streetwear when slots are limited.
            if category == "partnerships":
                items = sorted(items, key=lambda it: -it.get("prestige", 0))

            if len(items) > max_count:
                self.categorized[category] = self.gemini.rank(items, category, max_count)
            elif len(items) > 1 and self.gemini.is_available():
                self.categorized[category] = self.gemini.rank(items, category, len(items))
            else:
                self.categorized[category] = items[:max_count]

    def _apply_industry_news_tier(self, items, max_count):
        """v3.21: Prioritise hard news (exec moves, M&A, corporate) for the
        industry-news section, keeping a few slots for soft/variety.
        v3.25: cap runway-show reviews so they don't flood the soft pool.
        v3.31: FIX — the old HARD_QUOTA=4 was a CEILING, not a floor. hard[:4]
        + soft[:6] filled every slot and the leftover-hard backfill never ran,
        dropping 12 of 16 hard items (incl. the Chanel CFO appointment). Now
        hard news fills FIRST and we hold at most SOFT_RESERVE slots for soft."""
        SOFT_RESERVE = 3   # v3.31: max soft items when hard news is plentiful (tune here)
        RUNWAY_CAP = 3     # max runway-review items among the soft slots

        import re as _re
        def is_runway_review(it):
            # v3.31: calendar/schedule announcements are NOT show reviews, even
            # though they mention "Spring 2027" etc. — keep them out of the cap.
            if it.get("is_calendar"):
                return False
            t = (it.get("headline","") + " " + it.get("detail","")).lower()
            # "Men's Spring 2027", "Spring 2027:", "Fall 2026", "Couture Fall"
            if _re.search(r"(men'?s |women'?s )?(spring|fall|autumn|winter|resort|couture|pre-fall)\s+20\d\d", t):
                return True
            return False

        hard = [it for it in items if it.get("hardness") == "hard"]
        soft = [it for it in items if it.get("hardness") != "hard"]

        # v3.25: split soft into runway reviews and real soft news; cap runway
        soft_runway = [it for it in soft if is_runway_review(it)]
        soft_real = [it for it in soft if not is_runway_review(it)]

        # Rank each pool. Gemini.rank gracefully falls back to quality-sort.
        if len(hard) > 1 and self.gemini.is_available():
            hard = self.gemini.rank(hard, "industryNews", len(hard))
        if len(soft_real) > 1 and self.gemini.is_available():
            soft_real = self.gemini.rank(soft_real, "industryNews", len(soft_real))
        if len(soft_runway) > 1 and self.gemini.is_available():
            soft_runway = self.gemini.rank(soft_runway, "industryNews", len(soft_runway))

        # Build soft pool: real news first, then a capped number of runway reviews
        soft_combined = soft_real + soft_runway[:RUNWAY_CAP]

        # v3.31: Fill hard-FIRST. Reserve at most SOFT_RESERVE slots for soft
        # (only when soft exists); give the rest to hard; then backfill any gap
        # (soft short -> more hard, hard short -> more soft).
        soft_slots = min(SOFT_RESERVE, len(soft_combined))
        hard_slots = max_count - soft_slots

        result = hard[:hard_slots]
        result += soft_combined[:max_count - len(result)]
        if len(result) < max_count:                       # soft ran short -> more hard
            used_hard = min(hard_slots, len(hard))
            result += hard[used_hard: used_hard + (max_count - len(result))]
        result = result[:max_count]

        n_hard = sum(1 for it in result if it.get("hardness") == "hard")
        n_runway = sum(1 for it in result if is_runway_review(it))
        print(f"   📰 Industry news tier: {n_hard} hard + {len(result) - n_hard} soft "
              f"({n_runway} runway) from {len(hard)} hard / {len(soft)} soft available")
        return result

    def _format_item(self, item, category):
        # v3.30: strip wire prefixes ("EXCLUSIVE:") from what we display
        if item.get("title"):
            item = dict(item)
            item["title"] = _clean_headline(item["title"])
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
            # v3.21: tag hardness so the hard-news tier can reserve top slots
            hardness = classify_news_hardness((title + " " + summary).lower())
            return {"headline": title, "detail": summary, "link": link,
                    "source": source, "hardness": hardness}

        elif category == "ambassadors":
            # v3.18: Try structured talent+brand extraction first (clean "Talent for Brand"),
            # but fall back to the headline rather than discarding the item.
            result = self._extract_ambassador(title, link, source)
            if result and result.get("brand") and self._valid_ambassador_brand(result.get("brand")):
                result["headline"] = title  # keep headline available as fallback display
                return result
            # Headline fallback — section no longer empties when patterns don't match,
            # and avoids broken fragments like "Belmont Cameli for the Newest"
            return {"talent": "", "brand": "", "headline": title,
                    "detail": summary, "link": link, "source": source}

        elif category == "partnerships":
            # v3.18: Headline-based. Brand x Brand extraction produced nonsense,
            # so we display the article headline. Structured partners are still
            # extracted opportunistically for the plain-text digest version.
            result = self._extract_partnership(title, link, source)
            partner1 = result.get("partner1", "") if result else ""
            partner2 = result.get("partner2", "") if result else ""
            # v3.21: prestige score so luxury collabs rank above streetwear
            text_l = (title + " " + summary).lower()
            prestige = partnership_prestige(text_l)
            # v3.26: PRESTIGE FLOOR — reject prestige-0 partnerships (unknown /
            # streetwear / merch brands) UNLESS they involve a person (celebrity
            # collab like Rihanna/Savage X Fenty) or a notable cultural brand.
            # This removes Pokémon x G-SHOCK, SAN SAN GEAR, Abbey Road football
            # shirts, Wimbledon strawberry apparel, LEGO x F1, etc.
            if prestige == 0:
                has_person = _has_person_marker(text_l)
                # A recognised celebrity name also qualifies.
                NOTABLE_PEOPLE = [
                    "rihanna", "beyonce", "beyoncé", "zendaya", "hailey bieber",
                    "kim kardashian", "kylie", "khy", "travis scott", "pharrell",
                    "drake", "bad bunny", "dua lipa", "rosalia", "rosalía",
                    "a$ap", "asap rocky", "billie eilish", "taylor swift",
                    "kacey musgraves", "frank ocean", "tyler, the creator",
                    # v3.28
                    "olivia rodrigo", "sabrina carpenter", "chappell roan",
                    "charli xcx", "doja cat", "sza", "lisa", "jennie", "rose",
                    "jisoo", "jungkook", "jung kook", "stray kids", "newjeans",
                    "timothee", "timothée", "jacob elordi", "paul mescal",
                    "emma chamberlain", "kendall jenner", "gigi hadid",
                    "bella hadid", "kaia gerber", "hunter schafer",
                ]
                has_notable = any(p in text_l for p in NOTABLE_PEOPLE)
                # v3.27: tier-1 trade sources are editorially pre-filtered — a
                # prestige-0 collab they cover is usually newsworthy. Only apply
                # the hard floor to the high-volume streetwear feeds.
                TIER1_PARTNER_SOURCES = {
                    "WWD", "WWD Beauty", "BoF", "Business of Fashion",
                    "FashionNetwork", "FashionWeekDaily", "British Vogue", "Drapers",
                }
                is_tier1_src = item.get("source") in TIER1_PARTNER_SOURCES
                # Merch/product junk — v3.27: WORD-BOUNDARY matched (bare "watch"
                # was killing any item containing the word; "sneaker" too broad)
                import re as _re3
                MERCH_BLOCK = [
                    "g-shock", "gshock", "minicar", "lego",
                    # v3.28: tech/gaming hardware
                    "laptop", "rtx", "gpu", "gaming laptop", "collector's laptop",
                    "graphics card", "console", "motherboard",
                    "football shirt", "strawberry", "apparel collection",
                    "returning to theaters", "imax", "gacha", "keychain",
                    "figurine", "plush", "trading card", "video game",
                    "wristwatch", "timepiece",
                ]
                is_merch = any(
                    _re3.search(r"(?<![a-z])" + _re3.escape(m) + r"(?![a-z])", text_l)
                    for m in MERCH_BLOCK
                )
                if is_merch:
                    return None
                if not (has_person or has_notable or is_tier1_src):
                    return None  # reject low-relevance streetwear partnership
            # v3.28: single-brand PRODUCT LAUNCHES are not partnerships, at any
            # prestige level. "Victoria's Secret Is Making Underwire Feel Like No
            # Wire" / "Savage X Fenty Is Making Lace" are product news.
            SOLO_LAUNCH_MARKERS = [
                "is making", "is expanding its", "expanding its everyday",
                "is introducing", "introduces its", "adds to its",
                "latest addition to its", "new addition to its",
                "lineup with the launch", "is launching its own",
            ]
            if any(m in text_l for m in SOLO_LAUNCH_MARKERS):
                # only reject if there's no second party (no collab connector).
                # v3.28: strip brand names that themselves contain "x" first —
                # "Savage X Fenty Is Making Lace" was reading its own brand name
                # as a collaboration connector and escaping the filter.
                connector_text = text_l
                for _brandx in ("savage x fenty", "a x s", "off-white c/o"):
                    connector_text = connector_text.replace(_brandx, " ")
                COLLAB_CONNECTORS = [
                    " x ", " with ", "teams up", "teamed up", "partners with",
                    "partnered with", "collaborat", "joins forces", "tapped",
                ]
                if not any(c in connector_text for c in COLLAB_CONNECTORS):
                    return None
            return {"headline": title, "detail": summary, "link": link,
                    "source": source, "partner1": partner1, "partner2": partner2,
                    "prestige": prestige}

        elif category in ["castings", "filmProjects", "tvProjects", "inTheaters", "newMusic"]:
            return {"talent": title, "project": summary[:120] + "..." if len(summary) > 120 else summary, "link": link, "source": source}

        return None

    def _valid_ambassador_brand(self, brand):
        """v3.21: Reject junk brand fragments from failed extraction.
        v3.26: also reject brands ending in descriptive words ("... Latest
        Sun-Soaked", "... New Campaign") which are truncated extractions."""
        if not brand or len(brand.strip()) < 2:
            return False
        b = brand.strip().lower()
        junk_fragments = {
            "the newest", "the latest", "the new", "a new", "its latest",
            "its newest", "the next", "a brand", "the brand", "the first",
            "new", "newest", "latest", "the", "a", "an", "its", "her", "his",
            "this", "that", "their", "newest face", "new face",
        }
        if b in junk_fragments:
            return False
        # Reject if it starts with an article and is short (likely a fragment)
        if b.split()[0] in {"the", "a", "an", "its", "her", "his"} and len(b.split()) <= 2:
            return False
        # v3.29: a brand name never contains a connector/preposition. These are
        # truncated extractions: "Eyewear With Star-Studded", "for Our Next".
        connector_words = {"with", "for", "and", "from", "into", "onto", "plus",
                           "featuring", "starring", "amid", "after", "before"}
        if any(w in connector_words for w in b.split()):
            return False
        # v3.29: generic product categories are not brand names
        generic_categories = {
            "eyewear", "sunglasses", "makeup", "beauty", "fragrance", "perfume",
            "handbag", "handbags", "footwear", "apparel", "clothing", "denim",
            "global makeup", "skincare", "haircare", "jewelry", "jewellery",
            "activewear", "swimwear", "lingerie", "accessories",
        }
        if b in generic_categories:
            return False
        # v3.26: reject brands containing/ending in descriptive filler — these are
        # truncated extractions like "Oceanus' Latest Sun-Soaked", not brand names
        descriptive_tail = [
            "latest", "newest", "sun-soaked", "new campaign", "hot new",
            "dreamy", "summer", "collection", "capsule", "drop", "'s new",
            "'s latest", "'s hot", "'s dreamy",
        ]
        if any(d in b for d in descriptive_tail):
            return False
        # Reject possessive fragments (brand name should not contain apostrophe-s
        # followed by more words — that's the article continuing)
        if "'s " in b or "' " in b or b.endswith("'"):
            return False
        return True

    def _extract_ambassador(self, title, link, source):
        patterns = [
            # Pattern 1: Talent-first with extended connectors
            r'^([A-Z][a-zA-Z\s\.\'\'\-]+?)(?:\s+(?:stuns for|poses for|dazzles for|appears for|for|is|becomes|named|tapped as|tapped by|signs with|joins|fronts|stars in|is the new face of|is the face of))\s+([A-Z][a-zA-Z\s&\'\-]+?)(?:\s+(?:as|brand|ambassador|face|muse|spokesperson|campaign|global|house|beauty|fashion|\'s|$))',
            # Pattern 2: Brand-first with extended verbs
            r'^([A-Z][a-zA-Z\s&\'\-]+?)(?:\s+(?:taps|names|signs|welcomes|casts|chooses|picks|appoints|debuts|launches|unveils|presents))\s+([A-Z][a-zA-Z\s\.\'\'\-]+?)(?:\s+(?:as|for|to|its|global|house|brand|beauty|fashion|ambassador|face|muse|spokesperson|$))',
            r'^([A-Z][a-zA-Z\s\.\'\'\-]+?)(?:\s+(?:teams up with|collaborates with|partners with))\s+([A-Z][a-zA-Z\s&\'\-]+?)(?:\s+(?:for|on|to|as|\'s|$))',
            r'^([A-Z][a-zA-Z\s\.\'\'\-]+?)\s+(?:stars in|featured in|appears in|fronts)\s+([A-Z][a-zA-Z\s&\'\-]+?)(?:\'s\b|\s+(?:new|latest|global|house|brand))',
            r'^([A-Z][a-zA-Z\s\.\'\'\-]+?)\s+is\s+([A-Z][a-zA-Z\s&\'\-]+?)\'s\s+(?:newest|latest|new|global)',
            r'^([A-Z][a-zA-Z\s\.\'\'\-]+?)\s+(?:joins|becomes)\s+([A-Z][a-zA-Z\s&\'\-]+?)\s+(?:as|\'s)\s+(?:new|newest|latest|global)',
            # Pattern 7 (v3.14): Brand-first campaign debut
            r'^([A-Z][a-zA-Z\s&\'\-]+?)\s+(?:debuts|unveils|launches|releases)\s+(?:new\s+)?([A-Z][a-zA-Z\s\.\'\'\-]{3,30}?)\s+(?:campaign|fragrance|ad|editorial|film)',
        ]

        for pattern in patterns:
            match = re.search(pattern, title, re.IGNORECASE)
            if match:
                g1, g2 = match.group(1).strip(), match.group(2).strip()

                # v3.9: Clean brand name — strip possessives and generic qualifiers
                for grp_idx, grp in enumerate([g1, g2]):
                    cleaned = re.sub(
                        r"'s\s+(?:New|Latest|Newest|First|Global|House|Brand|Campaign|Collection).*$",
                        "", grp, flags=re.IGNORECASE,
                    ).strip()
                    if grp_idx == 0:
                        g1 = cleaned
                    else:
                        g2 = cleaned

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
                    # v3.9: sports-luxury crossover brands
                    'salomon', 'on running', 'hoka', 'new balance', 'under armour',
                    'stone island', 'patagonia', "arc'teryx", 'arcteryx',
                    'swarovski', 'pandora', 'chopard', 'van cleef',
                    'rimowa', 'tumi', 'montblanc', 'moncler', 'brunello cucinelli',
                    # v3.14: new brands from competitive gap analysis
                    'nothing', 'anua', 'laneige', 'calzedonia', 'oakley', 'revlon',
                    'boggi milano', 'casablanca', 'laduree', 'ladurée',
                    'michelob ultra', 'surfside', 'jameson', 'motorola',
                    'kidsuper', 'cecilie bahnsen', 'temperley london',
                    'erewhon', 'diesel', 'mugler', 'chloe', 'chloé',
                    'ferragamo', 'zegna', 'calvin klein', 'tommy hilfiger',
                    'hugo boss', 'boss', 'ted baker', 'reiss',
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
            ', ',   # v3.17: comma-space — "Marine Serre, Under Armour Partner on..."
        ]

        for sep in separators:
            if sep in title:
                parts = title.split(sep, 1)
                p1 = parts[0].strip()
                p2 = parts[1].strip()

                # v3.17: also strip "Partner/Partners/Team" from end of p2
                p2 = re.sub(
                    r'\s+(?:partner|partners|team|for|on|to|launch|collection|'
                    r'collaboration|partnership|drop|capsule|limited edition).*$',
                    '', p2, flags=re.IGNORECASE
                )
                p1 = re.sub(r'^(?:New|Exclusive|First Look|Inside|Weekly Drop Watch:|In pictures:)\s+', '', p1, flags=re.IGNORECASE)

                # v3.15: Reject if partner names are sentence-length (subtitle capture)
                # "It's Better Than the First" has 5 words — not a brand name
                if len(p1.split()) > 5 or len(p2.split()) > 5:
                    continue

                if len(p1) > 2 and len(p2) > 2 and len(p1) < 60 and len(p2) < 60:
                    return {"partner1": p1, "partner2": p2, "link": link, "source": source}

        and_match = re.search(r'^([A-Z][a-zA-Z\s&\'\-]+?)\s+(?:and|&)\s+([A-Z][a-zA-Z\s&\'\-]+?)(?:\s|$)', title)
        if and_match:
            return {"partner1": and_match.group(1).strip(), "partner2": and_match.group(2).strip(), "link": link, "source": source}

        return None

    def _is_duplicate(self, item, category):
        # v3.25: normalize headline/title for robust dedup (case, whitespace,
        # trailing punctuation) — the same article arriving from two feeds with
        # minor formatting differences was appearing twice (Burberry/Leeds).
        def _norm(s):
            if not s:
                return ""
            return " ".join(s.lower().split()).rstrip(".:;,!?")

        item_head = _norm(item.get('headline'))
        item_talent = _norm(item.get('talent'))
        item_name = _norm(item.get('name'))

        # v3.28: text used for near-duplicate comparison
        item_text = _dedup_text(item)

        # Check within the target category
        existing = self.categorized.get(category, [])
        for e in existing:
            if item_head and _norm(e.get('headline')) == item_head:
                return True
            if item_talent and _norm(e.get('talent')) == item_talent:
                return True
            if item_name and _norm(e.get('name')) == item_name:
                return True
            if item.get('partner1') and e.get('partner1') == item.get('partner1'):
                return True
            # v3.28: NEAR-duplicate — same story, different outlet/headline
            if item_text and is_near_duplicate(item_text, _dedup_text(e)):
                return True

        # v3.27/v3.28: cross-category dedup between ambassadors and industryNews —
        # the same article (SJP/Alba at Fendi, Eileen Gu/Armani) was appearing in
        # both sections. Now uses near-duplicate matching too.
        if category in ("ambassadors", "industryNews", "partnerships"):
            others = {"ambassadors", "industryNews", "partnerships"} - {category}
            for other in others:
                for e in self.categorized.get(other, []):
                    if item_head and _norm(e.get('headline')) == item_head:
                        return True
                    if item_text and is_near_duplicate(item_text, _dedup_text(e)):
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
                    # v3.28: near-duplicate across castings/film/tv
                    # (Zosia Mamet appeared in both castings and TV)
                    if item_text and is_near_duplicate(item_text, _dedup_text(e)):
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
        # v3.18: use clean "Talent for Brand" when available, else headline
        if item.get('talent') and item.get('brand'):
            lines.append(f"• {item['talent']} for {item['brand']}")
        else:
            lines.append(f"• {item.get('headline', '')}")

    lines.extend(["", "PARTNERSHIPS:", "-" * 30])
    for item in data.get('partnerships', []):
        # v3.18: headline-based
        lines.append(f"• {item.get('headline', '')}")

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
    print("VIP & INDUSTRY NEWS - AUTOMATED NEWSLETTER SYSTEM v3.31")
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
