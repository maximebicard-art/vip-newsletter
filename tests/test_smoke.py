#!/usr/bin/env python3
"""
VIP Newsletter — Smoke Tests
v4.0: Minimal test coverage to prevent import/runtime regressions.
v4.0.2: covers the Industry News routing fix, headline cleaning, the
ambassador job-ad filter, and ambassador brand validation.
"""
import os
import sys
import unittest

# Ensure package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vip_newsletter.config import (
    is_job_listing, classify_news_hardness, escape_rss, _clean_headline,
    MUSIC_EXCLUDE_SIGNALS,
)
from vip_newsletter.dedup import is_near_duplicate
from vip_newsletter.cache import _load_item_cache


class TestConfig(unittest.TestCase):
    def test_job_filter_catches_listings(self):
        self.assertTrue(is_job_listing("Marketing Manager jobs in New York"))
        self.assertFalse(is_job_listing("LVMH names new CEO"))

    def test_hard_news_classification(self):
        self.assertEqual(classify_news_hardness("lvmh acquires brand"), "hard")
        self.assertEqual(classify_news_hardness("trend report spring colors"), "soft")


class TestEscapeRss(unittest.TestCase):
    """v4.0.1 regression: feed titles often arrive pre-escaped, and titles
    pass through escape_rss more than once — output must stay single-escaped."""

    def test_plain_text_escaped_once(self):
        self.assertEqual(escape_rss("Anne & Valentin"), "Anne &amp; Valentin")

    def test_pre_escaped_feed_title_repaired(self):
        self.assertEqual(escape_rss("KATSEYE&#x27;s campaign"), "KATSEYE&#x27;s campaign")

    def test_double_escaped_legacy_entry_repaired(self):
        self.assertEqual(escape_rss("Anne &amp;amp; Valentin"), "Anne &amp; Valentin")

    def test_idempotent(self):
        once = escape_rss("A & B &#x27;quoted&#x27;")
        self.assertEqual(escape_rss(once), once)


class TestCleanHeadline(unittest.TestCase):
    """v4.0.2: _clean_headline strips editorial tags, trailing (Exclusive)
    markers, and repairs the missing space after commas."""

    def test_strips_leading_exclusive(self):
        self.assertEqual(
            _clean_headline("EXCLUSIVE: Zendaya for Lancome"),
            "Zendaya for Lancome")

    def test_strips_trailing_exclusive_marker(self):
        self.assertEqual(
            _clean_headline("Dior Names New Ambassador (Exclusive)"),
            "Dior Names New Ambassador")

    def test_repairs_comma_spacing(self):
        self.assertEqual(
            _clean_headline("Dior,Tomorrow Collection Revealed"),
            "Dior, Tomorrow Collection Revealed")

    def test_normal_headline_untouched(self):
        self.assertEqual(
            _clean_headline("Saint Laurent Unveils Fall Collection, First Look"),
            "Saint Laurent Unveils Fall Collection, First Look")


class TestAmbassadorJobFilter(unittest.TestCase):
    """v4.0.2: ambassador-recruitment ads are job listings in disguise and
    must not reach the Ambassadors section."""

    def test_looking_for_new_brand_ambassador(self):
        self.assertTrue(is_job_listing(
            "Anne & Valentin is looking for a new brand ambassador"))

    def test_seeking_global_ambassador(self):
        self.assertTrue(is_job_listing(
            "The brand is seeking a new global ambassador"))


class TestMusicExcludeSignals(unittest.TestCase):
    def test_signals_present(self):
        self.assertIn("you should listen", MUSIC_EXCLUDE_SIGNALS)
        self.assertIn("best albums", MUSIC_EXCLUDE_SIGNALS)


