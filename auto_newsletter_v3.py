#!/usr/bin/env python3
"""
VIP & Industry News - AUTOMATED NEWSLETTER SYSTEM v3.1
======================================================
Fixes from v3.0:
- Better event handling (no more false date extraction)
- Improved CSS inline styles for email client compatibility
- Source links and volume limits maintained
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
# RSS FEED SOURCES
# =============================================================================

RSS_SOURCES = {
    "events": [
        {"url": "https://www.hollywoodreporter.com/t/awards/feed", "source": "THR Awards"},
        {"url": "https://variety.com/e/contenders/feed", "source": "Variety Contenders"},
        {"url": "https://www.vogue.com/living/parties/feed/rss", "source": "Vogue Parties"},
    ],
    "industry_news": [
        {"url": "https://wwd.com/feed", "source": "WWD"},
        {"url": "https://www.businessoffashion.com/arc/outbound-feeds/rss/", "source": "BoF"},
        {"url": "https://fashionista.com/.rss/excerpt/", "source": "Fashionista"},
        {"url": "https://www.eonline.com/news/rss", "source": "E! Online"},
        {"url": "https://www.vogue.com/feed/rss", "source": "Vogue"},
        {"url": "https://www.vogue.fr/feed/rss", "source": "Vogue France"},
        {"url": "https://drapersonline.com/feed", "source": "Drapers"},
        {"url": "https://www.elle.com/rss/fashion.xml/", "source": "Elle Fashion"},
        {"url": "https://www.elle.com/rss/beauty.xml/", "source": "Elle Beauty"},
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
    "ambassadors": [
        {"url": "https://wwd.com/feed", "source": "WWD"},
        {"url": "https://fashionista.com/.rss/excerpt/", "source": "Fashionista"},
        {"url": "https://www.eonline.com/news/rss", "source": "E! Online"},
        {"url": "https://www.popsugar.com/fashion/feed", "source": "POPSUGAR Fashion"},
        {"url": "https://www.popsugar.com/beauty/feed", "source": "POPSUGAR Beauty"},
        {"url": "https://www.vogue.com/feed/rss", "source": "Vogue"},
        {"url": "https://www.vogue.fr/feed/rss", "source": "Vogue France"},
    ],
    "partnerships": [
        {"url": "https://wwd.com/feed", "source": "WWD"},
        {"url": "https://www.businessoffashion.com/arc/outbound-feeds/rss/", "source": "BoF"},
        {"url": "https://fashionista.com/.rss/excerpt/", "source": "Fashionista"},
        {"url": "https://www.vogue.com/feed/rss", "source": "Vogue"},
    ],
}

# =============================================================================
# CONTENT CLASSIFIERS
# =============================================================================

CLASSIFIERS = {
    "events": {
        "keywords": [
            "gala", "awards", "festival", "premiere", "screening", "ceremony",
            "met gala", "cannes", "sundance", "tiff", "venice film festival",
            "bafta", "oscars", "emmy", "grammy", "golden globe", "sag awards",
            "fashion week", "cruise show", "resort show", "couture",
            "ballet", "opera", "opening night", "benefit", "fundraiser",
            "after party", "red carpet", "afterparty", "opening",
        ],
        "exclude": ["after", "review", "recap", "best dressed", "worst dressed", "red carpet looks", "prediction", "predictions", "contender", "contenders", "nomination", "nominations"],
    },
    "ambassadors": {
        "keywords": [
            "brand ambassador", "named ambassador", "new face of", "face of",
            "signs with", "signs deal", "partnership with", "collaboration with",
            "tapped by", "tapped for", "campaign star", "new campaign",
            "global ambassador", "house ambassador", "beauty ambassador",
            "fashion ambassador", "named face", "new spokesperson",
            "new muse", "brand muse", "newest ambassador", "latest ambassador",
            "joins", "as ambassador", "as the face", "as new face",
            "for campaign", "fronts campaign", "stars in campaign",
            "new deal with", "partners with", "collaborates with",
            "x ", "teams up with", "joins forces with",
        ],
        "exclude": ["rumored", "speculated", "might", "could", "reportedly considering", "allegedly"],
    },
    "partnerships": {
        "keywords": [
            "collaboration", "capsule collection", "x ", "partners with",
            "teams up with", "joins forces", "limited edition", "drop",
            "exclusive collection", "debut collection", "联名", "collab",
            "special collection", "co-branded", "co-designed",
            "unveils collection with", "launches collection with",
        ],
        "exclude": ["rumored", "speculated", "might", "could", "allegedly"],
    },
    "casting": {
        "keywords": [
            "cast in", "joins cast", "set to star", "lands role", "to play",
            "in talks", "signed on", "boarded", "attached to", "to portray",
            "cast as", "joins ", "set to lead", "toplines", "in final talks",
            "replacing", "exits", "departs", "steps down", "new host",
            "to direct", "to produce", "writing ", "directing ",
            "returns for", "returning to", "back for", "reprises role",
            "to star in", "to appear in", "to feature in", "to cameo in",
        ],
        "exclude": ["rumored", "speculated", "might", "could", "allegedly", "reportedly in talks"],
    },
    "film_projects": {
        "keywords": [
            "movie", "film", "feature", "biopic", "sequel", "prequel",
            "franchise", "adaptation", "remake", "reboot", "spinoff",
            "in production", "wraps production", "starts filming",
            "begins shoot", "wraps shoot", "in theaters", "release date",
        ],
        "must_have": ["cast", "star", "direct", "produce", "write", "film", "movie"],
    },
    "tv_projects": {
        "keywords": [
            "series", "season", "episode", "show", "tv", "television",
            "streaming", "netflix", "hbo", "hulu", "apple tv", "amazon",
            "peacock", "paramount", "disney+", "starz", "fx", "amc",
            "renewed", "ordered", "greenlit", "picked up", "in development",
            "pilot", "limited series", "anthology",
        ],
        "must_have": ["cast", "star", "joins", "renewed", "ordered", "greenlit", "picks up"],
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
        }

    def fetch_all_feeds(self):
        print("\n" + "="*60)
        print("📡 FETCHING RSS FEEDS")
        print("="*60)
        total = 0
        failed = []

        for category, sources in RSS_SOURCES.items():
            for source in sources:
                try:
                    print(f"   → {source['source']} ({category})")
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
                            "category_hint": category,
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

        for item in self.raw_items:
            text = f"{item['title']} {item['summary']}".lower()
            assigned = False

            for category, rules in CLASSIFIERS.items():
                score = 0
                keywords = rules.get('keywords', [])
                exclude = rules.get('exclude', [])
                must_have = rules.get('must_have', [])

                if any(exc.lower() in text for exc in exclude):
                    continue

                if must_have and not any(mh.lower() in text for mh in must_have):
                    continue

                for kw in keywords:
                    if kw.lower() in text:
                        score += 1

                if score >= 1:
                    formatted = self._format_item(item, category)
                    if formatted and not self._is_duplicate(formatted, category):
                        self.categorized[category].append(formatted)
                        assigned = True
                        break

            if not assigned:
                formatted = self._format_item(item, "industryNews")
                if formatted and not self._is_duplicate(formatted, "industryNews"):
                    self.categorized["industryNews"].append(formatted)

        print("\n📊 CATEGORIZATION RESULTS (before limits):")
        for cat, items in self.categorized.items():
            print(f"   • {cat}: {len(items)} items")

        self._apply_limits()

        print("\n📊 FINAL RESULTS (after limits):")
        for cat, items in self.categorized.items():
            print(f"   • {cat}: {len(items)} items")

        return self

    def _apply_limits(self):
        for category, max_count in MAX_ITEMS.items():
            if len(self.categorized[category]) > max_count:
                self.categorized[category] = self.categorized[category][:max_count]
                print(f"   ↳ {category}: trimmed to {max_count} items")

    def _format_item(self, item, category):
        title = item['title'].strip()
        summary = re.sub(r'<[^>]+>', '', item['summary'])
        summary = summary[:200] + "..." if len(summary) > 200 else summary
        link = item.get('link', '')
        source = item.get('source', '')

        if category == "events":
            # Don't try to extract dates from headlines - they don't contain them
            # Just use the article title as the event name
            # The date will be empty or we can use the pub date
            pub_date = item.get('published')
            if pub_date:
                date_str = pub_date.strftime('%b %d') if isinstance(pub_date, datetime) else "TBA"
            else:
                date_str = "TBA"
            return {"date": date_str, "name": title, "link": link, "source": source}

        elif category == "industryNews":
            return {"headline": title, "detail": summary, "link": link, "source": source}

        elif category == "ambassadors":
            patterns = [
                r'([A-Z][a-zA-Z\s\.\'\-]+)(?:\s+(?:is|as|for|joins|signs|tapped|named|becomes)).*?([A-Z][a-zA-Z\s&\'\-]+?)(?:\s+(?:as|brand|ambassador|face|muse|spokesperson|campaign))',
                r'([A-Z][a-zA-Z\s\.\'\-]+)(?:\s+(?:fronts|stars in|leads)).*?([A-Z][a-zA-Z\s&\'\-]+?)(?:\s+campaign)',
            ]
            for pattern in patterns:
                match = re.search(pattern, title)
                if match:
                    return {"talent": match.group(1).strip(), "brand": match.group(2).strip(), "link": link, "source": source}
            return {"talent": title[:50], "brand": "New Campaign", "link": link, "source": source}

        elif category == "partnerships":
            separators = [' x ', ' X ', ' × ', ' Collaborates With ', ' Partners With ', ' Teams Up With ', ' Joins Forces With ']
            for sep in separators:
                if sep in title:
                    parts = title.split(sep, 1)
                    return {"partner1": parts[0].strip(), "partner2": parts[1].strip(), "link": link, "source": source}
            return {"partner1": title[:40], "partner2": "Collaboration", "link": link, "source": source}

        elif category in ["castings", "filmProjects", "tvProjects"]:
            return {"talent": title[:60], "project": summary[:100], "link": link, "source": source}

        return None

    def _is_duplicate(self, item, category):
        existing = self.categorized.get(category, [])
        for e in existing:
            if item.get('headline') and e.get('headline') == item.get('headline'):
                return True
            if item.get('talent') and e.get('talent') == item.get('talent'):
                return True
            if item.get('name') and e.get('name') == item.get('name'):
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
    print("VIP & INDUSTRY NEWS - AUTOMATED NEWSLETTER SYSTEM v3.1")
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
