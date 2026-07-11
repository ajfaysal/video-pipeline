"""
PhotoStudio - professional photo upscaling (up to 16K Ultra HD) and
color effects (DSLR look, cinematic, HDR, portrait bokeh, and more).

Usage:
    python photostudio/main.py --input photo.jpg --resolution 4k --effect dslr --output-dir ./output
    python photostudio/main.py --url https://.../photo.png --effect cinematic --output-dir ./output
    python photostudio/main.py --input photo.jpg --resolution 16k --output-dir ./output

Exactly one of --resolution / --effect is required (both together is fine):
  --resolution only  -> pure upscale
  --effect only      -> pure color grade at native resolution
  both               -> grade then upscale
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from aspectshift.downloader import (
    resolve_image_input,
    DownloadError,
    InvalidImageError,
    MissingDependencyError,
)
from photostudio.effects import PRESETS, apply_effect
from photostudio.upscaler import RESOLUTION_TIERS, upscale_file


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PhotoStudio: upscale photos up to 16K and apply pro color effects.")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--input", help="Path to a local image file.")
    src.add_argument("--url", help="URL of an image to download and process.")
    parser.add_argument("--resolution", choices=list(RESOLUTION_TIERS), default=None,
                        help="Upscale target: 2k, 4k, 8k, or 16k (long-edge pixels).")
    parser.add_argument("--effect", choices=list(PRESETS), default=None,
                        help="Color effect preset (e.g. dslr, cinematic, hdr, portrait).")
    parser.add_argument("--output-dir", default="./output", help="Directory to write outputs to.")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()

    if not args.resolution and not args.effect:
        print("[error] Provide --resolution and/or --effect (otherwise there is nothing to do).",
              file=sys.stderr)
        return 5

    try:
        source_path = resolve_image_input(input_path=args.input, url=args.url,
                                          output_dir=args.output_dir)

        if args.resolution:
            out_path = upscale_file(source_path, args.output_dir,
                                    resolution=args.resolution, effect=args.effect)
        else:
            # Effect-only: grade at native resolution.
            import cv2
            img = cv2.imread(source_path, cv2.IMREAD_COLOR)
            print(f"[main] Applying effect '{args.effect}' ...", file=sys.stderr)
            result = apply_effect(img, args.effect)
            base = os.path.splitext(os.path.basename(source_path))[0]
            out_path = os.path.join(args.output_dir, f"{base}_{args.effect}.png")
            cv2.imwrite(out_path, result, [cv2.IMWRITE_PNG_COMPRESSION, 6])

        print(f"[main] Done. Final output image: {out_path}")
        return 0

    except MissingDependencyError as e:
        print(f"[error] Missing dependency: {e}", file=sys.stderr)
        return 2
    except (DownloadError, InvalidImageError, ValueError) as e:
        print(f"[error] {e}", file=sys.stderr)
        return 3
    except Exception as e:
        print(f"[error] Unexpected failure: {e}", file=sys.stderr)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
