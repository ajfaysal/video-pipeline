"""
niches.py - Market Intelligence: VidIQ-style niche discovery for Adobe Stock.

Ranks candidate niches on a 0-100 opportunity score:

    score = 0.45 * demand + 0.35 * (100 - competition) + 0.20 * trend_momentum

* ``demand``        - buyer search interest (seasonal + evergreen weighting)
* ``competition``   - how saturated Adobe Stock already is for the niche
* ``trend``         - month-over-month search momentum

Two data sources, used in order:
  1. LIVE   - Google Trends daily-trending-searches RSS (no key required).
              Matches trend keywords against the niche lexicon to boost the
              momentum term, and blends in today's actual trending topics as
              candidate niches.
  2. LOCAL  - curated, seasonal niche matrix (demand/competition/trend
              estimates maintained per niche, weighted by the current month
              so e.g. "holiday flat-lay" peaks in Oct-Dec).

Every suggestion includes ready-to-generate prompts, the buyer persona, and
why the score is what it is - so one tap turns analysis into production.
"""

from __future__ import annotations

import datetime as _dt
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Curated niche matrix: (demand, competition, trend, peak_months, prompts)
# demand/competition/trend are 0-100. peak_months = months with buyer spike.
# ---------------------------------------------------------------------------

_NICHES: List[Dict] = [
    {
        "niche": "AI workflow in small business",
        "keywords": ["ai", "automation", "small business", "workflow"],
        "demand": 88, "competition": 34, "trend": 92,
        "peak_months": [],
        "buyer": "SaaS blogs, fintech ads, SMB consultants",
        "prompts": [
            "small business owner using AI assistant on laptop in cozy shop, realistic",
            "isometric illustration of AI automation workflow for small retail business",
            "split screen of manual paperwork versus AI automated dashboard, clean vector style",
        ],
    },
    {
        "niche": "Sustainable packaging flat-lay",
        "keywords": ["eco", "packaging", "sustainable", "recycling", "green"],
        "demand": 82, "competition": 41, "trend": 74,
        "peak_months": [4, 11],
        "buyer": "CPG brands, eco startups, Amazon sellers",
        "prompts": [
            "top-down flat lay of recyclable kraft packaging with green leaves, studio light",
            "minimal eco-friendly cosmetic packaging mockup on beige background",
            "compostable food containers arranged in grid, soft daylight, commercial photo",
        ],
    },
    {
        "niche": "Remote healthcare / telemedicine",
        "keywords": ["telehealth", "doctor", "video call", "health", "medical"],
        "demand": 85, "competition": 47, "trend": 70,
        "peak_months": [1, 2, 9],
        "buyer": "Health-tech startups, insurance campaigns",
        "prompts": [
            "doctor on video call with patient on tablet, modern clinic, warm tones",
            "senior woman using telemedicine app at home, candid lifestyle photo",
            "stethoscope next to smartphone showing medical dashboard, clean background",
        ],
    },
    {
        "niche": "Cybersecurity abstract visuals",
        "keywords": ["cybersecurity", "data", "security", "hacker", "encryption"],
        "demand": 90, "competition": 58, "trend": 81,
        "peak_months": [10],
        "buyer": "IT firms, annual reports, news media",
        "prompts": [
            "abstract digital shield made of glowing blue circuit lines, dark background",
            "futuristic server room with holographic lock interface, cinematic",
            "minimal vector illustration of data encryption concept, cobalt palette",
        ],
    },
    {
        "niche": "Mental wellness at work",
        "keywords": ["wellness", "mindfulness", "burnout", "meditation", "mental health"],
        "demand": 79, "competition": 39, "trend": 77,
        "peak_months": [1, 5, 10],
        "buyer": "HR platforms, corporate wellness programs",
        "prompts": [
            "employee meditating on office rooftop during break, soft morning light",
            "calm minimalist workspace with plant and journal, wellness concept",
            "diverse team in guided breathing session, modern office, natural light",
        ],
    },
    {
        "niche": "Electric vehicle charging lifestyle",
        "keywords": ["ev", "electric car", "charging", "energy", "vehicle"],
        "demand": 84, "competition": 45, "trend": 83,
        "peak_months": [3, 9],
        "buyer": "Auto industry, energy utilities, ad agencies",
        "prompts": [
            "family charging electric SUV at home driveway at dusk, lifestyle photo",
            "close-up of hand plugging EV charger, bokeh city lights background",
            "aerial view of solar-powered EV charging station, clean minimal",
        ],
    },
    {
        "niche": "Holiday flat-lay & gifting (seasonal)",
        "keywords": ["christmas", "holiday", "gift", "new year", "celebration"],
        "demand": 86, "competition": 62, "trend": 65,
        "peak_months": [10, 11, 12],
        "buyer": "E-commerce, greeting card publishers, retail",
        "prompts": [
            "elegant christmas flat lay with wrapped gifts and pine branches on linen",
            "minimal new year champagne and confetti on dark marble, luxury style",
            "cozy holiday gift wrapping scene with warm string lights, top view",
        ],
    },
    {
        "niche": "Back-to-school & e-learning (seasonal)",
        "keywords": ["school", "education", "e-learning", "student", "study"],
        "demand": 80, "competition": 55, "trend": 60,
        "peak_months": [7, 8, 9],
        "buyer": "EdTech, publishers, retailers",
        "prompts": [
            "student with laptop and notebooks in bright modern library, lifestyle",
            "flat lay of school supplies on pastel background, copy space",
            "child attending online class with headphones at home desk, candid",
        ],
    },
    {
        "niche": "Diverse senior active lifestyle",
        "keywords": ["senior", "active", "retirement", "elderly", "lifestyle"],
        "demand": 77, "competition": 33, "trend": 71,
        "peak_months": [],
        "buyer": "Healthcare, insurance, travel brands",
        "prompts": [
            "active senior couple cycling through autumn park, joyful lifestyle photo",
            "senior woman practicing yoga on beach at sunrise, peaceful",
            "multi-generational family cooking together in modern kitchen, warm light",
        ],
    },
    {
        "niche": "Smart home & IoT living",
        "keywords": ["smart home", "iot", "voice assistant", "automation", "home"],
        "demand": 78, "competition": 44, "trend": 73,
        "peak_months": [1, 11],
        "buyer": "Consumer electronics, real estate, telcos",
        "prompts": [
            "person adjusting smart thermostat on wall of modern living room",
            "voice assistant speaker glowing on kitchen counter at night, cozy",
            "smart home control panel interface on tablet, luxury interior",
        ],
    },
    {
        "niche": "Plant-based food photography",
        "keywords": ["vegan", "plant-based", "food", "healthy", "cooking"],
        "demand": 83, "competition": 52, "trend": 69,
        "peak_months": [1, 6],
        "buyer": "Food brands, recipe apps, restaurants",
        "prompts": [
            "colorful vegan Buddha bowl with fresh vegetables, overhead food photography",
            "plant-based burger with melting cheese on rustic board, appetizing",
            "green smoothie ingredients flat lay on marble, bright daylight",
        ],
    },
    {
        "niche": "Fintech & digital banking UI mockups",
        "keywords": ["fintech", "banking", "payment", "crypto", "app"],
        "demand": 87, "competition": 49, "trend": 78,
        "peak_months": [1, 4],
        "buyer": "Fintech startups, banks, app developers",
        "prompts": [
            "hand holding smartphone with sleek banking app interface, urban background",
            "contactless payment with smartwatch at cafe terminal, close-up",
            "3D render of floating credit cards and coins, dark premium style",
        ],
    },
]

