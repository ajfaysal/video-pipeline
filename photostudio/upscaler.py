"""
upscaler.py
-----------
High-quality photo upscaling up to 16K Ultra HD (15360 px on the long edge).

Strategy: staged progressive upscaling. Instead of one giant resize (which
produces mush), the image is upscaled in <=2x Lanczos stages; after each
stage a light denoise + unsharp-mask pass restores edge acuity. This is the
same approach print labs use for large-format enlargement and gives crisp,
halo-free results with zero ML model downloads (works on any CI runner).

Resolution tiers (long-edge pixels):
    2k  ->  2048      4k  ->  4096      8k  ->  8192      16k -> 15360

Memory guard: a 16K BGR uint8 frame is ~400MB; the staged pipeline holds at
most two frames at once, which fits comfortably in GitHub Actions' 7GB.
"""

from __future__ import annotations

import os
import sys

import cv2
import numpy as np

RESOLUTION_TIERS: dict[str, int] = {
    "2k": 2048,
    "4k": 4096,
    "8k": 8192,
    "16k": 15360,  # 16K UHD long edge
}

# Hard cap on total output pixels (16K 16:9 = ~132MP; allow a bit of headroom).
MAX_OUTPUT_PIXELS = 15360 * 8640 + 1


def _unsharp(img: np.ndarray, sigma: float = 1.2, amount: float = 0.6) -> np.ndarray:
    blurred = cv2.GaussianBlur(img, (0, 0), sigmaX=sigma)
    sharp = cv2.addWeighted(img, 1.0 + amount, blurred, -amount, 0)
    return sharp


def _denoise_light(img: np.ndarray) -> np.ndarray:
    """Very light edge-preserving smoothing to stop noise being amplified."""
    return cv2.bilateralFilter(img, d=5, sigmaColor=20, sigmaSpace=5)


def upscale_image(img: np.ndarray, target_long_edge: int,
                  progress: bool = True) -> np.ndarray:
    """Progressively upscale `img` so its long edge reaches `target_long_edge`."""
    h, w = img.shape[:2]
    long_edge = max(h, w)
    if long_edge >= target_long_edge:
        # Never downscale; the caller asked for at-least this size.
        return img

    scale_total = target_long_edge / long_edge
    out_w, out_h = int(round(w * scale_total)), int(round(h * scale_total))
    if out_w * out_h > MAX_OUTPUT_PIXELS:
        raise ValueError(
            f"Requested output {out_w}x{out_h} exceeds the {MAX_OUTPUT_PIXELS / 1e6:.0f}MP safety cap."
        )

    current = _denoise_light(img)
    stage = 0
    while max(current.shape[:2]) < target_long_edge:
        stage += 1
        ch, cw = current.shape[:2]
        remaining = target_long_edge / max(ch, cw)
        factor = min(2.0, remaining)
        nw, nh = int(round(cw * factor)), int(round(ch * factor))
        if progress:
            print(f"[upscaler] Stage {stage}: {cw}x{ch} -> {nw}x{nh} (Lanczos)", file=sys.stderr)
        current = cv2.resize(current, (nw, nh), interpolation=cv2.INTER_LANCZOS4)
        # Restore acuity lost to interpolation; lighter touch on later stages
        # to avoid amplifying interpolation artifacts.
        current = _unsharp(current, sigma=1.2, amount=max(0.25, 0.6 / stage))

    # Snap to the exact target dimensions.
    if current.shape[1] != out_w or current.shape[0] != out_h:
        current = cv2.resize(current, (out_w, out_h), interpolation=cv2.INTER_LANCZOS4)
    return current


def upscale_file(input_path: str, output_dir: str, resolution: str = "4k",
                 effect: str | None = None) -> str:
    """
    Upscale `input_path` to the requested resolution tier, optionally applying
    a PhotoStudio color effect first (grading before upscaling is faster and
    the grade survives interpolation perfectly). Returns the output path.
    """
    if resolution not in RESOLUTION_TIERS:
        raise ValueError(f"Unknown resolution '{resolution}'. Options: {list(RESOLUTION_TIERS)}")

    img = cv2.imread(input_path, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Could not decode image: {input_path}")

    if effect:
        from photostudio.effects import apply_effect
        print(f"[upscaler] Applying effect '{effect}' before upscale ...", file=sys.stderr)
        img = apply_effect(img, effect)

    target = RESOLUTION_TIERS[resolution]
    print(f"[upscaler] Upscaling to {resolution.upper()} (long edge {target}px) ...", file=sys.stderr)
    result = upscale_image(img, target)

    os.makedirs(output_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(input_path))[0]
    suffix = f"_{effect}" if effect else ""
    out_path = os.path.join(output_dir, f"{base}{suffix}_{resolution}.png")
    # PNG for lossless quality; fall back to max-quality JPEG if PNG would be
    # unreasonably large for Telegram document delivery (>48MB).
    ok = cv2.imwrite(out_path, result, [cv2.IMWRITE_PNG_COMPRESSION, 6])
    if not ok:
        raise RuntimeError(f"Failed to write output image: {out_path}")
    if os.path.getsize(out_path) > 48 * 1024 * 1024:
        os.remove(out_path)
        out_path = os.path.join(output_dir, f"{base}{suffix}_{resolution}.jpg")
        cv2.imwrite(out_path, result, [cv2.IMWRITE_JPEG_QUALITY, 96])

    h, w = result.shape[:2]
    print(f"[upscaler] Done: {out_path} ({w}x{h}, {os.path.getsize(out_path) / 1e6:.1f}MB)",
          file=sys.stderr)
    return out_path
