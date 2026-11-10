"""Four-corner board calibration geometry (OM-030 / AC-4.1–4.6).

Homography from four labeled board corners uses the exact 4-point perspective
system with h33=1, matching OpenCV getPerspectiveTransform:

  https://docs.opencv.org/4.11.0/da/d54/group__imgproc__transform.html

Pure stdlib; no OpenCV/numpy dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


CORNER_ORDER = ("tl", "tr", "br", "bl")
DEFAULT_MIN_AREA_FRACTION = 0.01
DEFAULT_MIN_EDGE_FRACTION = 0.02


@dataclass(frozen=True)
class Point:
    x: float
    y: float

    def as_tuple(self) -> tuple[float, float]:
        return (self.x, self.y)


@dataclass(frozen=True)
class CornerSet:
    tl: Point
    tr: Point
    br: Point
    bl: Point

    def ordered(self) -> tuple[Point, Point, Point, Point]:
        return (self.tl, self.tr, self.br, self.bl)

    def to_dict(self) -> dict:
        return {
            "tl": {"x": self.tl.x, "y": self.tl.y},
            "tr": {"x": self.tr.x, "y": self.tr.y},
            "br": {"x": self.br.x, "y": self.br.y},
            "bl": {"x": self.bl.x, "y": self.bl.y},
        }

    @staticmethod
    def from_dict(raw: dict) -> "CornerSet":
        def pt(key: str) -> Point:
            item = raw[key]
            return Point(float(item["x"]), float(item["y"]))

        return CornerSet(tl=pt("tl"), tr=pt("tr"), br=pt("br"), bl=pt("bl"))


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reasons: tuple[str, ...]
    area: float
    winding: str

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "reasons": list(self.reasons),
            "area": self.area,
            "winding": self.winding,
        }


def _cross(a: Point, b: Point, c: Point) -> float:
    return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)


def polygon_area(points: Sequence[Point]) -> float:
    if len(points) < 3:
        return 0.0
    total = 0.0
    for i, p in enumerate(points):
        q = points[(i + 1) % len(points)]
        total += p.x * q.y - q.x * p.y
    return abs(total) * 0.5


def winding_sign(points: Sequence[Point]) -> int:
    """+1 / -1 from shoelace; 0 degenerate.

    Image y-down: TL→TR→BR→BL yields positive shoelace (math CCW).
    """
    total = 0.0
    for i, p in enumerate(points):
        q = points[(i + 1) % len(points)]
        total += p.x * q.y - q.x * p.y
    if abs(total) < 1e-12:
        return 0
    return 1 if total > 0 else -1


def is_convex(points: Sequence[Point]) -> bool:
    if len(points) != 4:
        return False
    signs: list[int] = []
    for i in range(4):
        a = points[i]
        b = points[(i + 1) % 4]
        c = points[(i + 2) % 4]
        cr = _cross(a, b, c)
        if abs(cr) < 1e-12:
            return False
        signs.append(1 if cr > 0 else -1)
    return all(s == signs[0] for s in signs)


def validate_corners(
    corners: CornerSet,
    *,
    frame_width: float,
    frame_height: float,
    min_area_fraction: float = DEFAULT_MIN_AREA_FRACTION,
    min_edge_fraction: float = DEFAULT_MIN_EDGE_FRACTION,
) -> ValidationResult:
    reasons: list[str] = []
    pts = corners.ordered()
    if frame_width <= 0 or frame_height <= 0:
        reasons.append("frame_size_invalid")
    for label, p in zip(CORNER_ORDER, pts):
        if not (0.0 <= p.x <= frame_width and 0.0 <= p.y <= frame_height):
            reasons.append(f"{label}_out_of_frame")
    unique = {(round(p.x, 6), round(p.y, 6)) for p in pts}
    if len(unique) < 4:
        reasons.append("duplicate_corners")
    if not is_convex(pts):
        reasons.append("not_convex_or_crossed")
    wind = winding_sign(pts)
    # Screen coords (y down): TL→TR→BR→BL yields positive shoelace.
    if wind != 1:
        reasons.append("corner_order_not_tl_tr_br_bl")
    area = polygon_area(pts)
    frame_area = max(frame_width * frame_height, 1e-12)
    if area < min_area_fraction * frame_area:
        reasons.append("area_below_minimum")
    min_edge = min_edge_fraction * min(frame_width, frame_height)
    for i in range(4):
        a = pts[i]
        b = pts[(i + 1) % 4]
        edge = ((b.x - a.x) ** 2 + (b.y - a.y) ** 2) ** 0.5
        if edge < min_edge:
            reasons.append("edge_too_short")
            break
    winding_label = {1: "ccw", -1: "cw", 0: "degenerate"}[wind]
    return ValidationResult(
        ok=len(reasons) == 0,
        reasons=tuple(reasons),
        area=area,
        winding=winding_label,
    )


def _solve_linear(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting; square system."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-12:
            raise ValueError("singular_homography")
        if pivot != col:
            m[col], m[pivot] = m[pivot], m[col]
        div = m[col][col]
        for j in range(col, n + 1):
            m[col][j] /= div
        for row in range(n):
            if row == col:
                continue
            factor = m[row][col]
            for j in range(col, n + 1):
                m[row][j] -= factor * m[col][j]
    return [m[i][n] for i in range(n)]


def compute_homography(
    src: Sequence[Point],
    dst: Sequence[Point],
) -> list[list[float]]:
    """3x3 perspective matrix mapping src -> dst (h33 fixed to 1)."""
    if len(src) != 4 or len(dst) != 4:
        raise ValueError("exactly_four_point_pairs_required")
    a: list[list[float]] = []
    b: list[float] = []
    for (x, y), (u, v) in zip((p.as_tuple() for p in src), (p.as_tuple() for p in dst)):
        a.append([x, y, 1.0, 0.0, 0.0, 0.0, -x * u, -y * u])
        b.append(u)
        a.append([0.0, 0.0, 0.0, x, y, 1.0, -x * v, -y * v])
        b.append(v)
    c = _solve_linear(a, b)
    return [
        [c[0], c[1], c[2]],
        [c[3], c[4], c[5]],
        [c[6], c[7], 1.0],
    ]


def unit_board_dst(width: float = 1.0, height: float = 1.0) -> tuple[Point, Point, Point, Point]:
    return (
        Point(0.0, 0.0),
        Point(width, 0.0),
        Point(width, height),
        Point(0.0, height),
    )


def board_homography(corners: CornerSet, *, board_w: float = 1.0, board_h: float = 1.0) -> list[list[float]]:
    return compute_homography(corners.ordered(), unit_board_dst(board_w, board_h))


def apply_homography(h: Sequence[Sequence[float]], p: Point) -> Point:
    x = h[0][0] * p.x + h[0][1] * p.y + h[0][2]
    y = h[1][0] * p.x + h[1][1] * p.y + h[1][2]
    w = h[2][0] * p.x + h[2][1] * p.y + h[2][2]
    if abs(w) < 1e-12:
        raise ValueError("homography_w_zero")
    return Point(x / w, y / w)


def invert_homography(h: Sequence[Sequence[float]]) -> list[list[float]]:
    a, b, c = h[0]
    d, e, f = h[1]
    g, hh, i = h[2]
    det = (
        a * (e * i - f * hh)
        - b * (d * i - f * g)
        + c * (d * hh - e * g)
    )
    if abs(det) < 1e-12:
        raise ValueError("homography_not_invertible")
    inv = [
        [
            (e * i - f * hh) / det,
            (c * hh - b * i) / det,
            (b * f - c * e) / det,
        ],
        [
            (f * g - d * i) / det,
            (a * i - c * g) / det,
            (c * d - a * f) / det,
        ],
        [
            (d * hh - e * g) / det,
            (b * g - a * hh) / det,
            (a * e - b * d) / det,
        ],
    ]
    scale = inv[2][2]
    if abs(scale) < 1e-12:
        raise ValueError("homography_scale_zero")
    return [[cell / scale for cell in row] for row in inv]


def normalize_point(p: Point, *, width: float, height: float) -> Point:
    if width <= 0 or height <= 0:
        raise ValueError("frame_size_invalid")
    return Point(p.x / width, p.y / height)


def denormalize_point(p: Point, *, width: float, height: float) -> Point:
    return Point(p.x * width, p.y * height)


def normalize_corners(corners: CornerSet, *, width: float, height: float) -> CornerSet:
    return CornerSet(
        tl=normalize_point(corners.tl, width=width, height=height),
        tr=normalize_point(corners.tr, width=width, height=height),
        br=normalize_point(corners.br, width=width, height=height),
        bl=normalize_point(corners.bl, width=width, height=height),
    )


def denormalize_corners(corners: CornerSet, *, width: float, height: float) -> CornerSet:
    return CornerSet(
        tl=denormalize_point(corners.tl, width=width, height=height),
        tr=denormalize_point(corners.tr, width=width, height=height),
        br=denormalize_point(corners.br, width=width, height=height),
        bl=denormalize_point(corners.bl, width=width, height=height),
    )


def rotate_normalized(p: Point, degrees: int) -> Point:
    """Rotate normalized [0,1] point by 0/90/180/270 about frame center."""
    d = degrees % 360
    if d == 0:
        return p
    if d == 90:
        return Point(1.0 - p.y, p.x)
    if d == 180:
        return Point(1.0 - p.x, 1.0 - p.y)
    if d == 270:
        return Point(p.y, 1.0 - p.x)
    raise ValueError("rotation_must_be_multiple_of_90")


def map_normalized_corners_to_display(
    normalized: CornerSet,
    *,
    display_width: float,
    display_height: float,
    rotation_degrees: int = 0,
) -> CornerSet:
    """AC-4.6: re-project freeze-normalized corners into current display pixels."""
    rotated = CornerSet(
        tl=rotate_normalized(normalized.tl, rotation_degrees),
        tr=rotate_normalized(normalized.tr, rotation_degrees),
        br=rotate_normalized(normalized.br, rotation_degrees),
        bl=rotate_normalized(normalized.bl, rotation_degrees),
    )
    return denormalize_corners(
        rotated,
        width=display_width,
        height=display_height,
    )


def rectified_preview_size(
    corners: CornerSet,
    *,
    max_side: int = 640,
) -> tuple[int, int]:
    width = (
        ((corners.tr.x - corners.tl.x) ** 2 + (corners.tr.y - corners.tl.y) ** 2) ** 0.5
        + ((corners.br.x - corners.bl.x) ** 2 + (corners.br.y - corners.bl.y) ** 2) ** 0.5
    ) * 0.5
    height = (
        ((corners.bl.x - corners.tl.x) ** 2 + (corners.bl.y - corners.tl.y) ** 2) ** 0.5
        + ((corners.br.x - corners.tr.x) ** 2 + (corners.br.y - corners.tr.y) ** 2) ** 0.5
    ) * 0.5
    width = max(width, 1.0)
    height = max(height, 1.0)
    scale = max_side / max(width, height)
    return (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
