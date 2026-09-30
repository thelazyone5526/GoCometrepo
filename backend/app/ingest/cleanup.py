"""Scan clean-up (OpenCV): grayscale, straighten, denoise, raise contrast.

Only scans go through here. A digital PDF page already has a text layer (`textlayer.py`) and
needs none of this.

Straightening uses the projection-profile method: try a range of correction angles, rotate a
downscaled binary version of the page for each, and keep the angle whose horizontal projection
(row sums) has the highest variance. A level page's text lines are sharp, high-contrast bands,
which is exactly what maximises that variance; a skewed page smears each line across several
rows and flattens the profile. The search is coarse-to-fine so it stays fast: roughly 0.5deg
steps across +/-10deg, then 0.05deg steps around the best candidate.

Sign convention: angles here follow `cv2.getRotationMatrix2D`, positive counter-clockwise,
the same convention `samples/degrade.py` records in each answer file's `rotation_deg`. So
`estimate_skew_deg` on a page skewed by that amount returns (within the search resolution)
that same value, and `straighten` applies the negative of it to bring the page level.
"""

from __future__ import annotations

import cv2
import numpy as np

# Downscale the binary image to at most this many pixels on the long side before searching
# for the skew angle. The angle itself doesn't depend on resolution, and searching a small
# image is much faster.
_SKEW_SEARCH_MAX_SIDE = 700

# Coarse-to-fine search ranges and steps, in degrees.
_COARSE_RANGE_DEG = 10.0
_COARSE_STEP_DEG = 0.5
_FINE_RADIUS_DEG = 0.6
_FINE_STEP_DEG = 0.05

# Contrast (CLAHE): a moderate clip limit avoids blowing out noise into false edges.
_CLAHE_CLIP_LIMIT = 2.0
_CLAHE_TILE_SIZE = (8, 8)


def to_grayscale(rgb: np.ndarray) -> np.ndarray:
    if rgb.ndim == 2:
        return rgb
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)


def _rotate(img: np.ndarray, angle_deg: float, *, fill: float, flags: int) -> np.ndarray:
    h, w = img.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), angle_deg, 1.0)
    return cv2.warpAffine(
        img, matrix, (w, h), flags=flags, borderMode=cv2.BORDER_CONSTANT, borderValue=fill
    )


def _binarize_for_skew_search(gray: np.ndarray) -> np.ndarray:
    """Otsu-thresholded, text-as-white-on-black, downscaled for a fast angle search."""
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    h, w = binary.shape
    scale = _SKEW_SEARCH_MAX_SIDE / max(h, w)
    if scale < 1.0:
        new_size = (max(1, round(w * scale)), max(1, round(h * scale)))
        binary = cv2.resize(binary, new_size, interpolation=cv2.INTER_NEAREST)
    return binary


def _projection_variance(binary: np.ndarray, candidate_deg: float) -> float:
    """How well `candidate_deg` (as the true skew) explains the page, once undone.

    Rotating by `-candidate_deg` is what would level a page truly skewed by `candidate_deg`
    (rotating by an angle then its negative is the identity). Row sums of a level page's text
    form sharp bands, which is high variance; a still-tilted page smears them, which is lower.
    """
    corrected = _rotate(binary, -candidate_deg, fill=0, flags=cv2.INTER_NEAREST)
    row_sums = corrected.sum(axis=1).astype(np.float64)
    return float(row_sums.var())


def estimate_skew_deg(gray: np.ndarray) -> float:
    """The page's current skew angle, in `cv2.getRotationMatrix2D` degrees (positive CCW)."""
    binary = _binarize_for_skew_search(gray)

    best_angle, best_score = 0.0, -1.0
    coarse = np.arange(-_COARSE_RANGE_DEG, _COARSE_RANGE_DEG + _COARSE_STEP_DEG, _COARSE_STEP_DEG)
    for angle in coarse:
        score = _projection_variance(binary, float(angle))
        if score > best_score:
            best_score, best_angle = score, float(angle)

    fine_start = best_angle - _FINE_RADIUS_DEG
    fine_stop = best_angle + _FINE_RADIUS_DEG + _FINE_STEP_DEG
    fine = np.arange(fine_start, fine_stop, _FINE_STEP_DEG)
    for angle in fine:
        score = _projection_variance(binary, float(angle))
        if score > best_score:
            best_score, best_angle = score, float(angle)

    return round(best_angle, 2)


def straighten(gray: np.ndarray, *, fill: int = 255) -> tuple[np.ndarray, float]:
    """Level the page. Returns the corrected image and the skew angle that was removed."""
    angle = estimate_skew_deg(gray)
    corrected = _rotate(gray, -angle, fill=float(fill), flags=cv2.INTER_LINEAR)
    return corrected, angle


def denoise(gray: np.ndarray) -> np.ndarray:
    """Remove scan noise (Gaussian grain and speckle dropouts) while keeping text edges."""
    return cv2.fastNlMeansDenoising(gray, h=7, templateWindowSize=7, searchWindowSize=21)


def raise_contrast(gray: np.ndarray) -> np.ndarray:
    """CLAHE: local contrast, so a faint or unevenly lit scan still separates ink from paper."""
    clahe = cv2.createCLAHE(clipLimit=_CLAHE_CLIP_LIMIT, tileGridSize=_CLAHE_TILE_SIZE)
    return clahe.apply(gray)


def clean_scan(rgb_or_gray: np.ndarray) -> tuple[np.ndarray, float]:
    """The full clean-up pipeline for one scanned page: grayscale, straighten, denoise,
    raise contrast. Returns the cleaned image (what RapidOCR reads, and what the UI shows and
    draws evidence boxes over) and the skew angle that was corrected."""
    gray = to_grayscale(rgb_or_gray)
    straightened, angle = straighten(gray)
    cleaned = raise_contrast(denoise(straightened))
    return cleaned, angle
