"""
thumbnail.py
------------
Auto-generates a high-quality thumbnail from a video:
  1. Extract 15-20 candidate frames evenly spaced through the video.
  2. Score each candidate by sharpness (Laplacian variance), saturation,
     and face-detection presence.
  3. Pick the best-scoring frame.
  4. Upscale to true HD (>= 1920x1080) using Lanczos resampling.
  5. Enhance contrast/saturation and apply an unsharp-mask sharpen pass.
  6. Save as both .jpg (quality 100) and .png.
"""

from __future__ import annotations

import os
import sys

import cv2
import numpy as np

from aspectshift.downloader import probe_video, InvalidVideoError, load_face_cascade

MIN_HD_W = 1920
MIN_HD_H = 1080
NUM_CANDIDATES = 18


def _extract_candidate_frames(video_path: str, num_candidates: int = NUM_CANDIDATES) -> list[np.ndarray]:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise InvalidVideoError(f"OpenCV could not open '{video_path}' for thumbnail extraction.")

    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if frame_count <= 0:
        raise InvalidVideoError(f"'{video_path}' reports zero frames; cannot extract a thumbnail.")

    # Skip the very first and last 3% to avoid black intro/outro frames.
    lo = int(frame_count * 0.03)
    hi = int(frame_count * 0.97)
    hi = max(hi, lo + 1)

    indices = np.linspace(lo, hi, num=num_candidates, dtype=int)
    frames = []
    for idx in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
        ok, frame = cap.read()
        if ok and frame is not None:
            frames.append(frame)
    cap.release()

    if not frames:
        raise InvalidVideoError(f"Could not extract any usable frames from '{video_path}'.")
    return frames


def _sharpness_score(frame: np.ndarray) -> float:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _saturation_score(frame: np.ndarray) -> float:
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    return float(hsv[:, :, 1].mean())


def _face_score(frame: np.ndarray, cascade: cv2.CascadeClassifier) -> float:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(50, 50))
    if len(faces) == 0:
        return 0.0
    # Reward presence and size of the largest face (a clear, prominent subject).
    largest_area = max(w * h for (_, _, w, h) in faces)
    frame_area = frame.shape[0] * frame.shape[1]
    return float(np.clip((largest_area / frame_area) * 10.0, 0, 1.0)) * 100.0


def _pick_best_frame(frames: list[np.ndarray]) -> np.ndarray:
    cascade = load_face_cascade()

    sharpness_vals = [_sharpness_score(f) for f in frames]
    saturation_vals = [_saturation_score(f) for f in frames]
    if cascade is not None:
        face_vals = [_face_score(f, cascade) for f in frames]
    else:
        face_vals = [0.0] * len(frames)

    def normalize(vals: list[float]) -> list[float]:
        lo, hi = min(vals), max(vals)
        if hi - lo < 1e-6:
            return [0.5] * len(vals)
        return [(v - lo) / (hi - lo) for v in vals]

    n_sharp = normalize(sharpness_vals)
    n_sat = normalize(saturation_vals)
    n_face = normalize(face_vals)

    # Sharpness matters most for a usable thumbnail; saturation and faces are tie-breakers.
    composite = [
        0.5 * s + 0.2 * sat + 0.3 * f
        for s, sat, f in zip(n_sharp, n_sat, n_face)
    ]
    best_idx = int(np.argmax(composite))
    return frames[best_idx]


def _upscale_lanczos(frame: np.ndarray, min_w: int = MIN_HD_W, min_h: int = MIN_HD_H) -> np.ndarray:
    h, w = frame.shape[:2]
    if w >= min_w and h >= min_h:
        return frame
    scale = max(min_w / w, min_h / h)
    new_w, new_h = int(round(w * scale)), int(round(h * scale))
    return cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4)


def _enhance_and_sharpen(frame: np.ndarray) -> np.ndarray:
    # Contrast + saturation boost in HSV/LAB space.
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l = clahe.apply(l)
    enhanced = cv2.merge((l, a, b))
    enhanced = cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)

    hsv = cv2.cvtColor(enhanced, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[:, :, 1] = np.clip(hsv[:, :, 1] * 1.15, 0, 255)  # +15% saturation
    enhanced = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)

    # Unsharp mask.
    blurred = cv2.GaussianBlur(enhanced, (0, 0), sigmaX=3)
    sharpened = cv2.addWeighted(enhanced, 1.5, blurred, -0.5, 0)
    return sharpened


def generate_thumbnail(video_path: str, output_dir: str, basename: str = "thumbnail") -> dict:
    """
    Generates thumbnail.jpg and thumbnail.png in output_dir.
    Returns {"jpg": path, "png": path}.
    """
    os.makedirs(output_dir, exist_ok=True)

    frames = _extract_candidate_frames(video_path)
    best = _pick_best_frame(frames)
    upscaled = _upscale_lanczos(best)
    final = _enhance_and_sharpen(upscaled)

    jpg_path = os.path.join(output_dir, f"{basename}.jpg")
    png_path = os.path.join(output_dir, f"{basename}.png")

    cv2.imwrite(jpg_path, final, [cv2.IMWRITE_JPEG_QUALITY, 100])
    cv2.imwrite(png_path, final, [cv2.IMWRITE_PNG_COMPRESSION, 1])

    print(f"[thumbnail] Best frame selected from {len(frames)} candidates -> {jpg_path}, {png_path}", file=sys.stderr)
    return {"jpg": jpg_path, "png": png_path}
