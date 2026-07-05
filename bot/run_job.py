"""
run_job.py
----------
Entry point invoked by .github/workflows/telegram-dispatch.yml.
Reads job parameters from environment variables (populated from the
repository_dispatch client_payload sent by the Cloudflare Worker), runs
the requested tool (AspectShift / ClipHarvest / WatermarkWipe), and sends
the resulting file(s) straight back to the requesting Telegram chat.

Expected environment variables:
    CHAT_ID        - Telegram chat id to reply to (required)
    TOOL           - "aspectshift" | "clipharvest" | "watermarkwipe" (required)
    SOURCE_TYPE    - "url" | "path"  (required)
    SOURCE_VALUE   - the URL or repo-relative path (required)
    MODE           - aspectshift: "blur"|"crop"; watermarkwipe: "crop"|"inpaint"
    NUM_CLIPS      - clipharvest only, default "5"
    MIN_DURATION   - clipharvest only, default "20"
    MAX_DURATION   - clipharvest only, default "90"
    CAPTIONS       - clipharvest only, "true"|"false"
    REGION         - watermarkwipe only, manual "x,y,w,h" (omit for --auto-detect)
    INTRO_TEXT     - introoutro only, intro text string
    OUTRO_TEXT     - introoutro only, outro text string
    INTRO_DURATION - introoutro only, intro duration in seconds
    OUTRO_DURATION - introoutro only, outro duration in seconds
    BROLL_SOURCES_JSON - abroll only, JSON array of B-roll source URLs/paths
    STITCH_CLIPS_JSON  - stitcher only, JSON array of clip URLs/paths
    TRANSITION     - stitcher only, transition type
    TRANSITION_DURATION - stitcher only, xfade duration in seconds
    VOICEOVER_SOURCE - audioduck only, audio URL/path for narration
"""

from __future__ import annotations

import os
import subprocess
import sys
import traceback
import json

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from bot.telegram_notify import send_message, send_video, send_photo, send_document


def _env(name: str, default: str | None = None, required: bool = False) -> str:
    val = os.environ.get(name, default)
    if required and not val:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return val


def _run(cmd: list[str]) -> None:
    print(f"[run_job] $ {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {result.returncode}: {' '.join(cmd)}")


def _send_output_videos(chat_id: str, output_dir: str, caption_prefix: str) -> None:
    video_files = [f for f in sorted(os.listdir(output_dir)) if f.endswith(".mp4")]
    for f in video_files:
        send_video(chat_id, os.path.join(output_dir, f), caption=f"✅ {caption_prefix} done: {f}")


def run_aspectshift(chat_id: str) -> None:
    source_type = _env("SOURCE_TYPE", required=True)
    source_value = _env("SOURCE_VALUE", required=True)
    mode = _env("MODE", "blur")
    output_dir = "./job_output"

    cmd = [sys.executable, "aspectshift/main.py", "--mode", mode, "--output-dir", output_dir]
    cmd += ["--url", source_value] if source_type == "url" else ["--input", source_value]
    _run(cmd)

    video_files = [f for f in os.listdir(output_dir) if f.endswith(".mp4")]
    thumb_files = [f for f in os.listdir(output_dir) if f.endswith(".jpg")]

    for f in video_files:
        send_video(chat_id, os.path.join(output_dir, f), caption=f"✅ AspectShift ({mode}) done: {f}")
    for f in thumb_files:
        send_photo(chat_id, os.path.join(output_dir, f), caption="Thumbnail")


def run_clipharvest(chat_id: str) -> None:
    source_type = _env("SOURCE_TYPE", required=True)
    source_value = _env("SOURCE_VALUE", required=True)
    num_clips = _env("NUM_CLIPS", "5")
    min_duration = _env("MIN_DURATION", "20")
    max_duration = _env("MAX_DURATION", "90")
    captions = _env("CAPTIONS", "true").lower() == "true"
    output_dir = "./job_output"

    cmd = [
        sys.executable, "clipharvest/main.py",
        "--num-clips", num_clips, "--min-duration", min_duration, "--max-duration", max_duration,
        "--output-dir", output_dir,
    ]
    cmd += ["--url", source_value] if source_type == "url" else ["--input", source_value]
    if captions:
        cmd.append("--captions")
    _run(cmd)

    for f in sorted(os.listdir(output_dir)):
        path = os.path.join(output_dir, f)
        if f.endswith("_9x16.mp4"):
            send_video(chat_id, path, caption=f"🎬 {f}")
        elif f.endswith("_thumbnail.jpg"):
            send_photo(chat_id, path, caption=f.replace("_thumbnail.jpg", ""))

    report_md = os.path.join(output_dir, "report.md")
    if os.path.isfile(report_md):
        send_document(chat_id, report_md, caption="📊 Full scoring report")


