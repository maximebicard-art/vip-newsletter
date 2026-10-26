#!/usr/bin/env python3
"""
VIP Newsletter — Configuration & Pure Logic
v4.0: Extracted from monolithic v3.36. All constants and stateless helpers.
"""
import os
import re
import html as _html
from urllib.parse import quote_plus

# =============================================================================
# v4.0 SECURITY: HTML escape helper
# RSS titles/summaries are untrusted external input. We escape them at ingestion
# so no feed can inject markup into the newsletter.
# =============================================================================
def escape_rss(text):
    if not text:
        return ""
    return _html.escape(text)

# =============================================================================
# v3.31/v3.32: Resilient feed fetching
# =============================================================================
_XML_ILLEGAL_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')
_BARE_AMP_RE = re.compile(r'&(?!#\d+;|#x[0-9A-Fa-f]+;|[A-Za-z][A-Za-z0-9]*;)')
_BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")


def _sanitize_xml(text):
    text = _XML_ILLEGAL_RE.sub("", text)
    text = _BARE_AMP_RE.sub("&amp;", text)
    return text


class _Entry(dict):
    """dict that also supports attribute access, so recovered items behave like
    feedparser entries for the rest of the pipeline."""
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)


class _RecoveredFeed:
    def __init__(self, entries):
        self.entries = entries
        self.bozo = 0


def _recover_items_from_xml(text):
    """Last-resort: regex-extract <item>/<entry> blocks."""
    from email.utils import parsedate_to_datetime as _parsedate
    entries = []
    for _tag, block in re.findall(r'<(item|entry)\b[^>]*>(.*?)</\1>',
                                  text, re.DOTALL | re.IGNORECASE):
        def grab(field):
            m = re.search(rf'<{field}\b[^>]*>(.*?)</{field}>',
                          block, re.DOTALL | re.IGNORECASE)
            if not m:
                return ""
            val = m.group(1).strip()
            cd = re.match(r'^<!\[CDATA\[(.*?)\]\]>$', val, re.DOTALL)
            if cd:
                val = cd.group(1).strip()
            return _html.unescape(val)
        title = grab("title")
        link = grab("link")
        if not link:
            m = re.search(r'<link\b[^>]*href=["\']([^"\']+)["\']', block, re.IGNORECASE)
            if m:
                link = m.group(1).strip()
        summary = grab("description") or grab("summary") or grab("content")
        if not (title or link):
            continue
        e = _Entry(title=title, link=link, summary=summary, description=summary)
        pub = grab("pubDate") or grab("published") or grab("updated")
        if pub:
            try:
                e["published_parsed"] = _parsedate(pub).timetuple()
            except Exception:
                pass
        entries.append(e)
    return entries


def fetch_feed(url):
    """Return (feed, note). Tries requests+sanitize, feedparser-native, and regex
    recovery; returns whichever yields the MOST entries."""
    import requests
    import feedparser
    candidates = []
    raw_text = None

    try:
        resp = requests.get(url, headers={"User-Agent": _BROWSER_UA}, timeout=20)
        resp.raise_for_status()
        enc = resp.encoding or resp.apparent_encoding or "utf-8"
        raw_text = resp.content.decode(enc, errors="replace")
        f1 = feedparser.parse(_sanitize_xml(raw_text))
        candidates.append((len(f1.entries), f1, "requests+sanitize"))
    except Exception as e:
        candidates.append((0, None, f"requests:{e}"))

    try:
        f2 = feedparser.parse(url)
        candidates.append((len(f2.entries), f2, "feedparser-native"))
    except Exception as e:
        candidates.append((0, None, f"feedparser:{e}"))

    if raw_text:
        try:
            rec = _recover_items_from_xml(_sanitize_xml(raw_text))
            if rec:
                candidates.append((len(rec), _RecoveredFeed(rec), "regex-recovery"))
        except Exception:
            pass

    candidates.sort(key=lambda c: c[0], reverse=True)
    best_n, best_feed, best_label = candidates[0]
    if not best_feed or best_n == 0:
        detail = "; ".join(f"{lbl}:{n}" for n, _f, lbl in candidates)
        return best_feed, f"0 entries ({detail})"
    note = None if best_label == "requests+sanitize" else f"recovered via {best_label} ({best_n})"
    return best_feed, note


