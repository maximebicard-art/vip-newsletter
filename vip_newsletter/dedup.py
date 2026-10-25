#!/usr/bin/env python3
"""
VIP Newsletter — Near-Duplicate Detection
v4.0: Extracted from monolithic v3.36.
"""
import re as _re

_DEDUP_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "for", "with", "from", "into", "onto",
    "his", "her", "its", "their", "this", "that", "these", "those", "is", "are",
    "was", "were", "be", "been", "as", "at", "by", "in", "of", "on", "to", "up",
    "new", "latest", "first", "says", "said", "will", "has", "have", "had",
    "exclusive", "must", "read", "summary", "more", "than", "who", "what",
    "how", "why", "when", "where", "amid", "ahead", "after", "before", "over",
}

_DEDUP_TOPIC_WORDS = {
    "campaign", "collection", "ambassador", "collab", "collaboration",
    "capsule", "launch", "show", "trailer", "season", "cast", "casts",
    "joins", "appearance", "debut", "premiere", "partnership", "boutique",
    "acquires", "acquisition", "appointed", "names", "voice", "role",
    "leak", "leaks", "bootleg", "piracy",
}

_DEDUP_GENERIC = {
    "star", "stars", "season", "series", "show", "shows", "film", "films",
    "movie", "movies", "actor", "actress", "brand", "brands", "fashion",
    "beauty", "luxury", "style", "collection", "campaign", "video", "prime",
    "netflix", "hulu", "hbo", "amazon", "apple", "disney", "paramount",
    "exclusive", "official", "global", "world", "york", "paris", "london",
    "milan", "week", "spring", "summer", "fall", "winter", "autumn",
    "regular", "recurring", "joins", "adds", "sets", "part", "years",
}


def _dedup_stem(word):
    for suf in ("ing", "ed", "es", "s"):
        if len(word) > len(suf) + 3 and word.endswith(suf):
            return word[: -len(suf)]
    return word


def _dedup_tokens(text):
    if not text:
        return set()
    t = _re.sub(r"[^a-z0-9\s-]", " ", text.lower())
    toks = {w.strip("-") for w in t.split() if len(w.strip("-")) >= 3}
    return {_dedup_stem(w) for w in toks if w not in _DEDUP_STOPWORDS}


def is_near_duplicate(text_a, text_b):
    ta, tb = _dedup_tokens(text_a), _dedup_tokens(text_b)
    if not ta or not tb:
        return False
    inter = ta & tb
    if not inter:
        return False

    jaccard = len(inter) / len(ta | tb)
    containment = len(inter) / min(len(ta), len(tb))

    if jaccard >= 0.45:
        return True
    if containment >= 0.75 and min(len(ta), len(tb)) >= 2:
        return True

    shared_entities = {
        w for w in inter
        if w not in _DEDUP_TOPIC_WORDS
        and w not in _DEDUP_GENERIC
        and len(w) >= 4
    }
    shared_topics = inter & _DEDUP_TOPIC_WORDS
    if shared_entities and shared_topics and len(inter) >= 2:
        return True

    long_entities = {w for w in shared_entities if len(w) >= 5}
    if len(long_entities) >= 3:
        return True

    return False


def _dedup_text(item):
    parts = [
        item.get("headline") or "",
        item.get("talent") or "",
        item.get("brand") or "",
        item.get("name") or "",
        item.get("project") or "",
    ]
    return " ".join(p for p in parts if p).strip()
