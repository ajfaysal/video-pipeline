"""
LoudNorm CLI - normalizes audio loudness to a target LUFS level with
ffmpeg's loudnorm filter using a two-pass measurement/normalization flow.

Usage:
    python loudnorm/main.py --input video.mp4 --target-lufs -14 --output-dir ./output
    python loudnorm/main.py --url "https://youtube.com/..." --target-lufs -14 --output-dir ./output
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import traceback

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from aspectshift.downloader import DownloadError, InvalidVideoError, MissingDependencyError, probe_video, resolve_input


def _run(cmd: list[str], capture_output: bool = False) -> subprocess.CompletedProcess[str]:
    print(f"[loudnorm] $ {' '.join(cmd)}")
    return subprocess.run(cmd, capture_output=capture_output, text=True)


def _looks_like_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def _resolve_source(value: str, output_dir: str) -> str:
    return resolve_input(url=value, output_dir=output_dir) if _looks_like_url(value) else resolve_input(input_path=value, output_dir=output_dir)


def _extract_measurements(stderr_text: str) -> dict:
    matches = re.findall(r"\{[^{}]*\}", stderr_text, flags=re.S)
    if not matches:
        raise RuntimeError("ffmpeg loudnorm did not return JSON measurements.")

    for candidate in reversed(matches):
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if "input_i" in data:
            return data

    raise RuntimeError("Could not parse loudnorm measurements from ffmpeg output.")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Normalize audio loudness to a target LUFS level with ffmpeg loudnorm.")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--input", help="Path to a local video file.")
    src.add_argument("--url", help="URL of a video to download and normalize.")
    parser.add_argument("--target-lufs", type=float, default=-14.0, help="Target integrated loudness in LUFS (default: -14).")
    parser.add_argument("--target-tp", type=float, default=-1.5, help="Target true peak in dBTP (default: -1.5).")
    parser.add_argument("--target-lra", type=float, default=11.0, help="Target loudness range in LU (default: 11).")
    parser.add_argument("--output-dir", default="./output", help="Directory to write outputs to.")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    os.makedirs(args.output_dir, exist_ok=True)

    try:
        with tempfile.TemporaryDirectory(prefix="loudnorm_") as temp_dir:
            source_path = _resolve_source(args.input or args.url, temp_dir)
            info = probe_video(source_path)
            if not info["has_audio"]:
                print("[error] The input video has no audio stream to normalize.", file=sys.stderr)
                return 5

            base_name = os.path.splitext(os.path.basename(source_path))[0]
            output_path = os.path.join(args.output_dir, f"{base_name}_loudnorm.mp4")

            analysis_filter = f"loudnorm=I={args.target_lufs}:TP={args.target_tp}:LRA={args.target_lra}:print_format=json"
            first_pass = _run([
                "ffmpeg", "-y", "-i", source_path,
                "-vn",
                "-af", analysis_filter,
                "-f", "null", "-",
            ], capture_output=True)
            if first_pass.returncode != 0:
                raise RuntimeError(f"First-pass loudnorm measurement failed:\n{first_pass.stderr[-2000:]}")

            measurements = _extract_measurements(first_pass.stderr or "")

            normalization_filter = (
                "loudnorm="
                f"I={args.target_lufs}:TP={args.target_tp}:LRA={args.target_lra}:"
                f"measured_I={measurements['input_i']}:"
                f"measured_TP={measurements['input_tp']}:"
                f"measured_LRA={measurements['input_lra']}:"
                f"measured_thresh={measurements['input_thresh']}:"
                f"offset={measurements['target_offset']}:"
                "linear=true:print_format=summary"
            )

            second_pass = _run([
                "ffmpeg", "-y", "-i", source_path,
                "-c:v", "copy",
                "-af", normalization_filter,
                "-c:a", "aac", "-ar", "48000", "-ac", "2",
                output_path,
            ], capture_output=True)
            if second_pass.returncode != 0:
                raise RuntimeError(f"Second-pass loudnorm normalization failed:\n{second_pass.stderr[-2000:]}")

            print(f"[loudnorm] Done. Output video: {output_path}")
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