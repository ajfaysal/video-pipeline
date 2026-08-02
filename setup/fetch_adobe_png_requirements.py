from __future__ import annotations

import json
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


def main() -> int:
    try:
        resp = requests.get(SOURCE_URL, timeout=30)
        resp.raise_for_status()
        body = resp.text
        plain = re.sub(r"<[^>]+>", " ", body)
        plain = re.sub(r"\s+", " ", plain).strip()
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
                "min_width": 1600,
                "min_height": 2400,
            },
            "fetch_error": str(exc),
        }
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUT_PATH.write_text(json.dumps(fallback, indent=2), encoding="utf-8")
        print(f"Warning: failed to fetch Adobe spec, kept fallback cache at {OUT_PATH}: {exc}")
        return 0

    parsed = {
        "max_file_size_mb": _extract_int(r"(\d+)\s*MB", plain),
        "min_pixels": _extract_int(r"(\d[\d,]*)\s*(?:pixels|px)", plain),
        "min_width": _extract_int(r"minimum\s+width\s*(?:of)?\s*(\d[\d,]*)", plain),
        "min_height": _extract_int(r"minimum\s+height\s*(?:of)?\s*(\d[\d,]*)", plain),
    }

    # Keep safe defaults if parser misses any field while still preserving fetched text.
    if not parsed["max_file_size_mb"]:
        parsed["max_file_size_mb"] = 45
    if not parsed["min_pixels"]:
        parsed["min_pixels"] = 4_000_000
    if not parsed["min_width"]:
        parsed["min_width"] = 1600
    if not parsed["min_height"]:
        parsed["min_height"] = 2400

    payload = {
        "source_url": SOURCE_URL,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "raw_excerpt": plain[:5000],
        "parsed": parsed,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
