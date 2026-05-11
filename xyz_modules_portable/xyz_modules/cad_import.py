"""DXF floor-plan parser.

Reads a DXF (AutoCAD Drawing Exchange Format) byte stream, extracts every
drawable entity that bounds a region the layout cannot intrude into, and
returns a list of obstacle polygons in mm + the bounding box of all
entities (suggested cell envelope).

Supported entities:
  - LINE       — a single segment (treated as an open obstacle wall)
  - LWPOLYLINE — closed -> filled polygon; open -> wall polyline
  - POLYLINE   — same handling as LWPOLYLINE
  - CIRCLE     — discretised to a 32-gon
  - ARC        — discretised to N segments (open)

Design notes:
  - DXF units default to "unitless"; we assume mm. Most CAD floor plans use
    mm or m. Caller can pass `scale_to_mm` to multiply (e.g. 1000 if the
    drawing is in metres, 25.4 if inches).
  - Closed entities become "obstacle polygons" the layout must avoid.
    Open entities become "wall polylines" — same effect for non-overlap
    purposes since both block component placement.
  - Layers are flattened. Phase 2 could add layer filtering.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass

import ezdxf
from ezdxf.entities import Arc, Circle, DXFGraphic, Line, LWPolyline, Polyline


@dataclass
class CadObstacle:
    id: str
    polygon: list[list[float]]  # [[x_mm, y_mm], ...] — closed if first == last
    closed: bool
    source_layer: str
    source_entity: str  # "LINE" / "LWPOLYLINE" / "CIRCLE" / "ARC" / "POLYLINE"


@dataclass
class CadImportResult:
    obstacles: list[CadObstacle]
    bounding_box_mm: tuple[float, float, float, float] | None  # (min_x, min_y, max_x, max_y)
    suggested_cell_envelope_mm: tuple[float, float] | None     # (W, H) — bbox W/H
    units_assumed: str
    n_entities_imported: int
    n_entities_skipped: int


CIRCLE_SEGMENTS = 32
ARC_SEGMENTS_PER_RADIAN = 6


def parse_dxf(
    data: bytes,
    scale_to_mm: float = 1.0,
    margin_mm: float = 0.0,
    treat_largest_as_boundary: bool = True,
) -> CadImportResult:
    """Parse a DXF byte stream into obstacle polygons + envelope estimate.

    `scale_to_mm`: multiplier applied to all coordinates (e.g. 1000 if the
        drawing is in metres). Default 1.0 = drawing already in mm.
    `margin_mm`: shift the bounding box origin so the smallest (x, y) lands
        at (margin_mm, margin_mm). 0 = no shift.
    `treat_largest_as_boundary`: if True (default), the closed polygon with
        the largest bbox area is interpreted as the cell outer wall (defines
        the envelope, NOT added as an obstacle). Disable if every closed
        polygon should be a forbidden region (e.g. drawings of equipment only).
    """
    text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data
    doc = ezdxf.read(io.StringIO(text))
    msp = doc.modelspace()
    obstacles: list[CadObstacle] = []
    skipped = 0
    counter = 0
    for entity in msp:
        polygon = _entity_to_polygon(entity, scale_to_mm)
        if polygon is None:
            skipped += 1
            continue
        is_closed = _is_closed_polygon(polygon)
        obstacles.append(
            CadObstacle(
                id=f"cad_{entity.dxftype().lower()}_{counter}",
                polygon=polygon,
                closed=is_closed,
                source_layer=entity.dxf.layer if entity.dxf.hasattr("layer") else "0",
                source_entity=entity.dxftype(),
            )
        )
        counter += 1

    # Drop the largest closed polygon — typically the cell outer wall.
    if treat_largest_as_boundary:
        closed = [(i, _polygon_bbox_area(o.polygon)) for i, o in enumerate(obstacles) if o.closed]
        if closed:
            largest_idx = max(closed, key=lambda t: t[1])[0]
            obstacles[largest_idx] = obstacles[largest_idx]  # noqa
            removed = obstacles.pop(largest_idx)
            # Use the boundary's bbox for the envelope, not all entities (so
            # equipment outside the wall doesn't inflate the envelope).
            bbox = (
                min(p[0] for p in removed.polygon),
                min(p[1] for p in removed.polygon),
                max(p[0] for p in removed.polygon),
                max(p[1] for p in removed.polygon),
            )
        else:
            bbox = _compute_bbox(obstacles)
    else:
        bbox = _compute_bbox(obstacles)

    if bbox and margin_mm > 0:
        # Shift so origin lands at (margin_mm, margin_mm) for easier review.
        dx = margin_mm - bbox[0]
        dy = margin_mm - bbox[1]
        for o in obstacles:
            for pt in o.polygon:
                pt[0] += dx
                pt[1] += dy
        bbox = (bbox[0] + dx, bbox[1] + dy, bbox[2] + dx, bbox[3] + dy)

    cell_envelope = None
    if bbox:
        # Add 5% padding to the suggested envelope so it's not exactly flush.
        w = (bbox[2] - bbox[0]) * 1.05
        h = (bbox[3] - bbox[1]) * 1.05
        cell_envelope = (w, h)

    return CadImportResult(
        obstacles=obstacles,
        bounding_box_mm=bbox,
        suggested_cell_envelope_mm=cell_envelope,
        units_assumed="mm" if scale_to_mm == 1.0 else f"scaled x{scale_to_mm}",
        n_entities_imported=len(obstacles),
        n_entities_skipped=skipped,
    )


def _entity_to_polygon(entity: DXFGraphic, scale: float) -> list[list[float]] | None:
    """Convert an entity to a list of [x, y] mm points. None = skip."""
    if isinstance(entity, Line):
        return [
            [float(entity.dxf.start.x) * scale, float(entity.dxf.start.y) * scale],
            [float(entity.dxf.end.x) * scale, float(entity.dxf.end.y) * scale],
        ]
    if isinstance(entity, LWPolyline):
        pts = [[float(p[0]) * scale, float(p[1]) * scale] for p in entity.get_points("xy")]
        if entity.closed and pts and pts[0] != pts[-1]:
            pts.append([pts[0][0], pts[0][1]])
        return pts if pts else None
    if isinstance(entity, Polyline):
        pts = [
            [float(v.dxf.location.x) * scale, float(v.dxf.location.y) * scale]
            for v in entity.vertices
        ]
        if entity.is_closed and pts and pts[0] != pts[-1]:
            pts.append([pts[0][0], pts[0][1]])
        return pts if pts else None
    if isinstance(entity, Circle):
        cx = float(entity.dxf.center.x) * scale
        cy = float(entity.dxf.center.y) * scale
        r = float(entity.dxf.radius) * scale
        return _discretise_arc(cx, cy, r, 0.0, 2 * math.pi, CIRCLE_SEGMENTS, close=True)
    if isinstance(entity, Arc):
        cx = float(entity.dxf.center.x) * scale
        cy = float(entity.dxf.center.y) * scale
        r = float(entity.dxf.radius) * scale
        a0 = math.radians(float(entity.dxf.start_angle))
        a1 = math.radians(float(entity.dxf.end_angle))
        if a1 < a0:
            a1 += 2 * math.pi
        n = max(2, int((a1 - a0) * ARC_SEGMENTS_PER_RADIAN))
        return _discretise_arc(cx, cy, r, a0, a1, n, close=False)
    return None


def _discretise_arc(
    cx: float,
    cy: float,
    r: float,
    a0: float,
    a1: float,
    n: int,
    close: bool,
) -> list[list[float]]:
    pts: list[list[float]] = []
    for i in range(n + 1):
        t = a0 + (a1 - a0) * (i / n)
        pts.append([cx + r * math.cos(t), cy + r * math.sin(t)])
    if close and pts[0] != pts[-1]:
        pts.append([pts[0][0], pts[0][1]])
    return pts


def _is_closed_polygon(pts: list[list[float]]) -> bool:
    return len(pts) >= 4 and abs(pts[0][0] - pts[-1][0]) < 1e-6 and abs(pts[0][1] - pts[-1][1]) < 1e-6


def _polygon_bbox_area(polygon: list[list[float]]) -> float:
    if len(polygon) < 2:
        return 0.0
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    return (max(xs) - min(xs)) * (max(ys) - min(ys))


def _compute_bbox(obstacles: list[CadObstacle]) -> tuple[float, float, float, float] | None:
    if not obstacles:
        return None
    xs: list[float] = []
    ys: list[float] = []
    for o in obstacles:
        for x, y in o.polygon:
            xs.append(x)
            ys.append(y)
    return (min(xs), min(ys), max(xs), max(ys))


# ---------------------------------------------------------------------------
# Geometry helper used by scoring + validation
# ---------------------------------------------------------------------------


def aabb_intersects_polygon(
    rect_x: float,
    rect_y: float,
    rect_w: float,
    rect_h: float,
    polygon: list[list[float]],
) -> bool:
    """Test whether an axis-aligned rectangle intersects a polygon.

    Uses two cheap tests: (1) any rect corner inside polygon, (2) any polygon
    edge crosses any rect edge. Catches all geometric intersection cases for
    convex or concave simple polygons.
    """
    if not polygon or len(polygon) < 2:
        return False
    rect_corners = [
        (rect_x, rect_y),
        (rect_x + rect_w, rect_y),
        (rect_x + rect_w, rect_y + rect_h),
        (rect_x, rect_y + rect_h),
    ]
    # 1) Any rect corner inside polygon -> intersect.
    if _is_closed_polygon(polygon):
        for c in rect_corners:
            if _point_in_polygon(c[0], c[1], polygon):
                return True
        # Any polygon vertex inside rect -> intersect.
        for p in polygon[:-1]:
            if rect_x <= p[0] <= rect_x + rect_w and rect_y <= p[1] <= rect_y + rect_h:
                return True
    # 2) Edge-edge crossing.
    rect_edges = [
        (rect_corners[0], rect_corners[1]),
        (rect_corners[1], rect_corners[2]),
        (rect_corners[2], rect_corners[3]),
        (rect_corners[3], rect_corners[0]),
    ]
    for i in range(len(polygon) - 1):
        p1 = (polygon[i][0], polygon[i][1])
        p2 = (polygon[i + 1][0], polygon[i + 1][1])
        for r1, r2 in rect_edges:
            if _segments_intersect(p1, p2, r1, r2):
                return True
    return False


def _point_in_polygon(x: float, y: float, polygon: list[list[float]]) -> bool:
    """Ray-casting test. Polygon must be a closed ring (first == last)."""
    inside = False
    n = len(polygon) - 1
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y):
            x_intersect = (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi
            if x < x_intersect:
                inside = not inside
        j = i
    return inside


def _segments_intersect(
    a1: tuple[float, float],
    a2: tuple[float, float],
    b1: tuple[float, float],
    b2: tuple[float, float],
) -> bool:
    def ccw(p, q, r) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    d1 = ccw(b1, b2, a1)
    d2 = ccw(b1, b2, a2)
    d3 = ccw(a1, a2, b1)
    d4 = ccw(a1, a2, b2)
    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and (
        (d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)
    ):
        return True
    return False
