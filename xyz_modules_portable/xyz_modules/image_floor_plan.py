"""Floor-plan PNG/JPG → obstacle polygons.

Four parser modes:

  mode='cv'     : (DEFAULT) Otsu → findContours → minAreaRect per
                  connected region. Fast, near-perfect for vector-style
                  plans where every shape is already its own connected
                  blob. Empirically the best mode for clean rendered
                  floor plans (CAD exports, Konva-style PNGs). Only
                  fails when two physical objects literally share
                  pixels — e.g. an X = two crossing bars that touch in
                  the binary mask, which findContours then sees as a
                  single contour. Use 'hough' or 'hybrid' for those.

  mode='auto'   : Per-contour smart dispatch — solid filled shapes
                  (circle, diamond, triangle) keep their single
                  minAreaRect, while sparse / crossed contours
                  (X-shapes, plus signs, T-intersections) get
                  re-extracted with Hough line clustering. Pure CV.
                  Helps only when shapes physically merge; otherwise
                  matches or trails 'cv'.

  mode='hough'  : Otsu → Canny → HoughLinesP → cluster line segments
                  by (angle, perpendicular offset). Recovers individual
                  bars in X-shapes but over-segments closed polygon
                  outlines (a diamond outline → 4 walls).

  mode='hybrid' : OpenCV for geometry + Gemini Vision for semantic
                  judgement. Sends the image AND the cv-mode candidate
                  rects to the vision LLM, which then keeps / drops /
                  splits / re-classifies them. Falls back to 'auto' if
                  no GOOGLE_API_KEY is configured. Best results for
                  ambiguous floor plans (handwritten, scanned, with
                  text labels, mixed equipment types) where pure CV
                  can't tell a decorative outline from a real wall.

Pipeline shared by cv / hough / auto:
  1. Decode bytes (OpenCV).
  2. Otsu-threshold to binary: dark = wall / obstacle, light = floor.
  3. (mode-specific extraction; see the four functions below.)
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

Hybrid mode bypasses steps 4-5 (the LLM directly emits final classified
rects) but still runs steps 6-7 to map into the world frame and emit
polygons.
"""

from __future__ import annotations

