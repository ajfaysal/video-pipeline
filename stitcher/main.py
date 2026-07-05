"""
Stitcher CLI - joins multiple clips into one with configurable ffmpeg xfade
transitions between each pair.

Usage:
    python stitcher/main.py --clips clip1.mp4 clip2.mp4 clip3.mp4 --transition crossfade --output-dir ./output
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
    print(f"[stitcher] $ {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {result.returncode}: {' '.join(cmd)}\n{result.stderr[-2000:]}")


def _normalize_clip(source_path: str, output_path: str, target_width: int, target_height: int, target_fps: float) -> None:
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


def _concat_copy(segment_paths: list[str], output_path: str) -> None:
    list_file = output_path + ".concat.txt"
    with open(list_file, "w", encoding="utf-8") as handle:
        for segment_path in segment_paths:
            handle.write(f"file '{Path(segment_path).resolve().as_posix()}'\n")
    try:
        _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", output_path])
    finally:
        if os.path.exists(list_file):
            os.remove(list_file)


def _xfade_join(segment_paths: list[str], durations: list[float], transition: str, transition_duration: float, output_path: str) -> None:
    transition_name = {
        "crossfade": "fade",
        "wipe-left": "wipeleft",
        "wipe-right": "wiperight",
    }[transition]

    cmd = ["ffmpeg", "-y"]
    for segment_path in segment_paths:
        cmd += ["-i", segment_path]

    filter_parts: list[str] = []
    for idx in range(len(segment_paths)):
        filter_parts.append(f"[{idx}:v:0]setpts=PTS-STARTPTS[v{idx}]")
        filter_parts.append(f"[{idx}:a:0]asetpts=PTS-STARTPTS[a{idx}]")

    video_label = "v0"
    audio_label = "a0"
    cumulative = durations[0]
    for idx in range(1, len(segment_paths)):
        offset = max(0.0, cumulative - transition_duration * idx)
        next_video = f"v{idx}"
        next_audio = f"a{idx}"
        out_video = f"vx{idx}"
        out_audio = f"ax{idx}"
        filter_parts.append(f"[{video_label}][{next_video}]xfade=transition={transition_name}:duration={transition_duration}:offset={offset}[{out_video}]")
        filter_parts.append(f"[{audio_label}][{next_audio}]acrossfade=d={transition_duration}[{out_audio}]")
        video_label = out_video
        audio_label = out_audio
        cumulative += durations[idx]

    cmd += [
        "-filter_complex", ";".join(filter_parts),
        "-map", f"[{video_label}]",
        "-map", f"[{audio_label}]",
        "-c:v", "libx264", "-crf", "16", "-preset", "slow", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "48000", "-ac", "2",
        output_path,
    ]
    _run(cmd)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Join multiple video clips with configurable transitions.")
    parser.add_argument("--clips", nargs="+", required=True, help="Two or more clips to stitch together.")
    parser.add_argument("--transition", choices=["crossfade", "wipe-left", "wipe-right", "cut"], default="crossfade")
    parser.add_argument("--transition-duration", type=float, default=0.8, help="Duration of the xfade overlap in seconds.")
    parser.add_argument("--output-dir", default="./output", help="Directory to write outputs to.")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    os.makedirs(args.output_dir, exist_ok=True)

    if len(args.clips) < 2:
        print("[error] Provide at least two clips.", file=sys.stderr)
        return 5

    try:
        with TemporaryDirectory(prefix="stitcher_") as temp_dir:
            resolved_paths = [_resolve_source(path, temp_dir) for path in args.clips]
            clip_infos = [probe_video(path) for path in resolved_paths]
            target_width = clip_infos[0]["width"]
            target_height = clip_infos[0]["height"]
            target_fps = clip_infos[0]["fps"] or 30.0

            base_name = os.path.splitext(os.path.basename(resolved_paths[0]))[0]
            output_path = os.path.join(args.output_dir, f"{base_name}_stitched.mp4")

            normalized_paths: list[str] = []
            durations: list[float] = []
            for index, (source_path, info) in enumerate(zip(resolved_paths, clip_infos, strict=True)):
                normalized_path = os.path.join(temp_dir, f"clip_{index:02d}.mp4")
                _normalize_clip(source_path, normalized_path, target_width, target_height, target_fps)
                normalized_paths.append(normalized_path)
                durations.append(info["duration"])

            if args.transition == "cut":
                _concat_copy(normalized_paths, output_path)
            elif len(normalized_paths) == 1:
                shutil.copy2(normalized_paths[0], output_path)
            else:
                shortest = min(durations)
                transition_duration = min(args.transition_duration, max(0.2, shortest * 0.35))
                _xfade_join(normalized_paths, durations, args.transition, transition_duration, output_path)

            print(f"[stitcher] Done. Output video: {output_path}")
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
