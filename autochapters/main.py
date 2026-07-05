"""
AutoChapters CLI - generates YouTube-style chapter timestamps from a long
video by combining transcript pacing/topic-shift heuristics with silence-gap
detection, then embeds the chapter metadata into the output file.

Usage:
    python autochapters/main.py --input video.mp4 --output-dir ./output
    python autochapters/main.py --url "https://youtube.com/..." --output-dir ./output
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import traceback
from collections import Counter
from pathlib import Path

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from aspectshift.downloader import DownloadError, InvalidVideoError, MissingDependencyError, probe_video, resolve_input
from clipharvest.config import WHISPER_MODEL_SIZE
from clipharvest.transcriber import transcribe


STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from", "has", "have", "he",
    "her", "his", "i", "if", "in", "into", "is", "it", "its", "me", "my", "of", "on", "or",
    "our", "out", "she", "so", "than", "that", "the", "their", "them", "then", "there", "these",
    "they", "this", "to", "up", "was", "we", "were", "what", "when", "which", "who", "will", "with",
    "you", "your",
}


def _looks_like_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def _resolve_source(value: str, output_dir: str) -> str:
    return resolve_input(url=value, output_dir=output_dir) if _looks_like_url(value) else resolve_input(input_path=value, output_dir=output_dir)


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    print(f"[autochapters] $ {' '.join(cmd)}")
    return subprocess.run(cmd, capture_output=True, text=True)


def _format_timestamp(seconds: float) -> str:
    total_ms = int(round(max(0.0, seconds) * 1000))
    hours, remainder = divmod(total_ms, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    secs = remainder // 1000
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _clean_words(text: str) -> list[str]:
    return [word.lower() for word in re.findall(r"[A-Za-z0-9']+", text) if word.lower() not in STOPWORDS]


def _silence_boundaries(path: str, silence_db: str = "-35dB", silence_min_duration: float = 0.7) -> list[float]:
    result = _run([
        "ffmpeg", "-hide_banner", "-i", path,
        "-af", f"silencedetect=noise={silence_db}:d={silence_min_duration}",
        "-f", "null", "-",
    ])
    text = result.stderr + "\n" + result.stdout
    if result.returncode not in (0, 255):
        raise RuntimeError(f"ffmpeg silencedetect failed:\n{text[-2000:]}")
    return [float(match.group(1)) for match in re.finditer(r"silence_end:\s*([0-9.]+)", text)]


def _topic_shift_boundaries(transcript: dict) -> list[float]:
    segments = transcript.get("segments", [])
    if len(segments) < 2:
        return []

    boundaries: list[float] = []
    previous_tokens: set[str] | None = None
    previous_pace: float | None = None

    for segment in segments:
        text = segment.get("text", "")
        tokens = set(_clean_words(text))
        duration = max(0.1, float(segment.get("end", 0.0)) - float(segment.get("start", 0.0)))
        pace = len(_clean_words(text)) / duration

        if previous_tokens is not None:
            overlap = len(tokens & previous_tokens) / max(1, len(tokens | previous_tokens))
            pace_change = abs(pace - (previous_pace or pace)) / max(1.0, previous_pace or pace)
            if overlap < 0.22 and pace_change > 0.55:
                boundaries.append(float(segment.get("start", 0.0)))

        previous_tokens = tokens or previous_tokens
        previous_pace = pace

    return boundaries


def _candidate_boundaries(transcript: dict, silence_points: list[float]) -> list[float]:
    candidates = list(silence_points)
    candidates.extend(_topic_shift_boundaries(transcript))
    candidates.extend(float(seg.get("start", 0.0)) for seg in transcript.get("segments", [])[1:])
    return candidates


def _select_boundaries(candidates: list[float], duration: float, min_gap: float = 45.0, max_chapters: int = 8) -> list[float]:
    selected: list[float] = []
    last = 0.0
    for point in sorted({round(max(0.0, c), 2) for c in candidates}):
        if point < 20.0 or point > duration - 15.0:
            continue
        if point - last < min_gap:
            continue
        selected.append(point)
        last = point
        if len(selected) >= max_chapters - 1:
            break
    return selected


def _chapter_title(transcript: dict, start: float, end: float, index: int) -> str:
    if index == 0:
        return "Intro"

    window_tokens: list[str] = []
    for segment in transcript.get("segments", []):
        segment_start = float(segment.get("start", 0.0))
        if segment_start < start:
            continue
        if segment_start > end:
            break
        window_tokens.extend(_clean_words(segment.get("text", "")))

    if not window_tokens:
        return f"Part {index + 1}"

    counts = Counter(window_tokens)
    ordered = [word for word, _ in counts.most_common() if len(word) > 2]
    if not ordered:
        ordered = [word for word in window_tokens if len(word) > 2]

    title_words = ordered[:4] if ordered else [f"Part {index + 1}"]
    title = " ".join(word.capitalize() for word in title_words)
    return title[:40].rstrip()


def _write_chapter_files(chapters: list[tuple[float, str]], duration: float, output_dir: str, base_name: str) -> tuple[str, str]:
    chapters_txt = os.path.join(output_dir, f"{base_name}_chapters.txt")
    ffmetadata_path = os.path.join(output_dir, f"{base_name}_chapters.ffmetadata")

    with open(chapters_txt, "w", encoding="utf-8") as handle:
        for timestamp, title in chapters:
            handle.write(f"{_format_timestamp(timestamp)} {title}\n")

    with open(ffmetadata_path, "w", encoding="utf-8") as handle:
        handle.write(";FFMETADATA1\n")
        for idx, (timestamp, title) in enumerate(chapters):
            start_ms = int(round(timestamp * 1000))
            end_ms = int(round((chapters[idx + 1][0] if idx + 1 < len(chapters) else duration) * 1000))
            handle.write("[CHAPTER]\n")
            handle.write("TIMEBASE=1/1000\n")
            handle.write(f"START={start_ms}\n")
            handle.write(f"END={max(start_ms + 1, end_ms)}\n")
            handle.write(f"title={title}\n")

    return chapters_txt, ffmetadata_path


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate YouTube chapter timestamps from a long video transcript.")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--input", help="Path to a local video file.")
    src.add_argument("--url", help="URL of a video to download and chapterize.")
    parser.add_argument("--output-dir", default="./output", help="Directory to write outputs to.")
    parser.add_argument("--whisper-model", default=WHISPER_MODEL_SIZE, help="faster-whisper model size.")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    os.makedirs(args.output_dir, exist_ok=True)

    try:
        with tempfile.TemporaryDirectory(prefix="autochapters_") as temp_dir:
            source_path = _resolve_source(args.input or args.url, temp_dir)
            info = probe_video(source_path)
            if not info["has_audio"]:
                print("[error] The input video has no audio stream, so AutoChapters cannot build a transcript.", file=sys.stderr)
                return 5

            cache_dir = os.path.join(args.output_dir, "_transcript_cache")
            transcript = transcribe(source_path, cache_dir, model_size=args.whisper_model)

            duration = float(transcript.get("duration") or info["duration"] or 0.0)
            silence_points = _silence_boundaries(source_path)
            candidates = _candidate_boundaries(transcript, silence_points)
            boundaries = _select_boundaries(candidates, duration)

            chapters: list[tuple[float, str]] = [(0.0, "Intro")]
            chapter_starts = [0.0, *boundaries]
            for idx, start in enumerate(chapter_starts[1:], start=1):
                end = chapter_starts[idx + 1] if idx + 1 < len(chapter_starts) else duration
                chapters.append((start, _chapter_title(transcript, start, end, idx)))

            base_name = os.path.splitext(os.path.basename(source_path))[0]
            output_path = os.path.join(args.output_dir, f"{base_name}_chapters.mp4")
            chapters_txt, ffmetadata_path = _write_chapter_files(chapters, duration, args.output_dir, base_name)

            remux = _run([
                "ffmpeg", "-y", "-i", source_path, "-i", ffmetadata_path,
                "-map", "0",
                "-map_metadata", "1",
                "-codec", "copy",
                output_path,
            ])
            if remux.returncode != 0:
                raise RuntimeError(f"ffmpeg chapter remux failed:\n{remux.stderr[-2000:]}")

            print(f"[autochapters] Chapters text: {chapters_txt}")
            print(f"[autochapters] Chapters metadata: {ffmetadata_path}")
            print(f"[autochapters] Done. Output video: {output_path}")
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