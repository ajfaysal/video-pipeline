"""
seo.py - SEO & sales optimization for Adobe Stock submissions.

For every generation StockFlow produces:

* A commercial title capped at 70 characters (Adobe Stock's hard limit),
  built from the subject + highest-value buyer modifiers, title-cased,
  deduped, and stripped of characters Adobe flags.
* A comma-separated list of up to 50 high-performing tags, ranked by
  buyer-intent weight: subject -> attributes -> commercial use-cases ->
  style/technique -> conceptual -> seasonal. Adobe search weighs the first
  ~10 tags heaviest, so the ranking here matters.

Heuristics are distilled from Adobe Stock contributor guidance and common
top-seller metadata patterns (copy-space, isolated, mockup, lifestyle...).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

TITLE_MAX = 70
TAG_MAX = 50
TAG_HARD_LIMIT = 50  # Adobe Stock rejects assets with > 50 keywords

_STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "in", "on", "for", "to", "at", "with",
    "by", "from", "is", "are", "this", "that", "over", "under", "into", "about",
    "using", "use", "used", "via", "very", "etc", "its", "it", "as", "so",
    "generate", "create", "make", "image", "picture", "photo", "illustration",
    "stock", "commercial", "professional", "high", "quality", "beautiful",
}

# Acronyms that must stay uppercased in titles/tags.
_ACRONYMS = {
    "ai": "AI", "iot": "IoT", "ev": "EV", "ui": "UI", "ux": "UX",
    "vr": "VR", "ar": "AR", "3d": "3D", "5g": "5G", "nft": "NFT",
    "api": "API", "saas": "SaaS", "seo": "SEO", "led": "LED", "4k": "4K",
}

# Ordered modifier pools (highest buyer value first).
_USE_CASE_MODIFIERS = [
    "copy space", "banner", "isolated", "mockup", "background",
    "close-up", "top view", "flat lay", "lifestyle", "studio shot",
]
_COMMERCIAL_MODIFIERS = [
    "business", "marketing", "technology", "modern", "creative",
    "professional", "minimal", "luxury", "abstract",
]
_CONCEPT_TAGS = [
    "success", "growth", "innovation", "connection", "future", "digital",
    "sustainability", "wellness", "communication", "teamwork",
]

# Universal high-performing stock tags used to fill the list up to the
# requested count (ranked by typical buyer-filter usage).
_STOCK_FILLER = [
    "copy space", "horizontal", "nobody", "selective focus", "depth of field",
    "bokeh", "soft light", "natural light", "vibrant", "bright", "elegant",
    "contemporary", "stylish", "concept", "design", "template",
    "web banner", "social media", "advertising", "presentation", "editorial",
    "negative space", "text space", "high resolution", "detail", "texture",
    "simplicity", "minimalism", "trendy", "inspiration", "closeup",
    "indoors", "outdoors", "vertical", "moody", "fresh", "clean",
    "realistic", "vivid", "premium", "modern lifestyle", "creativity",
]

_SEASONAL = {
    1: ["new year", "resolution", "winter"],
    2: ["valentine", "love", "winter"],
    3: ["spring", "renewal", "easter"],
    4: ["spring", "earth day", "eco"],
    5: ["mother's day", "spring", "wellness"],
    6: ["summer", "father's day", "pride"],
    7: ["summer", "vacation", "independence day"],
    8: ["back to school", "summer", "education"],
    9: ["autumn", "back to school", "labor day"],
    10: ["halloween", "autumn", "cyber security month"],
    11: ["thanksgiving", "black friday", "autumn"],
    12: ["christmas", "holiday", "winter"],
}


@dataclass
class SeoPackage:
    title: str
    tags: List[str]
    tags_csv: str
    title_length: int
    tag_count: int

    def as_dict(self) -> Dict:
        return {
            "title": self.title,
            "tags": self.tags,
            "tags_csv": self.tags_csv,
            "title_length": self.title_length,
            "tag_count": self.tag_count,
        }


def _tokenize(prompt: str) -> List[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9'\-]*", prompt.lower())
    return [w for w in words if w not in _STOPWORDS and len(w) > 2]


def _clean_words(text: str, min_len: int = 2) -> List[str]:
    """Ordered, deduped content words from free text."""
    seen: List[str] = []
    for w in re.findall(r"[a-zA-Z][a-zA-Z0-9'\-]*", text.lower()):
        if w not in _STOPWORDS and len(w) >= min_len and w not in seen:
            seen.append(w)
    return seen


def _key_phrases(prompt: str, max_phrases: int = 8) -> List[str]:
    """Natural tag phrases: cleaned comma-segments (<=6 words) + bigrams."""
    phrases: List[str] = []

    def push(p: str) -> None:
        if p and p not in phrases and len(p) >= 3:
            phrases.append(p)

    for segment in re.split(r"[,;:.!?]+", prompt.lower()):
        words = [w for w in segment.split() if w not in _STOPWORDS and len(w) > 1]
        if not words:
            continue
        if 2 <= len(words) <= 6:
            push(" ".join(words))
        for i in range(len(words) - 1):
            push(f"{words[i]} {words[i + 1]}")
        if len(phrases) >= max_phrases:
            break
    return phrases[:max_phrases]


def _display_word(word: str) -> str:
    return _ACRONYMS.get(word, word.capitalize())


def make_title(prompt: str, niche: Optional[str] = None, max_len: int = TITLE_MAX) -> str:
    """Build a commercial title guaranteed <= max_len characters.

    Structure: deduped subject words (niche first), then one high-value
    commercial modifier if it still fits, title-cased with acronyms preserved.
    """
    words: List[str] = []
    for text in (niche or "", prompt):
        for w in _clean_words(text):
            if w not in words:
                words.append(w)

    # Greedily fit subject words, reserving room for a modifier tail.
    title = ""
    for w in words:
        candidate = f"{title} {_display_word(w)}".strip()
        if len(candidate) > max_len - 14:  # keep space for ', Modifier'
            break
        title = candidate
    if not title:  # degenerate prompt - fall back to raw truncation
        title = re.sub(r"\s+", " ", prompt.strip())[: max_len - 14]

    for mod in _USE_CASE_MODIFIERS + _COMMERCIAL_MODIFIERS:
        candidate = f"{title}, {_display_word(mod)}"
        if len(candidate) <= max_len and mod not in title.lower():
            title = candidate
            break

    title = re.sub(r"[\"@#$%^&*()_+=\[\]{}<>~`|\\]", "", title).strip(" ,.-;:")
    return title[:max_len]


def make_tags(
    prompt: str,
    niche: Optional[str] = None,
    title: Optional[str] = None,
    count: int = TAG_MAX,
    month: Optional[int] = None,
) -> List[str]:
    """Build a ranked, deduped tag list of exactly `count` (<=50) tags."""
    count = max(1, min(TAG_HARD_LIMIT, count))
    ranked: List[str] = []

    def push(tag: str) -> None:
        tag = re.sub(r"\s+", " ", tag.strip().lower())
        if tag and len(tag) >= 2 and tag not in ranked and tag not in _STOPWORDS:
            ranked.append(tag)

    # 1. Subject phrases from the prompt (search-relevant, in prompt order).
    for phrase in _key_phrases(prompt, max_phrases=14):
        push(phrase)
    for word in _clean_words(prompt):
        push(word)

    # 2. Niche & title words.
    if niche:
        push(niche)
        for w in _tokenize(niche):
            push(w)
    if title:
        for w in _tokenize(title):
            push(w)

    # 3. Commercial use-case & attribute tags (what buyers filter by).
    for tag in _USE_CASE_MODIFIERS:
        push(tag)
    for tag in _COMMERCIAL_MODIFIERS:
        push(tag)

    # 4. Concept tags.
    for tag in _CONCEPT_TAGS:
        push(tag)

    # 5. Seasonal relevance.
    if month:
        for tag in _SEASONAL.get(month, []):
            push(tag)

    # 6. Universal high-performing filler to reach the requested count.
    for tag in _STOCK_FILLER:
        if len(ranked) >= count:
            break
        push(tag)

    return ranked[:count]


def build_seo_package(
    prompt: str,
    niche: Optional[str] = None,
    title_length: int = TITLE_MAX,
    tag_count: int = TAG_MAX,
    month: Optional[int] = None,
) -> SeoPackage:
    import datetime as _dt

    month = month or _dt.datetime.now().month
    title = make_title(prompt, niche=niche, max_len=title_length)
    tags = make_tags(prompt, niche=niche, title=title, count=tag_count, month=month)
    return SeoPackage(
        title=title,
        tags=tags,
        tags_csv=", ".join(tags),
        title_length=len(title),
        tag_count=len(tags),
    )
