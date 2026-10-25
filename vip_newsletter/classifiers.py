#!/usr/bin/env python3
"""
VIP Newsletter — Content Classifiers & Formatters
v4.0: Extracted from monolithic v3.36.
"""
import re
from .config import (
    CLASSIFIERS, ENTERTAINMENT_SOURCES, NON_ENTERTAINMENT_CATEGORIES,
    SOURCE_CATEGORY_RESTRICTIONS, HARD_NEWS_SIGNALS, classify_news_hardness,
    partnership_prestige, looks_like_explicit_ambassador,
    looks_like_ambassador_campaign, _clean_headline, is_job_listing,
    _clean_gn_summary, escape_rss,
)
from .dedup import is_near_duplicate, _dedup_text


def _passes_classifier(text_lower, classifier):
    if not text_lower:
        return False
    keywords = classifier.get("keywords", [])
    exclude = classifier.get("exclude", [])
    must_have = classifier.get("must_have", [])

    if any(exc in text_lower for exc in exclude):
        return False
    if must_have and not any(mh in text_lower for mh in must_have):
        return False
    return any(kw in text_lower for kw in keywords)


def _format_industry_news(item):
    title = item.get("title", "")
    summary = item.get("summary", "")
    text_lower = f"{title} {summary}".lower()

    if is_job_listing(title, summary):
        return None

    hardness = classify_news_hardness(text_lower)
    prestige = 0
    if hardness == "hard":
        prestige = 3 if any(b in text_lower for b in {
            "lvmh", "kering", "chanel", "dior", "hermès", "hermes", "prada",
            "gucci", "cartier", "tiffany", "bulgari", "ysl", "saint laurent",
            "balenciaga", "bottega veneta", "valentino", "celine", "fendi",
            "givenchy", "loewe", "moncler", "burberry", "versace", "armani",
        }) else 2

    return {
        "headline": escape_rss(title),
        "summary": escape_rss(summary),
        "link": item.get("link", ""),
        "source": item.get("source", ""),
        "hardness": hardness,
        "prestige": prestige,
    }


def _extract_ambassador(item):
    title = item.get("title", "")
    summary = item.get("summary", "")
    text_lower = f"{title} {summary}".lower()

    if is_job_listing(title, summary):
        return None

    brand = ""
    talent = ""

    brand_match = re.search(r'(?:for|with|of|at)\s+([A-Z][A-Za-z0-9\s&\'\.]+?)(?:\s+as|\s+campaign|$)', title)
    if brand_match:
        brand = brand_match.group(1).strip()

    talent_match = re.search(r'^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)', title)
    if talent_match:
        talent = talent_match.group(1).strip()

    if looks_like_explicit_ambassador(text_lower):
        confidence = "explicit"
    elif looks_like_ambassador_campaign(text_lower):
        confidence = "campaign"
    else:
        confidence = "weak"

    return {
        "headline": escape_rss(title),
        "summary": escape_rss(summary),
        "link": item.get("link", ""),
        "source": item.get("source", ""),
        "talent": escape_rss(talent),
        "brand": escape_rss(brand),
        "confidence": confidence,
    }


def _extract_partnership(item):
    title = item.get("title", "")
    summary = item.get("summary", "")
    text_lower = f"{title} {summary}".lower()

    if is_job_listing(title, summary):
        return None

    prestige = partnership_prestige(text_lower)

    return {
        "headline": escape_rss(title),
        "summary": escape_rss(summary),
        "link": item.get("link", ""),
        "source": item.get("source", ""),
        "prestige": prestige,
    }


def _format_item(item, category):
    if category == "industryNews":
        return _format_industry_news(item)
    elif category == "ambassadors":
        return _extract_ambassador(item)
    elif category == "partnerships":
        return _extract_partnership(item)
    elif category in ("castings", "filmProjects", "tvProjects"):
        return {
            "headline": escape_rss(item.get("title", "")),
            "summary": escape_rss(item.get("summary", "")),
            "link": item.get("link", ""),
            "source": item.get("source", ""),
        }
    return None


def _is_duplicate(new_item, existing_items, category):
    new_text = _dedup_text(new_item)
    if not new_text:
        return False

    for existing in existing_items:
        if is_near_duplicate(new_text, _dedup_text(existing)):
            return True
    return False


def apply_limits(items, max_count):
    return items[:max_count]


def apply_industry_news_tier(items):
    hard = [i for i in items if i.get("hardness") == "hard"]
    soft = [i for i in items if i.get("hardness") != "hard"]
    hard_sorted = sorted(hard, key=lambda x: x.get("prestige", 0), reverse=True)
    soft_sorted = sorted(soft, key=lambda x: x.get("prestige", 0), reverse=True)
    return hard_sorted + soft_sorted


def valid_ambassador_brand(brand):
    if not brand:
        return False
    trivial = {"the", "a", "an", "new", "old", "big", "small", "his", "her"}
    return brand.lower() not in trivial and len(brand) > 1


def categorize_content(raw_items):
    from .config import RSS_SOURCES, CATEGORY_MAP
    categorized = {cat: [] for cat in CATEGORY_MAP.values()}
    categorized["events"] = []
    categorized["inTheaters"] = []
    categorized["newMusic"] = []
    categorized["tvPremieres"] = []

    for item in raw_items:
        title = item.get("title", "")
        summary = item.get("summary", "")
        source = item.get("source", "")
        text_lower = f"{title} {summary}".lower()

        if is_job_listing(title, summary):
            continue

        assigned = False
        for feed_key, target_cat in CATEGORY_MAP.items():
            if assigned:
                break
            for src_def in RSS_SOURCES.get(feed_key, []):
                if src_def["source"] == source:
                    if _passes_classifier(text_lower, CLASSIFIERS.get(target_cat, {})):
                        formatted = _format_item(item, target_cat)
                        if formatted and not _is_duplicate(formatted, categorized[target_cat], target_cat):
                            categorized[target_cat].append(formatted)
                            assigned = True
                    break

        if not assigned and source in ENTERTAINMENT_SOURCES:
            for ent_cat in ("castings", "filmProjects", "tvProjects"):
                if _passes_classifier(text_lower, CLASSIFIERS.get(ent_cat, {})):
                    formatted = _format_item(item, ent_cat)
                    if formatted and not _is_duplicate(formatted, categorized[ent_cat], ent_cat):
                        categorized[ent_cat].append(formatted)
                        assigned = True
                    break

    return categorized
