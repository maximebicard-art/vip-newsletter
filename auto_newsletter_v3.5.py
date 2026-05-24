#!/usr/bin/env python3
"""
VIP & Industry News - AUTOMATED NEWSLETTER SYSTEM v3.5
======================================================
Fixes from v3.4:
- Events: Replaced RSS feeds with curated events list + date extraction
- Industry News: Stronger weakness scoring, rejects product guides
- Ambassadors: Added more sources, tightened classifier, cross-check with castings
- Partnerships: Fixed false positives on "x/×" in article titles
- Castings: Better source filtering, removed industry news bleed
- Film/TV: Cross-category duplicate detection
- All sections: Better must_have keywords, improved Gemini timeout
- Template: Calendar link fallback
"""

import os
import sys
import json
import re
import feedparser
from datetime import datetime, timedelta
from pathlib import Path

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
    "events": 12,
    "industryNews": 12,
    "ambassadors": 15,
    "partnerships": 12,
    "castings": 10,
    "filmProjects": 10,
    "tvProjects": 10,
}

# =============================================================================
# CURATED EVENTS - Replace RSS with known major events
# =============================================================================

def get_curated_events():
    """Return curated list of major industry events for the current/next month."""
    now = datetime.now()
    current_year = now.year

    # Major events with known dates - update quarterly
    events = [
        # Cannes already happened (May 13-24, 2026)
        {"date": "Jun 05", "name": "Tony Awards 2026", "link": "", "source": "Industry Calendar"},
        {"date": "Jun 08", "name": "CFDA Fashion Awards", "link": "", "source": "Industry Calendar"},
        {"date": "Jun 12", "name": "Tribeca Film Festival", "link": "", "source": "Industry Calendar"},
        {"date": "Jun 15", "name": "Paris Men's Fashion Week SS27", "link": "", "source": "Industry Calendar"},
        {"date": "Jun 21", "name": "Milan Men's Fashion Week SS27", "link": "", "source": "Industry Calendar"},
        {"date": "Jun 24", "name": "London Fashion Week Men's SS27", "link": "", "source": "Industry Calendar"},
        {"date": "Jul 04", "name": "Paris Haute Couture Week", "link": "", "source": "Industry Calendar"},
        {"date": "Jul 14", "name": "Emmy Nominations 2026", "link": "", "source": "Industry Calendar"},
        {"date": "Aug 23", "name": "MTV VMAs 2026", "link": "", "source": "Industry Calendar"},
        {"date": "Aug 30", "name": "Venice Film Festival 2026", "link": "", "source": "Industry Calendar"},
        {"date": "Sep 04", "name": "Telluride Film Festival", "link": "", "source": "Industry Calendar"},
        {"date": "Sep 10", "name": "Toronto International Film Festival (TIFF)", "link": "", "source": "Industry Calendar"},
        {"date": "Sep 10", "name": "New York Fashion Week SS27", "link": "", "source": "Industry Calendar"},
        {"date": "Sep 14", "name": "London Fashion Week SS27", "link": "", "source": "Industry Calendar"},
        {"date": "Sep 19", "name": "Milan Fashion Week SS27", "link": "", "source": "Industry Calendar"},
        {"date": "Sep 23", "name": "Paris Fashion Week SS27", "link": "", "source": "Industry Calendar"},
        {"date": "Oct 05", "name": "Met Gala 2026", "link": "", "source": "Industry Calendar"},
        {"date": "Nov 02", "name": "LACMA Art+Film Gala", "link": "", "source": "Industry Calendar"},
        {"date": "Nov 15", "name": "CFDA/Vogue Fashion Fund Awards", "link": "", "source": "Industry Calendar"},
        {"date": "Dec 06", "name": "British Fashion Awards", "link": "", "source": "Industry Calendar"},
    ]

    # Filter to show events from today forward, plus 1 past event for context
    cutoff = now - timedelta(days=3)
    future_events = []
    for e in events:
        try:
            event_dt = datetime.strptime(f"{e['date']} {current_year}", "%b %d %Y")
            if event_dt >= cutoff:
                future_events.append(e)
        except:
            continue

    return future_events[:MAX_ITEMS["events"]]

