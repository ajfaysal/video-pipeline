"""
LofiLoop CLI - render an ultra-high-quality, monetization-safe lofi video by
seamlessly looping a short clip + long audio to any target duration.

Usage:
    python lofiloop/main.py \
        --video loop.mp4 \
        --audio "https://drive.google.com/file/d/<id>/view" \
        --hours 10 \
        --output-dir ./job_output

    # Local audio also works:
    python lofiloop/main.py --video loop.mp4 --audio track.mp3 --hours 2

The final render is written to --output-dir and (unless --no-upload) uploaded to
a free, API-key-less host; the direct link is printed and written to
<output-dir>/lofi_manifest.json.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from lofiloop import DownloadError, RenderError, UploadError
from lofiloop.downloader import resolve_audio, resolve_video
from lofiloop.render import render_lofi
from lofiloop.uploader import upload_file


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Render a seamless, monetization-safe lofi loop video.")
    p.add_argument("--video", required=True, help="Short (~10s) looping .mp4: local path or URL.")
    p.add_argument("--audio", required=True, help="Long audio: Google Drive link, direct URL, or local path.")
    p.add_argument("--hours", type=float, required=True, help="Target video duration in hours (e.g. 2, 10, 24).")
    p.add_argument("--output-dir", default="./job_output", help="Directory to write outputs to.")
    p.add_argument("--fps", type=float, default=None, help="Output fps (defaults to source loop fps).")
    p.add_argument("--crf", type=int, default=18, help="H.264 CRF quality (lower=better, 18=visually lossless).")
    p.add_argument("--preset", default="veryfast", help="x264 preset (ultrafast..veryslow).")
    p.add_argument("--noise", type=int, default=1, help="Invisible per-frame noise strength (1=microscopic).")
    p.add_argument("--seed", type=int, default=None, help="RNG seed (random per-render by default).")
    p.add_argument("--no-upload", action="store_true", help="Skip uploading; only render locally.")
    p.add_argument("--output-name", default=None, help="Custom output filename (without extension).")
    return p


def main() -> int:
    args = build_arg_parser().parse_args()
    os.makedirs(args.output_dir, exist_ok=True)

    try:
        print("[lofiloop] Resolving loop video...")
        video_path = resolve_video(args.video, args.output_dir)

        print("[lofiloop] Resolving long audio track...")
        audio_path = resolve_audio(args.audio, args.output_dir)

        # NOTE: parenthesized deliberately — the previous version had a
        # precedence bug where --output-name was silently ignored whenever
        # a fractional number of hours was requested.
        default_base = f"lofi_{int(args.hours)}h" if args.hours == int(args.hours) else f"lofi_{args.hours}h"
        base = args.output_name or default_base
        output_path = os.path.join(args.output_dir, f"{base}.mp4")

        info = render_lofi(
            video_path=video_path,
            audio_path=audio_path,
            output_path=output_path,
            duration_hours=args.hours,
            fps=args.fps,
            crf=args.crf,
            preset=args.preset,
            noise_strength=args.noise,
            seed=args.seed,
        )

        manifest = dict(info)
        manifest["uploaded"] = False
        manifest["download_url"] = None
        manifest["upload_host"] = None

        if not args.no_upload:
            try:
                up = upload_file(output_path)
                manifest["uploaded"] = True
                manifest["download_url"] = up["url"]
                manifest["upload_host"] = up["host"]
                print(f"[lofiloop] Direct download link: {up['url']}")
            except UploadError as e:
                print(f"[lofiloop] Upload failed (file still available locally): {e}", file=sys.stderr)

        manifest_path = os.path.join(args.output_dir, "lofi_manifest.json")
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)
        print(f"[lofiloop] Manifest written: {manifest_path}")

        print(f"[lofiloop] Done. Output: {output_path}")
        return 0

    except DownloadError as e:
        print(f"[error] Download failed: {e}", file=sys.stderr)
        return 3
    except RenderError as e:
        print(f"[error] Render failed: {e}", file=sys.stderr)
        return 4
    except Exception as e:
        print(f"[error] Unexpected failure: {e}", file=sys.stderr)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
