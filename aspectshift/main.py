"""
AspectShift CLI - converts 16:9 video to 9:16 with zero visible quality loss.

Usage:
    python main.py --input video.mp4 --mode blur --output-dir ./output
    python main.py --url "https://youtube.com/..." --mode blur --output-dir ./output
    python main.py --input video.mp4 --mode crop --output-dir ./output
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback

# Allow running as `python aspectshift/main.py` from repo root as well as
# `python -m aspectshift.main`.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from aspectshift.downloader import resolve_input, DownloadError, InvalidVideoError, MissingDependencyError
from aspectshift.converter import convert_to_vertical
from aspectshift.thumbnail import generate_thumbnail


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Convert 16:9 video to 9:16 with zero visible quality loss.")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--input", help="Path to a local video file.")
    src.add_argument("--url", help="URL of a video to download (via yt-dlp) and convert.")
    parser.add_argument("--mode", choices=["blur", "crop"], default="blur",
                         help="Conversion mode: 'blur' (blur-background-fill, default) or 'crop' (smart content-aware crop).")
    parser.add_argument("--output-dir", default="./output", help="Directory to write outputs to.")
    parser.add_argument("--no-thumbnail", action="store_true", help="Skip automatic thumbnail generation.")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    os.makedirs(args.output_dir, exist_ok=True)

    try:
        source_path = resolve_input(input_path=args.input, url=args.url, output_dir=args.output_dir)

        base_name = os.path.splitext(os.path.basename(source_path))[0]
        output_video = os.path.join(args.output_dir, f"{base_name}_9x16_{args.mode}.mp4")

        print(f"[main] Converting '{source_path}' -> '{output_video}' (mode={args.mode})")
        convert_to_vertical(source_path, output_video, mode=args.mode)

        if not args.no_thumbnail:
            thumbs = generate_thumbnail(output_video, args.output_dir, basename=f"{base_name}_thumbnail")
            print(f"[main] Thumbnails: {thumbs['jpg']}, {thumbs['png']}")

        print(f"[main] Done. Output video: {output_video}")
        return 0

    except MissingDependencyError as e:
        print(f"[error] Missing dependency: {e}", file=sys.stderr)
        return 2
    except (DownloadError, InvalidVideoError) as e:
        print(f"[error] {e}", file=sys.stderr)
        return 3
    except Exception as e:
        print(f"[error] Unexpected failure: {e}", file=sys.stderr)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