class TestIndustryNewsRouting(unittest.TestCase):
    """v4.0.2 regression: CLASSIFIERS has no industryNews entry, which made
    the section permanently empty. Curated industry sources must now route
    straight into industryNews."""

    def test_curated_source_populates_industry_news(self):
        from vip_newsletter.classifiers import categorize_content
        raw = [
            {"title": "LVMH Appoints New CEO of Fashion Group",
             "summary": "LVMH has named a new chief executive.",
             "link": "http://x/1", "source": "WWD"},
            {"title": "Kering Reports Q3 Revenue Decline",
             "summary": "Kering posted quarterly results.",
             "link": "http://x/2", "source": "BoF"},
        ]
        categorized = categorize_content(raw)
        headlines = [i["headline"] for i in categorized["industryNews"]]
        self.assertEqual(len(headlines), 2)
        self.assertTrue(any("LVMH" in h for h in headlines))

    def test_job_listing_still_excluded_from_industry_news(self):
        from vip_newsletter.classifiers import categorize_content
        raw = [{"title": "Retail Jobs in Paris | 75001",
                "summary": "apply now", "link": "x", "source": "WWD"}]
        categorized = categorize_content(raw)
        self.assertEqual(categorized["industryNews"], [])


class TestAmbassadorBrandValidation(unittest.TestCase):
    def test_trivial_brands_rejected(self):
        from vip_newsletter.classifiers import valid_ambassador_brand
        for bad in ["", "A", "The", "a new", "His"]:
            self.assertFalse(valid_ambassador_brand(bad), bad)

    def test_real_brand_accepted(self):
        from vip_newsletter.classifiers import valid_ambassador_brand
        self.assertTrue(valid_ambassador_brand("Lancome"))


class TestDedup(unittest.TestCase):
    def test_near_duplicate_detection(self):
        a = "Chanel names new ambassador for fragrance line"
        b = "Chanel appoints ambassador for new fragrance"
        self.assertTrue(is_near_duplicate(a, b))

    def test_non_duplicate(self):
        a = "Nike launches new running shoe"
        b = "Prada opens flagship store in Tokyo"
        self.assertFalse(is_near_duplicate(a, b))


class TestCache(unittest.TestCase):
    def test_load_missing_cache(self):
        result = _load_item_cache("/nonexistent/path.json")
        self.assertEqual(result, {})


class TestTemplateRendering(unittest.TestCase):
    """v4.0.1 regression: render() must provide the UPPERCASE keys the
    template consumes, and section items must render their headlines."""

    def _render(self, data):
        from jinja2 import Template
        tpl_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "newsletter_template.html")
        with open(tpl_path, encoding="utf-8") as f:
            return Template(f.read()).render(**data)

    def test_uppercase_signature_keys(self):
        html = self._render({"SENDER_NAME": "Max", "SENDER_TITLE": "VIP Director",
                             "COMPANY_NAME": "Acme", "COMPANY_ADDRESS": "",
                             "SENDER_EMAIL": "m@acme.com", "SENDER_PHONE": "",
                             "COMPANY_URL": "", "CALENDAR_LINK": "",
                             "MONTH": "October", "YEAR": "2026", "PREHEADER": ""})
        self.assertIn("Max", html)
        self.assertIn("October 2026", html)

    def test_castings_render_headline(self):
        html = self._render({"castings": [{"headline": "Ayo Edebiri Joins Cast",
                                           "summary": "", "link": "", "source": "Deadline"}]})
        self.assertIn("Ayo Edebiri Joins Cast", html)

    def test_music_and_premieres_inside_wrapper(self):
        html = self._render({"newMusic": [{"title": "New Album Announced",
                                           "source": "Pitchfork", "link": ""}],
                             "tvPremieres": [{"title": "Nocturne", "date": "Fri, Oct 30",
                                              "platform": "Apple TV", "season": 1,
                                              "is_new_series": True}]})
        self.assertIn("New Album Announced", html)
        self.assertIn("Nocturne", html)
        # both sections must appear BEFORE the end of .email-wrapper
        wrapper_end = html.index("end .email-wrapper")
        self.assertLess(html.index("New Music"), wrapper_end)
        self.assertLess(html.index("TV Premieres"), wrapper_end)

    def test_no_empty_strong_tags(self):
        html = self._render({"castings": [{"headline": "Some Casting", "summary": "",
                                           "link": "", "source": ""}]})
        self.assertNotIn('<strong style="font-weight: bold; color: #000000;"></strong>', html)


if __name__ == "__main__":
    unittest.main()
