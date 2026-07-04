"""
clipper.py
----------
Cuts a single clip [start, end] from the source video into its own file.
Uses accurate (re-encoded) seeking rather than stream-copy trimming, since
copy-trim can only cut on keyframes and would drift from the scored
sentence-boundary timestamps.
"""

from __future__ import annotations

import os
import subprocess

from aspectshift.downloader import _require_binary


def extract_audio(video_path: str, output_path: str) -> str:
    """Extract a mono 16kHz WAV of the full video's audio, for librosa scoring."""
    _require_binary("ffmpeg")
    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Audio extraction failed.\nstderr: {result.stderr[-1500:]}")
    return output_path


def cut_clip(source_path: str, start: float, end: float, output_path: str,
             padding: float = 0.15) -> str:
    """
    Cuts [start - padding, end + padding] (clamped) from source_path into
    output_path, re-encoded at near-lossless quality so the cut point is frame-accurate.
    """
    _require_binary("ffmpeg")
    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)

    clip_start = max(0.0, start - padding)
    duration = (end - start) + 2 * padding

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{clip_start:.3f}",
        "-i", source_path,
        "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-crf", "16", "-preset", "slow",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "320k",
        "-movflags", "+faststart",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Clip cut failed for [{start:.2f}, {end:.2f}].\nstderr: {result.stderr[-1500:]}")
    return output_path
