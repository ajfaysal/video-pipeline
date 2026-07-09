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
import urllib.parse
import urllib.request

from lofiloop import DownloadError

_AUDIO_EXTS = (".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma")
_VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v")


# --------------------------------------------------------------------------- #
# Google Drive helpers
# --------------------------------------------------------------------------- #
def _extract_drive_id(url: str) -> str | None:
    """Pull the file id out of the many shapes a Drive share link can take."""
    patterns = [
        r"/file/d/([a-zA-Z0-9_-]{20,})",          # .../file/d/<id>/view
        r"[?&]id=([a-zA-Z0-9_-]{20,})",           # ...open?id=<id>  /  uc?id=<id>
        r"/d/([a-zA-Z0-9_-]{20,})",               # short /d/<id>
        r"/document/d/([a-zA-Z0-9_-]{20,})",      # docs
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


def _is_drive_url(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    return "drive.google.com" in host or "docs.google.com" in host


def _download_from_drive(url: str, dest_dir: str) -> str:
    """Download a *public* Drive file with gdown (no API key required)."""
    try:
        import gdown  # noqa: F401
    except ImportError as e:  # pragma: no cover - environment guard
        raise DownloadError(
            "gdown is required to fetch Google Drive links. Add `gdown` to requirements.txt."
        ) from e

    file_id = _extract_drive_id(url)
    if not file_id:
        raise DownloadError(f"Couldn't extract a Google Drive file id from: {url}")

    os.makedirs(dest_dir, exist_ok=True)
    out_template = os.path.join(dest_dir, "drive_audio")

    print(f"[lofiloop] Google Drive file id: {file_id}")

    import gdown as _gdown

    # gdown handles the big-file confirm token + cookies automatically.
    downloaded = _gdown.download(
        id=file_id,
        output=out_template,
        quiet=False,
        fuzzy=True,
    )
    if not downloaded or not os.path.isfile(downloaded):
        # Fallback: try the classic uc?export=download URL directly.
        uc_url = f"https://drive.google.com/uc?id={file_id}&export=download"
        downloaded = _gdown.download(uc_url, out_template, quiet=False, fuzzy=True)

    if not downloaded or not os.path.isfile(downloaded):
        raise DownloadError(
            "gdown failed to download the file. Make sure the Drive link is set to "
            "'Anyone with the link can view'."
        )

    return _ensure_media_extension(downloaded, prefer="audio")


# --------------------------------------------------------------------------- #
# Direct URL helpers
# --------------------------------------------------------------------------- #
def _download_direct(url: str, dest_dir: str, prefer: str) -> str:
    os.makedirs(dest_dir, exist_ok=True)
    parsed = urllib.parse.urlparse(url)
    name = os.path.basename(parsed.path) or ("audio_input" if prefer == "audio" else "video_input")
    dest = os.path.join(dest_dir, name)

    print(f"[lofiloop] Downloading direct URL -> {dest}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (LofiLoop)"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as out:
            shutil.copyfileobj(resp, out, length=1024 * 1024)
    except Exception as e:
        raise DownloadError(f"Failed to download {url}: {e}") from e

    if os.path.getsize(dest) == 0:
        raise DownloadError(f"Downloaded file is empty: {url}")

    return _ensure_media_extension(dest, prefer=prefer)


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
        return _download_from_drive(source, output_dir)

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
        return _download_from_drive(source, output_dir)

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
