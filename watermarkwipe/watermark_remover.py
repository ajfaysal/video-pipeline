"""
watermark_remover.py
---------------------
Two removal strategies:

  crop_remove()    - for corner/edge watermarks: crop the offending strip
                      out entirely and rescale back to the original
                      resolution. Fast, ffmpeg-only, perfect quality
                      everywhere except the removed strip is gone (not
                      reconstructed) - appropriate when the watermark sits
                      in a region that can be sacrificed (e.g. a thin edge
                      bug) since scale-back keeps the rest sharp.

  inpaint_remove()  - for center/moving/semi-transparent watermarks:
                      frame-by-frame OpenCV inpainting (Telea or
                      Navier-Stokes) with temporal smoothing against the
                      previous inpainted frame (blended only within the
                      masked region) to reduce flicker. Processed in
                      batches with tqdm progress and re-muxed with the
                      original audio via ffmpeg.

  quality_check()   - exports a single before/after side-by-side JPEG so
                      results can be sanity-checked before processing the
                      full video.

NOTE ON QUALITY: inpaint mode reconstructs pixels by extrapolating from
surrounding content. It works very well on simple/static backgrounds (sky,
walls, solid colors, blurred backdrops) and may show slight softness or
smearing on complex/detailed regions (faces, busy textures, fine text)
that happen to sit under the watermark. This is an inherent limitation of
inpainting-based reconstruction, not a bug - always run --quality-check
first on footage with a complex watermark region.
"""

from __future__ import annotations

import os
import subprocess

import cv2
import numpy as np
from tqdm import tqdm

from aspectshift.downloader import _require_binary, probe_video, InvalidVideoError

Region = tuple[int, int, int, int]  # x, y, w, h


def crop_remove(input_path: str, output_path: str, region: Region) -> str:
    """
    Removes a corner/edge watermark by cropping the strip containing it out
    of the frame entirely, then scaling back up to the original resolution.
    """
    _require_binary("ffmpeg")
    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    info = probe_video(input_path)
    src_w, src_h = info["width"], info["height"]
    x, y, w, h = region

    # Decide which single strip (top/bottom/left/right) fully covers the
    # watermark with the least amount of frame sacrificed, then crop it away.
    dist_top, dist_bottom = y + h, src_h - y
    dist_left, dist_right = x + w, src_w - x
    options = {
        "top": dist_top,       # crop away rows [0, y+h)
        "bottom": src_h - y,   # crop away rows [y, src_h)
        "left": dist_left,     # crop away cols [0, x+w)
        "right": src_w - x,    # crop away cols [x, src_w)
    }
    strip = min(options, key=options.get)

    if strip == "top":
        crop_filter = f"crop={src_w}:{src_h - (y + h)}:0:{y + h}"
    elif strip == "bottom":
        crop_filter = f"crop={src_w}:{y}:0:0"
    elif strip == "left":
        crop_filter = f"crop={src_w - (x + w)}:{src_h}:{x + w}:0"
    else:  # right
        crop_filter = f"crop={x}:{src_h}:0:0"

    filter_chain = f"{crop_filter},scale={src_w}:{src_h}:flags=lanczos"

    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", filter_chain,
        "-c:v", "libx264", "-crf", "16", "-preset", "slow",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        fallback_cmd = cmd[:-3] + ["-c:a", "aac", "-b:a", "320k", output_path]
        fallback = subprocess.run(fallback_cmd, capture_output=True, text=True)
        if fallback.returncode != 0:
            raise RuntimeError(
                f"Crop removal failed.\ncopy stderr: {result.stderr[-1000:]}\n"
                f"aac stderr: {fallback.stderr[-1000:]}"
            )
    return output_path


def _inpaint_frame(frame: np.ndarray, mask: np.ndarray, method: str) -> np.ndarray:
    flag = cv2.INPAINT_TELEA if method == "telea" else cv2.INPAINT_NS
    return cv2.inpaint(frame, mask, inpaintRadius=5, flags=flag)


