"""
captioner.py
------------
Generates word-by-word ("karaoke style") burned-in captions for a clip,
using the word-level timestamps from the Whisper transcript.

Approach: build an .ass subtitle file where each word appears as its own
short-lived event (highlighted styling), timed relative to the clip's own
start, then burn it into the video with ffmpeg's `ass` filter. This is far
more robust than chaining hundreds of drawtext filters.
"""

from __future__ import annotations

import os
import subprocess

from aspectshift.downloader import _require_binary

_ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Word,Arial Black,90,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,6,0,2,60,60,260,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _fmt_ts(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def build_word_ass(words: list[dict], clip_start: float, clip_end: float, ass_path: str) -> str:
    """
    `words` is a list of {"start","end","word"} in the *source video's*
    absolute timeline. Timestamps are re-based to be relative to clip_start.
    Each word is shown individually, slightly overlapping its neighbor so
    there's no visible flicker gap between words.
    """
    os.makedirs(os.path.dirname(os.path.abspath(ass_path)) or ".", exist_ok=True)
    lines = [_ASS_HEADER]

    for w in words:
        w_start = w["start"] - clip_start
        w_end = w["end"] - clip_start
        if w_end <= 0 or w_start >= (clip_end - clip_start):
            continue
        w_start = max(w_start, 0.0)
        w_end = min(w_end, clip_end - clip_start)
        # Small overlap so consecutive words don't visibly flash to blank.
        display_end = min(w_end + 0.08, clip_end - clip_start)

        text = w["word"].strip().upper().replace("\n", " ")
        if not text:
            continue

        lines.append(
            f"Dialogue: 0,{_fmt_ts(w_start)},{_fmt_ts(display_end)},Word,,0,0,0,,{{\\an2}}{text}"
        )

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return ass_path


def burn_captions(clip_video_path: str, ass_path: str, output_path: str) -> str:
    _require_binary("ffmpeg")
    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)

    # ffmpeg's ass filter needs an escaped path (colons on Windows-style paths
    # in particular); on POSIX this is normally safe as-is, but escape defensively.
    escaped_ass = ass_path.replace("\\", "\\\\").replace(":", "\\:")

    cmd = [
        "ffmpeg", "-y", "-i", clip_video_path,
        "-vf", f"ass={escaped_ass}",
        "-c:v", "libx264", "-crf", "16", "-preset", "slow",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        # Retry with re-encoded audio if stream copy failed.
        fallback_cmd = cmd[:-3] + ["-c:a", "aac", "-b:a", "320k", output_path]
        fallback = subprocess.run(fallback_cmd, capture_output=True, text=True)
        if fallback.returncode != 0:
            raise RuntimeError(
                f"Caption burn-in failed.\ncopy stderr: {result.stderr[-1000:]}\n"
                f"aac stderr: {fallback.stderr[-1000:]}"
            )
    return output_path