def run_watermarkwipe(chat_id: str) -> None:
    source_type = _env("SOURCE_TYPE", required=True)
    source_value = _env("SOURCE_VALUE", required=True)
    mode = _env("MODE", "inpaint")
    region = _env("REGION", "")
    color_grade_style = _env("COLOR_GRADE", "")  # e.g. "cinematic" | "vibrant" | "warm" | "cool"
    background_blur = _env("BACKGROUND_BLUR", "false").lower() == "true"
    output_dir = "./job_output"

    cmd = [sys.executable, "watermarkwipe/main.py", "--mode", mode, "--quality-check", "--output-dir", output_dir]
    cmd += ["--url", source_value] if source_type == "url" else ["--input", source_value]
    cmd += ["--region", region] if region else ["--auto-detect"]
    if color_grade_style:
        cmd += ["--color-grade", color_grade_style]
    if background_blur:
        cmd.append("--background-blur")
    _run(cmd)

    qc_path = os.path.join(output_dir, "quality_check.jpg")
    if os.path.isfile(qc_path):
        send_photo(chat_id, qc_path, caption="🔍 Before/after preview")

    # Prefer the most-processed variant (bgblur > graded > clean) so we don't
    # spam the chat with every intermediate stage.
    all_mp4 = [f for f in os.listdir(output_dir) if f.endswith(".mp4")]
    final_file = (
        next((f for f in all_mp4 if f.endswith("_bgblur.mp4")), None)
        or next((f for f in all_mp4 if f.endswith("_graded.mp4")), None)
        or next((f for f in all_mp4 if "_clean_" in f), None)
    )
    if final_file:
        send_video(chat_id, os.path.join(output_dir, final_file), caption=f"✅ WatermarkWipe ({mode}) done: {final_file}")


def run_introoutro(chat_id: str) -> None:
    source_type = _env("SOURCE_TYPE", required=True)
    source_value = _env("SOURCE_VALUE", required=True)
    intro_text = _env("INTRO_TEXT", "Your Channel Name")
    outro_text = _env("OUTRO_TEXT", "Subscribe for more")
    intro_duration = _env("INTRO_DURATION", "3.5")
    outro_duration = _env("OUTRO_DURATION", "3.5")
    output_dir = "./job_output"

    cmd = [sys.executable, "introoutro/main.py", "--output-dir", output_dir,
           "--intro-text", intro_text, "--outro-text", outro_text,
           "--intro-duration", intro_duration, "--outro-duration", outro_duration]
    cmd += ["--url", source_value] if source_type == "url" else ["--input", source_value]
    _run(cmd)

    _send_output_videos(chat_id, output_dir, "IntroOutro")


def run_abroll(chat_id: str) -> None:
    source_type = _env("SOURCE_TYPE", required=True)
    source_value = _env("SOURCE_VALUE", required=True)
    broll_sources = json.loads(_env("BROLL_SOURCES_JSON", "[]") or "[]")
    output_dir = "./job_output"

    if not broll_sources:
        raise RuntimeError("ABRoll requires at least one B-roll source.")

    cmd = [sys.executable, "abroll/main.py", "--output-dir", output_dir]
    cmd += ["--main", source_value] if source_type == "path" else ["--main", source_value]
    cmd += ["--broll", *broll_sources]
    _run(cmd)

    _send_output_videos(chat_id, output_dir, "ABRoll")


def run_stitcher(chat_id: str) -> None:
    source_value = _env("SOURCE_VALUE", required=True)
    stitch_clips = json.loads(_env("STITCH_CLIPS_JSON", "[]") or "[]")
    transition = _env("TRANSITION", "crossfade")
    transition_duration = _env("TRANSITION_DURATION", "0.8")
    output_dir = "./job_output"

    clips = stitch_clips or [source_value]
    if source_value and source_value not in clips:
        clips = [source_value, *clips]
    if len(clips) < 2:
        raise RuntimeError("Stitcher requires at least two clips.")

    cmd = [sys.executable, "stitcher/main.py", "--output-dir", output_dir, "--transition", transition,
           "--transition-duration", transition_duration, "--clips", *clips]
    _run(cmd)

    _send_output_videos(chat_id, output_dir, "Stitcher")


def run_audioduck(chat_id: str) -> None:
    source_type = _env("SOURCE_TYPE", required=True)
    source_value = _env("SOURCE_VALUE", required=True)
    voiceover_source = _env("VOICEOVER_SOURCE", required=True)
    output_dir = "./job_output"

    cmd = [sys.executable, "audioduck/main.py", "--output-dir", output_dir]
    cmd += ["--video-url", source_value] if source_type == "url" else ["--video", source_value]
    cmd += ["--voiceover-url", voiceover_source] if voiceover_source.startswith(("http://", "https://")) else ["--voiceover", voiceover_source]
    _run(cmd)

    _send_output_videos(chat_id, output_dir, "AudioDuck")


TOOL_RUNNERS = {
    "aspectshift": run_aspectshift,
    "clipharvest": run_clipharvest,
    "watermarkwipe": run_watermarkwipe,
    "introoutro": run_introoutro,
    "abroll": run_abroll,
    "stitcher": run_stitcher,
    "audioduck": run_audioduck,
}


def main() -> int:
    chat_id = _env("CHAT_ID", required=True)
    tool = _env("TOOL", required=True)

    if tool not in TOOL_RUNNERS:
        send_message(chat_id, f"❌ Unknown tool '{tool}'. Must be one of: {', '.join(TOOL_RUNNERS)}.")
        return 1

    send_message(chat_id, f"⏳ Job started: {tool}. This can take a few minutes depending on video length...")

    try:
        TOOL_RUNNERS[tool](chat_id)
        send_message(chat_id, "🎉 All done! Your file(s) are above.")
        return 0
    except Exception as e:
        traceback.print_exc()
        send_message(chat_id, f"❌ Job failed: {e}\n\nCheck the GitHub Actions run log for full details.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