# =============================================================================
# v3.33: Google News helpers
# =============================================================================
def gnews_feed(site, when="7d"):
    q = quote_plus(f"site:{site} when:{when}")
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


def gnews_topic(query, when="7d"):
    q = quote_plus(f'{query} when:{when}')
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


_GN_TITLE_SUFFIX_RE = re.compile(r"\s+-\s+[^-]{2,40}$")


def _clean_gn_title(title):
    if not title:
        return title
    cleaned = _GN_TITLE_SUFFIX_RE.sub("", title).strip()
    return cleaned if len(cleaned) >= 15 else title.strip()


_TAG_RE = re.compile(r"<[^>]+>")


def _clean_gn_summary(summary, title):
    if not summary:
        return ""
    text = _html.unescape(_TAG_RE.sub(" ", summary))
    text = re.sub(r"\s+", " ", text).strip()
    norm = lambda s: re.sub(r"[^a-z0-9]+", "", (s or "").lower())
    t_norm = norm(title)
    if t_norm and norm(text).startswith(t_norm) and len(norm(text)) - len(t_norm) < 30:
        return ""
    return text


# =============================================================================
# v3.34: Job-listing filter
# =============================================================================
_JOB_PATTERNS = [
    r"\bjobs?\s+in\b",
    r"\bjob\s+with\b",
    r"\bjobs?\s+at\b",
    r"coordinator\s*/\s*executive",
    r"director\s*/\s*president\s*/\s*c\s*suite",
    r"\bc\s*suite\s+jobs\b",
    r"\|\s*\d{5,7}\s*$",
    r"\bdirect\s+employer\b",
    r"\bfull[- ]time\b.*\b(intern|internship)\b",
    r"\$\s?[\d,]+\s*(or more|per year|k\b).*\bjobs?\b",
    r"\b(apply now|now hiring|job opening|job vacancy|vacancies)\b",
    r"\b(part[- ]time|full[- ]time)\s+(sales associate|retail|store)\b",
    r"\b(coordinator|executive|manager|assistant|associate|designer|merchandiser|"
    r"buyer|analyst|consultant|specialist|supervisor|technician|intern)\s*/\s*"
    r"(coordinator|executive|manager|assistant|associate|senior|junior|lead|head|director)\b",
    r"\b(jobs|careers|vacancies)\s*(\||-|—|:)",
    r"\bjobs\b.*\b(remote|hybrid|on[- ]site|permanent|temporary|freelance)\b",
    r"\b(salary|£|\$)\s?[\d,]+\s*[-–]\s*[£$]?[\d,]+",
    r"\bmulti[- ]?site\b.*\bjobs?\b",
    r"\bjob\s+(id|ref|reference)\b",
]
_JOB_RE = [re.compile(p, re.IGNORECASE) for p in _JOB_PATTERNS]


def is_job_listing(title, summary=""):
    text = f"{title or ''} {summary or ''}"
    return any(rx.search(text) for rx in _JOB_RE)


# =============================================================================
# Email & volume config
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

