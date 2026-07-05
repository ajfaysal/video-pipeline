"""
IntroOutro CLI - adds a branded intro and outro to any video using ffmpeg
drawtext overlays with fade-in/out.

Usage:
    python introoutro/main.py --input video.mp4 --intro-text "..." --outro-text "..." --output-dir ./output
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path
from tempfile import TemporaryDirectory

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from aspectshift.downloader import DownloadError, InvalidVideoError, MissingDependencyError, probe_video, resolve_input


def _looks_like_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def _resolve_source(value: str, output_dir: str) -> str:
    return resolve_input(url=value, output_dir=output_dir) if _looks_like_url(value) else resolve_input(input_path=value, output_dir=output_dir)


def _run(cmd: list[str]) -> None:
    print(f"[introoutro] $ {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {result.returncode}: {' '.join(cmd)}\n{result.stderr[-2000:]}")


def _normalize_video(source_path: str, output_path: str, target_width: int, target_height: int, target_fps: float) -> None:
    info = probe_video(source_path)
    duration = max(0.1, info["duration"])
    vf = (
        f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,"
        f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={target_fps},format=yuv420p"
    )
    if info["has_audio"]:
        cmd = [
            "ffmpeg", "-y", "-i", source_path,
            "-vf", vf,
            "-c:v", "libx264", "-crf", "16", "-preset", "slow", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "48000", "-ac", "2",
            "-shortest",
            output_path,
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-i", source_path,
            "-f", "lavfi", "-t", f"{duration:.3f}", "-i", "anullsrc=r=48000:cl=stereo",
            "-vf", vf,
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-crf", "16", "-preset", "slow", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "48000", "-ac", "2",
            "-shortest",
            output_path,
        ]
    _run(cmd)


def _brand_clip(kind: str, text: str, duration: float, output_path: str, target_width: int, target_height: int, target_fps: float) -> None:
    text_file = output_path + f".{kind}.txt"
    with open(text_file, "w", encoding="utf-8") as handle:
        handle.write(text.strip() + "\n")

    try:
        font_size = max(28, int(min(target_width, target_height) * 0.06))
        fade_duration = min(0.6, duration / 3.0)
        fade_out_start = max(0.0, duration - fade_duration)
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=0x0b1020:s={target_width}x{target_height}:r={target_fps}:d={duration:.3f}",
            "-f", "lavfi", "-t", f"{duration:.3f}", "-i", "anullsrc=r=48000:cl=stereo",
            "-vf", (
                f"drawtext=font='DejaVu Sans':textfile={Path(text_file).resolve().as_posix()}:reload=0:"
                f"fontcolor=white:fontsize={font_size}:line_spacing=12:box=1:boxcolor=black@0.42:boxborderw=28:"
                f"x=(w-text_w)/2:y=(h-text_h)/2,"
                f"fade=t=in:st=0:d={fade_duration},"
                f"fade=t=out:st={fade_out_start}:d={fade_duration},format=yuv420p"
            ),
            "-c:v", "libx264", "-crf", "16", "-preset", "slow", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "48000", "-ac", "2",
            "-shortest",
            output_path,
        ]
        _run(cmd)
    finally:
        if os.path.exists(text_file):
            os.remove(text_file)


def _concat_segments(segment_paths: list[str], output_path: str) -> None:
    list_file = output_path + ".concat.txt"
    with open(list_file, "w", encoding="utf-8") as handle:
        for segment_path in segment_paths:
            handle.write(f"file '{Path(segment_path).resolve().as_posix()}'\n")
    try:
        _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", output_path])
    finally:
        if os.path.exists(list_file):
            os.remove(list_file)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Add a branded intro and outro to a video.")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--input", help="Path to a local video file.")
    src.add_argument("--url", help="URL of a video to download and brand.")
    parser.add_argument("--intro-text", default="Your Channel Name", help="Text shown in the intro card.")
    parser.add_argument("--outro-text", default="Subscribe for more", help="Text shown in the outro card.")
    parser.add_argument("--intro-duration", type=float, default=3.5, help="Intro duration in seconds (3-5 recommended).")
    parser.add_argument("--outro-duration", type=float, default=3.5, help="Outro duration in seconds (3-5 recommended).")
    parser.add_argument("--output-dir", default="./output", help="Directory to write outputs to.")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    os.makedirs(args.output_dir, exist_ok=True)

    if not 3.0 <= args.intro_duration <= 5.0:
        print("[error] --intro-duration must be between 3 and 5 seconds.", file=sys.stderr)
        return 5
    if not 3.0 <= args.outro_duration <= 5.0:
        print("[error] --outro-duration must be between 3 and 5 seconds.", file=sys.stderr)
        return 5

    try:
        with TemporaryDirectory(prefix="introoutro_") as temp_dir:
            source_path = _resolve_source(args.input or args.url, temp_dir)
            info = probe_video(source_path)

            target_width = info["width"]
            target_height = info["height"]
            target_fps = info["fps"] or 30.0

            base_name = os.path.splitext(os.path.basename(source_path))[0]
            output_path = os.path.join(args.output_dir, f"{base_name}_branded.mp4")

            intro_path = os.path.join(temp_dir, "intro.mp4")
            outro_path = os.path.join(temp_dir, "outro.mp4")
            main_path = os.path.join(temp_dir, "main_norm.mp4")

            print("[introoutro] Building intro card...")
            _brand_clip("intro", args.intro_text, args.intro_duration, intro_path, target_width, target_height, target_fps)
            print("[introoutro] Normalizing main video...")
            _normalize_video(source_path, main_path, target_width, target_height, target_fps)
            print("[introoutro] Building outro card...")
            _brand_clip("outro", args.outro_text, args.outro_duration, outro_path, target_width, target_height, target_fps)

            _concat_segments([intro_path, main_path, outro_path], output_path)
            print(f"[introoutro] Done. Output video: {output_path}")
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