# =============================================================================
# RSS FEED SOURCES - FIXED
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
        {"url": "https://www.popsugar.com/fashion/feed", "source": "POPSUGAR Fashion"},
        {"url": "https://www.popsugar.com/beauty/feed", "source": "POPSUGAR Beauty"},
        {"url": "https://www.vogue.com/feed/rss", "source": "Vogue"},
        {"url": "https://www.vogue.fr/feed/rss", "source": "Vogue France"},
        # NEW: Beauty industry sources for ambassador news
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
# CONTENT CLASSIFIERS - TIGHTENED
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
            # NEW: Exclude talent agency signings
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
            # NEW: Exclude article types that use "x" in titles
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
            # NEW: Exclude editorial hires
            "joins magazine", "joins publication", "editor in chief",
            "creative director", "appointed ceo", "named ceo",
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
            # NEW: Exclude casting news (handled by castings section)
            "cast in", "joins cast", "to star", "lands role", "to play",
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
            # NEW: Exclude obituaries
            "dies", "dead", "death", "passed away",
        ],
    },
}

# =============================================================================
# GEMINI AI RANKING - IMPROVED
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
                timeout=60  # INCREASED from 30 to 60 seconds
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
1. **Major luxury brands**: LVMH, Kering, Richemont, Hermès, Chanel, Dior, Gucci, Prada, Louis Vuitton, Cartier, Tiffany, Bulgari, L'Oréal, Estée Lauder, etc.
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
        }
        self.gemini = GeminiRanker()
        self._seen_links = set()  # NEW: Global duplicate tracking

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

        # NEW: Load curated events first
        curated_events = get_curated_events()
        self.categorized["events"] = curated_events
        print(f"   📅 Loaded {len(curated_events)} curated events")

        for item in self.raw_items:
            text = f"{item['title']} {item['summary']}".lower()
            assigned = False
            hint = item.get('category_hint', '')

            # Skip if we've seen this link in any category
            link = item.get('link', '')
            if link and link in self._seen_links:
                continue

            # If item has a category hint from RSS source, try that first
            if hint in CLASSIFIERS:
                rules = CLASSIFIERS[hint]
                if self._passes_classifier(text, rules):
                    formatted = self._format_item(item, hint)
                    if formatted and not self._is_duplicate(formatted, hint):
                        self.categorized[hint].append(formatted)
                        if link:
                            self._seen_links.add(link)
                        assigned = True

            # If not assigned by hint, try all classifiers
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
                            assigned = True
                            break

            # Fallback to industry news with strict relevance check
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
        """Check if text passes classifier rules."""
        keywords = rules.get('keywords', [])
        exclude = rules.get('exclude', [])
        must_have = rules.get('must_have', [])

        # Check excludes first
        if any(exc.lower() in text for exc in exclude):
            return False

        # Check must-have keywords
        if must_have and not any(mh.lower() in text for mh in must_have):
            return False

        # Score keywords
        score = sum(1 for kw in keywords if kw.lower() in text)
        return score >= 1

    def _format_industry_news(self, item):
        """Format item as industry news only if it passes relevance scoring."""
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

        # ENHANCED: More weak signals to catch product guides
        weak_signals = [
            "boot", "shoe", "sneaker", "bag", "dress", "outfit",
            "makeup", "lipstick", "skincare", "fragrance", "perfume",
            "collection", "lookbook", "runway", "trend", "style",
            "shop for", "to shop", "shopping", "buy now", "on sale",
            "discount", "markdown", "deal", "steal", "bargain",
            "gift guide", "holiday gift", "wishlist", "must-have",
            "pack a", "wear this", "outfit idea", "get the look",
            "beginner's guide", "how to", "tutorial", "tips for",
            "products to", "new launches", " launches to",
        ]

        tier1_sources = ["WWD", "BoF", "Vogue", "Vogue France", "Drapers", "Business of Fashion"]
        is_tier1 = item.get('source') in tier1_sources

        relevance_score = sum(1 for signal in strong_signals if signal in text_lower)
        weakness_score = sum(1 for signal in weak_signals if signal in text_lower)

        # STRICTER: Require higher relevance or lower weakness
        if (relevance_score >= 2 or (is_tier1 and relevance_score >= 1)) and weakness_score < 2:
            return self._format_item(item, "industryNews")
        return None

    def _apply_limits(self):
        """Apply volume limits with Gemini AI ranking for intelligent selection."""
        for category, max_count in MAX_ITEMS.items():
            items = self.categorized[category]

            # Events already sorted and limited
            if category == "events":
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
            return {"talent": title, "brand": "See article", "link": link, "source": source}

        elif category == "partnerships":
            result = self._extract_partnership(title, link, source)
            if result:
                return result
            return {"partner1": title, "partner2": "See article", "link": link, "source": source}

        elif category in ["castings", "filmProjects", "tvProjects"]:
            return {"talent": title, "project": summary[:120] + "..." if len(summary) > 120 else summary, "link": link, "source": source}

        return None

    def _extract_ambassador(self, title, link, source):
        """Extract talent and brand from ambassador announcement headlines."""

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
        """Extract partner names from collaboration headlines."""

        # Skip titles that are clearly not partnerships
        skip_patterns = [
            r'^how\s', r'^must\s+read', r'^weekly\s+drop', r'^school\s+',
            r'^pourquoi', r'^mode\s+', r'^pedro\s+almod', r'^american\s+horror',
            r'^\d+\s+new\s+', r'^\d+\s+best\s+', r'^a\s+beginner',
            r'^maximize\s+your', r'^a\s+river\s+runs',
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
                p1 = re.sub(r'^(?:New|Exclusive|First Look|Inside|Weekly Drop Watch:)\s+', '', p1, flags=re.IGNORECASE)

                if len(p1) > 2 and len(p2) > 2 and len(p1) < 60 and len(p2) < 60:
                    return {"partner1": p1, "partner2": p2, "link": link, "source": source}

        and_match = re.search(r'^([A-Z][a-zA-Z\s&\'\-]+?)\s+(?:and|&)\s+([A-Z][a-zA-Z\s&\'\-]+?)(?:\s|$)', title)
        if and_match:
            return {"partner1": and_match.group(1).strip(), "partner2": and_match.group(2).strip(), "link": link, "source": source}

        return None

    def _is_duplicate(self, item, category):
        """Check for duplicates within category AND cross-category for castings/film/tv."""
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

        # NEW: Cross-category check for castings/film/tv
        if category in ["castings", "filmProjects", "tvProjects"]:
            cross_cats = ["castings", "filmProjects", "tvProjects"]
            cross_cats.remove(category)
            for cross_cat in cross_cats:
                for e in self.categorized.get(cross_cat, []):
                    if item.get('talent') and e.get('talent') == item.get('talent'):
                        # Same talent - check if same project by comparing links
                        if item.get('link') == e.get('link'):
                            return True
                        # Or fuzzy match on title
                        t1 = item.get('talent', '').lower()
                        t2 = e.get('talent', '').lower()
                        if t1 and t2 and (t1 in t2 or t2 in t1):
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
            **self.categorized
        }


