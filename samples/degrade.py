"""Turn a clean invoice PDF into a scan-like page image (Pillow, OpenCV).

Conditions:
- C0 clean: the text PDF itself (nothing to do here).
- C1 skewed scan: rotated 2-4 degrees, grayscale, 200 DPI.
- C2 blurred scan: Gaussian blur, grayscale, 200 DPI.
- C3 noisy low-resolution scan: colour, a red "RECEIVED" stamp partly covering the gross
  weight, then downsampled to 100 DPI with noise, speckles and heavy JPEG compression.

Every random choice comes from a numpy Generator seeded per document, so each file
regenerates identically. The parameters actually used are returned and stored in the
answer file (for example the exact skew angle, which Phase 3's straightening test needs).
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import cv2
import numpy as np
import pymupdf
from PIL import Image, ImageDraw, ImageFont

RENDER_DPI = 200

CONDITIONS: tuple[str, ...] = ("C0", "C1", "C2", "C3")

CONDITION_DESCRIPTIONS: dict[str, str] = {
    "C0": "Clean: the generated text PDF, with a real text layer",
    "C1": "Skewed scan: rotated 2-4 degrees, grayscale, 200 DPI, image-only PDF",
    "C2": "Blurred scan: Gaussian blur (sigma 1.9-2.3 px), grayscale, 200 DPI, image-only PDF",
    "C3": (
        "Noisy low-resolution scan: 100 DPI colour, noise, speckles, heavy JPEG compression "
        "and a RECEIVED stamp partly covering the gross weight, image-only PDF"
    ),
}

# Ink colour of the stamp (RGB)
STAMP_INK = np.array([190, 35, 45], dtype=np.float32)


@dataclass(frozen=True)
class Degradation:
    """What was done to the page. Angles are counter-clockwise positive, as in OpenCV."""

    condition: str
    dpi: int
    grayscale: bool
    rotation_deg: float
    blur_sigma: float
    noise_sigma: float
    speckle_fraction: float
    jpeg_quality: int
    stamp: bool
    width_px: int
    height_px: int


@dataclass(frozen=True)
class Scan:
    jpeg: bytes
    degradation: Degradation


def rasterise(pdf: bytes, dpi: int = RENDER_DPI) -> np.ndarray:
    """First page of a PDF as an RGB uint8 array."""
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        pix = doc[0].get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
        rows = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)
        return rows[:, : pix.width * 3].reshape(pix.height, pix.width, 3).copy()


def _paper_tone(img: np.ndarray, ink: float, paper: np.ndarray | float) -> np.ndarray:
    """Real scans never have pure black ink or pure white paper."""
    return ink + img.astype(np.float32) * ((np.asarray(paper, np.float32) - ink) / 255.0)


def _rotate(img: np.ndarray, angle_deg: float, fill: float | tuple[float, ...]) -> np.ndarray:
    h, w = img.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle_deg, 1.0)
    return cv2.warpAffine(
        img,
        matrix,
        (w, h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=fill,
    )


def _stamp_mask(rng: np.random.Generator, px_per_mm: float, date: str) -> np.ndarray:
    """Ink coverage (0..1) of a rubber 'RECEIVED' stamp, tilted, with patchy ink."""
    w, h = round(52 * px_per_mm), round(21 * px_per_mm)
    layer = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(layer)
    border = max(3, round(1.0 * px_per_mm))
    half = border // 2
    draw.rounded_rectangle(
        (half, half, w - 1 - half, h - 1 - half),
        radius=round(2.5 * px_per_mm),
        outline=255,
        width=border,
    )
    inset = round(2.4 * px_per_mm)
    draw.rounded_rectangle(
        (inset, inset, w - 1 - inset, h - 1 - inset),
        radius=round(1.5 * px_per_mm),
        outline=255,
        width=max(2, border // 3),
    )
    big = ImageFont.load_default(size=round(8.5 * px_per_mm))
    small = ImageFont.load_default(size=round(4.0 * px_per_mm))
    draw.text(
        (w / 2, h * 0.42),
        "RECEIVED",
        fill=255,
        font=big,
        anchor="mm",
        stroke_width=1,
        stroke_fill=255,
    )
    draw.text((w / 2, h * 0.76), date, fill=255, font=small, anchor="mm")
    tilt = float(rng.uniform(8.0, 14.0))
    layer = layer.rotate(tilt, resample=Image.Resampling.BICUBIC, expand=True)

    mask = np.asarray(layer, dtype=np.float32) / 255.0
    texture = rng.uniform(0.55, 1.0, mask.shape).astype(np.float32)
    dropout = (rng.random(mask.shape) > 0.10).astype(np.float32)
    return cv2.GaussianBlur(mask * texture * dropout, (0, 0), 0.8)


def _apply_stamp(
    img: np.ndarray,
    rng: np.random.Generator,
    target_mm: tuple[float, float, float, float],
    date: str,
) -> np.ndarray:
    """Multiply-blend red ink over the page. The stamp's left edge lands on the gross weight
    value, so it covers the second half of the value and spills into the next box."""
    px_per_mm = RENDER_DPI / 25.4
    mask = _stamp_mask(rng, px_per_mm, date) * 0.9
    x, y, w, h = target_mm
    cx = (x + w * 0.85 + rng.uniform(-1.5, 1.5)) * px_per_mm
    cy = (y + h * 0.60 + rng.uniform(-1.0, 1.0)) * px_per_mm
    mh, mw = mask.shape
    top, left = round(cy - mh / 2), round(cx - mw / 2)
    region = img[top : top + mh, left : left + mw]
    alpha = mask[: region.shape[0], : region.shape[1], None]
    img[top : top + mh, left : left + mw] = region * (1.0 - alpha * (1.0 - STAMP_INK / 255.0))
    return img


def _add_noise(
    img: np.ndarray, rng: np.random.Generator, sigma: float, speckle_fraction: float
) -> np.ndarray:
    img = img + rng.normal(0.0, sigma, img.shape).astype(np.float32)
    if speckle_fraction > 0:
        hit = rng.random(img.shape[:2])
        img[hit < speckle_fraction / 2] = 40.0  # dust
        img[(hit >= speckle_fraction / 2) & (hit < speckle_fraction)] = 250.0  # dropouts
    return img


def _jpeg(img: np.ndarray, quality: int, dpi: int) -> bytes:
    arr = np.clip(np.rint(img), 0, 255).astype(np.uint8)
    buf = io.BytesIO()
    if arr.ndim == 2:
        Image.fromarray(arr, mode="L").save(buf, "JPEG", quality=quality, dpi=(dpi, dpi))
    else:
        Image.fromarray(arr, mode="RGB").save(
            buf, "JPEG", quality=quality, dpi=(dpi, dpi), subsampling=2
        )
    return buf.getvalue()


def scan(
    pdf: bytes,
    condition: str,
    rng: np.random.Generator,
    stamp_target_mm: tuple[float, float, float, float],
    stamp_date: str,
) -> Scan:
    """Degrade the clean PDF for condition C1, C2 or C3 and return the page as JPEG."""
    page = rasterise(pdf)

    if condition == "C1":
        angle = round(float(rng.uniform(2.0, 4.0)) * float(rng.choice([-1.0, 1.0])), 2)
        gray = cv2.cvtColor(page, cv2.COLOR_RGB2GRAY)
        img = _paper_tone(gray, ink=25.0, paper=243.0)
        img = _rotate(img, angle, fill=243.0)
        img = _add_noise(img, rng, sigma=2.5, speckle_fraction=0.0)
        params = dict(
            dpi=RENDER_DPI,
            grayscale=True,
            rotation_deg=angle,
            blur_sigma=0.0,
            noise_sigma=2.5,
            speckle_fraction=0.0,
            jpeg_quality=80,
            stamp=False,
        )

    elif condition == "C2":
        sigma = round(float(rng.uniform(1.9, 2.3)), 2)
        gray = cv2.cvtColor(page, cv2.COLOR_RGB2GRAY)
        img = _paper_tone(gray, ink=25.0, paper=243.0)
        img = cv2.GaussianBlur(img, (0, 0), sigma)
        img = _add_noise(img, rng, sigma=2.5, speckle_fraction=0.0)
        params = dict(
            dpi=RENDER_DPI,
            grayscale=True,
            rotation_deg=0.0,
            blur_sigma=sigma,
            noise_sigma=2.5,
            speckle_fraction=0.0,
            jpeg_quality=80,
            stamp=False,
        )

    elif condition == "C3":
        low_dpi = 100
        img = _paper_tone(page, ink=35.0, paper=np.array([236.0, 232.0, 222.0]))
        img = _apply_stamp(img, rng, stamp_target_mm, stamp_date)
        h, w = img.shape[:2]
        size = (round(w * low_dpi / RENDER_DPI), round(h * low_dpi / RENDER_DPI))
        img = cv2.resize(img, size, interpolation=cv2.INTER_AREA)
        img = _add_noise(img, rng, sigma=12.0, speckle_fraction=0.004)
        params = dict(
            dpi=low_dpi,
            grayscale=False,
            rotation_deg=0.0,
            blur_sigma=0.0,
            noise_sigma=12.0,
            speckle_fraction=0.004,
            jpeg_quality=55,
            stamp=True,
        )

    else:
        raise ValueError(f"No scan degradation for condition {condition!r}")

    jpeg = _jpeg(img, int(params["jpeg_quality"]), int(params["dpi"]))
    height, width = img.shape[:2]
    return Scan(
        jpeg=jpeg,
        degradation=Degradation(condition=condition, width_px=width, height_px=height, **params),
    )
