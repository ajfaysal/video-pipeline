"""
qualityboost/main.py
--------------------
Fast FFmpeg-only video upscale + quality-fix pipeline.

Takes any video (local file or URL) and produces an upscaled, optionally
denoised / stabilized / sharpened version using pure FFmpeg filters —
no ML models, no GPU, no VPS. Typical turnaround: 30-60 seconds on a
GitHub Actions runner for a standard-length clip.

Pipeline stages (all optional except upscale):
  1. Download (yt-dlp for URLs)
  2. Denoise — hqdn3d (temporal + spatial noise reduction)
  3. Stabilize — vidstab two-pass (analyse → transform)
  4. Upscale — Lanczos (scale=w*N:h*N:flags=lanczos)
  5. Sharpen — unsharp mask (luma + chroma)
  6. Encode — libx264 at configurable CRF

Usage:
  python qualityboost/main.py --input video.mp4 --output-dir ./output
  python qualityboost/main.py --url "https://..." --scale-factor 2 --denoise --sharpen --output-dir ./output
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time


def _probe(path: str) -> dict:
    """Run ffprobe and return the first video stream info."""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_streams", "-select_streams", "v:0", path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}: {result.stderr.strip()}")
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    if not streams:
        raise RuntimeError(f"No video stream found in {path}")
    return streams[0]


def _download(url: str, work_dir: str) -> str:
    """Download a video from URL using yt-dlp."""
    out_template = os.path.join(work_dir, "source.%(ext)s")
    cmd = [
        "yt-dlp", "--no-playlist", "-f",
        "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "-o", out_template, url,
    ]
    print(f"[qualityboost] Downloading: {url}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp download failed for: {url}")

    # Find the downloaded file
    for f in os.listdir(work_dir):
        if f.startswith("source."):
            return os.path.join(work_dir, f)
    raise RuntimeError("Download completed but no output file found")


def _build_filter_chain(
    scale_factor: int,
    denoise: bool,
    sharpen: bool,
    width: int,
    height: int,
) -> str:
    """Build the FFmpeg video filter chain string."""
    filters: list[str] = []

    # Stage 1: Denoise (before upscale for best quality)
    if denoise:
        # hqdn3d: luma_spatial:chroma_spatial:luma_temporal:chroma_temporal
        filters.append("hqdn3d=4:3:6:4.5")

    # Stage 2: Upscale with Lanczos
    new_w = width * scale_factor
    new_h = height * scale_factor
    # Ensure even dimensions (required by most codecs)
    new_w = new_w + (new_w % 2)
    new_h = new_h + (new_h % 2)
    filters.append(f"scale={new_w}:{new_h}:flags=lanczos")

    # Stage 3: Sharpen (after upscale to restore edge definition)
    if sharpen:
        # unsharp: luma_x:luma_y:luma_amount:chroma_x:chroma_y:chroma_amount
        filters.append("unsharp=5:5:0.8:3:3:0.4")

    return ",".join(filters)


def _stabilize_pass(input_path: str, work_dir: str) -> str:
    """Run vidstab two-pass stabilization, return path to stabilized file."""
    transforms_file = os.path.join(work_dir, "transforms.trf")
    stabilized_path = os.path.join(work_dir, "stabilized.mp4")

    # Pass 1: Analyse
    print("[qualityboost] Stabilize pass 1/2: analysing motion...")
    cmd_analyse = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", f"vidstabdetect=stepsize=6:shakiness=8:accuracy=9:result={transforms_file}",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd_analyse)
    if result.returncode != 0:
        print("[qualityboost] Warning: vidstab analysis failed, skipping stabilization")
        return input_path

    # Pass 2: Transform
    print("[qualityboost] Stabilize pass 2/2: applying transforms...")
    cmd_transform = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", f"vidstabtransform=input={transforms_file}:zoom=1:smoothing=10",
        "-c:v", "libx264", "-crf", "14", "-preset", "fast",
        "-c:a", "copy", stabilized_path,
    ]
    result = subprocess.run(cmd_transform)
    if result.returncode != 0:
        print("[qualityboost] Warning: vidstab transform failed, skipping stabilization")
        return input_path

    return stabilized_path


def boost(
    input_path: str,
    output_dir: str,
    scale_factor: int = 2,
    denoise: bool = False,
    stabilize: bool = False,
    sharpen: bool = True,
    crf: int = 16,
) -> str:
    """Run the full QualityBoost pipeline. Returns the output file path."""

    os.makedirs(output_dir, exist_ok=True)
    work_dir = tempfile.mkdtemp(prefix="qualityboost_")

    try:
        current_input = input_path

        # Probe source video dimensions
        info = _probe(current_input)
        width = int(info.get("width", 1920))
        height = int(info.get("height", 1080))
        print(f"[qualityboost] Source: {width}x{height}")

        # Optional stabilization (must be done as a separate pass before filters)
        if stabilize:
            current_input = _stabilize_pass(current_input, work_dir)

        # Build the filter chain
        vf = _build_filter_chain(scale_factor, denoise, sharpen, width, height)
        new_w = (width * scale_factor) + ((width * scale_factor) % 2)
        new_h = (height * scale_factor) + ((height * scale_factor) % 2)

        # Generate output filename
        src_hash = hashlib.md5(os.path.basename(input_path).encode()).hexdigest()[:8]
        out_name = f"boosted_{src_hash}_{new_w}x{new_h}.mp4"
        out_path = os.path.join(output_dir, out_name)

        # Encode
        print(f"[qualityboost] Upscaling {width}x{height} -> {new_w}x{new_h} (Lanczos, CRF {crf})")
        start = time.time()

        cmd = [
            "ffmpeg", "-y", "-i", current_input,
            "-vf", vf,
            "-c:v", "libx264", "-crf", str(crf), "-preset", "slow",
            "-c:a", "aac", "-b:a", "320k",
            "-movflags", "+faststart",
            out_path,
        ]
        print(f"[qualityboost] $ {' '.join(cmd)}")
        result = subprocess.run(cmd)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg encode failed (exit {result.returncode})")

        elapsed = time.time() - start
        size_mb = os.path.getsize(out_path) / 1024 / 1024
        print(f"[qualityboost] Done in {elapsed:.1f}s -> {out_name} ({size_mb:.1f} MB)")

        # Write a small manifest
        manifest = {
            "source_width": width,
            "source_height": height,
            "output_width": new_w,
            "output_height": new_h,
            "scale_factor": scale_factor,
            "denoise": denoise,
            "stabilize": stabilize,
            "sharpen": sharpen,
            "crf": crf,
            "elapsed_seconds": round(elapsed, 1),
            "output_size_mb": round(size_mb, 1),
            "output_file": out_name,
        }
        manifest_path = os.path.join(output_dir, "qualityboost_manifest.json")
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)

        return out_path

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="QualityBoost — fast FFmpeg-only video upscale + quality fix"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--input", help="Local video file path")
    group.add_argument("--url", help="Video URL (downloaded via yt-dlp)")

    parser.add_argument("--output-dir", required=True, help="Output directory")
    parser.add_argument("--scale-factor", type=int, default=2, choices=[2, 3, 4],
                        help="Upscale multiplier (default: 2)")
    parser.add_argument("--denoise", action="store_true",
                        help="Apply hqdn3d denoiser before upscale")
    parser.add_argument("--stabilize", action="store_true",
                        help="Apply vidstab two-pass stabilization")
    parser.add_argument("--sharpen", action="store_true", default=True,
                        help="Apply unsharp mask after upscale (default: true)")
    parser.add_argument("--no-sharpen", action="store_true",
                        help="Disable sharpening")
    parser.add_argument("--crf", type=int, default=16,
                        help="H.264 CRF quality, 0-51 (default: 16)")

    args = parser.parse_args()

    sharpen = args.sharpen and not args.no_sharpen

    if args.url:
        work_dir = tempfile.mkdtemp(prefix="qualityboost_dl_")
        try:
            input_path = _download(args.url, work_dir)
        except Exception as e:
            print(f"[qualityboost] Download failed: {e}", file=sys.stderr)
            shutil.rmtree(work_dir, ignore_errors=True)
            return 1
    else:
        input_path = args.input
        work_dir = None

    if not os.path.isfile(input_path):
        print(f"[qualityboost] Input file not found: {input_path}", file=sys.stderr)
        return 1

    try:
        out = boost(
            input_path=input_path,
            output_dir=args.output_dir,
            scale_factor=args.scale_factor,
            denoise=args.denoise,
            stabilize=args.stabilize,
            sharpen=sharpen,
            crf=args.crf,
        )
        print(f"[qualityboost] Output: {out}")
        return 0
    except Exception as e:
        print(f"[qualityboost] Error: {e}", file=sys.stderr)
        return 1
    finally:
        if work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
