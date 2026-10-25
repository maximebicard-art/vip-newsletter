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

from vip_newsletter.config import is_job_listing, classify_news_hardness
from vip_newsletter.dedup import is_near_duplicate
from vip_newsletter.cache import _load_item_cache


class TestConfig(unittest.TestCase):
    def test_job_filter_catches_listings(self):
        self.assertTrue(is_job_listing("Marketing Manager jobs in New York"))
        self.assertFalse(is_job_listing("LVMH names new CEO"))

    def test_hard_news_classification(self):
        self.assertEqual(classify_news_hardness("lvmh acquires brand"), "hard")
        self.assertEqual(classify_news_hardness("trend report spring colors"), "soft")


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


if __name__ == "__main__":
    unittest.main()
