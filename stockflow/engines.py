"""
engines.py - multi-engine AI image generation for StockFlow AI.

Supported API formats (set per engine in config.json):

  huggingface        Hugging Face Inference Providers (default, free tier).
                     POST base_url  {inputs, parameters:{seed, width, height}}
                     -> raw image bytes.  FLUX.1-schnell natively renders up
                     to 2048px and takes any aspect; anything above that is
                     finished with the staged-Lanczos upscaler (up to 16K).
  openai             OpenAI Images API.  POST base_url  {model, prompt, size}
                     -> JSON {data:[{b64_json|url}]}.
  openai-compatible  Same wire shape as OpenAI - covers Midjourney proxies
                     (MidJourney-API / TTAPI / GoAPI style gateways) and any
                     custom OpenAI-shaped endpoint you add in /settings.
  stability          Stability AI v2beta.  multipart POST -> raw image bytes.

Every call returns a GenerationResult with PNG bytes (engines never add
watermarks; StockFlow adds none either), the resolved seed, and metadata.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

import requests
from PIL import Image

from .config import Config
from .seeds import SeedRegistry, apply_entropy

# Native long-edge each engine renders comfortably before we upscale.
RESOLUTION_TIERS: Dict[str, int] = {
    "1k": 1024,
    "2k": 2048,
    "4k": 4096,
    "8k": 8192,
    "16k": 15360,
}

ASPECTS: Dict[str, Tuple[int, int]] = {
    "square": (1, 1),
    "landscape": (16, 9),
    "portrait": (9, 16),
}

DEFAULT_TIMEOUT = 300  # HF free tier can cold-start FLUX for ~60s

STYLE_SUFFIX = (
    "commercial stock photography, clean isolated subject potential, "
    "high detail, professional color grading, no text, no watermark, "
    "no logo, no trademark"
)


class EngineError(RuntimeError):
    """Raised when a generation engine fails or is misconfigured."""


@dataclass
class GenerationResult:
    png_bytes: bytes
    seed: int
    shield_code: str
    engine: str
    model: str
    width: int
    height: int
    prompt_used: str
    meta: Dict[str, Any] = field(default_factory=dict)


def _target_size(resolution: str, aspect: str) -> Tuple[int, int]:
    long_edge = RESOLUTION_TIERS.get(resolution, 2048)
    ar_w, ar_h = ASPECTS.get(aspect, (1, 1))
    if ar_w >= ar_h:
        return long_edge, max(256, long_edge * ar_h // ar_w)
    return max(256, long_edge * ar_w // ar_h), long_edge


def _engine_native_cap(engine: Dict[str, Any]) -> int:
    """Max long-edge we ask the engine itself to render (rest is upscaled)."""
    fmt = engine.get("api_format", "huggingface")
    return {"huggingface": 2048, "openai": 1024, "openai-compatible": 1024,
            "stability": 1536}.get(fmt, 1024)


# ---------------------------------------------------------------------------
# Per-format request builders
# ---------------------------------------------------------------------------

def _gen_huggingface(engine: Dict[str, Any], api_key: str, prompt: str,
                     width: int, height: int, seed: int) -> bytes:
    if not api_key:
        raise EngineError(
            "Hugging Face token missing. Create a FREE token at "
            "https://huggingface.co/settings/tokens and either export "
            "HF_TOKEN or paste it in /settings."
        )
    base_url = (engine.get("base_url") or "").strip()
    if not base_url:
        raise EngineError("Hugging Face engine has no base_url configured.")
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "image/png"}
    headers.update(engine.get("extra_headers") or {})
    payload = {
        "inputs": prompt,
        "parameters": {
            "seed": seed,
            "width": width,
            "height": height,
            "num_inference_steps": 4,   # FLUX.1-schnell sweet spot
            "guidance_scale": 0.0,      # schnell is distilled; 0 = max speed
        },
    }
    resp = requests.post(base_url, json=payload, headers=headers,
                         timeout=DEFAULT_TIMEOUT)
    if resp.status_code == 503:  # model cold start - one retry
        resp = requests.post(base_url, json=payload, headers=headers,
                             timeout=DEFAULT_TIMEOUT)
    if resp.status_code != 200:
        raise EngineError(f"Hugging Face API {resp.status_code}: {resp.text[:400]}")
    ctype = resp.headers.get("Content-Type", "")
    if "image" not in ctype and not resp.content.startswith(b"\x89PNG"):
        raise EngineError(f"Hugging Face returned non-image payload: {resp.text[:300]}")
    return resp.content


def _gen_openai_shape(engine: Dict[str, Any], api_key: str, prompt: str,
                      width: int, height: int, seed: int) -> bytes:
    """OpenAI Images API and any OpenAI-compatible (Midjourney proxy) gateway."""
    if not api_key:
        raise EngineError(f"API key missing for engine '{engine.get('id')}'.")
    base_url = (engine.get("base_url") or "").strip()
    if not base_url:
        raise EngineError(
            f"Engine '{engine.get('id')}' has no base_url. Add the endpoint in /settings."
        )
    opts = engine.get("options") or {}
    size = opts.get("size") or f"{min(width, 1536)}x{min(height, 1536)}"
    payload: Dict[str, Any] = {
        "model": engine.get("model") or "gpt-image-1",
        "prompt": prompt,
        "n": 1,
        "size": size,
    }
    if engine.get("api_format") == "openai" and opts.get("quality"):
        payload["quality"] = opts["quality"]
    if seed:  # harmless for gateways that ignore it; used by proxies that honor it
        payload["seed"] = seed
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    headers.update(engine.get("extra_headers") or {})
    resp = requests.post(base_url, json=payload, headers=headers, timeout=DEFAULT_TIMEOUT)
    if resp.status_code != 200:
        raise EngineError(f"API {resp.status_code}: {resp.text[:400]}")
    try:
        data = resp.json()["data"][0]
    except (ValueError, KeyError, IndexError) as exc:
        raise EngineError(f"Unexpected API response: {resp.text[:300]}") from exc
    if data.get("b64_json"):
        return base64.b64decode(data["b64_json"])
    if data.get("url"):
        img = requests.get(data["url"], timeout=DEFAULT_TIMEOUT)
        img.raise_for_status()
        return img.content
    raise EngineError("API response contained neither b64_json nor url.")


def _gen_stability(engine: Dict[str, Any], api_key: str, prompt: str,
                   width: int, height: int, seed: int) -> bytes:
    if not api_key:
        raise EngineError("Stability AI key missing (STABILITY_API_KEY or /settings).")
    base_url = (engine.get("base_url") or "").strip()
    if not base_url:
        raise EngineError("Stability engine has no base_url configured.")
    opts = engine.get("options") or {}
    aspect_ratio = opts.get("aspect_ratio")
    if not aspect_ratio:
        aspect_ratio = "1:1" if width == height else ("16:9" if width > height else "9:16")
    form = {
        "prompt": (None, prompt),
        "output_format": (None, opts.get("output_format", "png")),
        "aspect_ratio": (None, aspect_ratio),
        "seed": (None, str(seed)),
    }
    if engine.get("model"):
        form["model"] = (None, engine["model"])
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "image/*"}
    headers.update(engine.get("extra_headers") or {})
    resp = requests.post(base_url, files=form, headers=headers, timeout=DEFAULT_TIMEOUT)
    if resp.status_code != 200:
        raise EngineError(f"Stability API {resp.status_code}: {resp.text[:400]}")
    return resp.content


_FORMAT_DISPATCH = {
    "huggingface": _gen_huggingface,
    "openai": _gen_openai_shape,
    "openai-compatible": _gen_openai_shape,
    "stability": _gen_stability,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_image(
    config: Config,
    prompt: str,
    engine_id: Optional[str] = None,
    resolution: Optional[str] = None,
    aspect: Optional[str] = None,
    upscale: Optional[bool] = None,
    seed_registry: Optional[SeedRegistry] = None,
    seed: Optional[int] = None,
) -> GenerationResult:
    """Run one full Uniqueness-Shielded generation.

    1. Issue (or reuse) a unique seed.
    2. Mix entropy tokens into the prompt + commercial stock suffix.
    3. Call the configured engine at its native cap.
    4. If the requested tier exceeds the engine's native cap, upscale.
    """
    from .upscaler import upscale_to_tier  # local import keeps cv2 optional

    if not prompt or not prompt.strip():
        raise EngineError("Prompt may not be empty.")

    engine = config.get_engine(engine_id)
    resolution = resolution or config.get_default("resolution", "2k")
    aspect = aspect or config.get_default("aspect", "square")
    if upscale is None:
        upscale = bool(config.get_default("upscale", False))

    # --- Uniqueness Shield -------------------------------------------------
    registry = seed_registry or SeedRegistry()
    if seed is None:
        bundle = registry.issue(prompt)
        seed = int(bundle["seed"])
        shield = str(bundle["shield_code"])
    else:
        from .seeds import shield_code
        shield = shield_code(seed)

    styled_prompt = f"{prompt.strip()}, {STYLE_SUFFIX}"
    final_prompt = apply_entropy(styled_prompt, seed)

    # --- Engine call --------------------------------------------------------
    target_w, target_h = _target_size(resolution, aspect)
    native_cap = _engine_native_cap(engine)
    native_w, native_h = target_w, target_h
    if max(target_w, target_h) > native_cap:
        scale = native_cap / max(target_w, target_h)
        native_w, native_h = int(target_w * scale), int(target_h * scale)

    api_key = config.api_key_for(engine)
    fmt = engine.get("api_format", "huggingface")
    handler = _FORMAT_DISPATCH.get(fmt)
    if handler is None:
        raise EngineError(f"Unsupported api_format '{fmt}' for engine '{engine['id']}'.")

    raw = handler(engine, api_key, final_prompt, native_w, native_h, seed)

    img = Image.open(io.BytesIO(raw))
    img.load()
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")

    # --- Post upscale to requested tier (16K-ready) ------------------------
    if upscale or max(target_w, target_h) > max(img.size):
        img = upscale_to_tier(img, target_w, target_h)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)  # lossless, watermark-free
    return GenerationResult(
        png_bytes=buf.getvalue(),
        seed=seed,
        shield_code=shield,
        engine=engine["id"],
        model=engine.get("model", ""),
        width=img.width,
        height=img.height,
        prompt_used=final_prompt,
        meta={"resolution": resolution, "aspect": aspect, "upscaled": upscale},
    )