MAX_ITEMS = {
    "events": 30,
    "industryNews": 20,
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
# v3.36: Source containment
# =============================================================================
ENTERTAINMENT_SOURCES = {
    "Deadline", "Deadline Casting", "Deadline TV", "Deadline Film",
    "Variety", "Variety Film", "Variety TV",
    "THR", "THR Music", "THR Film", "THR TV", "The Hollywood Reporter",
    "TMDB", "Billboard", "Rolling Stone", "Pitchfork", "Consequence",
}

NON_ENTERTAINMENT_CATEGORIES = {
    "industryNews", "ambassadors", "partnerships", "events",
}

SOURCE_CATEGORY_RESTRICTIONS = {
    "WWD":                {"industryNews", "ambassadors", "partnerships"},
    "BoF":                {"industryNews", "partnerships"},
    "Business of Fashion": {"industryNews", "partnerships"},
    "Drapers":            {"industryNews"},
    "FashionNetwork":     {"industryNews", "ambassadors", "partnerships"},
    "GN Ambassadors":     {"ambassadors", "partnerships"},
    "GN Campaigns":       {"ambassadors", "partnerships"},
    "Hypebeast":          {"ambassadors", "partnerships"},
    "Hypebae":            {"ambassadors", "partnerships"},
    "POPSUGAR Fashion":   {"ambassadors", "partnerships"},
    "POPSUGAR Beauty":    {"ambassadors", "partnerships"},
    "Beauty Packaging":   {"ambassadors"},
    "Vogue France":       {"ambassadors", "partnerships"},
    "Fashionista":        {"ambassadors", "partnerships"},
    "Vogue":              {"ambassadors", "partnerships"},
    "WWD Beauty":         {"ambassadors", "partnerships", "industryNews"},
    "FashionWeekDaily":   {"ambassadors", "partnerships", "industryNews"},
    "The Impression":     {"ambassadors", "partnerships"},
    "British Vogue":      {"ambassadors", "partnerships", "industryNews"},
    "Vogue Italia":       {"ambassadors", "partnerships"},
    "Vogue España":       {"ambassadors", "partnerships"},
    "People":             {"ambassadors"},
    "Nylon":              {"industryNews", "castings"},
    "Complex Style":      {"partnerships", "ambassadors", "industryNews"},
    "THR Music":          {"industryNews"},
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
# TMDB & Music
# =============================================================================
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")
TMDB_BASE = "https://api.themoviedb.org/3"

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
# Semi-static annual events
# =============================================================================
SEMI_STATIC_EVENTS = {
    "Gotham TV Awards":              "2026-06-01",
    "Monaco Grand Prix":             "2026-06-05",
    "Governor's Ball":               "2026-06-05",
    "NBA Finals":                    "2026-06-04",
    "BET Awards":                    "2026-06-14",
    "Bonnaroo":                      "2026-06-11",
    "FIFA World Cup 2026":           "2026-06-11",
    "Wimbledon":                     "2026-06-30",
    "FIFA World Cup Final":          "2026-07-19",
    "Pitti Uomo":                    "2026-06-10",
    "Milan Men's Fashion Week":      "2026-06-19",
    "London Men's Fashion Week":     "2026-06-20",
    "Paris Men's Fashion Week":      "2026-06-23",
    "Los Angeles Men's Fashion Week":"2026-06-24",
    "Paris Haute Couture Week":      "2026-07-04",
    "Copenhagen Fashion Week":       "2026-08-03",
    "New York Fashion Week":         "2026-09-08",
    "London Fashion Week":           "2026-09-17",
    "Milan Fashion Week":            "2026-09-22",
    "Paris Fashion Week":            "2026-09-28",
    "Cannes Film Festival":          "2026-05-12",
    "Venice Film Festival":          "2026-08-27",
    "Toronto International Film Festival (TIFF)": "2026-09-03",
    "Tribeca Film Festival":         "2026-06-04",
    "Telluride Film Festival":       "2026-08-29",
    "CFDA Fashion Awards":           "2026-06-09",
    "LACMA Art+Film Gala":           "2026-11-07",
    "British Fashion Awards":        "2026-12-07",
}

# =============================================================================
# Luxury brands & prestige
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
# Hard-news tier
# =============================================================================
HARD_NEWS_SIGNALS = [
    "ceo", "chief executive", "deputy ceo", "co-ceo", "coo", "cfo",
    "creative director", "design director", "artistic director",
    "appointed", "appoints", "names as", "names a new", "named director",
    "steps down", "resigns", "joins as", "to lead", "to head",
    "president", "chairman", "chairwoman", "managing director",
    "people moves", "power moves", "daily media", "executive moves",
    "new hire", "names chief", "name chief", "named chief",
    "acquires", "acquisition", "merger", "to merge", "buys", "acquired by",
    "ipo", "spin off", "spins off", "spinoff", "divests", "divestment",
    "stake in", "majority stake", "takeover", "bankruptcy", "chapter 11",
    "earnings", "revenue", "quarterly results", "fiscal", "valuation",
    "restructuring", "layoffs", "fundraise", "investment round",
    "new store", "opens store", "opens its", "boutique", "flagship",
    "first store", "largest store", "new location", "opens flagship",
    "newest store", "third store", "second store", "store is", "opens a",
    "taps", "as svp", "as evp", "as chief", "as vp of",
    "acquiring", "to acquire", "buys", "acquires stake", "is acquiring",
    "sets date for", "first haute couture", "first couture",
    "met en vente", "vente aux enchères", "cède ses",
    "promotes", "promoted to", "elevates", "elevated to",
    "chief communications", "chief marketing", "chief brand", "chief creative",
    "cmo", "cco", "named inaugural", "to lead americas",
    "confirms exit", "confirms departure", "exit of", "departure of",
    "new communications director", "new creative director", "global communications",
    "has a new", "to return to", "returns to", "leaves after", "after 13-year",
    "receivership", "redressement judiciaire", "liquidation judiciaire",
    "insolvency", "insolvent", "administration", "chapter 7",
    "files for bankruptcy", "placed in receivership", "court protection",
    "creditor protection", "winding up", "en faillite", "cessation de paiement",
    "takes over", "taking over", "pop-up store", "opens summer", "opens pop",
    "flagship store", "to open flagship", "opens a pop", "store takeover",
    "confirms show venue", "show venue", "permanent exhibition", "exhibition space",
    "content series", "new content", "milestone with capsule",
]


def classify_news_hardness(text_lower):
    for sig in HARD_NEWS_SIGNALS:
        if sig in text_lower:
            return "hard"
    return "soft"


def _phrase_in(text_lower, phrase):
    return re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])",
                     text_lower) is not None


