"""
ABRoll CLI - auto-detects natural cut points in a main video and interleaves
short B-roll inserts with ffmpeg xfade transitions.

Usage:
    python abroll/main.py --main video.mp4 --broll clip1.mp4 clip2.mp4 --output-dir ./output
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import traceback
from pathlib import Path
from tempfile import TemporaryDirectory

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from aspectshift.downloader import (
    DownloadError,
    InvalidVideoError,
    MissingDependencyError,
    probe_video,
    resolve_input,
)


def _looks_like_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def _resolve_source(value: str, output_dir: str) -> str:
    return resolve_input(url=value, output_dir=output_dir) if _looks_like_url(value) else resolve_input(input_path=value, output_dir=output_dir)


def _run(cmd: list[str]) -> None:
    print(f"[abroll] $ {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {result.returncode}: {' '.join(cmd)}\n{result.stderr[-2000:]}")


def _detect_points_with_silence(main_path: str, silence_db: str, min_duration: float) -> list[float]:
    cmd = [
        "ffmpeg", "-hide_banner", "-i", main_path,
        "-af", f"silencedetect=noise={silence_db}:d={min_duration}",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    text = result.stderr + "\n" + result.stdout
    return [float(match.group(1)) for match in re.finditer(r"silence_end:\s*([0-9.]+)", text)]


def _detect_points_with_scene(main_path: str, threshold: float) -> list[float]:
    cmd = [
        "ffmpeg", "-hide_banner", "-i", main_path,
        "-vf", f"select='gt(scene,{threshold})',showinfo",
        "-an", "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    text = result.stderr + "\n" + result.stdout
    return [float(match.group(1)) for match in re.finditer(r"pts_time:([0-9.]+)", text)]


def _select_cut_points(candidates: list[float], duration: float, max_inserts: int, min_gap: float) -> list[float]:
    selected: list[float] = []
    last = -min_gap
    for point in sorted({round(value, 2) for value in candidates}):
        if point < 1.5 or point > duration - 1.5:
            continue
        if point - last < min_gap:
            continue
        selected.append(point)
        last = point
        if len(selected) >= max_inserts:
            break
    return selected


def _normalize_segment(source_path: str, output_path: str, start: float, duration: float,
                       target_width: int, target_height: int, target_fps: float) -> None:
    info = probe_video(source_path)
    vf = (
        f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,"
        f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={target_fps},format=yuv420p"
    )
    clip_duration = max(0.1, duration)
    if info["has_audio"]:
        cmd = [
            "ffmpeg", "-y", "-ss", f"{start:.3f}", "-t", f"{clip_duration:.3f}", "-i", source_path,
            "-vf", vf,
            "-c:v", "libx264", "-crf", "16", "-preset", "slow", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "48000", "-ac", "2",
            "-shortest",
            output_path,
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-ss", f"{start:.3f}", "-t", f"{clip_duration:.3f}", "-i", source_path,
            "-f", "lavfi", "-t", f"{clip_duration:.3f}", "-i", "anullsrc=r=48000:cl=stereo",
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


def _xfade_join(segment_paths: list[str], durations: list[float], transition: str,
                transition_duration: float, output_path: str) -> None:
    transition_name = {
        "crossfade": "fade",
        "wipe-left": "wipeleft",
        "wipe-right": "wiperight",
    }[transition]

    cmd = ["ffmpeg", "-y"]
    for segment_path in segment_paths:
        cmd += ["-i", segment_path]

    filter_parts: list[str] = []
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

    for idx in range(len(segment_paths)):
        filter_parts.insert(0, f"[{idx}:v:0]setpts=PTS-STARTPTS[v{idx}]")
        filter_parts.insert(1, f"[{idx}:a:0]asetpts=PTS-STARTPTS[a{idx}]")

    cmd += ["-filter_complex", ";".join(filter_parts), "-map", f"[{video_label}]", "-map", f"[{audio_label}]", "-c:v", "libx264", "-crf", "16", "-preset", "slow", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-ac", "2", output_path]
    _run(cmd)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Auto-detect natural cut points and interleave B-roll inserts.")
    parser.add_argument("--main", required=True, help="Path or URL to the main video.")
    parser.add_argument("--broll", nargs="+", required=True, help="One or more B-roll clips to insert.")
    parser.add_argument("--output-dir", default="./output", help="Directory to write outputs to.")
    parser.add_argument("--insert-duration", type=float, default=2.5, help="Target length of each B-roll insert in seconds.")
    parser.add_argument("--transition-duration", type=float, default=0.75, help="xfade overlap duration in seconds.")
    parser.add_argument("--scene-threshold", type=float, default=0.35, help="ffmpeg scene detection threshold.")
    parser.add_argument("--silence-db", default="-35dB", help="ffmpeg silencedetect threshold.")
    parser.add_argument("--silence-min-duration", type=float, default=0.45, help="Minimum silence duration to count as a cut point.")
    parser.add_argument("--max-inserts", type=int, default=6, help="Maximum number of B-roll inserts to place.")
    parser.add_argument("--min-gap", type=float, default=8.0, help="Minimum gap between selected cut points.")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    os.makedirs(args.output_dir, exist_ok=True)

    try:
        with TemporaryDirectory(prefix="abroll_") as temp_dir:
            main_path = _resolve_source(args.main, temp_dir)
            broll_paths = [_resolve_source(path, temp_dir) for path in args.broll]

            main_info = probe_video(main_path)
            target_width = main_info["width"]
            target_height = main_info["height"]
            target_fps = main_info["fps"] or 30.0

            print("[abroll] Detecting cut points...")
            candidates = _detect_points_with_silence(main_path, args.silence_db, args.silence_min_duration)
            candidates += _detect_points_with_scene(main_path, args.scene_threshold)
            cut_points = _select_cut_points(candidates, main_info["duration"], args.max_inserts, args.min_gap)
            print(f"[abroll] Selected cut points: {cut_points}")

            base_name = os.path.splitext(os.path.basename(main_path))[0]
            output_path = os.path.join(args.output_dir, f"{base_name}_abroll.mp4")
            normalized_segments: list[str] = []
            segment_durations: list[float] = []

            if not cut_points:
                print("[abroll] No strong cut points found; exporting the main video without inserts.")
                normalized_main = os.path.join(temp_dir, "main_norm.mp4")
                _normalize_segment(main_path, normalized_main, 0.0, main_info["duration"], target_width, target_height, target_fps)
                shutil.copy2(normalized_main, output_path)
                print(f"[abroll] Done. Output video: {output_path}")
                return 0

            segment_ranges: list[tuple[str, str, float, float]] = []
            timeline = [0.0] + cut_points + [main_info["duration"]]
            for idx in range(len(timeline) - 1):
                start = timeline[idx]
                end = timeline[idx + 1]
                main_duration = max(0.1, end - start)
                segment_ranges.append(("main", main_path, start, main_duration))
                if idx < len(cut_points):
                    broll_source = broll_paths[idx % len(broll_paths)]
                    broll_info = probe_video(broll_source)
                    desired_broll = min(args.insert_duration, broll_info["duration"], max(1.0, main_duration * 0.5))
                    if desired_broll > 0.2:
                        segment_ranges.append(("broll", broll_source, 0.0, desired_broll))

            for index, (_, source_path, start, duration) in enumerate(segment_ranges):
                normalized_path = os.path.join(temp_dir, f"segment_{index:02d}.mp4")
                _normalize_segment(source_path, normalized_path, start, duration, target_width, target_height, target_fps)
                normalized_segments.append(normalized_path)
                segment_durations.append(duration)

            if len(normalized_segments) == 1:
                shutil.copy2(normalized_segments[0], output_path)
            else:
                transition_duration = min(args.transition_duration, max(0.2, min(segment_durations) * 0.35))
                _xfade_join(normalized_segments, segment_durations, "crossfade", transition_duration, output_path)

            print(f"[abroll] Done. Output video: {output_path}")
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
