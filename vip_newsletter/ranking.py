#!/usr/bin/env python3
"""
VIP Newsletter — Gemini Ranking Engine
v4.0: Extracted from monolithic v3.36.
v4.0.2: GeminiRanker.rank() takes a caller-controlled max_items (the
newsletter's MAX_ITEMS[category]) instead of a hardcoded 10.
"""
import os
import re
import requests

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")


class GeminiRanker:
    def __init__(self, api_key=""):
        self.api_key = api_key or GEMINI_API_KEY
        self.model = "gemini-3.8-flash"
        self.base = "https://generativelanguage.googleapis.com/v1beta/models"
        self._available = None

    def is_available(self):
        if self._available is None:
            self._available = bool(self.api_key)
        return self._available

    def rank(self, items, category, max_items=10):
        """v4.0.2: max_items is caller-controlled (MAX_ITEMS[category])
        instead of a hardcoded 10 — industryNews/events legitimately want
        more items than partnerships, and Gemini previously ignored that."""
        if not items:
            return []

        item_lines = []
        for i, item in enumerate(items):
            line = f"[{i}] {item.get('headline', item.get('title', ''))}"
            if item.get("source"):
                line += f" | Source: {item['source']}"
            if item.get("summary"):
                line += f" | {item['summary'][:120]}"
            item_lines.append(line)

        priority = self._get_priority(category)
        return _gemini_select(self, item_lines, category, max_items, priority, items)

    def _get_priority(self, category):
        priorities = {
            "industryNews": (
                "PRIORITIZE: 1) C-suite changes, acquisitions, mergers, IPOs, "
                "restructurings, bankruptcy filings 2) Creative-director appointments "
                "3) New store openings, flagship launches, pop-ups 4) Earnings, "
                "revenue results, financial performance 5) Major investment rounds "
                "6) Layoffs, workforce changes. SKIP: routine product launches, "
                "minor collabs, casting-only stories, interviews, profiles, "
                "festival coverage, runway reviews."
            ),
            "ambassadors": (
                "PRIORITIZE: 1) Luxury & prestige brand ambassadors (LVMH, Kering, "
                "Chanel, Dior, Hermès, Prada, Gucci, Cartier, Tiffany, Bulgari, "
                "YSL, Balenciaga, Bottega Veneta, Valentino, Celine, Fendi, "
                "Givenchy, Loewe, Moncler, Burberry, Versace, Armani, Brunello "
                "Cucinelli, Estée Lauder, Lancôme, Shiseido) 2) Mid-tier brand "
                "ambassadors (Nike, Adidas, New Balance, Puma, Uniqlo, Gap, Levi's, "
                "Lacoste, Diesel, Coach, Michael Kors, Kate Spade, Marc Jacobs, "
                "Tommy Hilfiger, Calvin Klein, Fenty, Savage X Fenty, Rihanna, "
                "Puma, Adidas, Nike) 3) High-profile celebrity campaigns "
                "4) Global/house ambassador announcements. SKIP: routine product "
                "launches without ambassador, minor collabs, casting-only stories, "
                "interviews, profiles, festival coverage, runway reviews, "
                "executive appointments, people-moves, store openings."
            ),
            "partnerships": (
                "PRIORITIZE: 1) Luxury brand collabs (LVMH, Kering, Chanel, Dior, "
                "Hermès, Prada, Gucci, Cartier, Tiffany, Bulgari, YSL, Balenciaga, "
                "Bottega Veneta, Valentino, Celine, Fendi, Givenchy, Loewe, Moncler, "
                "Burberry, Versace, Armani, Brunello Cucinelli) 2) Mid-tier brand "
                "collabs (Nike, Adidas, New Balance, Puma, Uniqlo, Gap, Levi's, "
                "Lacoste, Diesel, Coach, Michael Kors, Kate Spade, Marc Jacobs, "
                "Tommy Hilfiger, Calvin Klein, Fenty, Savage X Fenty) 3) Celebrity "
                "x brand capsule collections 4) Limited-edition drops with cultural "
                "significance. SKIP: routine product launches, minor collabs, "
                "casting-only stories, interviews, profiles, festival coverage, "
                "runway reviews, executive appointments, people-moves, store openings."
            ),
            "castings": (
                "PRIORITIZE: 1) A-list talent in major projects 2) Prestige TV/streaming "
                "series 3) Major studio films 4) Notable director attachments "
                "5) High-profile replacements. SKIP: minor roles, background casting, "
                "commercials, music videos, reality TV, web series, student films."
            ),
            "filmProjects": (
                "PRIORITIZE: 1) Major studio productions 2) Prestige indie films "
                "3) Notable director projects 4) Franchise entries 5) Biopics of "
                "significant figures. SKIP: minor indie projects, student films, "
                "short films, direct-to-streaming without notable talent."
            ),
            "tvProjects": (
                "PRIORITIZE: 1) Major streaming platform series 2) Prestige cable "
                "shows 3) Notable creator attachments 4) Franchise/sequel series "
                "5) Limited series with A-list talent. SKIP: reality TV, game shows, "
                "talk shows, minor web series, pilots without notable attachments."
            ),
            "inTheaters": (
                "PRIORITIZE: 1) Major studio releases 2) Prestige indie films "
                "3) Notable director projects 4) Franchise entries 5) Films with "
                "significant cultural buzz. SKIP: minor releases, direct-to-video, "
                "films with limited theatrical runs."
            ),
            "newMusic": (
                "PRIORITIZE: 1) Major artist album releases 2) Notable single drops "
                "3) World/arena tour announcements 4) Residency announcements "
                "5) Significant music video releases. SKIP: minor indie releases, "
                "local shows, club performances, mixtapes without notable artists."
            ),
            "tvPremieres": (
                "PRIORITIZE: 1) Major streaming platform premieres 2) Prestige cable "
                "premieres 3) Notable creator-driven series 4) Franchise/sequel "
                "premieres 5) Limited series with A-list talent. SKIP: reality TV, "
                "game shows, talk shows, minor web series."
            ),
        }
        return priorities.get(category, "")


def _gemini_select(ranker, item_lines, category, max_items, priority, original_items):
    if not item_lines:
        return []

    prompt = f"""You are curating VIP & Industry News for a high-end fashion and entertainment newsletter.

Category: {category}

Here are the candidate items (each line is [index] headline | source | summary):

{chr(10).join(item_lines)}

{priority}

Please select the TOP {max_items} most newsworthy items for this category.
Return ONLY a comma-separated list of the selected indices (e.g., "0, 3, 5, 7").
If fewer than {max_items} items are truly newsworthy, return fewer indices.
Do not include any explanation or commentary."""

    try:
        resp = requests.post(
            f"{ranker.base}/{ranker.model}:generateContent?key={ranker.api_key}",
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=30,
        )
        if resp.status_code != 200:
            print(f"      ⚠️ Gemini API error {resp.status_code}: {resp.text[:200]}")
            return original_items[:max_items]

        text = resp.json().get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        indices = [int(x.strip()) for x in re.findall(r"\b\d+\b", text) if int(x.strip()) < len(original_items)]
        selected = [original_items[i] for i in indices]
        print(f"      ✓ Gemini selected {len(selected)}/{len(original_items)} for {category}")
        return selected
    except Exception as e:
        print(f"      ⚠️ Gemini ranking error for {category}: {e}")
        return original_items[:max_items]