def _any_phrase_in(text_lower, phrases):
    return any(_phrase_in(text_lower, p) for p in phrases)


def partnership_prestige(text_lower):
    if _any_phrase_in(text_lower, LUXURY_BRANDS):
        return 3
    mid_market = [
        "nike", "adidas", "new balance", "puma", "reebok", "uniqlo", "gap",
        "levi's", "levis", "free people", "anthropologie", "h&m", "zara",
        "calvin klein", "tommy hilfiger", "lacoste", "diesel", "guess",
        "coach", "michael kors", "kate spade", "marc jacobs",
        "alpine", "ferrari", "porsche", "bmw", "mercedes", "savage x fenty",
        "fenty", "rimowa", "moncler", "canada goose", "north face",
    ]
    if _any_phrase_in(text_lower, mid_market):
        return 2
    return 0


# =============================================================================
# Ambassador / partnership routing
# =============================================================================
AMBASSADOR_VERB_PHRASES = [
    "linked up with", "links up with", "teams up with", "partners with",
    "taps", "recruited", "recruits", "enlists", "enlisted", "fronts",
    "stars in", "is the new face", "new face of", "campaign with",
    "campaign starring", "named the face", "named ambassador",
]

PERSON_MARKERS = [
    "pop princess", "pop star", "popstar", "singer", "rapper", "musician",
    "actor", "actress", "supermodel", "athlete",
    "olympian", "footballer", "songwriter", "performer", "it girl", "muse",
]


def _has_person_marker(text_lower):
    for m in PERSON_MARKERS:
        if re.search(r'(?<![a-z])' + re.escape(m) + r'(?![a-z])', text_lower):
            return True
    return False


