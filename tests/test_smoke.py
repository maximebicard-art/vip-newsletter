#!/usr/bin/env python3
"""
VIP Newsletter — Smoke Tests
v4.0: Minimal test coverage to prevent import/runtime regressions.
"""
import os
import sys
import unittest

# Ensure package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vip_newsletter.config import is_job_listing, classify_news_hardness, escape_rss
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
                             "tvPremieres": [{"title": "Nocturne", "date": "2026-10-30",
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
