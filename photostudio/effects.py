"""
effects.py
----------
Professional photo color effects for PhotoStudio, implemented with
OpenCV + NumPy only (no heavyweight ML dependencies).

Each effect is a function (bgr_uint8_image) -> bgr_uint8_image.
The PRESETS dict maps a stable effect id -> (label, function).

The flagship "dslr" effect emulates the look of a photo shot on a
full-frame DSLR: local-contrast pop (CLAHE), gentle S-curve tone mapping,
subtle warmth, vibrance (saturates muted colors more than already-vivid
ones), and a soft optical vignette.
"""

from __future__ import annotations

import cv2
import numpy as np


# --------------------------------------------------------------------------
# Building blocks
# --------------------------------------------------------------------------

def _s_curve(img: np.ndarray, strength: float = 0.12) -> np.ndarray:
    """Gentle S-curve contrast via a smoothstep-blended LUT."""
    x = np.arange(256, dtype=np.float32) / 255.0
    s = x * x * (3.0 - 2.0 * x)  # smoothstep
    curve = ((1.0 - strength) * x + strength * s) * 255.0
    lut = np.clip(curve, 0, 255).astype(np.uint8)
    return cv2.LUT(img, lut)


def _clahe_pop(img: np.ndarray, clip: float = 2.0, grid: int = 8) -> np.ndarray:
    """Local contrast enhancement on the L channel (LAB) only."""
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=clip, tileGridSize=(grid, grid))
    l = clahe.apply(l)
    return cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)


def _vibrance(img: np.ndarray, amount: float = 0.25) -> np.ndarray:
    """Boost saturation of muted colors more than already-saturated ones."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
    s = hsv[:, :, 1] / 255.0
    hsv[:, :, 1] = np.clip((s + amount * (1.0 - s) * s * 2.0) * 255.0, 0, 255)
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)


def _saturation(img: np.ndarray, factor: float) -> np.ndarray:
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[:, :, 1] = np.clip(hsv[:, :, 1] * factor, 0, 255)
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)


def _white_balance_shift(img: np.ndarray, r: float = 0.0, g: float = 0.0, b: float = 0.0) -> np.ndarray:
    """Additive per-channel shift in the -1..1 range (fraction of 255)."""
    out = img.astype(np.float32)
    out[:, :, 2] += r * 255.0
    out[:, :, 1] += g * 255.0
    out[:, :, 0] += b * 255.0
    return np.clip(out, 0, 255).astype(np.uint8)


def _vignette(img: np.ndarray, strength: float = 0.35, softness: float = 1.6) -> np.ndarray:
    """Soft radial darkening toward the corners (optical-lens style)."""
    h, w = img.shape[:2]
    y, x = np.ogrid[:h, :w]
    cy, cx = h / 2.0, w / 2.0
    dist = np.sqrt(((x - cx) / cx) ** 2 + ((y - cy) / cy) ** 2) / np.sqrt(2.0)
    mask = 1.0 - strength * np.clip(dist, 0, 1) ** softness
    return np.clip(img.astype(np.float32) * mask[:, :, np.newaxis], 0, 255).astype(np.uint8)


def _lift_shadows(img: np.ndarray, lift: float = 0.06) -> np.ndarray:
    """Matte-style raised black point."""
    out = img.astype(np.float32) / 255.0
    out = out * (1.0 - lift) + lift
    return np.clip(out * 255.0, 0, 255).astype(np.uint8)


def _split_tone(img: np.ndarray, shadow_bgr: tuple, highlight_bgr: tuple, amount: float = 0.08) -> np.ndarray:
    """Tint shadows and highlights toward different colors (teal & orange etc)."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    lum = gray[:, :, np.newaxis]
    shadow = np.array(shadow_bgr, dtype=np.float32)
    highlight = np.array(highlight_bgr, dtype=np.float32)
    tint = shadow * (1.0 - lum) + highlight * lum
    out = img.astype(np.float32) * (1.0 - amount) + tint * amount
    return np.clip(out, 0, 255).astype(np.uint8)


def _grain(img: np.ndarray, sigma: float = 4.0) -> np.ndarray:
    noise = np.random.default_rng(42).normal(0, sigma, img.shape[:2]).astype(np.float32)
    return np.clip(img.astype(np.float32) + noise[:, :, np.newaxis], 0, 255).astype(np.uint8)


