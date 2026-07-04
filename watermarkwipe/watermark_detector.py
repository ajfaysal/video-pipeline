"""
watermark_detector.py
----------------------
Auto-detects the bounding box of a watermark/logo overlay by sampling
frames across the video and finding regions that are:
  - static (low frame-to-frame variance in position), and
  - visually distinct from the surrounding motion (an overlay tends to
    sit "on top" of otherwise-changing content, so pixels under a logo
    have a much lower temporal variance than the rest of the frame).

Two entry points:
  detect_corner_region()  - for --mode crop: looks specifically in the four
                             corners/edges for a compact static blob.
  detect_watermark_region() - general-purpose static-pixel-variance scan,
                             used for --mode inpaint auto-detect (handles
                             center-positioned or larger logos too).
"""

from __future__ import annotations

import sys

import cv2
import numpy as np

from aspectshift.downloader import InvalidVideoError


def _sample_frames(video_path: str, num_samples: int = 30) -> list[np.ndarray]:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise InvalidVideoError(f"OpenCV could not open '{video_path}' for watermark detection.")

    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if frame_count <= 0:
        raise InvalidVideoError(f"'{video_path}' has no readable frames.")

    indices = np.linspace(0, frame_count - 1, num=min(num_samples, frame_count), dtype=int)
    frames = []
    for idx in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
        ok, frame = cap.read()
        if ok:
            frames.append(frame)
    cap.release()

    if len(frames) < 3:
        raise InvalidVideoError(f"Not enough readable frames in '{video_path}' to detect a watermark.")
    return frames


def _low_variance_mask(frames: list[np.ndarray], variance_percentile: float = 15.0) -> np.ndarray:
    """
    Returns a boolean mask (H, W) of pixels whose intensity barely changes
    across the sampled frames - i.e. likely to be a static overlay rather
    than underlying (changing) video content.
    """
    gray_stack = np.stack([cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32) for f in frames], axis=0)
    variance_map = gray_stack.var(axis=0)

    threshold = np.percentile(variance_map, variance_percentile)
    mask = variance_map <= threshold
    return mask


def _largest_bbox_from_mask(mask: np.ndarray, min_area_frac: float = 0.0005) -> tuple[int, int, int, int] | None:
    mask_u8 = (mask.astype(np.uint8)) * 255
    # Clean up noise: close small gaps, remove tiny speckles.
    kernel = np.ones((5, 5), np.uint8)
    mask_u8 = cv2.morphologyEx(mask_u8, cv2.MORPH_CLOSE, kernel, iterations=2)
    mask_u8 = cv2.morphologyEx(mask_u8, cv2.MORPH_OPEN, kernel, iterations=1)

    contours, _ = cv2.findContours(mask_u8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    h, w = mask.shape
    frame_area = h * w
    min_area = frame_area * min_area_frac

    best = None
    best_area = 0
    for c in contours:
        x, y, cw, ch = cv2.boundingRect(c)
        area = cw * ch
        if area < min_area:
            continue
        if area > best_area:
            best_area = area
            best = (x, y, cw, ch)

    return best


def detect_corner_region(video_path: str, num_samples: int = 30) -> tuple[int, int, int, int]:
    """
    Looks specifically in the four corner/edge strips (each ~22% of width/height)
    for a compact static blob - typical of channel-bug / corner watermarks.
    Returns (x, y, w, h) in full-frame coordinates.
    """
    frames = _sample_frames(video_path, num_samples)
    h, w = frames[0].shape[:2]
    strip_w, strip_h = int(w * 0.22), int(h * 0.22)

    corners = {
        "top_left": (0, 0, strip_w, strip_h),
        "top_right": (w - strip_w, 0, strip_w, strip_h),
        "bottom_left": (0, h - strip_h, strip_w, strip_h),
        "bottom_right": (w - strip_w, h - strip_h, strip_w, strip_h),
    }

    best_region = None
    best_score = -1.0

    for name, (cx, cy, cw, ch) in corners.items():
        crops = [f[cy:cy + ch, cx:cx + cw] for f in frames]
        mask = _low_variance_mask(crops, variance_percentile=25.0)
        bbox = _largest_bbox_from_mask(mask, min_area_frac=0.01)
        if bbox is None:
            continue
        bx, by, bw, bh = bbox
        # Score by how "blob-like" and non-trivial the region is.
        score = bw * bh
        if score > best_score:
            best_score = score
            # Pad slightly so we fully cover the logo edges.
            pad = 6
            best_region = (
                max(cx + bx - pad, 0),
                max(cy + by - pad, 0),
                min(bw + 2 * pad, w),
                min(bh + 2 * pad, h),
            )

    if best_region is None:
        raise InvalidVideoError(
            "Could not auto-detect a corner watermark. Specify --region x,y,w,h manually."
        )

    print(f"[detector] Detected corner watermark region: {best_region}", file=sys.stderr)
    return best_region


def detect_watermark_region(video_path: str, num_samples: int = 30) -> tuple[int, int, int, int]:
    """
    General-purpose detection for inpaint mode: finds the largest static
    (low-variance) blob anywhere in the frame, which covers center-positioned
    or semi-transparent logos as well as corner bugs.
    Returns (x, y, w, h).
    """
    frames = _sample_frames(video_path, num_samples)
    mask = _low_variance_mask(frames, variance_percentile=12.0)
    bbox = _largest_bbox_from_mask(mask, min_area_frac=0.0008)

    if bbox is None:
        raise InvalidVideoError(
            "Could not auto-detect a watermark region. Specify --region x,y,w,h manually."
        )

    x, y, w, h = bbox
    pad = 8
    frame_h, frame_w = frames[0].shape[:2]
    region = (
        max(x - pad, 0),
        max(y - pad, 0),
        min(w + 2 * pad, frame_w),
        min(h + 2 * pad, frame_h),
    )
    print(f"[detector] Detected watermark region: {region}", file=sys.stderr)
    return region
