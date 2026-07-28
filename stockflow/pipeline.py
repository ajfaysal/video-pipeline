"""
pipeline.py - end-to-end production orchestration for StockFlow AI.

One call -> one finished, Adobe-Stock-ready asset bundle:

    run_production(config, prompt, ...)
        -> GenerationResult  (image bytes, unique seed, shield code)
        -> SeoPackage        (70-char title + 50 ranked tags)
        -> files on disk     (PNG and/or SVG + metadata.json)

Output layout (under STOCKFLOW_OUTPUT, default ./stockflow_output):

    <UTC-timestamp>_<slug>_<shield>/
        asset.png            (or asset.svg)
        metadata.json        (title, tags, seed, shield, engine, niche)
"""

from __future__ import annotations

import datetime as _dt
import io
import json
import os
import re
import zipfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .config import Config
from .engines import GenerationResult, generate_image
from .seo import SeoPackage, build_seo_package
from .seeds import SeedRegistry
from .svg import png_to_svg

DEFAULT_OUTPUT_DIR = os.path.join(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
    "stockflow_output",
)

_registry: Optional[SeedRegistry] = None


def get_registry() -> SeedRegistry:
    global _registry
    if _registry is None:
        os.makedirs(output_dir(), exist_ok=True)
        _registry = SeedRegistry(os.path.join(output_dir(), ".seed_registry.json"))
    return _registry


def output_dir() -> str:
    return os.environ.get("STOCKFLOW_OUTPUT", DEFAULT_OUTPUT_DIR)


def _slug(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].strip("-") or "asset"


@dataclass
class ProductionBundle:
    job_id: str
    directory: str
    files: List[str]
    generation: GenerationResult
    seo: SeoPackage
    niche: Optional[str] = None
    meta: Dict = field(default_factory=dict)

    def metadata_dict(self) -> Dict:
        g, s = self.generation, self.seo
        return {
            "job_id": self.job_id,
            "created_utc": _dt.datetime.utcnow().isoformat() + "Z",
            "title": s.title,
            "title_length": s.title_length,
            "tags": s.tags,
            "tags_csv": s.tags_csv,
            "tag_count": s.tag_count,
            "seed": g.seed,
            "shield_code": g.shield_code,
            "engine": g.engine,
            "model": g.model,
            "width": g.width,
            "height": g.height,
            "resolution_tier": g.meta.get("resolution"),
            "aspect": g.meta.get("aspect"),
            "niche": self.niche,
            "prompt": g.prompt_used,
            "files": [os.path.basename(f) for f in self.files],
            "watermark": None,
        }


def run_production(
    config: Config,
    prompt: str,
    niche: Optional[str] = None,
    engine_id: Optional[str] = None,
    resolution: Optional[str] = None,
    aspect: Optional[str] = None,
    out_format: Optional[str] = None,
    upscale: Optional[bool] = None,
    save: bool = True,
) -> ProductionBundle:
    """Generate + SEO-optimize + (optionally) persist one commercial asset."""
    out_format = (out_format or config.get_default("format", "png")).lower()
    if out_format not in ("png", "svg"):
        raise ValueError("format must be 'png' or 'svg'")

    generation = generate_image(
        config, prompt,
        engine_id=engine_id, resolution=resolution, aspect=aspect,
        upscale=upscale, seed_registry=get_registry(),
    )
    seo = build_seo_package(
        prompt, niche=niche,
        title_length=int(config.get_default("title_length", 70)),
        tag_count=int(config.get_default("tag_count", 50)),
    )

    job_id = "{}_{}_{}".format(
        _dt.datetime.utcnow().strftime("%Y%m%d-%H%M%S"),
        _slug(prompt, 28),
        generation.shield_code.replace("SF-", "").lower(),
    )

    bundle = ProductionBundle(
        job_id=job_id, directory="", files=[],
        generation=generation, seo=seo, niche=niche,
    )

    if not save:
        return bundle

    directory = os.path.join(output_dir(), job_id)
    os.makedirs(directory, exist_ok=True)
    bundle.directory = directory

    if out_format == "svg":
        svg_bytes = png_to_svg(generation.png_bytes, mode="auto")
        path = os.path.join(directory, "asset.svg")
        with open(path, "wb") as fh:
            fh.write(svg_bytes)
        bundle.files.append(path)
    else:
        path = os.path.join(directory, "asset.png")
        with open(path, "wb") as fh:
            fh.write(generation.png_bytes)
        bundle.files.append(path)

    meta_path = os.path.join(directory, "metadata.json")
    with open(meta_path, "w", encoding="utf-8") as fh:
        json.dump(bundle.metadata_dict(), fh, indent=2, ensure_ascii=False)
    bundle.files.append(meta_path)
    return bundle


def bundle_zip(bundle: ProductionBundle) -> bytes:
    """In-memory ZIP of the asset + metadata (used for bot delivery)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if bundle.generation and not bundle.files:
            zf.writestr("asset.png", bundle.generation.png_bytes)
        for path in bundle.files:
            zf.write(path, arcname=os.path.basename(path))
        if not bundle.files:
            zf.writestr(
                "metadata.json",
                json.dumps(bundle.metadata_dict(), indent=2, ensure_ascii=False),
            )
    return buf.getvalue()


def latest_jobs(limit: int = 10) -> List[Dict]:
    """List recent production bundles (for the Mini-App gallery)."""
    root = output_dir()
    if not os.path.isdir(root):
        return []
    jobs = []
    for name in sorted(os.listdir(root), reverse=True):
        meta = os.path.join(root, name, "metadata.json")
        if not os.path.isfile(meta):
            continue
        try:
            with open(meta, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            data["job_id"] = name
            jobs.append(data)
        except (json.JSONDecodeError, OSError):
            continue
        if len(jobs) >= limit:
            break
    return jobs


def job_file(job_id: str, filename: str) -> Optional[str]:
    """Safe path resolution for serving generated files (no traversal)."""
    root = os.path.realpath(output_dir())
    if not re.fullmatch(r"[A-Za-z0-9_\-.]+", job_id) or \
       not re.fullmatch(r"[A-Za-z0-9_\-.]+", filename):
        return None
    path = os.path.realpath(os.path.join(root, job_id, filename))
    if not path.startswith(root + os.sep) or not os.path.isfile(path):
        return None
    return path
