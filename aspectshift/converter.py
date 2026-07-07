"""
converter.py
------------
Converts a 16:9 (or any aspect) source video into a 1080x1920 (9:16) output
using one of two strategies:

  1. "blur"  (default) - blur-background-fill: the original frame, scaled to
     fit the full output height and centered, is overlaid on top of a
     heavily blurred + cropped version of the same frame stretched to fill
     the entire 1080x1920 canvas. Nothing is stretched or cropped away from
     the sharp foreground -> zero visible quality loss on the subject.

  2. "crop"  - smart content-aware crop: uses OpenCV face + motion detection
     to find the most important horizontal region of the frame, then crops
     a 9:16 window centered on that region (no blur pillarbox).

Both modes encode with libx264 -crf 16 -preset slow (near-lossless) and
copy the source audio codec when possible, falling back to AAC 320k.
"""

from __future__ import annotations

import os
import subprocess
import sys

import cv2
import numpy as np

from aspectshift.downloader import probe_video, InvalidVideoError, MissingDependencyError, _require_binary, load_face_cascade

TARGET_W = 1080
TARGET_H = 1920


def _run_ffmpeg(cmd: list[str]) -> None:
    _require_binary("ffmpeg")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg command failed.\ncmd: {' '.join(cmd)}\nstderr: {result.stderr[-2000:]}")


def _audio_map_args(input_path: str) -> list[str]:
    """Return ffmpeg args that copy source audio, falling back to AAC 320k if copy fails."""
    info = probe_video(input_path)
    if not info["has_audio"]:
        return ["-an"]
    return ["-map", "0:a:0", "-c:a", "copy"]


def _encode_with_audio_fallback(base_cmd_no_audio_out: list[str], input_path: str, output_path: str) -> None:
    """
    Try encoding with audio stream copy first (fast, lossless audio).
    If that fails (incompatible codec for the container), retry with AAC 320k.
    """
    audio_args = _audio_map_args(input_path)
    cmd = base_cmd_no_audio_out + audio_args + [output_path]
    _require_binary("ffmpeg")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        return

    if "-an" in audio_args:
        raise RuntimeError(f"ffmpeg encode failed.\nstderr: {result.stderr[-2000:]}")

    # Retry with re-encoded AAC audio as a fallback.
    fallback_cmd = base_cmd_no_audio_out + ["-map", "0:a:0", "-c:a", "aac", "-b:a", "320k"] + [output_path]
    fallback_result = subprocess.run(fallback_cmd, capture_output=True, text=True)
    if fallback_result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg encode failed even with AAC audio fallback.\n"
            f"copy stderr: {result.stderr[-1000:]}\n"
            f"aac stderr: {fallback_result.stderr[-1000:]}"
        )


def convert_blur(input_path: str, output_path: str) -> str:
    """Blur-background-fill 9:16 conversion. Returns the output path."""
    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)

    filter_complex = (
        f"[0:v]split=2[bg_src][fg_src];"
        f"[bg_src]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
        f"crop={TARGET_W}:{TARGET_H},gblur=sigma=25,eq=brightness=-0.02[bg];"
        f"[fg_src]scale={TARGET_W}:-2:force_original_aspect_ratio=decrease,"
        f"scale='min(iw,{TARGET_W})':'min(ih,{TARGET_H})'[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2:format=auto[v]"
    )

    base_cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-c:v", "libx264", "-crf", "16", "-preset", "slow",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
    ]
    _encode_with_audio_fallback(base_cmd, input_path, output_path)
    print(f"[converter] blur-pad 9:16 written to {output_path}", file=sys.stderr)
    return output_path


def _detect_focus_center_x(input_path: str, sample_count: int = 12) -> float:
    """
    Sample frames across the video, detect faces (Haar cascade) and estimate
    motion energy (frame diff), and return a fractional x-center (0.0-1.0)
    for where the crop window should be centered. Falls back to 0.5 (true
    center) if nothing interesting is detected.
    """
    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        raise InvalidVideoError(f"OpenCV could not open '{input_path}' for crop analysis.")

    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1

    face_cascade = load_face_cascade()  # None if this OpenCV build lacks cascade support

    sample_indices = np.linspace(0, max(frame_count - 1, 0), num=sample_count, dtype=int)
    weighted_x_sum = 0.0
    weight_total = 0.0
    prev_gray = None

    for idx in sample_indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
        ok, frame = cap.read()
        if not ok:
            continue

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        if face_cascade is not None:
            faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
        else:
            faces = []
        if len(faces) > 0:
            # Weight by face area - bigger/closer faces matter more.
            for (x, y, w, h) in faces:
                cx = (x + w / 2.0) / width
                area = w * h
                weighted_x_sum += cx * area
                weight_total += area

        if prev_gray is not None and len(faces) == 0:
            diff = cv2.absdiff(gray, prev_gray)
            _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
            ys, xs = np.where(thresh > 0)
            if len(xs) > 200:  # enough motion pixels to be meaningful
                cx = float(np.mean(xs)) / width
                weight = float(len(xs))
                weighted_x_sum += cx * weight
                weight_total += weight

        prev_gray = gray

    cap.release()

    if weight_total == 0:
        return 0.5
    center = weighted_x_sum / weight_total
    return float(np.clip(center, 0.15, 0.85))


def convert_crop(input_path: str, output_path: str) -> str:
    """Smart content-aware crop 9:16 conversion (no blur pillarbox). Returns output path."""
    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    info = probe_video(input_path)
    src_w, src_h = info["width"], info["height"]

    target_ratio = TARGET_W / TARGET_H  # 9/16
    src_ratio = src_w / src_h

    if src_ratio > target_ratio:
        # Source is wider than target -> crop width, keep full height.
        crop_h = src_h
        crop_w = int(round(src_h * target_ratio))
    else:
        # Source is taller/narrower than target -> crop height, keep full width.
        crop_w = src_w
        crop_h = int(round(src_w / target_ratio))

    focus_x = _detect_focus_center_x(input_path)
    ideal_x = focus_x * src_w - crop_w / 2.0
    max_x = src_w - crop_w
    crop_x = int(np.clip(ideal_x, 0, max(max_x, 0)))
    crop_y = int(max(src_h - crop_h, 0) / 2)  # vertical center; content is usually centered vertically

    filter_chain = (
        f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},"
        f"scale={TARGET_W}:{TARGET_H}:flags=lanczos"
    )

    base_cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", filter_chain,
        "-c:v", "libx264", "-crf", "16", "-preset", "slow",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
    ]
    _encode_with_audio_fallback(base_cmd, input_path, output_path)
    print(
        f"[converter] smart-crop 9:16 written to {output_path} "
        f"(focus_x={focus_x:.2f}, crop_x={crop_x})",
        file=sys.stderr,
    )
    return output_path


def convert_to_vertical(input_path: str, output_path: str, mode: str = "blur") -> str:
    """Dispatch to convert_blur or convert_crop based on `mode`."""
    if mode == "blur":
        return convert_blur(input_path, output_path)
    elif mode == "crop":
        return convert_crop(input_path, output_path)
    else:
        raise ValueError(f"Unknown mode '{mode}'. Use 'blur' or 'crop'.")