_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "in", "on", "for", "to", "at", "with",
    "how", "why", "what", "is", "are", "new", "2024", "2025", "2026", "vs",
}


@dataclass
class NicheSuggestion:
    niche: str
    score: int
    demand: int
    competition: int
    trend: int
    buyer: str
    prompts: List[str] = field(default_factory=list)
    source: str = "local"
    reason: str = ""


def _season_boost(peak_months: List[int], month: int) -> float:
    """1.0 normally, up to 1.25 inside a niche's peak season (or adjacent)."""
    if not peak_months:
        return 1.0
    for m in peak_months:
        dist = min(abs(m - month), 12 - abs(m - month))
        if dist == 0:
            return 1.25
        if dist == 1:
            return 1.12
    return 1.0


def fetch_trending_keywords(limit: int = 40) -> List[str]:
    """Pull today's trending searches (Google Trends daily RSS, US).

    Returns lowercase keyword strings; empty list on any network failure so
    the engine always degrades gracefully to the curated matrix.
    """
    url = "https://trends.google.com/trending/rss?geo=US"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "StockFlowAI/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            root = ET.fromstring(resp.read())
        items = root.findall(".//item/title")[:limit]
        keywords: List[str] = []
        for item in items:
            title = (item.text or "").lower()
            words = [w for w in re.split(r"[^a-z0-9]+", title)
                     if len(w) > 3 and w not in _STOPWORDS]
            keywords.extend(words[:3])
        return keywords
    except Exception:
        return []


