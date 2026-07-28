"""
svg.py - PNG -> SVG conversion for vector-format Adobe Stock submissions.

Two modes:

* ``traced``  - True vectorization via vtracer (pip install vtracer).
                Produces real multi-color SVG paths. Preferred when the
                optional dependency is present.
* ``embedded``- Standards-compliant SVG wrapper that embeds the PNG as a
                base64 <image> element. Always available, lossless, and
                accepted by tools that need an .svg container. This is the
                automatic fallback so the SVG option never hard-fails.
"""

from __future__ import annotations

import base64
import io
from typing import Tuple

from PIL import Image


class SvgError(RuntimeError):
    pass


def _svg_embed(png_bytes: bytes, width: int, height: int) -> bytes:
    b64 = base64.b64encode(png_bytes).decode("ascii")
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{width}" height="{height}" viewBox="0 0 {width} {height}">\n'
        f'  <title>StockFlow AI asset</title>\n'
        f'  <image width="{width}" height="{height}" '
        f'xlink:href="data:image/png;base64,{b64}"/>\n'
        f'</svg>\n'
    )
    return svg.encode("utf-8")


def _png_to_ppm(png_bytes: bytes, max_pixels: int = 4_000_000) -> Tuple[bytes, int, int]:
    """vtracer wants PPM/PBM raw pixels; downscale huge rasters for tracing."""
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    if img.width * img.height > max_pixels:
        ratio = (max_pixels / (img.width * img.height)) ** 0.5
        img = img.resize((int(img.width * ratio), int(img.height * ratio)),
                         Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PPM")
    return buf.getvalue(), img.width, img.height


def png_to_svg(png_bytes: bytes, mode: str = "auto") -> bytes:
    """Convert PNG bytes to SVG bytes.

    mode: "auto" (traced if vtracer installed, else embedded),
          "traced" (require vtracer), "embedded" (force wrapper).
    """
    img = Image.open(io.BytesIO(png_bytes))
    width, height = img.size

    want_trace = mode in ("auto", "traced")
    if want_trace:
        try:
            import vtracer  # type: ignore

            ppm, w, h = _png_to_ppm(png_bytes)
            svg_str = vtracer.convert_pixels_to_svg(
                ppm, img_format="ppm",
                colormode="color", hierarchical="stacked",
                mode="spline", filter_speckle=4, color_precision=6,
                layer_difference=16, corner_threshold=60,
                length_threshold=4.0, splice_threshold=45,
                path_precision=3,
            )
            return svg_str.encode("utf-8")
        except ImportError:
            if mode == "traced":
                raise SvgError(
                    "True vector tracing requires the optional 'vtracer' package "
                    "(pip install vtracer). Falling back is automatic in 'auto' mode."
                )
        except Exception as exc:
            if mode == "traced":
                raise SvgError(f"vtracer failed: {exc}") from exc

    return _svg_embed(png_bytes, width, height)
