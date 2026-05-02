"""Floor-plan PNG/JPG → obstacle polygons.

Three parser modes:

  mode='auto'  : (DEFAULT) per-contour smart dispatch — solid filled
                 shapes (circle, diamond, triangle) keep their single
                 minAreaRect, while sparse / crossed contours (X-shapes,
                 plus signs, T-intersections) get re-extracted with
                 Hough line clustering. Combines the strengths of cv +
                 hough without their respective failure modes.

  mode='cv'    : Otsu → findContours → minAreaRect per connected region.
                 Fast, perfect for vector-style plans where every shape is
                 already its own connected blob. Fails on touching shapes
                 (e.g. an X = two crossing bars get merged into ONE rect).

  mode='hough' : Otsu → Canny → HoughLinesP → cluster line segments by
                 (angle, perpendicular offset) → one rect per cluster.
                 Recovers individual bars in X-shapes and crossing walls,
                 but over-segments closed polygon outlines (a diamond
                 outline becomes 4 separate walls).

Pipeline shared by all three:
  1. Decode bytes (OpenCV).
  2. Otsu-threshold to binary: dark = wall / obstacle, light = floor.
  3. (mode-specific extraction; see the two functions below.)
  4. Drop the largest detected region as the outer wall outline when
     treat_largest_as_boundary is True (cell envelope already encodes it).
  5. Classify by aspect ratio:
       max(w,h) / min(w,h) > WALL_ASPECT  → 'wall'   (long thin rectangle)
       otherwise                          → 'obstacle' (compact)
  6. Map pixel coords → mm using floor_w_m / floor_h_m supplied by the
     caller. Pixel origin is top-left, y-down → world origin is bottom-
     left, y-up (matches the rest of the codebase, CLAUDE.md convention).
  7. Emit each rect as a 4-corner closed polygon so the result plugs into
     the existing /api/cad obstacle pipeline (polygon-vs-rect intersection,
     SA gradient, CP-SAT constraint) for free.

Hook left for future:
  - mode='hybrid' : OpenCV for geometry, vision LLM for semantic labels
                    (wall vs column vs equipment vs door). Raises
                    NotImplementedError today; signature is stable so the
                    endpoint won't change when it's added.
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass, field
from typing import Literal

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Tuning constants — see module docstring for what each one controls.
WALL_ASPECT = 8.0          # max(w,h) / min(w,h) above which a rect is a "wall"
MIN_AREA_MM2 = 50_000.0    # 0.05 m² — drops dust / printed text / scale bars
EPSILON_DOUGLAS = 0.005    # cv2.approxPolyDP epsilon as fraction of perimeter

ParseMode = Literal["auto", "cv", "hough", "llm", "hybrid"]

# 'auto' mode tuning: per-contour solidity = contourArea / minAreaRect_area.
# Solid filled shapes (rect / circle / diamond / triangle) sit > ~0.7;
# sparse line-art (X / + / single bar / T) sits < ~0.4. Between we default
# to the cv path so we don't over-segment ambiguous blobs.
AUTO_SOLIDITY_FILLED = 0.62  # >= this → keep as a single minAreaRect
AUTO_SOLIDITY_SPARSE = 0.38  # <= this → recurse into Hough on this contour

# Hough tuning — works well on the bundled vector PNGs at ~2k px square.
# Scale-invariant within a factor of 2; tune if you push very different sizes.
HOUGH_CANNY_LOW = 60
HOUGH_CANNY_HIGH = 180
HOUGH_THRESHOLD = 80         # min votes to call a Hough peak a line
HOUGH_MIN_LINE_PX_FRAC = 0.04  # minLineLength = 4% of image diagonal
HOUGH_MAX_GAP_PX_FRAC = 0.01   # maxLineGap = 1% of diagonal
HOUGH_ANGLE_BIN_DEG = 5.0    # cluster lines whose angles differ by < this
HOUGH_OFFSET_BIN_PX = 25     # ... AND perpendicular offset differ by < this
HOUGH_MIN_TOTAL_LEN_FRAC = 0.03  # drop clusters shorter than 3% of diagonal


@dataclass(frozen=True)
class FloorPlanRect:
    """One detected rectangle, classified."""

    id: str
    kind: Literal["wall", "obstacle"]
    polygon: list[list[float]]   # 5 points (closed: last == first), mm
    cx_mm: float
    cy_mm: float
    width_mm: float
    depth_mm: float
    yaw_deg: float
    area_mm2: float
    source: str = "image_cv"     # 'image_cv' | 'image_llm'


@dataclass
class ImageParseResult:
    rects: list[FloorPlanRect] = field(default_factory=list)
    bounding_box_mm: tuple[float, float, float, float] | None = None
    suggested_cell_envelope_mm: tuple[float, float] | None = None
    image_size_px: tuple[int, int] = (0, 0)
    floor_size_mm: tuple[float, float] = (0.0, 0.0)
    n_walls: int = 0
    n_obstacles: int = 0
    n_skipped: int = 0
    mode: str = "cv"


def parse_image(
    image_bytes: bytes,
    floor_w_m: float,
    floor_h_m: float,
    *,
    mode: ParseMode = "auto",
    treat_largest_as_boundary: bool = True,
    min_area_mm2: float = MIN_AREA_MM2,
    wall_aspect: float = WALL_ASPECT,
    margin_mm: float = 200.0,
) -> ImageParseResult:
    """Detect walls + obstacles from a top-down floor plan image.

    Args:
        image_bytes: PNG / JPG bytes (anything OpenCV can decode).
        floor_w_m, floor_h_m: real-world size the image represents.
        mode: 'cv' = OpenCV only (today). 'llm' / 'hybrid' raise
              NotImplementedError; the signature is stable so the endpoint
              won't change when they're added.
        treat_largest_as_boundary: if True, the largest dark region is
              assumed to be the outer wall outline and dropped from the
              obstacle list (the cell envelope already encodes it).
        min_area_mm2: smaller dark blobs are ignored as noise.
        wall_aspect: above this length-to-width ratio, a rect is labelled
              'wall' instead of 'obstacle'.
        margin_mm: shift world coords so the smallest (x, y) sits at
              (margin_mm, margin_mm), matching the DXF importer.
    """
    if mode in ("llm", "hybrid"):
        raise NotImplementedError(
            f"image parse mode '{mode}' is reserved; use 'auto', 'cv' or "
            f"'hough'. The 'hybrid' branch will route OpenCV-detected "
            f"rects through a vision LLM for semantic labels "
            f"(wall/column/equipment/door)."
        )
    if mode not in ("auto", "cv", "hough"):
        raise ValueError(
            f"unknown mode {mode!r}; expected one of auto/cv/hough/llm/hybrid"
        )
    if floor_w_m <= 0 or floor_h_m <= 0:
        raise ValueError("floor_w_m and floor_h_m must be positive metres.")

    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError("Could not decode image (corrupt or unsupported format).")
    h_px, w_px = img.shape[:2]

    # Otsu — picks the threshold automatically. We then INVERT so dark
    # walls/obstacles become 255 (foreground for findContours / Hough).
    _, binary = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # Light morphological close to bridge 1-2px gaps in dashed lines /
    # antialiased edges so each wall comes back as a single contour.
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=1)

    mm_per_px_x = (floor_w_m * 1000.0) / w_px
    mm_per_px_y = (floor_h_m * 1000.0) / h_px

    # Pass 1: pixel-space rects depend on the mode. Both modes hand back
    # the same shape: list of ((cx_px, cy_px), (w_mm, h_mm), angle_deg, area_mm2).
    if mode == "cv":
        raw = _extract_rects_minarea(binary, mm_per_px_x, mm_per_px_y)
    elif mode == "hough":
        raw = _extract_rects_hough(binary, mm_per_px_x, mm_per_px_y, w_px, h_px)
    else:  # 'auto'
        raw = _extract_rects_auto(binary, mm_per_px_x, mm_per_px_y, w_px, h_px)

    # Drop the largest (outer wall outline) when requested — same convention
    # as the DXF importer.
    if treat_largest_as_boundary and raw:
        idx_largest = max(range(len(raw)), key=lambda i: raw[i][3])
        boundary_drop = raw.pop(idx_largest)
        logger.info(
            "Dropped largest contour (boundary): area=%.1f mm²", boundary_drop[3]
        )

    rects: list[FloorPlanRect] = []
    n_skipped = 0
    for (cx_px, cy_px), (w_mm, h_mm), angle, area_mm2 in raw:
        if area_mm2 < min_area_mm2:
            n_skipped += 1
            continue
        # Pixel origin = top-left, y-down. World origin = bottom-left, y-up.
        cx_mm = cx_px * mm_per_px_x + margin_mm
        cy_mm = (h_px - cy_px) * mm_per_px_y + margin_mm
        # In OpenCV, the (w, h) returned for a minAreaRect is in the
        # rect's local frame; we just keep (longer, shorter).
        long_side = max(w_mm, h_mm)
        short_side = min(w_mm, h_mm)
        aspect = long_side / max(1e-3, short_side)
        kind: Literal["wall", "obstacle"] = "wall" if aspect > wall_aspect else "obstacle"
        polygon = _rotated_rect_polygon(cx_mm, cy_mm, w_mm, h_mm, angle)
        rects.append(
            FloorPlanRect(
                id=f"img_{kind}_{uuid.uuid4().hex[:6]}",
                kind=kind,
                polygon=polygon,
                cx_mm=cx_mm,
                cy_mm=cy_mm,
                width_mm=w_mm,
                depth_mm=h_mm,
                yaw_deg=float(angle),
                area_mm2=area_mm2,
            )
        )

    # Bounding box across kept rects (in world mm).
    bbox = None
    suggested = None
    if rects:
        xs = [p[0] for r in rects for p in r.polygon]
        ys = [p[1] for r in rects for p in r.polygon]
        bbox = (min(xs), min(ys), max(xs), max(ys))
        suggested = (
            (bbox[2] - bbox[0]) * 1.05,
            (bbox[3] - bbox[1]) * 1.05,
        )
    else:
        # Caller still wants the floor envelope they paid for.
        suggested = (floor_w_m * 1000.0, floor_h_m * 1000.0)

    n_walls = sum(1 for r in rects if r.kind == "wall")
    n_obstacles = sum(1 for r in rects if r.kind == "obstacle")
    return ImageParseResult(
        rects=rects,
        bounding_box_mm=bbox,
        suggested_cell_envelope_mm=suggested,
        image_size_px=(w_px, h_px),
        floor_size_mm=(floor_w_m * 1000.0, floor_h_m * 1000.0),
        n_walls=n_walls,
        n_obstacles=n_obstacles,
        n_skipped=n_skipped,
        mode=mode,
    )


def _extract_rects_minarea(
    binary: np.ndarray, mm_per_px_x: float, mm_per_px_y: float,
) -> list[tuple[tuple[float, float], tuple[float, float], float, float]]:
    """Original CV mode: one minAreaRect per connected component."""
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out: list[tuple[tuple[float, float], tuple[float, float], float, float]] = []
    for c in contours:
        rect = cv2.minAreaRect(c)
        (cx_px, cy_px), (w_rot_px, h_rot_px), angle = rect
        if w_rot_px <= 0 or h_rot_px <= 0:
            continue
        w_mm = w_rot_px * mm_per_px_x
        h_mm = h_rot_px * mm_per_px_y
        out.append(((cx_px, cy_px), (w_mm, h_mm), angle, w_mm * h_mm))
    return out


def _extract_rects_hough(
    binary: np.ndarray,
    mm_per_px_x: float,
    mm_per_px_y: float,
    w_px: int,
    h_px: int,
) -> list[tuple[tuple[float, float], tuple[float, float], float, float]]:
    """Hough mode: detect line segments, cluster by (angle, perpendicular
    offset), emit one oriented rect per cluster.

    Recovers individual bars in X-shapes / crossing walls that the cv mode
    merges into a single bbox. Falls back to plain edge contours for any
    *compact* (non-line) shapes — circles, diamonds, triangles — by
    overlaying their minAreaRects on top, since Hough is line-only.
    """
    diag_px = math.hypot(w_px, h_px)
    min_line_px = max(20, int(HOUGH_MIN_LINE_PX_FRAC * diag_px))
    max_gap_px = max(4, int(HOUGH_MAX_GAP_PX_FRAC * diag_px))

    # 1) Detect line segments.
    edges = cv2.Canny(binary, HOUGH_CANNY_LOW, HOUGH_CANNY_HIGH)
    lines = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=math.pi / 360.0,
        threshold=HOUGH_THRESHOLD,
        minLineLength=min_line_px,
        maxLineGap=max_gap_px,
    )
    segments: list[tuple[float, float, float, float, float, float, float]] = []
    if lines is not None:
        for ln in lines:
            x1, y1, x2, y2 = (float(v) for v in ln[0])
            length = math.hypot(x2 - x1, y2 - y1)
            if length < min_line_px:
                continue
            # angle in [0, 180); use atan2 then mod
            angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
            angle %= 180.0
            mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
            # Perpendicular offset of the segment from origin in image frame.
            theta = math.radians(angle)
            offset = -math.sin(theta) * mx + math.cos(theta) * my
            segments.append((x1, y1, x2, y2, length, angle, offset))

    # 2) Cluster by (angle, offset). We don't use sklearn — small N, fine
    # to do an O(n²) union-find by tolerance.
    n = len(segments)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        a, b = find(i), find(j)
        if a != b:
            parent[a] = b

    for i in range(n):
        _, _, _, _, _, ai, oi = segments[i]
        for j in range(i + 1, n):
            _, _, _, _, _, aj, oj = segments[j]
            d_angle = min(abs(ai - aj), 180.0 - abs(ai - aj))
            if d_angle > HOUGH_ANGLE_BIN_DEG:
                continue
            if abs(oi - oj) > HOUGH_OFFSET_BIN_PX:
                continue
            union(i, j)

    clusters: dict[int, list[int]] = {}
    for i in range(n):
        clusters.setdefault(find(i), []).append(i)

    # 3) For each cluster, fit one oriented rect.
    out: list[tuple[tuple[float, float], tuple[float, float], float, float]] = []
    min_total_len_px = HOUGH_MIN_TOTAL_LEN_FRAC * diag_px
    consumed_mask = np.zeros_like(binary)
    for ids in clusters.values():
        # Mean angle of the cluster (weight by segment length).
        total_len = sum(segments[i][4] for i in ids)
        if total_len < min_total_len_px:
            continue
        angle_mean = sum(segments[i][5] * segments[i][4] for i in ids) / total_len
        theta = math.radians(angle_mean)
        # Project all endpoints onto the line direction + perpendicular,
        # take min/max → length and thickness.
        u_dir = np.array([math.cos(theta), math.sin(theta)])
        v_perp = np.array([-math.sin(theta), math.cos(theta)])
        pts = np.array(
            [(segments[i][0], segments[i][1]) for i in ids]
            + [(segments[i][2], segments[i][3]) for i in ids],
            dtype=np.float64,
        )
        u = pts @ u_dir
        v = pts @ v_perp
        u_min, u_max = float(u.min()), float(u.max())
        v_min, v_max = float(v.min()), float(v.max())
        # Thickness: clamp to a sane minimum (Hough lines are 1-px wide;
        # the underlying wall is usually thicker — sample its actual
        # width by walking ⊥ from the segment midpoint into the binary).
        thickness_px = max(v_max - v_min, _sample_thickness_px(binary, segments[ids[0]]))
        length_px = u_max - u_min
        if length_px <= 0 or thickness_px <= 0:
            continue
        cx_uv = ((u_min + u_max) / 2.0, (v_min + v_max) / 2.0)
        cx_px = cx_uv[0] * u_dir[0] + cx_uv[1] * v_perp[0]
        cy_px = cx_uv[0] * u_dir[1] + cx_uv[1] * v_perp[1]
        w_mm = length_px * mm_per_px_x
        h_mm = thickness_px * mm_per_px_y
        # Mark this rect's pixels as consumed so the compact-shape fallback
        # below doesn't double-count walls.
        box_pts = cv2.boxPoints((
            (cx_px, cy_px),
            (length_px, thickness_px),
            angle_mean,
        )).astype(np.int32)
        cv2.fillPoly(consumed_mask, [box_pts], 255)
        out.append((
            (cx_px, cy_px),
            (w_mm, h_mm),
            angle_mean - 90.0,  # match minAreaRect's convention (angle of shorter edge)
            w_mm * h_mm,
        ))

    # 4) Compact shapes (circles, diamonds, triangles) won't show up as
    # Hough lines. Run findContours on the leftover binary and add their
    # minAreaRects (filtered to the OBSTACLE class — anything line-like
    # that survived this filter is unlikely).
    leftover = cv2.bitwise_and(binary, cv2.bitwise_not(consumed_mask))
    leftover = cv2.morphologyEx(
        leftover, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)),
    )
    for c in cv2.findContours(leftover, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]:
        rect = cv2.minAreaRect(c)
        (cx_px, cy_px), (w_rot_px, h_rot_px), angle = rect
        if w_rot_px <= 0 or h_rot_px <= 0:
            continue
        w_mm = w_rot_px * mm_per_px_x
        h_mm = h_rot_px * mm_per_px_y
        out.append(((cx_px, cy_px), (w_mm, h_mm), angle, w_mm * h_mm))

    return out


def _extract_rects_auto(
    binary: np.ndarray,
    mm_per_px_x: float,
    mm_per_px_y: float,
    w_px: int,
    h_px: int,
) -> list[tuple[tuple[float, float], tuple[float, float], float, float]]:
    """Per-contour smart dispatch: solid filled shapes keep one minAreaRect,
    sparse / crossed contours get re-extracted with Hough. Avoids
    over-segmenting closed polygon outlines (the failure mode of pure
    'hough' mode) while still splitting X-shapes (the failure mode of
    pure 'cv' mode)."""
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out: list[tuple[tuple[float, float], tuple[float, float], float, float]] = []
    for c in contours:
        rect = cv2.minAreaRect(c)
        (cx_px, cy_px), (w_rot_px, h_rot_px), angle = rect
        if w_rot_px <= 0 or h_rot_px <= 0:
            continue
        bbox_area = w_rot_px * h_rot_px
        contour_area = cv2.contourArea(c)
        solidity = contour_area / bbox_area if bbox_area > 0 else 0.0

        if solidity >= AUTO_SOLIDITY_FILLED:
            # Filled / nearly-rectangular shape (rect, circle, diamond,
            # triangle, single thick bar): keep as one rect.
            w_mm = w_rot_px * mm_per_px_x
            h_mm = h_rot_px * mm_per_px_y
            out.append(((cx_px, cy_px), (w_mm, h_mm), angle, w_mm * h_mm))
        elif solidity <= AUTO_SOLIDITY_SPARSE:
            # Sparse / crossed (X-shape, plus-sign, T-junction): re-run
            # Hough on JUST this contour's pixels.
            mask = np.zeros_like(binary)
            cv2.drawContours(mask, [c], -1, 255, thickness=cv2.FILLED)
            contour_only = cv2.bitwise_and(binary, mask)
            sub = _extract_rects_hough(contour_only, mm_per_px_x, mm_per_px_y, w_px, h_px)
            if sub:
                out.extend(sub)
            else:
                # Hough found nothing — keep the original bbox so we don't
                # silently lose the contour.
                w_mm = w_rot_px * mm_per_px_x
                h_mm = h_rot_px * mm_per_px_y
                out.append(((cx_px, cy_px), (w_mm, h_mm), angle, w_mm * h_mm))
        else:
            # Ambiguous (~0.4 - 0.6): default to cv path. Includes most
            # L/U-shapes and partially-filled rects where Hough would
            # over-segment but cv keeps it as one obstacle.
            w_mm = w_rot_px * mm_per_px_x
            h_mm = h_rot_px * mm_per_px_y
            out.append(((cx_px, cy_px), (w_mm, h_mm), angle, w_mm * h_mm))
    return out


def _sample_thickness_px(
    binary: np.ndarray,
    segment: tuple[float, float, float, float, float, float, float],
) -> float:
    """Probe the binary image perpendicular to a segment's midpoint to
    estimate its actual on-pixel thickness — Hough returns 1-px lines but
    the underlying walls are usually 5-30 px wide."""
    x1, y1, x2, y2, _length, angle, _ = segment
    h_px, w_px = binary.shape[:2]
    mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    theta = math.radians(angle)
    nx, ny = -math.sin(theta), math.cos(theta)
    # Walk ±max_probe in both directions, count consecutive on pixels.
    max_probe = 60
    count = 1
    for sign in (1, -1):
        for k in range(1, max_probe):
            x = int(round(mx + sign * k * nx))
            y = int(round(my + sign * k * ny))
            if 0 <= x < w_px and 0 <= y < h_px and binary[y, x] > 0:
                count += 1
            else:
                break
    return float(count)


def _rotated_rect_polygon(
    cx_mm: float, cy_mm: float, w_mm: float, h_mm: float, angle_deg: float,
) -> list[list[float]]:
    """4-corner closed polygon for a rotated rect, world coords (mm)."""
    angle = math.radians(angle_deg)
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    hw = w_mm / 2.0
    hh = h_mm / 2.0
    corners_local = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
    out: list[list[float]] = []
    for lx, ly in corners_local:
        wx = cx_mm + lx * cos_a - ly * sin_a
        wy = cy_mm + lx * sin_a + ly * cos_a
        out.append([wx, wy])
    out.append(out[0])  # closed
    return out
