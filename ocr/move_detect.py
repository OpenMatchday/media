"""Camera-move detection for calibrated Scoreboard Reader (OM-031 / AC-4.7).

Compares a small grayscale patch grid sampled at the four calibrated corners
against the freeze-frame fingerprint taken at calibration confirm. Mean absolute
difference (MAD) above a measured threshold marks the board geometry as moved.

No OpenCV dependency: Pillow only. Threshold is selected between same-frame and
shifted-frame MAD on the synthetic harness (recorded in measurement JSON).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from PIL import Image

from calibration.geometry import CornerSet, Point


@dataclass(frozen=True)
class FrameFingerprint:
    """Flattened 0–255 grayscale samples from corner patches."""

    samples: tuple[int, ...]
    patch_size: int
    grid: int

    def to_dict(self) -> dict:
        return {
            "samples": list(self.samples),
            "patch_size": self.patch_size,
            "grid": self.grid,
            "count": len(self.samples),
        }


@dataclass(frozen=True)
class MoveDetectResult:
    moved: bool
    mad: float
    threshold: float
    message: str

    def to_dict(self) -> dict:
        return {
            "moved": self.moved,
            "mad": self.mad,
            "threshold": self.threshold,
            "message": self.message,
        }


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def _sample_patch(
    gray: Image.Image,
    cx: float,
    cy: float,
    *,
    patch_size: int,
    grid: int,
) -> list[int]:
    w, h = gray.size
    half = patch_size // 2
    x0 = _clamp(int(cx) - half, 0, max(0, w - patch_size))
    y0 = _clamp(int(cy) - half, 0, max(0, h - patch_size))
    patch = gray.crop((x0, y0, x0 + patch_size, y0 + patch_size))
    small = patch.resize((grid, grid), Image.Resampling.BILINEAR)
    return list(small.getdata())


def build_fingerprint(
    image: Image.Image | Path,
    corners: CornerSet,
    *,
    patch_size: int = 48,
    grid: int = 8,
) -> FrameFingerprint:
    img = image if isinstance(image, Image.Image) else Image.open(image)
    gray = img.convert("L")
    samples: list[int] = []
    for point in corners.ordered():
        samples.extend(
            _sample_patch(
                gray,
                point.x,
                point.y,
                patch_size=patch_size,
                grid=grid,
            )
        )
    return FrameFingerprint(
        samples=tuple(samples),
        patch_size=patch_size,
        grid=grid,
    )


def mad_distance(a: FrameFingerprint, b: FrameFingerprint) -> float:
    if len(a.samples) != len(b.samples) or not a.samples:
        raise ValueError("fingerprint_shape_mismatch")
    total = sum(abs(x - y) for x, y in zip(a.samples, b.samples))
    return total / len(a.samples)


def detect_move(
    reference: FrameFingerprint,
    probe: FrameFingerprint,
    *,
    threshold: float,
) -> MoveDetectResult:
    mad = mad_distance(reference, probe)
    moved = mad > threshold
    return MoveDetectResult(
        moved=moved,
        mad=round(mad, 4),
        threshold=threshold,
        message="camera_move_detected" if moved else "geometry_stable",
    )


def choose_threshold(same_mad: float, moved_mad: float) -> float:
    """Pick midpoint between same-frame and moved-frame MAD (measured)."""
    if moved_mad <= same_mad:
        raise ValueError("moved_mad_must_exceed_same_mad")
    return round((same_mad + moved_mad) / 2.0, 4)


def shift_corners(corners: CornerSet, dx: float, dy: float) -> CornerSet:
    def _s(p: Point) -> Point:
        return Point(p.x + dx, p.y + dy)

    return CornerSet(tl=_s(corners.tl), tr=_s(corners.tr), br=_s(corners.br), bl=_s(corners.bl))


def translate_image(image: Image.Image, dx: int, dy: int, fill: int = 0) -> Image.Image:
    """Translate pixels; exposed edge filled. Used for synthetic move fixtures."""
    return image.transform(
        image.size,
        Image.Transform.AFFINE,
        (1, 0, -dx, 0, 1, -dy),
        fillcolor=fill,
    )


def ordered_corner_points(corners: CornerSet) -> Sequence[Point]:
    return corners.ordered()
