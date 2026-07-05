"""
downloader.py
--------------
Resolves a video "source" (local file path OR a URL) into a local video file
ready for processing. Used directly by AspectShift, and re-used by
ClipHarvest and WatermarkWipe so all three tools share one battle-tested
download/validation code path.

Public API:
    resolve_input(input_path=None, url=None, output_dir=".") -> str
        Returns the absolute path to a local, playable video file.

    probe_video(path) -> dict
        Returns basic stream info (width, height, duration, has_audio, codec)
        via ffprobe. Used to validate the file isn't corrupted and to drive
        downstream decisions (e.g. does this file even have audio to copy).

    load_face_cascade() -> cv2.CascadeClassifier | None
        Attempts to load an OpenCV Haar cascade for face detection. Returns
        None (instead of raising) when cv2 or the cascade data is unavailable,
        so callers can gracefully skip face-detection scoring.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path


class DownloadError(RuntimeError):
    """Raised when a URL could not be downloaded into a usable video file."""


class InvalidVideoError(RuntimeError):
    """Raised when a local file is missing, unreadable, or corrupted."""


class InvalidAudioError(RuntimeError):
    """Raised when a local file is missing, unreadable, or not audio-only."""


class MissingDependencyError(RuntimeError):
    """Raised when a required external binary (ffmpeg/ffprobe/yt-dlp) is absent."""


def _require_binary(name: str) -> None:
    if shutil.which(name) is None:
        raise MissingDependencyError(
            f"Required binary '{name}' was not found on PATH. "
            f"Install it (e.g. `apt-get install -y {name}` or `pip install {name}`) "
            f"before running this tool."
        )


def probe_video(path: str) -> dict:
    """Return {width, height, duration, has_audio, fps, video_codec} for a video file."""
    _require_binary("ffprobe")
    if not os.path.isfile(path):
        raise InvalidVideoError(f"File does not exist: {path}")

    cmd = [
        "ffprobe", "-v", "error",
        "-print_format", "json",
        "-show_streams", "-show_format",
        path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout.strip():
        raise InvalidVideoError(
            f"ffprobe could not read '{path}'. The file may be corrupted or "
            f"not a valid video. stderr: {result.stderr.strip()[:500]}"
        )

    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise InvalidVideoError(f"ffprobe returned malformed JSON for '{path}': {e}")

    streams = info.get("streams", [])
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

    if not video_streams:
        raise InvalidVideoError(f"'{path}' contains no video stream (corrupted or audio-only).")

    v = video_streams[0]
    duration = float(info.get("format", {}).get("duration", 0.0) or 0.0)

    fps_raw = v.get("avg_frame_rate", "0/1")
    try:
        num, den = fps_raw.split("/")
        fps = float(num) / float(den) if float(den) != 0 else 0.0
    except (ValueError, ZeroDivisionError):
        fps = 0.0

    return {
        "width": int(v.get("width", 0)),
        "height": int(v.get("height", 0)),
        "duration": duration,
        "has_audio": len(audio_streams) > 0,
        "fps": fps,
        "video_codec": v.get("codec_name", "unknown"),
        "audio_codec": audio_streams[0].get("codec_name") if audio_streams else None,
    }


def probe_audio(path: str) -> dict:
    """Return {duration, has_audio, sample_rate, channels, audio_codec} for an audio file."""
    _require_binary("ffprobe")
    if not os.path.isfile(path):
        raise InvalidAudioError(f"File does not exist: {path}")

    cmd = [
        "ffprobe", "-v", "error",
        "-print_format", "json",
        "-show_streams", "-show_format",
        path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout.strip():
        raise InvalidAudioError(
            f"ffprobe could not read '{path}'. The file may be corrupted or not a valid audio file. stderr: {result.stderr.strip()[:500]}"
        )

    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise InvalidAudioError(f"ffprobe returned malformed JSON for '{path}': {e}")

    streams = info.get("streams", [])
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

    if not audio_streams:
        raise InvalidAudioError(f"'{path}' contains no audio stream (corrupted or not audio-only).")

    a = audio_streams[0]
    duration = float(info.get("format", {}).get("duration", 0.0) or 0.0)
    return {
        "duration": duration,
        "has_audio": True,
        "sample_rate": int(a.get("sample_rate", 0) or 0),
        "channels": int(a.get("channels", 0) or 0),
        "audio_codec": a.get("codec_name", "unknown"),
    }


def download_from_url(url: str, output_dir: str, audio_only: bool = False) -> str:
    """Download `url` into `output_dir` using yt-dlp. Returns local path."""
    _require_binary("yt-dlp")
    os.makedirs(output_dir, exist_ok=True)

    unique_id = uuid.uuid4().hex[:8]
    out_template = os.path.join(output_dir, f"source_{unique_id}.%(ext)s")

    if audio_only:
        cmd = [
            "yt-dlp",
            "-f", "ba/bestaudio/b",
            "--no-playlist",
            "-o", out_template,
            url,
        ]
    else:
        cmd = [
            "yt-dlp",
            "-f", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
            "--merge-output-format", "mp4",
            "--no-playlist",
            "-o", out_template,
            url,
        ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise DownloadError(
            f"yt-dlp failed to download '{url}'.\nstderr: {result.stderr.strip()[-1000:]}"
        )

    expected_path = os.path.join(output_dir, f"source_{unique_id}.mp4")
    if os.path.isfile(expected_path):
        return os.path.abspath(expected_path)

    # yt-dlp occasionally muxes into a different container despite the template;
    # find whatever it actually produced.
    candidates = sorted(Path(output_dir).glob(f"source_{unique_id}.*"))
    if not candidates:
        raise DownloadError(
            f"yt-dlp reported success but no output file matching 'source_{unique_id}.*' "
            f"was found in {output_dir}."
        )
    return os.path.abspath(str(candidates[0]))


def resolve_input(input_path: str | None = None, url: str | None = None, output_dir: str = ".") -> str:
    """
    Resolve either a local file or a URL into a validated local video path.
    Exactly one of input_path / url must be provided.
    """
    if bool(input_path) == bool(url):
        raise ValueError("Provide exactly one of --input (local path) or --url, not both/neither.")

    os.makedirs(output_dir, exist_ok=True)

    if url:
        print(f"[downloader] Downloading source video from URL: {url}", file=sys.stderr)
        local_path = download_from_url(url, output_dir)
    else:
        if not os.path.isfile(input_path):
            raise InvalidVideoError(f"Input file not found: {input_path}")
        local_path = os.path.abspath(input_path)

    # Validate immediately so failures surface before expensive processing starts.
    info = probe_video(local_path)
    print(
        f"[downloader] Resolved input: {local_path} "
        f"({info['width']}x{info['height']}, {info['duration']:.1f}s, "
        f"audio={'yes' if info['has_audio'] else 'no'})",
        file=sys.stderr,
    )
    return local_path


def resolve_audio_input(input_path: str | None = None, url: str | None = None, output_dir: str = ".") -> str:
    """
    Resolve either a local audio file or a URL into a validated local audio path.
    Exactly one of input_path / url must be provided.
    """
    if bool(input_path) == bool(url):
        raise ValueError("Provide exactly one of --voiceover path or URL, not both/neither.")

    os.makedirs(output_dir, exist_ok=True)

    if url:
        print(f"[downloader] Downloading source audio from URL: {url}", file=sys.stderr)
        local_path = download_from_url(url, output_dir, audio_only=True)
    else:
        if not os.path.isfile(input_path):
            raise InvalidAudioError(f"Input file not found: {input_path}")
        local_path = os.path.abspath(input_path)

    info = probe_audio(local_path)
    print(
        f"[downloader] Resolved audio input: {local_path} "
        f"({info['duration']:.1f}s, sample_rate={info['sample_rate']}, channels={info['channels']})",
        file=sys.stderr,
    )
    return local_path


def load_face_cascade():
    """
    Attempts to load an OpenCV Haar cascade for frontal-face detection.

    Some OpenCV builds used in CI (e.g. opencv-python-headless without the
    bundled data files, or minimal wheels) do not expose cv2.CascadeClassifier
    or cv2.data. Rather than let every caller crash with an AttributeError,
    this returns None in that case so tools can gracefully skip face-based
    scoring and fall back to other heuristics (sharpness, saturation, etc).
    """
    try:
        import cv2
    except ImportError:
        print("[downloader] cv2 is not installed; face detection disabled.", file=sys.stderr)
        return None

    cascade_classifier = getattr(cv2, "CascadeClassifier", None)
    cascade_data = getattr(cv2, "data", None)
    if cascade_classifier is None or cascade_data is None:
        print(
            "[downloader] This OpenCV build has no CascadeClassifier/data module; "
            "face detection disabled.",
            file=sys.stderr,
        )
        return None

    try:
        cascade_path = os.path.join(cascade_data.haarcascades, "haarcascade_frontalface_default.xml")
        cascade = cascade_classifier(cascade_path)
        if cascade.empty():
            print(
                f"[downloader] Haar cascade failed to load from '{cascade_path}'; "
                f"face detection disabled.",
                file=sys.stderr,
            )
            return None
        return cascade
    except Exception as e:
        print(f"[downloader] Unexpected error loading face cascade: {e}; face detection disabled.", file=sys.stderr)
        return None