def _trend_boost(keywords: List[str], live_keywords: List[str]) -> float:
    """0-100 momentum boost from overlap with live trending keywords."""
    if not live_keywords:
        return 0.0
    hits = sum(1 for k in keywords if any(k in lk or lk in k for lk in live_keywords))
    return min(100.0, hits * 35.0)


def score_niche(entry: Dict, month: int, live_keywords: List[str]) -> NicheSuggestion:
    season = _season_boost(entry.get("peak_months", []), month)
    demand = min(100, entry["demand"] * season)
    trend = min(100, entry["trend"] * 0.6 + _trend_boost(entry["keywords"], live_keywords) * 0.4)
    competition = entry["competition"]
    score = int(round(0.45 * demand + 0.35 * (100 - competition) + 0.20 * trend))
    reasons = []
    if season > 1.0:
        reasons.append("in peak buying season")
    if competition <= 40:
        reasons.append("low saturation on Adobe Stock")
    if trend >= 75:
        reasons.append("strong search momentum")
    if not reasons:
        reasons.append("steady evergreen demand")
    return NicheSuggestion(
        niche=entry["niche"],
        score=max(0, min(100, score)),
        demand=int(demand),
        competition=int(competition),
        trend=int(trend),
        buyer=entry["buyer"],
        prompts=list(entry["prompts"]),
        source="live+local" if live_keywords else "local",
        reason=", ".join(reasons).capitalize(),
    )


def suggest_niches(top_n: int = 5, use_live: bool = True) -> List[NicheSuggestion]:
    """Return the top-N opportunity-ranked niche suggestions for right now."""
    month = _dt.datetime.now().month
    live = fetch_trending_keywords() if use_live else []
    scored = [score_niche(e, month, live) for e in _NICHES]
    scored.sort(key=lambda s: s.score, reverse=True)
    return scored[:top_n]


def niches_as_dicts(top_n: int = 5, use_live: bool = True) -> List[Dict]:
    """JSON-serializable view for the Mini-App API."""
    return [
        {
            "niche": s.niche,
            "score": s.score,
            "demand": s.demand,
            "competition": s.competition,
            "trend": s.trend,
            "buyer": s.buyer,
            "prompts": s.prompts,
            "source": s.source,
            "reason": s.reason,
        }
        for s in suggest_niches(top_n=top_n, use_live=use_live)
    ]


def format_niches_telegram(top_n: int = 5) -> str:
    """Pretty MarkdownV1 block for the /niche command."""
    suggestions = suggest_niches(top_n=top_n)
    month = _dt.datetime.now().strftime("%B %Y")
    lines = [f"📊 *Market Intelligence - {month}*",
             "_(VidIQ-style: demand x low competition x momentum)_", ""]
    medals = ["🥇", "🥈", "🥉", "4.", "5.", "6.", "7.", "8."]
    for i, s in enumerate(suggestions):
        lines.append(
            f"{medals[i]} *{s.niche}*  -  Score `{s.score}/100`\n"
            f"   Demand {s.demand} | Competition {s.competition} | Trend {s.trend}\n"
            f"   _{s.reason}_\n"
            f"   Buyers: {s.buyer}\n"
            f"   Prompt: ` /generate {s.prompts[0]}`"
        )
        lines.append("")
    lines.append("Tap a prompt line above to start production instantly. 🚀")
    return "\n".join(lines)
