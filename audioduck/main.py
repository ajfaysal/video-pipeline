"""
AudioDuck CLI - mixes a separate voiceover into a video and ducks the
background music automatically with ffmpeg sidechain compression.

Usage:
    python audioduck/main.py --video video.mp4 --voiceover narration.mp3 --output-dir ./output
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import traceback

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from aspectshift.downloader import (
    DownloadError,
    InvalidAudioError,
    InvalidVideoError,
    MissingDependencyError,
    probe_audio,
    probe_video,
    resolve_audio_input,
    resolve_input,
)


def _looks_like_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def _resolve_video(value: str, output_dir: str) -> str:
    return resolve_input(url=value, output_dir=output_dir) if _looks_like_url(value) else resolve_input(input_path=value, output_dir=output_dir)


def _resolve_audio(value: str, output_dir: str) -> str:
    return resolve_audio_input(url=value, output_dir=output_dir) if _looks_like_url(value) else resolve_audio_input(input_path=value, output_dir=output_dir)


def _run(cmd: list[str]) -> None:
    print(f"[audioduck] $ {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {result.returncode}: {' '.join(cmd)}\n{result.stderr[-2000:]}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Duck background music under a voiceover track.")
    video_src = parser.add_mutually_exclusive_group(required=True)
    video_src.add_argument("--video", help="Path to a local video file with background music.")
    video_src.add_argument("--video-url", help="URL of the video file with background music.")

    voice_src = parser.add_mutually_exclusive_group(required=True)
    voice_src.add_argument("--voiceover", help="Path to a local narration/voiceover audio file.")
    voice_src.add_argument("--voiceover-url", help="URL of the narration/voiceover audio file.")

    parser.add_argument("--output-dir", default="./output", help="Directory to write outputs to.")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    os.makedirs(args.output_dir, exist_ok=True)

    try:
        video_arg = args.video if args.video else args.video_url
        voice_arg = args.voiceover if args.voiceover else args.voiceover_url
        assert video_arg is not None and voice_arg is not None

        video_path = _resolve_video(video_arg, args.output_dir)
        voiceover_path = _resolve_audio(voice_arg, args.output_dir)

        video_info = probe_video(video_path)
        if not video_info["has_audio"]:
            print("[error] The input video must contain audio to duck against the voiceover.", file=sys.stderr)
            return 5

        probe_audio(voiceover_path)

        base_name = os.path.splitext(os.path.basename(video_path))[0]
        output_path = os.path.join(args.output_dir, f"{base_name}_ducked.mp4")

        filter_complex = (
            "[0:a]aresample=48000,volume=1.0[music];"
            "[1:a]aresample=48000,volume=1.0[voice];"
            "[music][voice]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=250:makeup=1[ducked];"
            "[ducked][voice]amix=inputs=2:duration=longest:dropout_transition=2[aout]"
        )

        cmd = [
            "ffmpeg", "-y", "-i", video_path, "-i", voiceover_path,
            "-filter_complex", filter_complex,
            "-map", "0:v:0",
            "-map", "[aout]",
            "-c:v", "libx264", "-crf", "16", "-preset", "slow", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "48000", "-ac", "2",
            "-shortest",
            output_path,
        ]
        _run(cmd)

        print(f"[audioduck] Done. Output video: {output_path}")
        return 0

    except MissingDependencyError as e:
        print(f"[error] Missing dependency: {e}", file=sys.stderr)
        return 2
    except (DownloadError, InvalidVideoError, InvalidAudioError) as e:
        print(f"[error] {e}", file=sys.stderr)
        return 3
    except Exception as e:
        print(f"[error] Unexpected failure: {e}", file=sys.stderr)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