def _face_bokeh(img: np.ndarray, blur_sigma: float = 12.0) -> np.ndarray:
    """
    Portrait bokeh: keep detected face region(s) sharp, blur the rest with a
    feathered elliptical mask. Falls back to the untouched image when no
    face is found or face detection is unavailable.
    """
    from aspectshift.downloader import load_face_cascade
    cascade = load_face_cascade()
    if cascade is None:
        return img
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5,
                                     minSize=(max(40, img.shape[1] // 20),) * 2)
    if len(faces) == 0:
        return img

    h, w = img.shape[:2]
    mask = np.zeros((h, w), dtype=np.float32)
    for (x, y, fw, fh) in faces:
        # Head + shoulders ellipse, generously expanded.
        cx, cy = x + fw // 2, y + int(fh * 1.1)
        cv2.ellipse(mask, (cx, cy), (int(fw * 1.4), int(fh * 2.4)), 0, 0, 360, 1.0, -1)
    feather = max(15, min(h, w) // 20) | 1
    mask = cv2.GaussianBlur(mask, (feather, feather), 0)
    blurred = cv2.GaussianBlur(img, (0, 0), sigmaX=blur_sigma)
    mask3 = mask[:, :, np.newaxis]
    return np.clip(img.astype(np.float32) * mask3 + blurred.astype(np.float32) * (1 - mask3),
                   0, 255).astype(np.uint8)


# --------------------------------------------------------------------------
# Effect presets
# --------------------------------------------------------------------------

def effect_dslr(img: np.ndarray) -> np.ndarray:
    """Full-frame DSLR look: local contrast, S-curve, warmth, vibrance, vignette."""
    out = _clahe_pop(img, clip=1.8)
    out = _s_curve(out, strength=0.14)
    out = _white_balance_shift(out, r=0.015, b=-0.012)
    out = _vibrance(out, amount=0.22)
    out = _vignette(out, strength=0.22, softness=1.9)
    return out


def effect_cinematic(img: np.ndarray) -> np.ndarray:
    out = _s_curve(img, strength=0.18)
    out = _split_tone(out, shadow_bgr=(120, 80, 20), highlight_bgr=(60, 140, 235), amount=0.10)
    out = _saturation(out, 0.92)
    out = _vignette(out, strength=0.3, softness=1.7)
    return out


def effect_hdr(img: np.ndarray) -> np.ndarray:
    out = _clahe_pop(img, clip=3.2, grid=8)
    out = _vibrance(out, amount=0.30)
    out = _s_curve(out, strength=0.10)
    return out


def effect_portrait(img: np.ndarray) -> np.ndarray:
    out = _face_bokeh(img, blur_sigma=max(6.0, min(img.shape[:2]) / 120.0))
    out = _clahe_pop(out, clip=1.5)
    out = _white_balance_shift(out, r=0.02, b=-0.01)
    out = _vibrance(out, amount=0.15)
    out = _vignette(out, strength=0.18, softness=2.0)
    return out


def effect_vivid(img: np.ndarray) -> np.ndarray:
    out = _vibrance(img, amount=0.4)
    out = _saturation(out, 1.15)
    out = _s_curve(out, strength=0.12)
    return out


def effect_matte(img: np.ndarray) -> np.ndarray:
    out = _lift_shadows(img, lift=0.08)
    out = _saturation(out, 0.85)
    out = _s_curve(out, strength=0.06)
    return out


def effect_bw(img: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    out = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    return _s_curve(out, strength=0.16)


def effect_teal_orange(img: np.ndarray) -> np.ndarray:
    out = _split_tone(img, shadow_bgr=(140, 90, 10), highlight_bgr=(30, 140, 250), amount=0.14)
    out = _s_curve(out, strength=0.12)
    out = _vibrance(out, amount=0.15)
    return out


def effect_golden_hour(img: np.ndarray) -> np.ndarray:
    out = _white_balance_shift(img, r=0.06, g=0.02, b=-0.05)
    out = _lift_shadows(out, lift=0.03)
    out = _vibrance(out, amount=0.18)
    out = _vignette(out, strength=0.25, softness=1.8)
    return out


def effect_film(img: np.ndarray) -> np.ndarray:
    out = _lift_shadows(img, lift=0.06)
    out = _split_tone(out, shadow_bgr=(90, 90, 30), highlight_bgr=(80, 160, 210), amount=0.08)
    out = _saturation(out, 0.82)
    out = _grain(out, sigma=3.5)
    out = _vignette(out, strength=0.2, softness=1.8)
    return out


PRESETS: dict[str, tuple[str, callable]] = {
    "dslr": ("DSLR (full-frame camera look)", effect_dslr),
    "cinematic": ("Cinematic (teal shadows, filmic curve)", effect_cinematic),
    "hdr": ("HDR (deep local contrast + vibrance)", effect_hdr),
    "portrait": ("Portrait (bokeh + warm skin tones)", effect_portrait),
    "vivid": ("Vivid (punchy saturated colors)", effect_vivid),
    "matte": ("Matte (soft lifted blacks)", effect_matte),
    "bw": ("Black & White (rich mono)", effect_bw),
    "teal_orange": ("Teal & Orange (blockbuster grade)", effect_teal_orange),
    "golden_hour": ("Golden Hour (warm sunset glow)", effect_golden_hour),
    "film": ("Film (analog grain + faded tones)", effect_film),
}


def apply_effect(img: np.ndarray, effect: str) -> np.ndarray:
    if effect not in PRESETS:
        raise ValueError(f"Unknown effect '{effect}'. Options: {list(PRESETS)}")
    return PRESETS[effect][1](img)