_EXPLICIT_AMBASSADOR_NOUNS = [
    "brand ambassador", "global ambassador", "house ambassador",
    "global makeup ambassador", "beauty ambassador", "fragrance ambassador",
    "as ambassador", "an ambassador", "new ambassador", "first ambassador",
    "named the face", "new face of", "is the face of", "the new face",
    "brand muse", "house muse", "new muse", "spokesperson for",
    "viva glam", "campaign star",
]

_EXEC_ROLE_MARKERS = [
    "as svp", "as evp", "as vp", "as ceo", "as cfo", "as coo", "as cmo",
    "as president", "as chief", "as director of", "as head of",
    "as general manager", "as managing director", "to lead americas",
    "to lead emea", "to lead apac", "promotes", "promoted to",
    "chief communications", "chief marketing", "named inaugural",
    "executive to lead", "leadership roles",
]


def _clean_headline(title):
    if not title:
        return title
    return re.sub(
        r"^\s*(?:EXCLUSIVE|EXCLUSIF|BREAKING|UPDATED|FIRST LOOK|SCOOP)\s*[:\-–]\s*",
        "", title, flags=re.IGNORECASE
    ).strip()


def looks_like_explicit_ambassador(text_lower):
    if any(e in text_lower for e in _EXEC_ROLE_MARKERS):
        return False
    return any(n in text_lower for n in _EXPLICIT_AMBASSADOR_NOUNS)


