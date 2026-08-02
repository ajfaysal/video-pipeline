from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from aspectshift.downloader import (  # noqa: E402
    DownloadError,
    InvalidImageError,
    MissingDependencyError,
    resolve_image_input,
)


@dataclass
class ImageResult:
    source_name: str
    output_path: str
    report_lines: list[str]
    eps_path: str | None = None
    preview_path: str | None = None


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="StockImageFix: fully automatic Adobe Stock image checks + fixes."
    )
    parser.add_argument("--input", action="append", default=[], help="Local image path (repeatable).")
    parser.add_argument("--url", action="append", default=[], help="Image URL (repeatable).")
    parser.add_argument("--output-dir", default="./output", help="Directory for output files.")
    parser.add_argument(
        "--png-spec",
        default=os.path.join("stockimagefix", "adobe_png_requirements.json"),
        help="Cached Adobe PNG requirements JSON path.",
    )
    return parser


def _load_png_spec(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def _is_edge_touching(x: int, y: int, w: int, h: int, width: int, height: int) -> bool:
    return x <= 0 or y <= 0 or (x + w) >= width or (y + h) >= height


def _foreground_mask(rgba: np.ndarray) -> np.ndarray:
    alpha = rgba[:, :, 3]
    if np.any(alpha < 250):
        return (alpha > 15).astype(np.uint8) * 255

    try:
        import cv2

        bgr = cv2.cvtColor(rgba[:, :, :3], cv2.COLOR_RGB2BGR)
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        if np.count_nonzero(mask) == 0:
            return np.ones_like(gray, dtype=np.uint8) * 255
        return mask
    except Exception:
        # If OpenCV fails, keep everything as foreground for safety.
        return np.ones((rgba.shape[0], rgba.shape[1]), dtype=np.uint8) * 255


def _remove_stray_elements(rgba: np.ndarray, report: list[str]) -> np.ndarray:
    canvas_area = float(rgba.shape[0] * rgba.shape[1])
    try:
        import cv2

        mask = _foreground_mask(rgba)
        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        if num_labels <= 2:
            report.append("- Stray elements: none detected.")
            return rgba

        areas = stats[1:, cv2.CC_STAT_AREA]
        main_label = int(np.argmax(areas)) + 1

        removal_mask = np.zeros(mask.shape, dtype=np.uint8)
        removed = 0
        ambiguous = 0
        main_area_ratio = stats[main_label, cv2.CC_STAT_AREA] / canvas_area

        for label in range(1, num_labels):
            if label == main_label:
                continue
            x, y, w, h, area = stats[label]
            area_ratio = area / canvas_area
            touching_edge = _is_edge_touching(int(x), int(y), int(w), int(h), rgba.shape[1], rgba.shape[0])
            disconnected = label != main_label

            high_confidence = area_ratio < 0.05 and disconnected and not touching_edge
            ambiguous_confidence = area_ratio < 0.08 and disconnected and not touching_edge

            if high_confidence:
                removal_mask[labels == label] = 255
                removed += 1
            elif ambiguous_confidence:
                ambiguous += 1
                if np.any(rgba[:, :, 3] < 250):
                    removal_mask[labels == label] = 255
                    removed += 1

        if np.count_nonzero(removal_mask) == 0:
            if ambiguous:
                report.append(
                    f"- Stray elements: {ambiguous} ambiguous component(s) detected; safest auto-fix kept opaque regions unchanged."
                )
            else:
                report.append("- Stray elements: none detected.")
            return rgba

        result = rgba.copy()
        has_transparency = np.any(result[:, :, 3] < 250)
        if has_transparency:
            result[:, :, 3][removal_mask > 0] = 0
        else:
            bgr = cv2.cvtColor(result[:, :, :3], cv2.COLOR_RGB2BGR)
            fixed = cv2.inpaint(bgr, removal_mask, 3, cv2.INPAINT_TELEA)
            result[:, :, :3] = cv2.cvtColor(fixed, cv2.COLOR_BGR2RGB)

        confidence_note = (
            "high-confidence (<5% area, disconnected, non-edge)"
            if ambiguous == 0
            else "mixed confidence (high-confidence + safe default on ambiguous components)"
        )
        report.append(
            f"- Stray elements: removed {removed} component(s), main-shape area ≈ {main_area_ratio:.1%}, rationale: {confidence_note}."
        )
        return result
    except Exception as exc:
        report.append(f"- Stray elements: OpenCV analysis unavailable ({exc}); skipped removal to avoid destructive edits.")
        return rgba


def _looks_vector_style(rgba: np.ndarray) -> bool:
    try:
        import cv2

        rgb = rgba[:, :, :3]
        q = (rgb // 24).reshape(-1, 3)
        unique_colors = len(np.unique(q, axis=0))
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        edges = cv2.Canny(gray, 80, 160)
        edge_density = float(np.count_nonzero(edges)) / edges.size
        return unique_colors <= 64 and edge_density <= 0.18
    except Exception:
        return False


def _upscale_with_realesrgan_if_available(image: Image.Image, scale_factor: float) -> Image.Image | None:
    exe = shutil.which("realesrgan-ncnn-vulkan")
    if not exe:
        return None
    int_scale = max(2, min(4, int(math.ceil(scale_factor))))
    with tempfile.TemporaryDirectory(prefix="stockfix_") as tmp:
        inp = os.path.join(tmp, "in.png")
        outp = os.path.join(tmp, "out.png")
        image.save(inp, format="PNG")
        cmd = [exe, "-i", inp, "-o", outp, "-s", str(int_scale)]
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if os.path.isfile(outp):
                return Image.open(outp).convert("RGBA")
        except Exception:
            return None
    return None


def _ensure_min_resolution(
    image: Image.Image,
    min_pixels: int,
    min_width: int,
    min_height: int,
    report: list[str],
) -> Image.Image:
    w, h = image.size
    required = max(math.sqrt(min_pixels / max(1, w * h)), min_width / max(1, w), min_height / max(1, h), 1.0)
    if required <= 1.0:
        report.append(f"- Resolution: already compliant at {w}x{h} ({w*h:,} px).")
        return image

    real_esrgan = _upscale_with_realesrgan_if_available(image, required)
    target_w = max(min_width, int(math.ceil(w * required)))
    target_h = max(min_height, int(math.ceil(h * required)))

    if real_esrgan is not None:
        upscaled = real_esrgan.resize((target_w, target_h), Image.Resampling.LANCZOS)
        report.append(
            f"- Resolution: upscaled {w}x{h} -> {target_w}x{target_h} using Real-ESRGAN (with Lanczos fit)."
        )
    else:
        upscaled = image.resize((target_w, target_h), Image.Resampling.LANCZOS)
        report.append(f"- Resolution: upscaled {w}x{h} -> {target_w}x{target_h} using Lanczos fallback.")

    report.append("- Quality warning: upscaling was required; inspect fine detail before stock submission.")
    return upscaled


def _cap_max_resolution(image: Image.Image, max_pixels: int, report: list[str]) -> Image.Image:
    """Downscale (Lanczos) when the image exceeds Adobe's maximum megapixel cap."""
    w, h = image.size
    if max_pixels <= 0 or (w * h) <= max_pixels:
        return image
    scale = math.sqrt(max_pixels / float(w * h))
    target_w = max(1, int(w * scale))
    target_h = max(1, int(h * scale))
    resized = image.resize((target_w, target_h), Image.Resampling.LANCZOS)
    report.append(f"- Resolution: downscaled {w}x{h} -> {target_w}x{target_h} to satisfy the {max_pixels:,} px maximum.")
    return resized


def _to_srgb(image: Image.Image) -> Image.Image:
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    try:
        srgb = ImageCms.createProfile("sRGB")
        return ImageCms.profileToProfile(image, srgb, srgb, outputMode=image.mode)
    except Exception:
        return image


def _save_jpeg_safely(image: Image.Image, output_path: str, max_size_mb: int, report: list[str]) -> None:
    rgb = _to_srgb(image.convert("RGB"))
    icc_profile = None
    try:
        icc_profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    except Exception:
        icc_profile = None

    max_bytes = max_size_mb * 1024 * 1024
    selected_quality = 95
    for quality in [95, 92, 90, 88, 85, 82, 80, 78, 75, 72, 70]:
        rgb.save(output_path, format="JPEG", quality=quality, optimize=True, progressive=True, icc_profile=icc_profile)
        if os.path.getsize(output_path) <= max_bytes:
            selected_quality = quality
            break
        selected_quality = quality

    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    report.append(f"- Output format: JPEG (sRGB), quality={selected_quality}, size={size_mb:.2f} MB.")
    if size_mb > max_size_mb:
        report.append(f"- Warning: output is {size_mb:.2f} MB which exceeds {max_size_mb} MB target.")


def _save_png_safely(image: Image.Image, output_path: str, png_spec: dict, report: list[str]) -> None:
    rgba = _to_srgb(image.convert("RGBA"))
    # Save without metadata chunks.
    clean = Image.new("RGBA", rgba.size)
    clean.paste(rgba)
    clean.save(output_path, format="PNG", optimize=True, compress_level=9)

    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    max_mb = (png_spec.get("parsed") or {}).get("max_file_size_mb")
    report.append(f"- Output format: PNG (alpha preserved), size={size_mb:.2f} MB.")
    if max_mb:
        report.append(f"- PNG spec check (cached): max file size {max_mb} MB.")
        if size_mb > float(max_mb):
            report.append(f"- Warning: PNG size exceeds cached max ({size_mb:.2f} MB > {max_mb} MB).")


def _build_before_after_preview(
    before: Image.Image,
    after: Image.Image,
    output_dir: str,
    base: str,
    report: list[str],
) -> str | None:
    """Render a side-by-side before/after comparison JPEG for the Telegram reply."""
    try:
        panel_w = 640
        panels = []
        for label_img in (before, after):
            img = label_img.convert("RGB")
            scale = panel_w / max(1, img.width)
            panel_h = max(1, int(round(img.height * scale)))
            panels.append(img.resize((panel_w, panel_h), Image.Resampling.LANCZOS))

        height = max(p.height for p in panels)
        divider = 4
        canvas = Image.new("RGB", (panel_w * 2 + divider, height), (32, 32, 32))
        canvas.paste(panels[0], (0, (height - panels[0].height) // 2))
        canvas.paste(panels[1], (panel_w + divider, (height - panels[1].height) // 2))

        preview_path = os.path.join(output_dir, f"{base}_before_after.jpg")
        canvas.save(preview_path, format="JPEG", quality=85, optimize=True)
        report.append("- Preview: generated side-by-side before/after comparison.")
        return preview_path
    except Exception as exc:
        report.append(f"- Preview: before/after render failed ({exc}); continuing without it.")
        return None


def _maybe_export_eps(output_png_path: str, report: list[str]) -> str | None:
    inkscape = shutil.which("inkscape")
    if not inkscape:
        report.append(
            "- Vector heuristic: flat/vector-like image detected; EPS fallback unavailable (Inkscape not installed)."
        )
        return None
    eps_path = str(Path(output_png_path).with_suffix(".eps"))
    try:
        subprocess.run(
            [inkscape, output_png_path, "--export-type=eps", f"--export-filename={eps_path}"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        report.append(
            "- Vector heuristic: exported EPS via Inkscape bitmap trace fallback. Warning: true vector redraw is still preferred for stock vectors."
        )
        return eps_path
    except Exception as exc:
        report.append(f"- Vector heuristic: EPS fallback failed ({exc}); kept raster output only.")
        return None


def process_one_image(source_path: str, output_dir: str, png_spec: dict) -> ImageResult:
    base = Path(source_path).stem
    report = [f"Source: {os.path.basename(source_path)}"]

    image = Image.open(source_path).convert("RGBA")
    original = image.copy()
    rgba = np.array(image)

    fixed_rgba = _remove_stray_elements(rgba, report)
    fixed_img = Image.fromarray(fixed_rgba, mode="RGBA")

    has_transparency = np.any(fixed_rgba[:, :, 3] < 250)
    is_vector_like = _looks_vector_style(fixed_rgba)

    spec_parsed = png_spec.get("parsed") or {}
    if has_transparency:
        min_pixels = int(spec_parsed.get("min_pixels") or 4_000_000)
        min_width = int(spec_parsed.get("min_width") or 0)
        min_height = int(spec_parsed.get("min_height") or 0)
        fixed_img = _ensure_min_resolution(fixed_img, min_pixels=min_pixels, min_width=min_width, min_height=min_height, report=report)
        fixed_img = _cap_max_resolution(fixed_img, int(spec_parsed.get("max_pixels") or 100_000_000), report)
        output_path = os.path.join(output_dir, f"{base}_stock_fixed.png")
        _save_png_safely(fixed_img, output_path, png_spec, report)
    else:
        fixed_img = _ensure_min_resolution(fixed_img, min_pixels=4_000_000, min_width=0, min_height=0, report=report)
        fixed_img = _cap_max_resolution(fixed_img, 100_000_000, report)
        output_path = os.path.join(output_dir, f"{base}_stock_fixed.jpg")
        _save_jpeg_safely(fixed_img, output_path, max_size_mb=45, report=report)

    report.append("- Metadata: stripped EXIF/tool tags by re-encoding clean output.")

    eps_path = None
    if is_vector_like:
        report.append("- Vector heuristic: detected clean edges + limited color complexity.")
        png_for_eps = output_path
        if not png_for_eps.lower().endswith(".png"):
            png_for_eps = os.path.join(output_dir, f"{base}_stock_fixed_for_eps.png")
            fixed_img.save(png_for_eps, format="PNG", optimize=True, compress_level=9)
        eps_path = _maybe_export_eps(png_for_eps, report)

    preview_path = _build_before_after_preview(original, fixed_img, output_dir, base, report)

    return ImageResult(
        source_name=os.path.basename(source_path),
        output_path=output_path,
        report_lines=report,
        eps_path=eps_path,
        preview_path=preview_path,
    )


def write_report(results: list[ImageResult], output_dir: str) -> str:
    lines = ["StockImageFix auto-fix report", f"Generated: {datetime.now(timezone.utc).isoformat()}", ""]
    for idx, result in enumerate(results, start=1):
        lines.append(f"[{idx}] {result.source_name}")
        lines.extend(result.report_lines)
        lines.append("")
    path = os.path.join(output_dir, "stockimagefix_report.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines).strip() + "\n")
    return path


def build_delivery(results: list[ImageResult], report_path: str, output_dir: str) -> str:
    deliverables = [r.output_path for r in results]
    for r in results:
        if r.eps_path and os.path.isfile(r.eps_path):
            deliverables.append(r.eps_path)

    if len(deliverables) == 1:
        return deliverables[0]

    zip_path = os.path.join(output_dir, "stockimagefix_delivery.zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in deliverables:
            zf.write(path, arcname=os.path.basename(path))
        zf.write(report_path, arcname=os.path.basename(report_path))
    return zip_path


def resolve_sources(args: argparse.Namespace) -> list[str]:
    sources: list[str] = []
    for local in args.input:
        sources.append(resolve_image_input(input_path=local, output_dir=args.output_dir))
    for url in args.url:
        sources.append(resolve_image_input(url=url, output_dir=args.output_dir))
    if not sources:
        raise ValueError("Provide at least one --input or --url source.")
    return sources


def main() -> int:
    args = build_arg_parser().parse_args()
    os.makedirs(args.output_dir, exist_ok=True)

    try:
        png_spec = _load_png_spec(args.png_spec)
        sources = resolve_sources(args)

        results: list[ImageResult] = []
        for source_path in sources:
            results.append(process_one_image(source_path, args.output_dir, png_spec))

        report_path = write_report(results, args.output_dir)
        delivery_path = build_delivery(results, report_path, args.output_dir)

        manifest = {
            "delivery_path": delivery_path,
            "report_path": report_path,
            "outputs": [r.output_path for r in results],
            "eps_outputs": [r.eps_path for r in results if r.eps_path],
            "previews": [r.preview_path for r in results if r.preview_path],
        }
        manifest_path = os.path.join(args.output_dir, "stockimagefix_manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)

        print(f"[stockimagefix] Done. Delivery: {delivery_path}")
        print(f"[stockimagefix] Report: {report_path}")
        return 0
    except MissingDependencyError as exc:
        print(f"[error] Missing dependency: {exc}", file=sys.stderr)
        return 2
    except (DownloadError, InvalidImageError, ValueError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 3
    except Exception as exc:
        print(f"[error] Unexpected failure: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
