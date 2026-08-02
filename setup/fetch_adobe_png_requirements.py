from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

SOURCE_URL = "https://helpx.adobe.com/stock/contributor/submit-your-content/submit-pngs/technical-requirements-png-submission.html"
OUT_PATH = Path("stockimagefix/adobe_png_requirements.json")


def _extract_int(pattern: str, text: str) -> int | None:
    m = re.search(pattern, text, flags=re.IGNORECASE)
    if not m:
        return None
    value = m.group(1).replace(",", "").strip()
    try:
        return int(value)
    except ValueError:
        return None


def _html_to_plain(body: str) -> str:
    """Strip script/style blocks first so inline JS/CSS never pollutes parsing."""
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", body, flags=re.DOTALL | re.IGNORECASE)
    body = re.sub(r"<!--.*?-->", " ", body, flags=re.DOTALL)
    plain = re.sub(r"<[^>]+>", " ", body)
    return re.sub(r"\s+", " ", plain).strip()


def _spec_excerpt(plain: str) -> str:
    """Return the excerpt centred on the requirements table, not page chrome."""
    anchor = re.search(r"Specifications\s+Requirements", plain, flags=re.IGNORECASE)
    if anchor:
        start = max(0, anchor.start() - 500)
        return plain[start : anchor.start() + 4500]
    return plain[:5000]


def _dispatched_tool() -> str:
    """Return the tool requested by the current repository_dispatch job, if any."""
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    if not event_path or not os.path.isfile(event_path):
        return ""
    try:
        with open(event_path, encoding="utf-8") as fh:
            event = json.load(fh)
        return str((event.get("client_payload") or {}).get("tool") or "")
    except (OSError, json.JSONDecodeError):
        return ""


def main() -> int:
    # Inside CI this step runs for every dispatched job; skip the network
    # round-trip entirely unless the job is actually a StockImageFix run so
    # other tools' jobs stay untouched. Local/manual runs always fetch.
    tool = _dispatched_tool()
    if tool and tool != "stockimagefix":
        print(f"Skipping Adobe spec fetch (dispatched tool is '{tool}', not stockimagefix).")
        return 0

    try:
        resp = requests.get(SOURCE_URL, timeout=30)
        resp.raise_for_status()
        plain = _html_to_plain(resp.text)
    except requests.RequestException as exc:
        existing = {}
        if OUT_PATH.exists():
            try:
                existing = json.loads(OUT_PATH.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                existing = {}
        fallback = {
            "source_url": SOURCE_URL,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "raw_excerpt": existing.get("raw_excerpt", ""),
            "parsed": existing.get("parsed") or {
                "max_file_size_mb": 45,
                "min_pixels": 4_000_000,
                "max_pixels": 100_000_000,
                "min_width": 0,
                "min_height": 0,
            },
            "fetch_error": str(exc),
        }
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUT_PATH.write_text(json.dumps(fallback, indent=2), encoding="utf-8")
        print(f"Warning: failed to fetch Adobe spec, kept fallback cache at {OUT_PATH}: {exc}")
        return 0

    # Adobe's live table reads e.g. "Image resolution 4MP-100MP (megapixels)"
    # and "File size 45MB maximum". Parse those, with megapixels -> pixels.
    min_mp = _extract_int(r"(\d[\d,]*)\s*MP\s*[-\u2013]\s*\d[\d,]*\s*MP", plain)
    max_mp = _extract_int(r"\d[\d,]*\s*MP\s*[-\u2013]\s*(\d[\d,]*)\s*MP", plain)
    parsed = {
        "max_file_size_mb": _extract_int(r"File\s+size\s*(\d[\d,]*)\s*MB", plain)
        or _extract_int(r"(\d[\d,]*)\s*MB\s+maximum", plain),
        "min_pixels": (min_mp * 1_000_000) if min_mp else None,
        "max_pixels": (max_mp * 1_000_000) if max_mp else None,
        "min_width": _extract_int(r"minimum\s+width\s*(?:of)?\s*(\d[\d,]*)", plain),
        "min_height": _extract_int(r"minimum\s+height\s*(?:of)?\s*(\d[\d,]*)", plain),
        "color_profile": "sRGB" if re.search(r"sRGB", plain) else None,
        "requires_transparent_background": bool(
            re.search(r"transparent\s+background", plain, flags=re.IGNORECASE)
        ),
    }

    # Keep safe defaults if parser misses any field while still preserving fetched text.
    if not parsed["max_file_size_mb"]:
        parsed["max_file_size_mb"] = 45
    if not parsed["min_pixels"]:
        parsed["min_pixels"] = 4_000_000
    if not parsed["max_pixels"]:
        parsed["max_pixels"] = 100_000_000
    # Adobe's PNG spec is megapixel-based; width/height stay 0 unless the live
    # page ever publishes explicit per-dimension minimums.
    if not parsed["min_width"]:
        parsed["min_width"] = 0
    if not parsed["min_height"]:
        parsed["min_height"] = 0

    payload = {
        "source_url": SOURCE_URL,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "raw_excerpt": _spec_excerpt(plain),
        "parsed": parsed,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
