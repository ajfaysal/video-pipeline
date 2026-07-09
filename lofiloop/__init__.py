"""
LofiLoop
========
Studio-grade Lofi video rendering module.

Takes a short (e.g. 10-second) seamlessly-looping .mp4 and a long audio file
(fetched zero-config from a public Google Drive link) and renders a long,
YouTube-monetization-safe lofi video of any target duration.

Public helpers:
    resolve_audio  - download / resolve the long audio track (gdown + direct URL).
    resolve_video  - resolve the local/remote short loop video.
    render_lofi    - the ultra-high-quality FFmpeg render engine.
    upload_file    - API-key-less upload (GoFile -> transfer.sh fallback).
"""

from __future__ import annotations

__all__ = [
    "resolve_audio",
    "resolve_video",
    "render_lofi",
    "upload_file",
    "DownloadError",
    "RenderError",
    "UploadError",
]


class LofiLoopError(Exception):
    """Base error for the LofiLoop module."""


class DownloadError(LofiLoopError):
    """Raised when an input (audio/video) cannot be fetched."""


class RenderError(LofiLoopError):
    """Raised when the FFmpeg render fails."""


class UploadError(LofiLoopError):
    """Raised when every upload backend fails."""


# Lazy re-exports keep import of the package cheap and avoid pulling in
# optional deps (gdown) until they are actually needed.
def resolve_audio(*args, **kwargs):
    from lofiloop.downloader import resolve_audio as _f
    return _f(*args, **kwargs)


def resolve_video(*args, **kwargs):
    from lofiloop.downloader import resolve_video as _f
    return _f(*args, **kwargs)


def render_lofi(*args, **kwargs):
    from lofiloop.render import render_lofi as _f
    return _f(*args, **kwargs)


def upload_file(*args, **kwargs):
    from lofiloop.uploader import upload_file as _f
    return _f(*args, **kwargs)
