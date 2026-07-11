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
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse

import requests

_VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mpg", ".mpeg", ".ts", ".3gp", ".flv", ".wmv")
_AUDIO_EXTS = (".mp3", ".m4a", ".aac", ".wav", ".ogg", ".oga", ".opus", ".flac", ".wma", ".aiff")
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".tif", ".heic")
_GDRIVE_ID_RE = re.compile(
    r"drive\.google\.com/(?:file/d/([-\w]{20,})|open\?id=([-\w]{20,})|uc\?(?:[^#]*&)?id=([-\w]{20,}))"
)
_DOWNLOAD_CHUNK = 1024 * 1024  # 1 MiB


class DownloadError(RuntimeError):
    """Raised when a URL could not be downloaded into a usable video file."""


class InvalidVideoError(RuntimeError):
    """Raised when a local file is missing, unreadable, or corrupted."""


class InvalidAudioError(RuntimeError):
    """Raised when a local file is missing, unreadable, or not audio-only."""


class InvalidImageError(RuntimeError):
    """Raised when a local file is missing, unreadable, or not a valid image."""


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


def _extract_gdrive_file_id(url: str) -> str | None:
    """Return the Google Drive file id if `url` is a Drive share/view link, else None."""
    m = _GDRIVE_ID_RE.search(url)
    if not m:
        return None
    return next(g for g in m.groups() if g)


def _ext_from_url_or_headers(url: str, content_type: str | None, content_disposition: str | None,
                             audio_only: bool) -> str:
    """Best-effort extension for a directly-downloaded file."""
    # 1. Filename in Content-Disposition header.
    if content_disposition:
        m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', content_disposition)
        if m:
            ext = os.path.splitext(m.group(1).strip())[1].lower()
            if ext in _VIDEO_EXTS or ext in _AUDIO_EXTS or ext in _IMAGE_EXTS:
                return ext
    # 2. Extension in the URL path.
    path_ext = os.path.splitext(urlparse(url).path)[1].lower()
    if path_ext in _VIDEO_EXTS or path_ext in _AUDIO_EXTS or path_ext in _IMAGE_EXTS:
        return path_ext
    # 3. Content-Type mapping.
    if content_type:
        ct = content_type.split(";")[0].strip().lower()
        ct_map = {
            "video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm",
            "video/x-matroska": ".mkv", "video/x-msvideo": ".avi", "video/mpeg": ".mpg",
            "audio/mpeg": ".mp3", "audio/mp4": ".m4a", "audio/aac": ".aac",
            "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/ogg": ".ogg",
            "audio/opus": ".opus", "audio/flac": ".flac",
            "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
            "image/bmp": ".bmp", "image/tiff": ".tiff",
        }
        if ct in ct_map:
            return ct_map[ct]
    return ".mp3" if audio_only else ".mp4"


