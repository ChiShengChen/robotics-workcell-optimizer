"""Floor-plan PNG/JPG → obstacle polygons.

Pipeline (mode='cv', the only one implemented today):
  1. Decode the image bytes (OpenCV).
  2. Otsu-threshold to a binary mask: dark = wall / obstacle, light = floor.
  3. cv2.findContours to extract every dark connected region.
  4. cv2.minAreaRect on each contour → rotated bounding rect.
  5. Filter out (a) the outer wall (largest area) when treat_largest_as_boundary
     is True, (b) noise contours below min_area_mm2.
  6. Classify the remainder by aspect ratio:
       max(w,h) / min(w,h) > WALL_ASPECT  → 'wall'   (long thin rectangle)
       otherwise                          → 'obstacle' (compact)
  7. Map pixel coords → mm using floor_w_m / floor_h_m supplied by the caller.
     Pixel-space origin is top-left, y-down → world origin is bottom-left,
     y-up to match the rest of the codebase (CLAUDE.md convention).
  8. Emit each rect as a 4-corner closed polygon so the result plugs into the
     existing /api/cad obstacle pipeline (polygon-vs-rect intersection,
     SA gradient, CP-SAT constraint) for free.

Hooks left for future:
  - mode='llm'     : send the cropped thumbnail to a vision LLM and ask it
                     to label each rectangle (wall / column / equipment / door).
  - mode='hybrid'  : OpenCV for geometry (precise), LLM for semantic labels.

Both branches raise NotImplementedError today; wiring is in place so the
endpoint signature won't change when they're added.
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

ParseMode = Literal["cv", "llm", "hybrid"]


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
    mode: ParseMode = "cv",
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
    if mode != "cv":
        raise NotImplementedError(
            f"image parse mode '{mode}' is reserved; use 'cv'. The 'llm' / "
            f"'hybrid' branches will route through services.llm to label "
            f"OpenCV-detected rects with semantic kinds."
        )
    if floor_w_m <= 0 or floor_h_m <= 0:
        raise ValueError("floor_w_m and floor_h_m must be positive metres.")

    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError("Could not decode image (corrupt or unsupported format).")
    h_px, w_px = img.shape[:2]

    # Otsu — picks the threshold automatically. We then INVERT so dark
    # walls/obstacles become 255 (foreground for findContours).
    _, binary = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # Light morphological close to bridge 1-2px gaps in dashed lines /
    # antialiased edges so each wall comes back as a single contour.
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=1)

    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    mm_per_px_x = (floor_w_m * 1000.0) / w_px
    mm_per_px_y = (floor_h_m * 1000.0) / h_px

    # Pass 1: rotated bounding rects + areas (still in pixel space).
    raw: list[tuple[tuple[float, float], tuple[float, float], float, float]] = []
    for c in contours:
        rect = cv2.minAreaRect(c)  # ((cx, cy), (w, h), angle)
        (cx_px, cy_px), (w_rot_px, h_rot_px), angle = rect
        if w_rot_px <= 0 or h_rot_px <= 0:
            continue
        # Convert to mm BEFORE area check so the user-facing threshold is
        # in mm² regardless of image resolution.
        w_mm = w_rot_px * mm_per_px_x
        h_mm = h_rot_px * mm_per_px_y
        area_mm2 = w_mm * h_mm
        raw.append(((cx_px, cy_px), (w_mm, h_mm), angle, area_mm2))

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