class EmailSender:
    def __init__(self, config):
        self.config = config
        self.provider = config.get("provider", "brevo")

    def send(self, to_email, subject, html_content):
        print(f"\n📧 Sending via {self.provider}...")

        if self.provider == "brevo":
            return self._send_brevo(to_email, subject, html_content)
        elif self.provider == "sendpulse":
            return self._send_sendpulse(to_email, subject, html_content)
        elif self.provider == "mailjet":
            return self._send_mailjet(to_email, subject, html_content)
        elif self.provider == "sendgrid":
            return self._send_sendgrid(to_email, subject, html_content)
        elif self.provider == "smtp":
            return self._send_smtp(to_email, subject, html_content)
        else:
            print("❌ Unknown provider")
            return False

    def _send_brevo(self, to_email, subject, html):
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

    def _send_mailjet(self, to_email, subject, html):
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

    def _send_sendgrid(self, to_email, subject, html):
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

    def _send_smtp(self, to_email, subject, html):
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = f"{self.config['from_name']} <{self.config['from_email']}>"
        msg['To'] = to_email
        msg.attach(MIMEText(html, 'html'))

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
    print("VIP & INDUSTRY NEWS - AUTOMATED NEWSLETTER SYSTEM v3.5")
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

    save_newsletter(html)

    if os.getenv("SEND_EMAIL", "0") == "1":
        subject = f"VIP & Industry News | {data['MONTH']} {data['YEAR']}"
        sender = EmailSender(EMAIL_CONFIG)
        success = sender.send(RECIPIENT_EMAIL, subject, html)

        if not success:
            print("\n⚠️ Email sending failed. HTML saved locally.")
    else:
        print("\n📌 Email sending skipped (set SEND_EMAIL=1 to enable)")
        print(f"📌 Recipient: {RECIPIENT_EMAIL}")

    print("\n✨ Done!")


if __name__ == "__main__":
    main()
