"""
upscaler.py - 16K-ready staged upscaling (Pillow-only, zero ML downloads).

Strategy: progressive <=2x Lanczos stages. After each stage a light
unsharp-mask pass restores edge acuity - the same approach print labs use
for large-format enlargement. Crisp, halo-free, and runs anywhere
(no GPU, no model weights), which keeps $0 hosting viable.

Safety: a 16K RGB image is ~700MB decoded, so we hard-cap total output
pixels and raise a clear error rather than OOM-ing a small free-tier host.
"""

from __future__ import annotations

from typing import Tuple

from PIL import Image, ImageFilter

# ~132 MP headroom (16K 16:9). Above this we refuse - protects free dynos.
MAX_OUTPUT_PIXELS = 15360 * 8640 + 1


class UpscaleError(RuntimeError):
    pass


def _unsharp(img: Image.Image, radius: float = 1.2, percent: int = 60) -> Image.Image:
    return img.filter(ImageFilter.UnsharpMask(radius=radius, percent=percent, threshold=2))


def upscale_to_tier(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Staged-Lanczos upscale so the image is exactly target_w x target_h."""
    if target_w * target_h > MAX_OUTPUT_PIXELS:
        raise UpscaleError(
            f"Requested {target_w}x{target_h} exceeds the {MAX_OUTPUT_PIXELS:,}-pixel "
            "safety cap. Choose a lower tier (8k/4k) or run the 16K tier on a "
            "machine with >=4GB free RAM."
        )
    if img.width >= target_w and img.height >= target_h:
        return img

    current = img
    while current.width < target_w or current.height < target_h:
        # Grow by at most 2x per stage towards the target.
        next_w = min(target_w, current.width * 2)
        next_h = min(target_h, current.height * 2)
        # Preserve aspect: scale by the smaller needed ratio.
        ratio = min(next_w / current.width, next_h / current.height)
        if ratio <= 1.0:
            break
        new_size: Tuple[int, int] = (
            max(current.width + 1, int(current.width * ratio)),
            max(current.height + 1, int(current.height * ratio)),
        )
        current = current.resize(new_size, Image.LANCZOS)
        current = _unsharp(current)

    # Final exact fit (never upscale beyond target in this last resize).
    if (current.width, current.height) != (target_w, target_h):
        current = current.resize((target_w, target_h), Image.LANCZOS)
    return current
