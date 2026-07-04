"""
WatermarkWipe CLI - removes watermarks/logos from videos via crop or inpaint.

Usage:
    python main.py --input video.mp4 --mode inpaint --auto-detect --quality-check --output-dir ./output
    python main.py --input video.mp4 --mode crop --region 1600,50,300,100 --output-dir ./output
    python main.py --url "https://youtube.com/..." --mode inpaint --auto-detect --output-dir ./output
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from aspectshift.downloader import resolve_input, DownloadError, InvalidVideoError, MissingDependencyError
from aspectshift.enhance import color_grade, background_blur, COLOR_GRADE_PRESETS
from watermarkwipe.watermark_detector import detect_corner_region, detect_watermark_region
from watermarkwipe.watermark_remover import crop_remove, inpaint_remove, quality_check


def _parse_region(value: str) -> tuple[int, int, int, int]:
    try:
        parts = [int(p.strip()) for p in value.split(",")]
        if len(parts) != 4:
            raise ValueError
        return tuple(parts)  # type: ignore[return-value]
    except ValueError:
        raise argparse.ArgumentTypeError("--region must be 'x,y,w,h' (four integers), e.g. 1600,50,300,100")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Remove watermarks/logos from a video.")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--input", help="Path to a local video file.")
    src.add_argument("--url", help="URL of a video to download and process.")
    parser.add_argument("--mode", choices=["crop", "inpaint"], default="inpaint")
    parser.add_argument("--region", type=_parse_region,
                         help="Manual watermark region as 'x,y,w,h'. If omitted, use --auto-detect.")
    parser.add_argument("--auto-detect", action="store_true",
                         help="Auto-detect the watermark region instead of specifying --region.")
    parser.add_argument("--inpaint-method", choices=["telea", "ns"], default="telea",
                         help="OpenCV inpainting algorithm (only used with --mode inpaint).")
    parser.add_argument("--quality-check", action="store_true",
                         help="Export a before/after comparison of one sample frame before full processing.")
    parser.add_argument("--color-grade", choices=list(COLOR_GRADE_PRESETS), default=None,
                         help="Optional professional color-grading preset applied after watermark removal.")
    parser.add_argument("--background-blur", action="store_true",
                         help="Optional portrait-mode style background blur applied as the final step.")
    parser.add_argument("--output-dir", default="./output")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    os.makedirs(args.output_dir, exist_ok=True)

    if not args.region and not args.auto_detect:
        print("[error] Provide either --region x,y,w,h or --auto-detect.", file=sys.stderr)
        return 5

    try:
        source_path = resolve_input(input_path=args.input, url=args.url, output_dir=args.output_dir)

        if args.region:
            region = args.region
            print(f"[main] Using manual region: {region}")
        else:
            print(f"[main] Auto-detecting watermark region for mode={args.mode} ...")
            region = (detect_corner_region(source_path) if args.mode == "crop"
                       else detect_watermark_region(source_path))

        if args.quality_check:
            qc_path = quality_check(source_path, args.output_dir, region, mode=args.mode,
                                     method=args.inpaint_method)
            print(f"[main] Quality-check comparison saved to {qc_path}. "
                  f"Review it, then re-run without --quality-check to process the full video "
                  f"(or add it -- it's cheap -- to double check after tweaking --region).")

        base_name = os.path.splitext(os.path.basename(source_path))[0]
        output_video = os.path.join(args.output_dir, f"{base_name}_clean_{args.mode}.mp4")

        print(f"[main] Removing watermark using mode={args.mode}, region={region} ...")
        if args.mode == "crop":
            crop_remove(source_path, output_video, region)
        else:
            inpaint_remove(source_path, output_video, region, method=args.inpaint_method)

        current_path = output_video
        if args.color_grade:
            graded_path = os.path.join(args.output_dir, f"{base_name}_graded.mp4")
            print(f"[main] Applying color grade '{args.color_grade}' ...")
            color_grade(current_path, graded_path, style=args.color_grade)
            current_path = graded_path

        if args.background_blur:
            blurred_path = os.path.join(args.output_dir, f"{base_name}_bgblur.mp4")
            print("[main] Applying background blur ...")
            background_blur(current_path, blurred_path)
            current_path = blurred_path

        print(f"[main] Done. Final output video: {current_path}")
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