import json
import logging
import math
import os
import uuid
from dataclasses import dataclass, field
from typing import Literal

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Gemini Vision config for hybrid mode.
HYBRID_GEMINI_MODEL = "gemini-2.5-flash"
HYBRID_MAX_OUTPUT_TOKENS = 16384

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
    if mode == "llm":
        raise NotImplementedError(
            f"image parse mode 'llm' (LLM-only, no CV pre-pass) is "
            f"reserved; use 'hybrid' to combine CV geometry with LLM "
            f"semantic judgement, or 'auto' / 'cv' / 'hough' for pure CV."
        )
    if mode not in ("auto", "cv", "hough", "hybrid"):
        raise ValueError(
            f"unknown mode {mode!r}; expected one of auto/cv/hough/hybrid/llm"
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

    # Hybrid skips the aspect-ratio classifier — the LLM already classified
    # each rect — so it returns 5-tuples carrying the kind override. Other
    # modes return 4-tuples.
    if mode == "cv":
        raw = _extract_rects_minarea(binary, mm_per_px_x, mm_per_px_y)
    elif mode == "hough":
        raw = _extract_rects_hough(binary, mm_per_px_x, mm_per_px_y, w_px, h_px)
    elif mode == "hybrid":
        cv_seed = _extract_rects_minarea(binary, mm_per_px_x, mm_per_px_y)
        raw = _extract_rects_hybrid(
            image_bytes, cv_seed, mm_per_px_x, mm_per_px_y,
            w_px, h_px, floor_w_m, floor_h_m,
        )
        if raw is None:
            # LLM unavailable or refused — fall back to auto so the user
            # still gets *something* without an opaque error.
            logger.warning("hybrid mode falling back to 'auto' (no LLM key or LLM failed)")
            raw = _extract_rects_auto(binary, mm_per_px_x, mm_per_px_y, w_px, h_px)
            mode = "auto"  # for the result.mode field
    else:  # 'auto'
        raw = _extract_rects_auto(binary, mm_per_px_x, mm_per_px_y, w_px, h_px)

    # Drop the largest (outer wall outline) when requested — same convention
    # as the DXF importer.
    # treat_largest_as_boundary applies only to pure-CV modes — the LLM
    # is told to skip the outer frame itself.
    #
    # We only drop the largest contour if its bbox covers most of the image
    # AND it's a SOLID filled blob — the classic "outer wall as one filled
    # rect" case. We do NOT drop it when it's a thin outline (e.g. four
    # perimeter wall segments that look like one big sparse contour to
    # findContours after morph-close bridges the corners), because that
    # outline IS made of real walls the layout needs to honour.
    if treat_largest_as_boundary and raw and mode != "hybrid":
        idx_largest = max(range(len(raw)), key=lambda i: raw[i][3])
        cx_px, cy_px = raw[idx_largest][0]
        w_mm, h_mm = raw[idx_largest][1]
        # bbox-area in pixels² for solidity check.
        w_px_box = w_mm / mm_per_px_x
        h_px_box = h_mm / mm_per_px_y
        bbox_area_px = w_px_box * h_px_box
        # Re-find the contour to compute its filled area (we don't carry
        # the contour through the tuple). Cheap: same binary, RETR_EXTERNAL.
        contours, _ = cv2.findContours(
            binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE,
        )
        # Match by centroid proximity — minAreaRect centers are stable.
        best_c, best_d = None, math.inf
        for c in contours:
            M = cv2.moments(c)
            if M["m00"] == 0:
                continue
            ccx = M["m10"] / M["m00"]
            ccy = M["m01"] / M["m00"]
            d = math.hypot(ccx - cx_px, ccy - cy_px)
            if d < best_d:
                best_d, best_c = d, c
        contour_area_px = cv2.contourArea(best_c) if best_c is not None else 0
        solidity = contour_area_px / bbox_area_px if bbox_area_px > 0 else 0.0
        # Bbox covers >= 90% of image AND it's a solid blob (>=0.5 fill)?
        covers_image = (
            w_px_box >= 0.9 * w_px and h_px_box >= 0.9 * h_px
        )
        if solidity >= 0.5 and covers_image:
            boundary_drop = raw.pop(idx_largest)
            logger.info(
                "Dropped largest contour (solid boundary): area=%.1f mm² solidity=%.2f",
                boundary_drop[3], solidity,
            )
        else:
            logger.info(
                "Kept largest contour (sparse outline): area=%.1f mm² solidity=%.2f",
                raw[idx_largest][3], solidity,
            )

    rects: list[FloorPlanRect] = []
    n_skipped = 0
    for entry in raw:
        # Tuples are 4 (cv/hough/auto) or 5 with a kind override (hybrid).
        if len(entry) == 5:
            (cx_px, cy_px), (w_mm, h_mm), angle, area_mm2, kind_override = entry
        else:
            (cx_px, cy_px), (w_mm, h_mm), angle, area_mm2 = entry
            kind_override = None
        if area_mm2 < min_area_mm2:
            n_skipped += 1
            continue
        # Pixel origin = top-left, y-down. World origin = bottom-left, y-up.
        cx_mm = cx_px * mm_per_px_x + margin_mm
        cy_mm = (h_px - cy_px) * mm_per_px_y + margin_mm
        if kind_override in ("wall", "obstacle"):
            kind: Literal["wall", "obstacle"] = kind_override
        else:
            long_side = max(w_mm, h_mm)
            short_side = min(w_mm, h_mm)
            aspect = long_side / max(1e-3, short_side)
            kind = "wall" if aspect > wall_aspect else "obstacle"
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
                source="image_hybrid" if mode == "hybrid" else "image_cv",
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


def _extract_rects_hybrid(
    image_bytes: bytes,
    cv_seed: list[tuple[tuple[float, float], tuple[float, float], float, float]],
    mm_per_px_x: float,
    mm_per_px_y: float,
    w_px: int,
    h_px: int,
    floor_w_m: float,
    floor_h_m: float,
) -> list[tuple[tuple[float, float], tuple[float, float], float, float, str]] | None:
    """Combine OpenCV geometry with Gemini Vision semantic judgement.

    Sends the original image AND the cv-mode candidate rects to Gemini.
    The model is asked to keep / drop / split / re-classify the candidates
    AND optionally add anything OpenCV missed. Returns 5-tuples carrying
    (center_px, size_mm, angle_deg, area_mm2, kind_override) so the
    caller skips the aspect-ratio classifier.

    Returns None when GOOGLE_API_KEY is missing or the LLM call / parse
    fails — caller should fall back to a pure-CV mode in that case.
    """
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return None

    # Serialise cv candidates compactly for the prompt.
    candidates: list[dict[str, float | int | str]] = []
    for i, ((cx_px, cy_px), (w_mm, h_mm), angle, area_mm2) in enumerate(cv_seed):
        # Rough aspect-ratio hint so the model has a starting class.
        long_side = max(w_mm, h_mm)
        short_side = min(w_mm, h_mm)
        aspect = long_side / max(1e-3, short_side)
        cv_w_px = w_mm / mm_per_px_x
        cv_h_px = h_mm / mm_per_px_y
        candidates.append({
            "id": i,
            "cx_px": round(cx_px, 1),
            "cy_px": round(cy_px, 1),
            "w_px": round(cv_w_px, 1),
            "h_px": round(cv_h_px, 1),
            "angle_deg": round(angle, 1),
            "size_mm": f"{w_mm:.0f}x{h_mm:.0f}",
            "aspect_ratio": round(aspect, 2),
        })

    prompt = (
        f"You are looking at a top-down floor plan. The image is "
        f"{w_px}x{h_px} pixels and represents a {floor_w_m:.1f} m x "
        f"{floor_h_m:.1f} m physical floor. Pixel coords: top-left = (0,0), "
        f"x-right, y-down.\n\n"
        f"OpenCV detected the following candidate rectangles (pixel space):\n"
        f"{json.dumps(candidates, indent=2)}\n\n"
        "Your job: produce the FINAL list of rectangles. Be aggressive about "
        "fixing OpenCV's mistakes — OpenCV often merges several walls into "
        "one big sparse contour, which you should split. For each output "
        "rectangle, return:\n"
        '  {"cx_px": float, "cy_px": float, "w_px": float, "h_px": float, '
        '"angle_deg": float, "kind": "wall" | "obstacle", "label": "<short>"}\n\n'
        "Rules:\n"
        "- 'wall' = thin / long structure. INCLUDES (a) each individual bar "
        "of an X / + / T / cross, AND (b) each side of the room's outer "
        "perimeter. If OpenCV gave you ONE giant rect that encloses the "
        "whole image and is hollow, that's the outer-wall outline — REPLACE "
        "it with 4 individual wall rectangles, one per visible side "
        "(top / bottom / left / right). Each side may itself be broken into "
        "multiple segments if you can see gaps; emit one wall per segment.\n"
        "- 'obstacle' = compact filled or outline shape (column, equipment, "
        "pallet, machine, circle, triangle, filled square). KEEP each as "
        "ONE obstacle — do NOT split a polygon outline into its edges.\n"
        "- Drop text labels, dimension annotations, scale bars, hatching.\n"
        "- If OpenCV merged an X-shape into a single tilted bbox, REPLACE it "
        "with two correctly-oriented thin walls along the actual bars.\n"
        "- You may add rectangles OpenCV missed and skip ones it hallucinated.\n"
        "Return ONLY a JSON array of objects (no prose, no markdown). Empty "
        "array is allowed if the image has nothing meaningful."
    )

    try:
        from google import genai
    except ImportError:
        logger.warning("google-genai not installed; hybrid mode unavailable")
        return None

    try:
        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model=HYBRID_GEMINI_MODEL,
            contents=[
                {"parts": [
                    {"inline_data": {"mime_type": "image/png", "data": image_bytes}},
                    {"text": prompt},
                ]},
            ],
            config={
                "temperature": 0.0,
                "max_output_tokens": HYBRID_MAX_OUTPUT_TOKENS,
                "response_mime_type": "application/json",
            },
        )
    except Exception:  # noqa: BLE001
        logger.exception("Gemini Vision call failed")
        return None

    text = (resp.text or "").strip()
    if not text:
        logger.warning("Gemini Vision returned empty body")
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Some models return ```json ... ``` despite the JSON mime hint.
        cleaned = text.strip("`").lstrip("json").strip()
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("Gemini Vision JSON parse failed; first 200 chars: %s",
                           text[:200])
            return None

    if not isinstance(parsed, list):
        logger.warning("Gemini Vision didn't return a top-level array; got %s",
                       type(parsed).__name__)
        return None

    out: list[tuple[tuple[float, float], tuple[float, float], float, float, str]] = []
    for r in parsed:
        try:
            cx_px = float(r["cx_px"])
            cy_px = float(r["cy_px"])
            w_p = float(r["w_px"])
            h_p = float(r["h_px"])
            angle = float(r.get("angle_deg", 0.0))
            kind = str(r.get("kind", "")).lower()
        except (KeyError, TypeError, ValueError):
            continue
        if kind not in ("wall", "obstacle"):
            continue
        if w_p <= 0 or h_p <= 0:
            continue
        w_mm = w_p * mm_per_px_x
        h_mm = h_p * mm_per_px_y
        out.append(((cx_px, cy_px), (w_mm, h_mm), angle, w_mm * h_mm, kind))
    logger.info(
        "hybrid mode: cv_seed=%d candidates -> LLM kept %d rects",
        len(cv_seed), len(out),
    )
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
    """4-corner closed polygon for a rotated rect, world coords (mm).

    The angle came from cv2.minAreaRect, which works in PIXEL space
    (y-down). Our world frame is y-up (CLAUDE.md convention), so a
    rotation that swept clockwise in the image becomes counter-clockwise
    in world coords. Negate the angle to compensate — without this, every
    rotated rect was drawn mirrored about its short axis.
    """
    angle = math.radians(-angle_deg)
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
