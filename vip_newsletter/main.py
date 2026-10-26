#!/usr/bin/env python3
"""
VIP Newsletter — Main Orchestrator
v4.0: Extracted from monolithic v3.36.
"""
import os
import sys
from datetime import datetime, timedelta

from .config import (
    RSS_SOURCES, MAX_ITEMS, RECIPIENT_EMAIL, TMDB_API_KEY,
    fetch_feed, _clean_gn_title, _clean_gn_summary, escape_rss,
)
from .cache import merge_and_persist_cache
from .dedup import is_near_duplicate
from .fetchers import get_tmdb_films, get_tvmaze_premieres, get_music_from_rss, get_curated_events
from .ranking import GeminiRanker
from .classifiers import categorize_content, apply_limits, apply_industry_news_tier
from .senders import EmailSender
from .template import render_newsletter, generate_plain_text, save_newsletter


class NewsletterScraper:
    def __init__(self):
        self.gemini = GeminiRanker()
        self.raw_items = []
        self.data = {}

    def fetch_feeds(self):
        print("\n📡 Fetching RSS feeds...")
        total_new = 0
        for category, sources in RSS_SOURCES.items():
            for src in sources:
                print(f"   → {src['source']}")
                feed, note = fetch_feed(src["url"])
                if note:
                    print(f"      {note}")
                if not feed or not hasattr(feed, "entries"):
                    continue
                for entry in feed.entries:
                    title = entry.get("title", "")
                    link = entry.get("link", "")
                    summary = entry.get("summary", entry.get("description", ""))
                    if src["source"].startswith("GN "):
                        title = _clean_gn_title(title)
                        summary = _clean_gn_summary(summary, title)
                    pub = None
                    for field in ["published_parsed", "updated_parsed"]:
                        if hasattr(entry, field) and getattr(entry, field):
                            pub = datetime(*getattr(entry, field)[:6])
                            break
                    self.raw_items.append({
                        "title": escape_rss(title),
                        "summary": escape_rss(summary),
                        "link": link,
                        "published": pub,
                        "source": src["source"],
                        "category_hint": category,
                    })
                    total_new += 1
        print(f"\n📥 Total raw items fetched: {total_new}")

    def build_cache(self):
        self.raw_items = merge_and_persist_cache(self.raw_items)

    def categorize(self):
        print("\n🗂️  Categorizing content...")
        self.data = categorize_content(self.raw_items)

    def fetch_external(self):
        print("\n🎬 Fetching external data...")
        if TMDB_API_KEY:
            films = get_tmdb_films()
            self.data["inTheaters"] = [
                {"title": f["title"], "release_date": f["release_date"],
                 "status": f["status"], "cast": f.get("cast", [])}
                for f in films if f["status"] == "Now Playing"
            ][:MAX_ITEMS["inTheaters"]]

        premieres = get_tvmaze_premieres()
        self.data["tvPremieres"] = [
            {"title": p["title"], "date": p["date"], "platform": p["platform"],
             "season": p["season"], "is_new_series": p["is_new_series"]}
            for p in premieres
        ][:MAX_ITEMS["tvPremieres"]]

        music = get_music_from_rss()
        self.data["newMusic"] = [
            {"title": m["title"], "source": m["source"], "link": m["link"]}
            for m in music
        ][:MAX_ITEMS["newMusic"]]

    def rank_and_limit(self):
        print("\n⭐ Ranking and applying limits...")
        for cat in ["industryNews", "ambassadors", "partnerships", "castings",
                    "filmProjects", "tvProjects"]:
            items = self.data.get(cat, [])
            if self.gemini.is_available() and len(items) > MAX_ITEMS[cat]:
                items = self.gemini.rank(items, cat)
            if cat == "industryNews":
                items = apply_industry_news_tier(items)
            self.data[cat] = apply_limits(items, MAX_ITEMS[cat])
            print(f"   {cat}: {len(self.data[cat])} items")

    def fetch_events(self):
        print("\n📅 Fetching events...")
        self.data["events"] = get_curated_events(self.gemini)

    def render(self):
        print("\n🎨 Rendering newsletter...")
        now = datetime.now()
        self.data["week_of"] = now.strftime("%B %d, %Y")
        # v4.0.1 FIX: the template expects UPPERCASE variable names
        # ({{ SENDER_NAME }}, {{ MONTH }}, {{ YEAR }}, ...). The previous
        # lowercase keys silently rendered an empty subtitle and an empty
        # signature block.
        self.data["PREHEADER"] = os.getenv("PREHEADER", "")
        self.data["MONTH"] = now.strftime("%B")
        self.data["YEAR"] = now.strftime("%Y")
        self.data["SENDER_NAME"] = os.getenv("SENDER_NAME", "")
        self.data["SENDER_TITLE"] = os.getenv("SENDER_TITLE", "")
        self.data["SENDER_EMAIL"] = os.getenv("SENDER_EMAIL", "")
        self.data["SENDER_PHONE"] = os.getenv("SENDER_PHONE", "")
        self.data["COMPANY_NAME"] = os.getenv("COMPANY_NAME", "")
        self.data["COMPANY_ADDRESS"] = os.getenv("COMPANY_ADDRESS", "")
        self.data["COMPANY_URL"] = os.getenv("COMPANY_URL", "")
        self.data["CALENDAR_LINK"] = os.getenv("CALENDAR_LINK", "")
        html = render_newsletter(self.data)
        text = generate_plain_text(self.data)
        return html, text

    def send(self, html, text):
        if os.getenv("SEND_EMAIL", "1") != "1":
            print("\n📭 SEND_EMAIL=0 — skipping send, saving to disk")
            save_newsletter(html, text)
            return
        sender = EmailSender()
        subject = f"VIP & Industry News — Week of {datetime.now().strftime('%B %d, %Y')}"
        success = sender.send(html, text, subject)
        if success:
            save_newsletter(html, text)

    def run(self):
        print("=" * 60)
        print("VIP & INDUSTRY NEWS v4.0")
        print("=" * 60)
        self.fetch_feeds()
        self.build_cache()
        self.categorize()
        self.fetch_external()
        self.fetch_events()
        self.rank_and_limit()
        html, text = self.render()
        self.send(html, text)
        print("\n✅ Done.")


def main():
    scraper = NewsletterScraper()
    scraper.run()


if __name__ == "__main__":
    main()