def looks_like_ambassador_campaign(text_lower):
    AMBASSADOR_HARD_BLOCK = [
        "everything we loved", "everything we added", "playlist this week",
        "music this week", "new single", "drops new", "shares new single",
        "ram costs", "cmf phone", "shelves next", "supply chain",
        "watch with a", "presage", "dial", "chronograph", "timepiece",
        "hypebeast cup", "lands in singapore", "month-long program",
        "sneaker archive", "boot test", "gaming", "video game",
        "radio player", "streams curated", "mixtape", "print t-shirts",
        "t-shirts spotlight", "best world cup capsule",
        "scent scanner", "ai-powered", "ai powered", "scanner on",
        "gadget", "wearable", "headphones", "earbuds",
        "smartphone", "phone as", "raises $", "million from",
        "for our next vintage",
        "ss27", "ss26", "fw27", "fw26", "aw27", "aw26", "spring 2027",
        "fall 2027", "men's fashion week", "fashion week ss", "fashion week fw",
        "bridal collection", "ballet era", "rewrites the", "our favorite shows",
        "euro-summer", "everywhere right now", "xt-icons", "collection for ss",
        "punk identity", "first-ever bridal", "got you covered",
        "gifts", "bespoke watch", "tracks one million", "million hours",
        "gifted", "presented with a watch",
        "launches its first", "launches its own", "first-ever sunglasses",
        "first-ever collection", "unveils its first", "expands beyond",
        "enters the", "is an ode to", "ode to nature",
        "must read:", "must-read:", "daily media:", "power moves:",
        "top stories", "what to know", "roundup",
        "attend ", "attends ", "attended ", "arrive at", "arrives at",
        "will accept is his own", "ordinary people suck",
        "revisits its roots", "trades neutrals", "turns up the heat with its",
        "fever dream",
    ]
    if any(b in text_lower for b in AMBASSADOR_HARD_BLOCK):
        return False

    STRONG_CAMPAIGN_PHRASES = [
        "stars in", "fronts", "is the new face", "new face of", "the face of",
        "named the face", "named ambassador", "global ambassador",
        "brand ambassador", "house ambassador", "becomes the face",
        "first creative partner", "creative partner in residence",
        "joins as", "campaign starring",
        "taps director", "taps rising star", "taps actor", "taps actress",
        "taps photographer", "taps model", "taps singer", "taps olympian",
        "as brand ambassador", "signs as", "as global makeup",
        "fronts the campaign", "is the face", "tapped as",
        "ambassador", "viva glam", "brand muse", "new muse", "spokesperson",
    ]
    has_campaign_person = (
        "campaign" in text_lower and (
            re.search(r"\b(his|her|she|he)\b", text_lower) is not None
            or _has_person_marker(text_lower)
        )
    )
    has_strong = any(p in text_lower for p in STRONG_CAMPAIGN_PHRASES)

    has_signature = bool(re.search(r"'s\b[^.]{0,40}\bsignature\b", text_lower)) or \
                    ("signature" in text_lower and "first-ever" in text_lower) or \
                    ("signature" in text_lower and "first ever" in text_lower)

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
# RSS Sources
# =============================================================================
RSS_SOURCES = {
    "industry_news": [
        {"url": "https://wwd.com/feed", "source": "WWD"},
        {"url": "https://wwd.com/beauty-industry-news/feed", "source": "WWD Beauty"},
        {"url": gnews_feed("businessoffashion.com"), "source": "BoF"},
        {"url": "https://drapersonline.com/feed", "source": "Drapers"},
        {"url": "https://fashionweekdaily.com/feed/", "source": "FashionWeekDaily"},
        {"url": "https://www.hollywoodreporter.com/t/music/feed/", "source": "THR Music"},
        {"url": gnews_feed("fashionnetwork.com"), "source": "FashionNetwork"},
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
        {"url": "https://wwd.com/beauty-industry-news/feed", "source": "WWD Beauty"},
        {"url": "https://fashionista.com/.rss/excerpt/", "source": "Fashionista"},
        {"url": "https://hypebeast.com/feed", "source": "Hypebeast"},
        {"url": "https://hypebae.com/feed", "source": "Hypebae"},
        {"url": "https://www.popsugar.com/fashion/feed", "source": "POPSUGAR Fashion"},
        {"url": "https://www.popsugar.com/beauty/feed", "source": "POPSUGAR Beauty"},
        {"url": "https://www.vogue.com/feed/rss", "source": "Vogue"},
        {"url": "https://www.vogue.fr/feed/rss", "source": "Vogue France"},
        {"url": "https://www.beautypackaging.com/rss", "source": "Beauty Packaging"},
        {"url": "https://fashionweekdaily.com/feed/", "source": "FashionWeekDaily"},
        {"url": "https://theimpression.com/feed/", "source": "The Impression"},
        {"url": "https://www.vogue.co.uk/feed/rss", "source": "British Vogue"},
        {"url": gnews_feed("fashionnetwork.com"), "source": "FashionNetwork"},
        {"url": gnews_topic('"global brand ambassador" fashion OR beauty'), "source": "GN Ambassadors"},
        {"url": gnews_topic('"brand ambassador" announced campaign fashion'), "source": "GN Ambassadors"},
        {"url": gnews_topic('celebrity fronts campaign fashion OR denim OR beauty'), "source": "GN Campaigns"},
    ],
    "partnerships": [
        {"url": "https://wwd.com/feed", "source": "WWD"},
        {"url": gnews_feed("businessoffashion.com"), "source": "BoF"},
        {"url": "https://fashionista.com/.rss/excerpt/", "source": "Fashionista"},
        {"url": "https://www.vogue.com/feed/rss", "source": "Vogue"},
        {"url": "https://fashionweekdaily.com/feed/", "source": "FashionWeekDaily"},
        {"url": "https://theimpression.com/feed/", "source": "The Impression"},
        {"url": "https://www.vogue.co.uk/feed/rss", "source": "British Vogue"},
        {"url": gnews_feed("fashionnetwork.com"), "source": "FashionNetwork"},
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
# Content classifiers
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
            "talent agency", "signs with agency", "management", "representation",
            "agent", "caa", "wme", "uta", "icm", "vmg",
            "kensington grey", "anonymous content", "paradigm", "endeavor",
            "signs with talent", "talent management", "signs with rep",
            "new representation", "joins roster",
            "as svp", "as evp", "as vp", "as ceo", "as cfo", "as coo",
            "as president", "as chief", "as director of", "as head of",
            "as general manager", "as managing director", "as global head",
            "as creative director", "as design director", "as senior vice",
            "deputy ceo", "names ceo", "appointed ceo", "joins as chief",
            "fragrance cluster", "to drive growth", "to lead", "names chief",
            "must read:", "must-read:", "daily media:", "power moves:",
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
            "partner on", "partners with", "partner with", "collaborates with",
            "collaborated on", "collaborated with", "collaborate on",
            "collab with", "links up with", "link up with", "limited-edition",
            "capsule",
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
            "skateboarding", "skate", "afa world cup", "world cup skateboarding",
            "snowboarding", "surfboarding",
            "coming to netflix", "new on netflix", "upcoming shows",
            "streaming guide", "new shows on", "new series on",
            "what's streaming", "everything coming to", "new movies on",
            "journey from", "explores the", "everything we loved",
            "music this week", "is touring", "enters gaming", "video game",
            "anthology", "album", "prints", "exhibition", "documentary",
            "celebrates the dialogue", "quietly celebrates",
            "rooted in legacy", "reimagines the", "abstract artist",
            "punk rocker", "suburbia", "dream house", "furniture designs",
        ],
        "must_have": ["capsule collection", "limited-edition", "limited edition",
                      "collaboration with", "collaborates with", "collab with",
                      "teams up with", "joins forces with", "partnership with",
                      "partners with", "partner on", "partner with",
                      "co-branded", "co-designed", " x ", "teams up", "links up with", "link up with",
                      "collaborated on", "collaborated with"],
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
            "joins as editor", "joins as writer", "joins as contributor",
            "joins column", "joins team", "hired as", "named editor",
            "appointed editor", "steps into", "takes over as",
            "talking with", "on taking", "on working with", "in conversation",
            "opens up about", "speaks with", "reflects on his", "reflects on her",
            "on his career", "on her career", "discusses his", "discusses her",
            "the story of", "how the", "unexpected turns",
            "buys rights", "acquires rights", "picks up rights",
            "distribution rights", "north american rights", "scoops up",
            "paramount buys", "netflix buys", "buys starring",
            "joins panel", "joins jury", "joins program", "joins industry",
            "docfest", "film festival program", "industry program",
            "where to watch", "how to watch", "when to watch",
            "streaming on", "now streaming", "available on",
            "artisans brought", "brought to life", "how they made",
            "easter eggs", "fans' belief", "heighten fans",
            "added to our", "new music friday", "music friday drops",
            "we added to our playlist",
            "virgin river", "baywatch",
            "love island",
            "tv ratings", "viewing record", "premiere week viewing",
            "multi-year high", "million minutes", "billion minutes",
            "nominated on sunday", "egot", "fastest streamer to",
            "poised to become",
            "sets series premiere", "premiere date on", "sets premiere date",
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
            "closes deal", "acquires", "lands rights", "picks up rights",
            "distribution deal", "market deal", "cannes market",
            "dies at", "passed away", "in memoriam", "pays tribute",
            "deeply saddened", "mourning the death", "former wife",
            "5 best", "10 best", "best movies", "best performances",
            "best films", "retrospective", "ranked", "greatest of all time",
            "warehouse", "film tax credits", "governor candidates",
            "uncapped credits", "film credits bill", "supply chain",
            "love island", "baywatch",
            "looking forward to", "is looking forward", "is terrified",
            "refused to use", "produced by conference", "comparing the",
            "could do what", "low-budget horror", "horror boom",
            "visited london", "dug through", "archive", "war museum",
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
            "awards", "winners", "full list", "nominations", "nominees",
            "bids farewell", "concludes", "final episode", "series finale",
            "ending", "canceled", "cancelled",
            "where to watch", "where to stream", "how to watch",
            "tv ratings", "viewing record", "multi-year high", "million minutes",
            "love island",
            "now streaming on", "available to stream",
            "pays tribute", "in memoriam", "deeply saddened",
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
