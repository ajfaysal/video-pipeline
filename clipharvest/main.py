"""
ClipHarvest CLI - analyzes a long-form video and extracts the best
short-clip-worthy segments, auto-converted to 9:16 with thumbnails.

Usage:
    python main.py --input long_video.mp4 --num-clips 5 --min-duration 20 --max-duration 90 --captions --output-dir ./clips
    python main.py --url "https://youtube.com/..." --num-clips 8 --output-dir ./clips
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

from aspectshift.downloader import resolve_input, DownloadError, InvalidVideoError, MissingDependencyError
from aspectshift.converter import convert_to_vertical
from aspectshift.thumbnail import generate_thumbnail
from clipharvest.config import DEFAULT_MIN_DURATION, DEFAULT_MAX_DURATION, DEFAULT_NUM_CLIPS, WHISPER_MODEL_SIZE
from clipharvest.transcriber import transcribe
from clipharvest.scorer import rank_top_clips
from clipharvest.clipper import extract_audio, cut_clip
from clipharvest.captioner import build_word_ass, burn_captions


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract the best short-clip-worthy segments from a long video.")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--input", help="Path to a local long-form video file.")
    src.add_argument("--url", help="URL of a video to download and analyze.")
    parser.add_argument("--num-clips", type=int, default=DEFAULT_NUM_CLIPS)
    parser.add_argument("--min-duration", type=float, default=DEFAULT_MIN_DURATION)
    parser.add_argument("--max-duration", type=float, default=DEFAULT_MAX_DURATION)
    parser.add_argument("--captions", action="store_true", help="Burn in word-by-word karaoke captions.")
    parser.add_argument("--whisper-model", default=WHISPER_MODEL_SIZE, help="faster-whisper model size.")
    parser.add_argument("--output-dir", default="./clips")
    return parser


def _find_words_in_range(transcript: dict, start: float, end: float) -> list[dict]:
    words = []
    for seg in transcript["segments"]:
        for w in (seg["words"] or []):
            if w["end"] >= start and w["start"] <= end:
                words.append(w)
    return words


def main() -> int:
    args = build_arg_parser().parse_args()
    os.makedirs(args.output_dir, exist_ok=True)
    cache_dir = os.path.join(args.output_dir, "_transcript_cache")

    try:
        source_path = resolve_input(input_path=args.input, url=args.url, output_dir=args.output_dir)

        print("[main] Extracting audio for scoring...")
        audio_path = extract_audio(source_path, os.path.join(cache_dir, "audio.wav"))

        print("[main] Transcribing (this may take a while on first run)...")
        transcript = transcribe(source_path, cache_dir, model_size=args.whisper_model)

        print("[main] Scoring candidate segments...")
        top_clips = rank_top_clips(
            transcript, source_path, audio_path,
            min_duration=args.min_duration, max_duration=args.max_duration,
            num_clips=args.num_clips,
        )

        if not top_clips:
            print("[main] No candidate segments met the duration/quality criteria. "
                  "Try lowering --min-duration or checking the source audio.", file=sys.stderr)
            return 4

        report_entries = []
        for i, cand in enumerate(top_clips, start=1):
            clip_id = f"clip_{i:02d}"
            raw_path = os.path.join(args.output_dir, f"{clip_id}_raw.mp4")
            print(f"[main] Cutting {clip_id} [{cand.start:.1f}s - {cand.end:.1f}s] score={cand.score}")
            cut_clip(source_path, cand.start, cand.end, raw_path)

            captioned_path = raw_path
            if args.captions:
                words = _find_words_in_range(transcript, cand.start, cand.end)
                ass_path = os.path.join(args.output_dir, f"{clip_id}.ass")
                build_word_ass(words, cand.start, cand.end, ass_path)
                captioned_path = os.path.join(args.output_dir, f"{clip_id}_captioned.mp4")
                burn_captions(raw_path, ass_path, captioned_path)

            vertical_path = os.path.join(args.output_dir, f"{clip_id}_9x16.mp4")
            convert_to_vertical(captioned_path, vertical_path, mode="blur")

            thumbs = generate_thumbnail(vertical_path, args.output_dir, basename=f"{clip_id}_thumbnail")

            title_idea = cand.text.strip().split(".")[0][:70]
            report_entries.append({
                "clip_id": clip_id,
                "start": round(cand.start, 2),
                "end": round(cand.end, 2),
                "duration": round(cand.duration, 2),
                "score": cand.score,
                "score_breakdown": cand.breakdown,
                "hook_reason": cand.reason,
                "suggested_title": title_idea if title_idea else cand.text[:70],
                "transcript_text": cand.text,
                "output_video": vertical_path,
                "thumbnail_jpg": thumbs["jpg"],
            })

        report_json_path = os.path.join(args.output_dir, "report.json")
        with open(report_json_path, "w", encoding="utf-8") as f:
            json.dump(report_entries, f, ensure_ascii=False, indent=2)

        report_md_path = os.path.join(args.output_dir, "report.md")
        with open(report_md_path, "w", encoding="utf-8") as f:
            f.write("# ClipHarvest Report\n\n")
            for e in report_entries:
                f.write(f"## {e['clip_id']} - score {e['score']}/100\n")
                f.write(f"- **Time range:** {e['start']}s - {e['end']}s ({e['duration']}s)\n")
                f.write(f"- **Suggested title:** {e['suggested_title']}\n")
                f.write(f"- **Why it was picked:** {e['hook_reason']}\n")
                f.write(f"- **Output:** `{e['output_video']}`\n\n")

        print(f"[main] Done. {len(report_entries)} clips written to {args.output_dir}")
        print(f"[main] Report: {report_json_path}, {report_md_path}")
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
