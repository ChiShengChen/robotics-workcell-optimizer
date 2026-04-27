// Geometry helpers — mm/px conversion + axis-aligned box / polyline ops.
// Coordinate convention from CLAUDE.md: cell origin = lower-left, x → right, y → up.
// Konva uses y-down, so we flip y at the canvas layer (not here).

import type { PlacedComponent } from '@/api/types'

export const DEFAULT_MM_PER_PX = 20 // 1 px = 20 mm → 10 m cell ≈ 500 px

export function mmToPx(mm: number, mmPerPx: number = DEFAULT_MM_PER_PX): number {
  return mm / mmPerPx
}

export function pxToMm(px: number, mmPerPx: number = DEFAULT_MM_PER_PX): number {
  return px * mmPerPx
}

export interface Rect {
  x: number
  y: number
  w: number
  h: number
}

export interface Point {
  x: number
  y: number
}

/** Axis-aligned bounding box of a placed component, ignoring yaw rotation
 *  (Phase 4 will refine for rotated cases). All values in mm. */
export function componentRect(c: PlacedComponent): Rect {
  if (c.type === 'robot') {
    const r = (c.dims.base_radius_mm as number | undefined) ?? 350
    return { x: c.x_mm - r, y: c.y_mm - r, w: 2 * r, h: 2 * r }
  }
  if (c.type === 'conveyor') {
    const length = (c.dims.length_mm as number) ?? 0
    const width = (c.dims.width_mm as number) ?? 0
    // Yaw 90° → length runs along y; otherwise length along x.
    const isVertical = Math.abs(((c.yaw_deg % 180) + 180) % 180 - 90) < 1e-3
    return isVertical
      ? { x: c.x_mm, y: c.y_mm, w: width, h: length }
      : { x: c.x_mm, y: c.y_mm, w: length, h: width }
  }
  if (c.type === 'pallet') {
    const length = (c.dims.length_mm as number) ?? 1200
    const width = (c.dims.width_mm as number) ?? 800
    return { x: c.x_mm, y: c.y_mm, w: length, h: width }
  }
  if (c.type === 'operator_zone') {
    const w = (c.dims.width_mm as number) ?? 1500
    const d = (c.dims.depth_mm as number) ?? 1500
    return { x: c.x_mm, y: c.y_mm, w, h: d }
  }
  // fence has no axis-aligned bbox; return empty.
  return { x: c.x_mm, y: c.y_mm, w: 0, h: 0 }
}

export function aabbOverlap(a: Rect, b: Rect): boolean {
  return !(a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y)
}

export function pointInRect(p: Point, r: Rect): boolean {
  return p.x >= r.x && p.x <= r.x + r.w && p.y >= r.y && p.y <= r.y + r.h
}

export function distance(a: Point, b: Point): number {
  return Math.hypot(a.x - b.x, a.y - b.y)
}

/** Distance from `p` to the closest segment in a polyline. */
export function distToPolyline(p: Point, poly: number[][]): number {
  if (poly.length < 2) return Infinity
  let best = Infinity
  for (let i = 0; i < poly.length - 1; i += 1) {
    const ax = poly[i][0], ay = poly[i][1]
    const bx = poly[i + 1][0], by = poly[i + 1][1]
    const dx = bx - ax, dy = by - ay
    const len2 = dx * dx + dy * dy
    let t = len2 === 0 ? 0 : ((p.x - ax) * dx + (p.y - ay) * dy) / len2
    t = Math.max(0, Math.min(1, t))
    const cx = ax + t * dx, cy = ay + t * dy
    best = Math.min(best, Math.hypot(p.x - cx, p.y - cy))
  }
  return best
}

/** Snap a value to the nearest grid step (mm). */
export function snapToGrid(value: number, step = 50): number {
  return Math.round(value / step) * step
}

/** Clamp a rect to lie inside [0, cellW] × [0, cellH]. */
export function clampRectInside(rect: Rect, cellW: number, cellH: number): Rect {
  const x = Math.max(0, Math.min(cellW - rect.w, rect.x))
  const y = Math.max(0, Math.min(cellH - rect.h, rect.y))
  return { x, y, w: rect.w, h: rect.h }
}

/** Reach annulus check: point is within [0, effective_reach] of robot center. */
export function reachableByRobot(
  target: Point,
  robotCenter: Point,
  effectiveReachMm: number,
): { ok: boolean; signedMargin: number } {
  const d = distance(target, robotCenter)
  const margin = effectiveReachMm - d
  return { ok: margin >= 0, signedMargin: margin }
}
