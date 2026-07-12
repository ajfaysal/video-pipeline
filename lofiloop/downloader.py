"""
lofiloop.downloader
--------------------
Zero-config input resolution for the LofiLoop module.

* Long audio track: fetched from a *public* Google Drive link with no API keys
  (uses `gdown`, which understands the confirm-token dance for large files).
  Also handles plain direct-download URLs and already-local paths.
* Short loop video: local path or any direct/streamable URL.

Nothing here needs credentials. Everything is best-effort with clear errors.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request

from lofiloop import DownloadError

_AUDIO_EXTS = (".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma")
_VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v")

# Aggressive retry policy shared by every download path.
_MAX_RETRIES = 4          # attempts per strategy
_BACKOFF_BASE = 3.0       # seconds; grows 3, 6, 12


def _retry_sleep(attempt: int) -> None:
    wait = _BACKOFF_BASE * (2 ** (attempt - 1))
    print(f"[lofiloop] retrying in {wait:.0f}s (attempt {attempt + 1}/{_MAX_RETRIES})...")
    time.sleep(wait)


# --------------------------------------------------------------------------- #
# Google Drive helpers
# --------------------------------------------------------------------------- #
def _extract_drive_id(url: str) -> str | None:
    """Pull the file id out of the many shapes a Drive share link can take."""
    # Normalise: strip whitespace, unwrap redirectors, percent-decode.
    url = urllib.parse.unquote(url.strip())
    m = re.search(r"[?&]q=(https?[^&\s]+)", url)  # google.com/url?q=<real-link>
    if m:
        url = urllib.parse.unquote(m.group(1))

    patterns = [
        r"/file/d/([a-zA-Z0-9_-]{20,})",          # .../file/d/<id>/view
        r"[?&]id=([a-zA-Z0-9_-]{20,})",           # ...open?id=<id>  /  uc?id=<id>
        r"/download\?id=([a-zA-Z0-9_-]{20,})",    # drive.usercontent.google.com
        r"/d/([a-zA-Z0-9_-]{20,})",               # short /d/<id>
        r"/document/d/([a-zA-Z0-9_-]{20,})",      # docs
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    # Last resort: a bare file id pasted on its own.
    if re.fullmatch(r"[a-zA-Z0-9_-]{25,}", url):
        return url
    return None


def _is_drive_url(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    return ("drive.google.com" in host or "docs.google.com" in host
            or "drive.usercontent.google.com" in host)


def _looks_like_html(path: str) -> bool:
    """Detect the classic Drive failure mode: an HTML error/quota page saved as the 'file'."""
    try:
        if os.path.getsize(path) > 5 * 1024 * 1024:
            return False  # real HTML error pages are small
        with open(path, "rb") as f:
            head = f.read(512).lstrip().lower()
        return head.startswith((b"<!doctype html", b"<html"))
    except Exception:
        return False


def _download_from_drive(url: str, dest_dir: str, prefer: str = "audio") -> str:
    """
    Download a *public* Drive file with gdown (no API key required).

    Bulletproofing:
      * up to _MAX_RETRIES attempts per strategy with exponential backoff
      * 4 fetch strategies covering every known Drive URL shape:
          1. gdown by bare file id
          2. gdown on the classic uc?export=download URL
          3. gdown fuzzy on the original URL exactly as pasted
          4. direct HTTP fetch of drive.usercontent.google.com (final fallback)
      * HTML quota/permission pages are detected and rejected instead of being
        passed to ffmpeg as 'audio'.
    """
    try:
        import gdown as _gdown
    except ImportError as e:  # pragma: no cover - environment guard
        raise DownloadError(
            "gdown is required to fetch Google Drive links. Add `gdown` to requirements.txt."
        ) from e

    file_id = _extract_drive_id(url)
    if not file_id:
        raise DownloadError(f"Couldn't extract a Google Drive file id from: {url}")

    os.makedirs(dest_dir, exist_ok=True)
    out_template = os.path.join(dest_dir, "drive_audio" if prefer == "audio" else "drive_video")

    print(f"[lofiloop] Google Drive file id: {file_id}")

    def _by_id():
        return _gdown.download(id=file_id, output=out_template, quiet=False, fuzzy=True)

    def _by_uc_url():
        uc_url = f"https://drive.google.com/uc?id={file_id}&export=download"
        return _gdown.download(uc_url, out_template, quiet=False, fuzzy=True)

    def _by_original_url():
        return _gdown.download(url, out_template, quiet=False, fuzzy=True)

    def _by_usercontent():
        direct = (f"https://drive.usercontent.google.com/download"
                  f"?id={file_id}&export=download&confirm=t")
        return _download_direct_once(direct, out_template + "_direct")

    strategies = [("gdown id", _by_id), ("gdown uc-url", _by_uc_url),
                  ("gdown original", _by_original_url), ("usercontent direct", _by_usercontent)]

    last_error: Exception | None = None
    for name, strategy in strategies:
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                print(f"[lofiloop] Drive strategy '{name}' attempt {attempt}/{_MAX_RETRIES}...")
                downloaded = strategy()
                if downloaded and os.path.isfile(downloaded) and os.path.getsize(downloaded) > 0:
                    if _looks_like_html(downloaded):
                        raise DownloadError("Got an HTML page instead of the file (permission/quota wall).")
                    return _ensure_media_extension(downloaded, prefer=prefer)
                raise DownloadError("strategy returned no file")
            except Exception as e:
                last_error = e
                print(f"[lofiloop] Drive strategy '{name}' failed: {e}")
                if attempt < _MAX_RETRIES:
                    _retry_sleep(attempt)

    raise DownloadError(
        "All Google Drive download strategies failed. Make sure the link is set to "
        f"'Anyone with the link can view'. Last error: {last_error}"
    )


# --------------------------------------------------------------------------- #
# Direct URL helpers
# --------------------------------------------------------------------------- #
def _download_direct_once(url: str, dest: str) -> str:
    """Single-attempt streaming download with HTTP Range resume support."""
    existing = os.path.getsize(dest) if os.path.isfile(dest) else 0
    headers = {"User-Agent": "Mozilla/5.0 (LofiLoop)"}
    mode = "wb"
    if existing > 0:
        headers["Range"] = f"bytes={existing}-"
        mode = "ab"

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=180) as resp:
        # Server ignored our Range request -> start over from scratch.
        if existing > 0 and getattr(resp, "status", 200) != 206:
            mode = "wb"
        with open(dest, mode) as out:
            shutil.copyfileobj(resp, out, length=4 * 1024 * 1024)

    if not os.path.isfile(dest) or os.path.getsize(dest) == 0:
        raise DownloadError(f"Downloaded file is empty: {url}")
    return dest


def _download_direct(url: str, dest_dir: str, prefer: str) -> str:
    os.makedirs(dest_dir, exist_ok=True)
    parsed = urllib.parse.urlparse(url)
    name = os.path.basename(parsed.path) or ("audio_input" if prefer == "audio" else "video_input")
    # Guard against querystring-only names and hostile characters.
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:120] or "media_input"
    dest = os.path.join(dest_dir, name)

    print(f"[lofiloop] Downloading direct URL -> {dest}")
    last_error: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            _download_direct_once(url, dest)  # resumes partial data on retry
            return _ensure_media_extension(dest, prefer=prefer)
        except Exception as e:
            last_error = e
            print(f"[lofiloop] Direct download attempt {attempt}/{_MAX_RETRIES} failed: {e}")
            if attempt < _MAX_RETRIES:
                _retry_sleep(attempt)

    raise DownloadError(f"Failed to download {url} after {_MAX_RETRIES} attempts: {last_error}")


def _ensure_media_extension(path: str, prefer: str) -> str:
    """If a file has no recognizable media extension, probe it and add one."""
    lower = path.lower()
    known = _AUDIO_EXTS if prefer == "audio" else _VIDEO_EXTS
    if lower.endswith(known + (_AUDIO_EXTS if prefer == "video" else _VIDEO_EXTS)):
        return path

    ext = _probe_extension(path, prefer)
    if ext and not lower.endswith(ext):
        new_path = path + ext
        os.replace(path, new_path)
        return new_path
    return path


def _probe_extension(path: str, prefer: str) -> str | None:
    """Use ffprobe to sniff the container/codec and pick a sane extension."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=format_name",
             "-of", "default=nk=1:nw=1", path],
            capture_output=True, text=True, timeout=60,
        ).stdout.strip().lower()
    except Exception:
        return ".mp3" if prefer == "audio" else ".mp4"

    if not out:
        return ".mp3" if prefer == "audio" else ".mp4"
    if "mp3" in out:
        return ".mp3"
    if "flac" in out:
        return ".flac"
    if "wav" in out:
        return ".wav"
    if "ogg" in out:
        return ".ogg"
    if any(k in out for k in ("mp4", "m4a", "mov")):
        return ".m4a" if prefer == "audio" else ".mp4"
    if "matroska" in out or "webm" in out:
        return ".webm"
    return ".mp3" if prefer == "audio" else ".mp4"


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def resolve_audio(source: str, output_dir: str) -> str:
    """Return a local path to the long audio track from a Drive/direct URL/path."""
    if not source:
        raise DownloadError("No audio source provided.")

    if os.path.isfile(source):
        print(f"[lofiloop] Audio is a local file: {source}")
        return source

    if _is_drive_url(source):
        print("[lofiloop] Resolving Google Drive audio (zero-config, gdown)...")
        return _download_from_drive(source, output_dir, prefer="audio")

    if source.startswith(("http://", "https://")):
        return _download_direct(source, output_dir, prefer="audio")

    raise DownloadError(f"Unrecognized audio source (not a file, Drive link, or URL): {source}")


def resolve_video(source: str, output_dir: str) -> str:
    """Return a local path to the short loop video from a path/URL."""
    if not source:
        raise DownloadError("No video source provided.")

    if os.path.isfile(source):
        print(f"[lofiloop] Loop video is a local file: {source}")
        return source

    if _is_drive_url(source):
        print("[lofiloop] Resolving Google Drive loop video (gdown)...")
        return _download_from_drive(source, output_dir, prefer="video")

    if source.startswith(("http://", "https://")):
        return _download_direct(source, output_dir, prefer="video")

    raise DownloadError(f"Unrecognized video source (not a file, Drive link, or URL): {source}")


if __name__ == "__main__":  # tiny manual smoke test
    if len(sys.argv) >= 3:
        kind, src = sys.argv[1], sys.argv[2]
        out = "./_lofi_dl_test"
        fn = resolve_audio if kind == "audio" else resolve_video
        print("Resolved:", fn(src, out))
    else:
        print("usage: python -m lofiloop.downloader [audio|video] <source>")