def _is_direct_file_url(url: str) -> bool:
    """
    True if `url` points straight at a media file that should be fetched with
    plain HTTP instead of yt-dlp: Telegram Bot API file links, any URL whose
    path ends in a known video/audio extension, or any URL whose server
    reports a video/* or audio/* content-type.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()

    # Telegram Bot API file endpoint: https://api.telegram.org/file/bot<token>/...
    if host == "api.telegram.org" and parsed.path.startswith("/file/"):
        return True

    ext = os.path.splitext(parsed.path)[1].lower()
    if ext in _VIDEO_EXTS or ext in _AUDIO_EXTS or ext in _IMAGE_EXTS:
        return True

    # Last resort: cheap HEAD request to sniff the content-type. Failures here
    # simply mean "not direct" and we fall back to yt-dlp.
    try:
        resp = requests.head(url, allow_redirects=True, timeout=15)
        ct = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
        return ct.startswith("video/") or ct.startswith("audio/") or ct.startswith("image/")
    except requests.RequestException:
        return False


def _stream_to_disk(resp: requests.Response, dest_path: str, url: str) -> str:
    try:
        with open(dest_path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=_DOWNLOAD_CHUNK):
                if chunk:
                    fh.write(chunk)
    except requests.RequestException as e:
        if os.path.isfile(dest_path):
            os.remove(dest_path)
        raise DownloadError(f"Connection dropped while downloading '{url}': {e}")
    if not os.path.isfile(dest_path) or os.path.getsize(dest_path) == 0:
        if os.path.isfile(dest_path):
            os.remove(dest_path)
        raise DownloadError(f"Download of '{url}' produced an empty file.")
    return os.path.abspath(dest_path)


def _download_direct(url: str, output_dir: str, audio_only: bool = False) -> str:
    """Download a direct file URL with streaming requests (no yt-dlp)."""
    os.makedirs(output_dir, exist_ok=True)
    unique_id = uuid.uuid4().hex[:8]
    try:
        resp = requests.get(url, stream=True, allow_redirects=True, timeout=(15, 120))
        resp.raise_for_status()
    except requests.RequestException as e:
        raise DownloadError(f"Direct download of '{url}' failed: {e}")

    ext = _ext_from_url_or_headers(
        url, resp.headers.get("Content-Type"), resp.headers.get("Content-Disposition"), audio_only
    )
    dest_path = os.path.join(output_dir, f"source_{unique_id}{ext}")
    path = _stream_to_disk(resp, dest_path, url)
    print(f"[downloader] Direct download complete: {path}", file=sys.stderr)
    return path


def _download_gdrive(file_id: str, output_dir: str, audio_only: bool = False) -> str:
    """
    Download a Google Drive file by id, handling the "can't scan for viruses"
    confirmation interstitial that Drive serves for large files.
    """
    os.makedirs(output_dir, exist_ok=True)
    unique_id = uuid.uuid4().hex[:8]
    session = requests.Session()
    url = f"https://drive.google.com/uc?export=download&id={file_id}"

    try:
        resp = session.get(url, stream=True, timeout=(15, 120))
        resp.raise_for_status()

        ct = resp.headers.get("Content-Type", "").lower()
        if "text/html" in ct:
            # Large file: Drive returned the confirmation page instead of the
            # bytes. Parse the download form's hidden fields and re-request
            # against the endpoint the form posts to.
            html = resp.text
            resp.close()

            action_m = re.search(r'action="([^"]+)"', html)
            action = action_m.group(1).replace("&amp;", "&") if action_m else \
                "https://drive.usercontent.google.com/download"
            params = {"id": file_id, "export": "download", "confirm": "t"}
            for name, value in re.findall(r'name="([^"]+)"\s+value="([^"]*)"', html):
                params[name] = value
            # Legacy cookie-based token (older interstitial variant).
            for cookie_name, cookie_value in session.cookies.items():
                if cookie_name.startswith("download_warning"):
                    params["confirm"] = cookie_value

            resp = session.get(action, params=params, stream=True, timeout=(15, 300))
            resp.raise_for_status()
            if "text/html" in resp.headers.get("Content-Type", "").lower():
                raise DownloadError(
                    f"Google Drive refused to serve file '{file_id}'. "
                    f"Make sure the file is shared as 'Anyone with the link'."
                )
    except requests.RequestException as e:
        raise DownloadError(f"Google Drive download of file '{file_id}' failed: {e}")

    ext = _ext_from_url_or_headers(
        "", resp.headers.get("Content-Type"), resp.headers.get("Content-Disposition"), audio_only
    )
    dest_path = os.path.join(output_dir, f"source_{unique_id}{ext}")
    path = _stream_to_disk(resp, dest_path, f"gdrive:{file_id}")
    print(f"[downloader] Google Drive download complete: {path}", file=sys.stderr)
    return path


def download_from_url(url: str, output_dir: str, audio_only: bool = False) -> str:
    """
    Download `url` into `output_dir`. Returns local path.

    Routing:
      - Google Drive share links -> direct-download endpoint (with large-file
        confirmation-token handling).
      - Direct file URLs (Telegram Bot API file links, *.mp4/... paths, or
        anything serving a video/audio content-type) -> streaming requests.
      - Everything else (YouTube, Twitter, TikTok, ...) -> yt-dlp extraction.
    """
    gdrive_id = _extract_gdrive_file_id(url)
    if gdrive_id:
        print(f"[downloader] Detected Google Drive link (id={gdrive_id}); using direct Drive download.",
              file=sys.stderr)
        return _download_gdrive(gdrive_id, output_dir, audio_only=audio_only)

    if _is_direct_file_url(url):
        print("[downloader] Detected direct file URL; downloading with plain HTTP (bypassing yt-dlp).",
              file=sys.stderr)
        return _download_direct(url, output_dir, audio_only=audio_only)

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


def resolve_image_input(input_path: str | None = None, url: str | None = None, output_dir: str = ".") -> str:
    """
    Resolve either a local image file or a URL into a validated local image path.
    Exactly one of input_path / url must be provided.
    """
    if bool(input_path) == bool(url):
        raise ValueError("Provide exactly one of --input (local path) or --url, not both/neither.")

    os.makedirs(output_dir, exist_ok=True)

    if url:
        print(f"[downloader] Downloading source image from URL: {url}", file=sys.stderr)
        gdrive_id = _extract_gdrive_file_id(url)
        if gdrive_id:
            local_path = _download_gdrive(gdrive_id, output_dir)
        else:
            local_path = _download_direct(url, output_dir)
    else:
        if not os.path.isfile(input_path):
            raise InvalidImageError(f"Input file not found: {input_path}")
        local_path = os.path.abspath(input_path)

    # Validate: must be decodable as an image.
    try:
        import cv2
        img = cv2.imread(local_path, cv2.IMREAD_COLOR)
    except ImportError:
        raise MissingDependencyError("opencv-python is required to validate/process images.")
    if img is None:
        raise InvalidImageError(
            f"'{local_path}' could not be decoded as an image. "
            f"Supported formats: {', '.join(_IMAGE_EXTS)}"
        )
    h, w = img.shape[:2]
    print(f"[downloader] Resolved image input: {local_path} ({w}x{h})", file=sys.stderr)
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