def inpaint_remove(input_path: str, output_path: str, region: Region,
                    method: str = "telea", batch_size: int = 64,
                    temporal_smoothing: float = 0.35) -> str:
    """
    Frame-by-frame inpainting removal with temporal smoothing to reduce
    flicker, processed in batches to bound memory use on long videos.
    """
    _require_binary("ffmpeg")
    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)

    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        raise InvalidVideoError(f"OpenCV could not open '{input_path}' for inpainting.")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    x, y, w, h = region
    mask = np.zeros((height, width), dtype=np.uint8)
    mask[y:y + h, x:x + w] = 255

    silent_video_path = output_path + ".silent.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(silent_video_path, fourcc, fps, (width, height))
    if not writer.isOpened():
        cap.release()
        raise RuntimeError("Could not open OpenCV VideoWriter for inpainted output.")

    prev_inpainted_region = None

    try:
        with tqdm(total=total_frames, desc="Inpainting frames", unit="frame") as pbar:
            batch: list[np.ndarray] = []
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                batch.append(frame)

                if len(batch) >= batch_size:
                    prev_inpainted_region = _process_batch(
                        batch, mask, region, method, temporal_smoothing, prev_inpainted_region, writer
                    )
                    pbar.update(len(batch))
                    batch = []

            if batch:
                prev_inpainted_region = _process_batch(
                    batch, mask, region, method, temporal_smoothing, prev_inpainted_region, writer
                )
                pbar.update(len(batch))
    finally:
        cap.release()
        writer.release()

    # Re-mux with original audio and re-encode video at the target quality/pix_fmt.
    info = probe_video(input_path)
    base_cmd = [
        "ffmpeg", "-y",
        "-i", silent_video_path,
        "-i", input_path,
        "-map", "0:v:0",
        "-c:v", "libx264", "-crf", "16", "-preset", "slow",
        "-pix_fmt", "yuv420p",
    ]
    if info["has_audio"]:
        cmd = base_cmd + ["-map", "1:a:0", "-c:a", "copy", "-shortest", output_path]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            fallback_cmd = base_cmd + ["-map", "1:a:0", "-c:a", "aac", "-b:a", "320k", "-shortest", output_path]
            fallback = subprocess.run(fallback_cmd, capture_output=True, text=True)
            if fallback.returncode != 0:
                os.remove(silent_video_path)
                raise RuntimeError(
                    f"Final mux failed.\ncopy stderr: {result.stderr[-1000:]}\n"
                    f"aac stderr: {fallback.stderr[-1000:]}"
                )
    else:
        cmd = base_cmd + [output_path]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            os.remove(silent_video_path)
            raise RuntimeError(f"Final encode failed.\nstderr: {result.stderr[-1000:]}")

    os.remove(silent_video_path)
    return output_path


def _process_batch(batch: list[np.ndarray], mask: np.ndarray, region: Region, method: str,
                    smoothing: float, prev_region_pixels: np.ndarray | None, writer: cv2.VideoWriter) -> np.ndarray:
    x, y, w, h = region
    for frame in batch:
        inpainted = _inpaint_frame(frame, mask, method)
        region_pixels = inpainted[y:y + h, x:x + w].astype(np.float32)

        if prev_region_pixels is not None:
            # Blend with the previous frame's inpainted region to reduce
            # frame-to-frame flicker in the reconstructed area only.
            blended = smoothing * prev_region_pixels + (1 - smoothing) * region_pixels
            inpainted[y:y + h, x:x + w] = blended.astype(np.uint8)
            region_pixels = blended

        writer.write(inpainted)
        prev_region_pixels = region_pixels

    return prev_region_pixels


def quality_check(input_path: str, output_dir: str, region: Region, mode: str = "inpaint",
                   method: str = "telea") -> str:
    """
    Grabs a single representative frame (25% into the video), applies the
    chosen removal mode to just that frame, and saves a side-by-side
    before/after comparison JPEG so results can be verified quickly.
    """
    os.makedirs(output_dir, exist_ok=True)
    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        raise InvalidVideoError(f"OpenCV could not open '{input_path}' for quality check.")

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    sample_idx = int(total_frames * 0.25)
    cap.set(cv2.CAP_PROP_POS_FRAMES, sample_idx)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise InvalidVideoError(f"Could not read a sample frame from '{input_path}' for quality check.")

    x, y, w, h = region
    if mode == "inpaint":
        mask = np.zeros(frame.shape[:2], dtype=np.uint8)
        mask[y:y + h, x:x + w] = 255
        after = _inpaint_frame(frame, mask, method)
    else:  # crop preview: draw a red box to show what will be removed, since crop changes framing
        after = frame.copy()
        cv2.rectangle(after, (x, y), (x + w, y + h), (0, 0, 255), 3)

    before_labeled = frame.copy()
    cv2.rectangle(before_labeled, (x, y), (x + w, y + h), (0, 0, 255), 3)
    cv2.putText(before_labeled, "BEFORE (region outlined)", (20, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 2)
    cv2.putText(after, "AFTER", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)

    side_by_side = np.hstack([before_labeled, after])
    out_path = os.path.join(output_dir, "quality_check.jpg")
    cv2.imwrite(out_path, side_by_side, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return out_path
