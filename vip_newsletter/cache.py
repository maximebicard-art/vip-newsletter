#!/usr/bin/env python3
"""
VIP Newsletter — Persistent Item Cache
v4.0: Extracted from monolithic v3.36.
"""
import os
import json
from datetime import datetime, timedelta

CACHE_PATH = os.getenv("ITEM_CACHE_PATH", "item_cache.json")
CACHE_RETENTION_DAYS = int(os.getenv("CACHE_RETENTION_DAYS", "10"))


def _load_item_cache(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_item_cache(path, cache):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    except OSError as e:
        print(f"   ⚠️ Could not write item cache ({path}): {e}")


def merge_and_persist_cache(raw_items, cache_path=None):
    """Merge fresh items into persistent cache, prune stale entries,
    purge job ads retroactively, and return rebuilt item list."""
    if cache_path is None:
        cache_path = CACHE_PATH
    from .config import is_job_listing, _clean_gn_summary, escape_rss

    now = datetime.now()
    retention = timedelta(days=CACHE_RETENTION_DAYS)
    cache = _load_item_cache(cache_path)

    # Migrate legacy keys
    migrated = 0
    for old_key in [k for k in cache if "||" not in k]:
        rec = cache.pop(old_key)
        new_key = f"{old_key}||{rec.get('category_hint', '')}"
        cache.setdefault(new_key, rec)
        migrated += 1
    if migrated:
        print(f"   ↻ Migrated {migrated} cache entries to per-category keys")

    added = 0
    for it in raw_items:
        base = (it.get("link") or "").strip() or (it.get("title") or "").strip()
        if not base:
            continue
        key = f"{base}||{it.get('category_hint', '')}"
        if key in cache:
            continue
        pub = it.get("published")
        cache[key] = {
            "title": it.get("title", ""),
            "summary": it.get("summary", ""),
            "link": it.get("link", ""),
            "source": it.get("source", ""),
            "category_hint": it.get("category_hint", ""),
            "published_iso": pub.isoformat() if isinstance(pub, datetime) else None,
            "first_seen_iso": now.isoformat(),
        }
        added += 1

    # Prune stale
    kept, pruned = {}, 0
    for key, rec in cache.items():
        basis = rec.get("published_iso") or rec.get("first_seen_iso")
        try:
            basis_dt = datetime.fromisoformat(basis) if basis else now
        except (ValueError, TypeError):
            basis_dt = now
        if now - basis_dt <= retention:
            kept[key] = rec
        else:
            pruned += 1

    # Retroactive job purge
    purged_jobs = 0
    for key in [k for k, r in kept.items()
                if is_job_listing(r.get("title", ""), r.get("summary", ""))]:
        kept.pop(key)
        purged_jobs += 1

    # Re-clean GN summaries
    recleaned = 0
    for rec in kept.values():
        new_summary = _clean_gn_summary(rec.get("summary", ""), rec.get("title", ""))
        if new_summary != rec.get("summary", ""):
            rec["summary"] = new_summary
            recleaned += 1

    # v4.0.1 FIX: normalize HTML entities in cached titles/summaries.
    # Entries written before the escape_rss fix contain double-encoded
    # entities ("&amp;amp;"); escape_rss now deep-unescapes then escapes
    # exactly once, which repairs them in place. Idempotent on clean text.
    refixed = 0
    for rec in kept.values():
        for field in ("title", "summary"):
            old = rec.get(field, "")
            new = escape_rss(old)
            if new != old:
                rec[field] = new
                refixed += 1
    if refixed:
        print(f"   🔧 Repaired HTML entities in {refixed} cached field(s)")

    _save_item_cache(cache_path, kept)

    # Rebuild raw_items
    rebuilt = []
    for rec in kept.values():
        published = None
        if rec.get("published_iso"):
            try:
                published = datetime.fromisoformat(rec["published_iso"])
            except (ValueError, TypeError):
                published = None
        rebuilt.append({
            "title": rec.get("title", ""),
            "summary": rec.get("summary", ""),
            "link": rec.get("link", ""),
            "published": published,
            "source": rec.get("source", ""),
            "category_hint": rec.get("category_hint", ""),
        })

    extra = ""
    if purged_jobs:
        extra += f", 🚫 {purged_jobs} cached job ads purged"
    if recleaned:
        extra += f", ✂️ {recleaned} summaries re-cleaned"
    print(f"\n🗃️  Item cache: +{added} new this run, {pruned} pruned, "
          f"{len(kept)} retained (retention {CACHE_RETENTION_DAYS}d){extra} → "
          f"{len(rebuilt)} items to categorize")
    return rebuilt
