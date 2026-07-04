"""
enhance.py
----------
Optional "professional editor" post-processing steps, usable by any tool
in this repo (currently wired into WatermarkWipe's output):

  color_grade()      - fast ffmpeg-only color grading presets (cinematic
                        teal-orange, vibrant, warm, cool).
  background_blur()  - portrait-mode style blur: detects the main subject
                        (largest/most consistent face across the clip),
                        tracks a smoothed foreground region frame-by-frame,
                        and blurs everything outside it while keeping the
                        subject sharp.
"""

from __future__ import annotations

import os
import subprocess
import sys

import cv2
import numpy as np
from tqdm import tqdm

from aspectshift.downloader import _require_binary, probe_video

COLOR_GRADE_PRESETS = {
    # eq: brightness/contrast/saturation/gamma tuning; curves: channel-specific
    # tone mapping. These are deliberately subtle to stay "professional" rather
    # than cartoonish.
    "cinematic": (
        "curves=r='0/0 0.5/0.48 1/0.95':b='0/0.05 0.5/0.5 1/0.9',"
        "eq=contrast=1.08:saturation=0.92:gamma=0.98"
    ),
    "vibrant": "eq=contrast=1.05:saturation=1.35:brightness=0.02",
    "warm": "colorbalance=rs=0.08:gs=0.02:bs=-0.08:rm=0.06:bm=-0.06,eq=saturation=1.1",
    "cool": "colorbalance=rs=-0.08:bs=0.08:rm=-0.05:bm=0.07,eq=saturation=1.05",
}


def color_grade(input_path: str, output_path: str, style: str = "cinematic") -> str:
    """Applies a color-grading preset. Raises ValueError for an unknown style."""
    if style not in COLOR_GRADE_PRESETS:
        raise ValueError(f"Unknown color grade style '{style}'. Options: {list(COLOR_GRADE_PRESETS)}")

    _require_binary("ffmpeg")
    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)

    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", COLOR_GRADE_PRESETS[style],
        "-c:v", "libx264", "-crf", "14", "-preset", "slow",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        fallback_cmd = cmd[:-3] + ["-c:a", "aac", "-b:a", "320k", output_path]
        fallback = subprocess.run(fallback_cmd, capture_output=True, text=True)
        if fallback.returncode != 0:
            raise RuntimeError(
                f"Color grading failed.\ncopy stderr: {result.stderr[-1000:]}\n"
                f"aac stderr: {fallback.stderr[-1000:]}"
            )
    print(f"[enhance] Color grade '{style}' applied -> {output_path}", file=sys.stderr)
    return output_path


def _detect_subject_box(frame: np.ndarray, cascade: cv2.CascadeClassifier,
                         prev_box: tuple[int, int, int, int] | None) -> tuple[int, int, int, int] | None:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60))
    if len(faces) == 0:
        return prev_box  # hold the last known box rather than snapping to nothing

    # Pick the largest face, then expand into a generous "subject" box
    # (head + shoulders) rather than just the tight face rectangle.
    x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
    frame_h, frame_w = frame.shape[:2]
    expand_x, expand_y_top, expand_y_bottom = int(w * 1.1), int(h * 0.9), int(h * 2.6)

    nx = max(x - expand_x, 0)
    ny = max(y - expand_y_top, 0)
    nw = min(w + 2 * expand_x, frame_w - nx)
    nh = min(h + expand_y_top + expand_y_bottom, frame_h - ny)
    return (nx, ny, nw, nh)


def _smooth_box(prev: tuple[int, int, int, int] | None, new: tuple[int, int, int, int],
                 alpha: float = 0.25) -> tuple[int, int, int, int]:
    if prev is None:
        return new
    return tuple(int(alpha * n + (1 - alpha) * p) for p, n in zip(prev, new))  # type: ignore[return-value]


def background_blur(input_path: str, output_path: str, blur_strength: int = 35) -> str:
    """
    Portrait-mode style background blur: keeps a smoothed subject region
    sharp and blurs everything else, frame by frame. Falls back to
    blurring nothing outside a full-frame box if no face is ever detected
    (i.e. the output is effectively unchanged) rather than blurring a
    presumed subject that was never found.
    """
    _require_binary("ffmpeg")
    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)

    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV could not open '{input_path}' for background blur.")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    silent_path = output_path + ".silent.mp4"
    writer = cv2.VideoWriter(silent_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    if not writer.isOpened():
        cap.release()
        raise RuntimeError("Could not open OpenCV VideoWriter for background-blur output.")

    smoothed_box = None
    raw_box = None
    frame_idx = 0
    detect_every = 5  # re-detect every N frames; interpolate/hold in between for speed + stability
    any_face_found = False

    try:
        with tqdm(total=total_frames, desc="Background blur", unit="frame") as pbar:
            while True:
                ok, frame = cap.read()
                if not ok:
                    break

                if frame_idx % detect_every == 0:
                    new_box = _detect_subject_box(frame, cascade, raw_box)
                    if new_box is not None:
                        any_face_found = True
                        raw_box = new_box
                if raw_box is not None:
                    smoothed_box = _smooth_box(smoothed_box, raw_box)

                if smoothed_box is not None:
                    blurred = cv2.GaussianBlur(frame, (0, 0), sigmaX=blur_strength / 3.0)
                    x, y, w, h = smoothed_box
                    mask = np.zeros((height, width), dtype=np.float32)
                    cv2.ellipse(mask, (x + w // 2, y + h // 2), (w // 2, h // 2), 0, 0, 360, 1.0, -1)
                    mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=25)  # feather the edge
                    mask_3c = cv2.merge([mask, mask, mask])
                    composed = (frame.astype(np.float32) * mask_3c + blurred.astype(np.float32) * (1 - mask_3c))
                    out_frame = composed.astype(np.uint8)
                else:
                    out_frame = frame

                writer.write(out_frame)
                frame_idx += 1
                pbar.update(1)
    finally:
        cap.release()
        writer.release()

    if not any_face_found:
        os.remove(silent_path)
        print("[enhance] No subject/face detected anywhere in the video - "
              "skipping background blur to avoid blurring the whole frame incorrectly.", file=sys.stderr)
        # Just re-encode to the target codec settings unchanged, so the caller
        # still gets a valid file at output_path.
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-c:v", "libx264", "-crf", "14", "-preset", "slow", "-pix_fmt", "yuv420p",
            "-c:a", "copy", output_path,
        ]
        subprocess.run(cmd, capture_output=True, text=True)
        return output_path

    info = probe_video(input_path)
    base_cmd = [
        "ffmpeg", "-y", "-i", silent_path, "-i", input_path,
        "-map", "0:v:0",
        "-c:v", "libx264", "-crf", "14", "-preset", "slow", "-pix_fmt", "yuv420p",
    ]
    if info["has_audio"]:
        cmd = base_cmd + ["-map", "1:a:0", "-c:a", "copy", "-shortest", output_path]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            fallback = base_cmd + ["-map", "1:a:0", "-c:a", "aac", "-b:a", "320k", "-shortest", output_path]
            subprocess.run(fallback, capture_output=True, text=True)
    else:
        subprocess.run(base_cmd + [output_path], capture_output=True, text=True)

    os.remove(silent_path)
    print(f"[enhance] Background blur applied -> {output_path}", file=sys.stderr)
    return output_path
