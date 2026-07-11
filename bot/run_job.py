"""
run_job.py
----------
Entry point invoked by .github/workflows/telegram-dispatch.yml.
Reads job parameters from environment variables (populated from the
repository_dispatch client_payload sent by the Cloudflare Worker), runs
the requested tool, and sends the resulting file(s) straight back to the
requesting Telegram chat.

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
    TARGET_LUFS    - loudnorm only, target integrated loudness in LUFS
    TARGET_TP      - loudnorm only, target true peak in dBTP
    TARGET_LRA     - loudnorm only, target loudness range in LU
    LOFI_AUDIO     - lofiloop only, Google Drive/direct URL/path to long audio
    LOFI_HOURS     - lofiloop only, target render duration in hours
    LOFI_CRF       - lofiloop only, H.264 CRF quality (default 18)
    LOFI_PRESET    - lofiloop only, x264 preset (default veryfast)
    LOFI_NOISE     - lofiloop only, invisible per-frame noise strength (default 1)
    RESOLUTION     - photostudio only, "2k"|"4k"|"8k"|"16k" upscale target
    EFFECT         - photostudio only, color effect preset (e.g. "dslr")

Options not present as env vars are read from the dispatch event payload
(GITHUB_EVENT_PATH) automatically - see _load_payload(). This lets new
options work without editing the workflow file (which this bot's GitHub
token has no permission to modify).
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


def _load_payload() -> dict:
    """Load the full repository_dispatch client_payload from GITHUB_EVENT_PATH."""
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    if not event_path or not os.path.isfile(event_path):
        return {}
    try:
        with open(event_path, encoding="utf-8") as fh:
            event = json.load(fh)
        return event.get("client_payload", {}) or {}
    except (OSError, json.JSONDecodeError):
        return {}


_PAYLOAD = _load_payload()
_PAYLOAD_OPTIONS = _PAYLOAD.get("options", {}) or {}


def _env(name: str, default: str | None = None, required: bool = False) -> str:
    val = os.environ.get(name)
    if not val:
        key = name.lower()
        val = _PAYLOAD_OPTIONS.get(key) or _PAYLOAD.get(key)
    if val is None or val == "":
        val = default
    if required and not val:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return str(val) if val is not None else val


def _run(cmd: list[str]) -> None:
    print(f"[run_job] $ {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {result.returncode}: {' '.join(cmd)}")


def _send_output_videos(chat_id: str, output_dir: str, caption_prefix: str) -> None:
    video_files = [f for f in sorted(os.listdir(output_dir)) if f.endswith(".mp4")]
    for f in video_files:
        send_video(chat_id, os.path.join(output_dir, f), caption=f"✅ {caption_prefix} done: {f}")


def _send_output_documents(chat_id: str, output_dir: str, suffixes: tuple[str, ...], caption_prefix: str) -> None:
    for f in sorted(os.listdir(output_dir)):
        if f.endswith(suffixes):
            send_document(chat_id, os.path.join(output_dir, f), caption=f"📄 {caption_prefix}: {f}")


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


def run_loudnorm(chat_id: str) -> None:
    source_type = _env("SOURCE_TYPE", required=True)
    source_value = _env("SOURCE_VALUE", required=True)
    target_lufs = _env("TARGET_LUFS", "-14")
    target_tp = _env("TARGET_TP", "-1.5")
    target_lra = _env("TARGET_LRA", "11")
    output_dir = "./job_output"

    cmd = [
        sys.executable, "loudnorm/main.py", "--output-dir", output_dir,
        "--target-lufs", target_lufs, "--target-tp", target_tp, "--target-lra", target_lra,
    ]
    cmd += ["--url", source_value] if source_type == "url" else ["--input", source_value]
    _run(cmd)

    _send_output_videos(chat_id, output_dir, f"LoudNorm ({target_lufs} LUFS)")


def run_photostudio(chat_id: str) -> None:
    source_type = _env("SOURCE_TYPE", required=True)
    source_value = _env("SOURCE_VALUE", required=True)
    resolution = _env("RESOLUTION", "")
    effect = _env("EFFECT", "")
    output_dir = "./job_output"

    cmd = [sys.executable, "photostudio/main.py", "--output-dir", output_dir]
    cmd += ["--url", source_value] if source_type == "url" else ["--input", source_value]
    if resolution:
        cmd += ["--resolution", resolution]
    if effect:
        cmd += ["--effect", effect]
    _run(cmd)

    # Send results as documents so Telegram doesn't recompress the pixels.
    # The raw download is exactly "source_<8 hex>.<ext>"; anything else with
    # an image extension is a processed output (e.g. source_ab12cd34_dslr_4k.png).
    import re
    is_raw_source = re.compile(r"^source_[0-9a-f]{8}\.[A-Za-z0-9]+$")
    label = resolution.upper() if resolution else effect
    for f in sorted(os.listdir(output_dir)):
        if f.endswith((".png", ".jpg", ".jpeg")) and not is_raw_source.match(f):
            send_document(chat_id, os.path.join(output_dir, f),
                          caption=f"🖼️ PhotoStudio ({label}) done: {f}")


def run_autochapters(chat_id: str) -> None:
    source_type = _env("SOURCE_TYPE", required=True)
    source_value = _env("SOURCE_VALUE", required=True)
    output_dir = "./job_output"

    cmd = [sys.executable, "autochapters/main.py", "--output-dir", output_dir]
    cmd += ["--url", source_value] if source_type == "url" else ["--input", source_value]
    _run(cmd)

    _send_output_documents(chat_id, output_dir, ("_chapters.txt", ".ffmetadata"), "AutoChapters")
    _send_output_videos(chat_id, output_dir, "AutoChapters")


def run_lofiloop(chat_id: str) -> None:
    """
    Render a seamless, monetization-safe lofi video by looping a short clip
    over a long audio track, then deliver it:
      1. If <= 2GB and Pyrogram is available -> send straight into the chat via
         MTProto (raises the 20/50MB Bot API cap to 2GB using the app API id/hash).
      2. Otherwise -> upload to a free, key-less host and return the direct link.
    """
    source_type = _env("SOURCE_TYPE", required=True)  # short loop video source
    source_value = _env("SOURCE_VALUE", required=True)
    audio_source = _env("LOFI_AUDIO", required=True)
    hours = _env("LOFI_HOURS", "2")
    crf = _env("LOFI_CRF", "18")
    preset = _env("LOFI_PRESET", "veryfast")
    noise = _env("LOFI_NOISE", "1")
    output_dir = "./job_output"
    os.makedirs(output_dir, exist_ok=True)

    send_message(chat_id, f"🎧 LofiLoop: rendering a {hours}h seamless, monetization-safe video. "
                          f"This can take a while for long durations — I'll ping you when it's ready.")

    cmd = [
        sys.executable, "lofiloop/main.py",
        "--video", source_value,
        "--audio", audio_source,
        "--hours", hours,
        "--crf", crf,
        "--preset", preset,
        "--noise", noise,
        "--output-dir", output_dir,
    ]
    _run(cmd)

    # Read the manifest the CLI wrote (contains the hosted download link, if any).
    manifest = {}
    manifest_path = os.path.join(output_dir, "lofi_manifest.json")
    if os.path.isfile(manifest_path):
        with open(manifest_path) as f:
            manifest = json.load(f)

    rendered = manifest.get("path")
    if not rendered or not os.path.isfile(rendered):
        mp4s = [f for f in sorted(os.listdir(output_dir)) if f.endswith(".mp4")]
        rendered = os.path.join(output_dir, mp4s[0]) if mp4s else None
    if not rendered or not os.path.isfile(rendered):
        raise RuntimeError("LofiLoop render produced no output file.")

    size_mb = os.path.getsize(rendered) / 1024 / 1024
    hosted_url = manifest.get("download_url")
    signature = manifest.get("signature", "")
    caption = (f"✅ LofiLoop {hours}h ready!\n"
               f"📦 {size_mb:.0f} MB • CRF {crf} • unique fingerprint 🔒\n"
               f"🆔 {signature[:16]}")

    # --- Delivery path 1: direct MTProto send (up to 2GB) ------------------
    delivered = False
    if size_mb <= 2048:
        try:
            from bot.mtproto_transfer import send_large_file, mtproto_available
            if mtproto_available():
                send_message(chat_id, f"📤 Uploading {size_mb:.0f} MB straight to this chat (up to 2GB supported)...")
                delivered = send_large_file(chat_id, rendered, caption=caption, as_video=True)
        except Exception as e:
            print(f"[run_job] MTProto delivery error: {e}")

    # --- Delivery path 2: hosted download link -----------------------------
    if not delivered:
        if hosted_url:
            send_message(chat_id, f"{caption}\n\n⬇️ Direct download ({manifest.get('upload_host','host')}):\n{hosted_url}")
        else:
            # Last resort: try the plain Bot API (works only if <50MB).
            send_video(chat_id, rendered, caption=caption)


TOOL_RUNNERS = {
    "lofiloop": run_lofiloop,
    "aspectshift": run_aspectshift,
    "clipharvest": run_clipharvest,
    "watermarkwipe": run_watermarkwipe,
    "introoutro": run_introoutro,
    "abroll": run_abroll,
    "stitcher": run_stitcher,
    "audioduck": run_audioduck,
    "loudnorm": run_loudnorm,
    "autochapters": run_autochapters,
    "photostudio": run_photostudio,
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
