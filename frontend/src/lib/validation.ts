// Pure-function client-side validation. Mirrors backend/app/services/scoring.py
// hard-violation logic so drag interactions can highlight problems instantly
// without waiting for /api/score round-trips.

import type { LayoutProposal, PlacedComponent, WorkcellSpec } from '@/api/types'
import {
  type Point,
  type Rect,
  aabbOverlap,
  componentRect,
  distToPolyline,
  pointInRect,
  reachableByRobot,
} from './geometry'

// ISO 13855 (legacy) S = K·T + C with K=2000 mm/s, T=0.3 s, C=850 mm body.
const ISO_K = 2000
const ISO_T = 0.3
const ISO_C_BODY = 850
const ISO_C_HARD_GUARD = 600

export function iso13855SafetyDistanceMm(hasHardGuard: boolean): number {
  return ISO_K * ISO_T + (hasHardGuard ? ISO_C_HARD_GUARD : ISO_C_BODY)
}

export interface ComponentViolation {
  componentId: string
  kind: 'overlap' | 'unreachable' | 'fence_clearance' | 'outside_envelope'
  partnerId?: string
  marginMm?: number
  message: string
}

export interface ValidationResult {
  perComponent: Map<string, ComponentViolation[]>
  overlaps: [string, string][]
  unreachableTargets: { componentId: string; signedMargin: number }[]
  fenceClearanceViolations: { componentId: string; slackMm: number }[]
  operatorZoneIntrusion: boolean
  outsideEnvelope: string[]
  summary: { ok: boolean; hardViolations: number; softWarnings: number }
}

interface PickPlaceTarget {
  componentId: string
  point: Point
}

function pickTargetsFor(c: PlacedComponent): PickPlaceTarget[] {
  if (c.type === 'conveyor') {
    const length = (c.dims.length_mm as number) ?? 0
    const width = (c.dims.width_mm as number) ?? 0
    const isVertical = Math.abs(((c.yaw_deg % 180) + 180) % 180 - 90) < 1e-3
    const point = isVertical
      ? { x: c.x_mm + width / 2, y: c.y_mm + length }
      : { x: c.x_mm + length, y: c.y_mm + width / 2 }
    return [{ componentId: c.id, point }]
  }
  if (c.type === 'pallet') {
    const length = (c.dims.length_mm as number) ?? 1200
    const width = (c.dims.width_mm as number) ?? 800
    return [{ componentId: c.id, point: { x: c.x_mm + length / 2, y: c.y_mm + width / 2 } }]
  }
  return []
}

export function validateLayout(
  proposal: LayoutProposal,
  spec: WorkcellSpec,
): ValidationResult {
  const perComponent = new Map<string, ComponentViolation[]>()
  const push = (id: string, v: ComponentViolation) => {
    const list = perComponent.get(id) ?? []
    list.push(v)
    perComponent.set(id, list)
  }

  const robot = proposal.components.find((c) => c.type === 'robot') ?? null
  const fence = proposal.components.find((c) => c.type === 'fence') ?? null
  const operator = proposal.components.find((c) => c.type === 'operator_zone') ?? null
  const bodies = proposal.components.filter(
    (c) => c.type !== 'fence' && c.type !== 'operator_zone',
  )

  // 1. Pairwise AABB overlap among bodies.
  const overlaps: [string, string][] = []
  for (let i = 0; i < bodies.length; i += 1) {
    const ri = componentRect(bodies[i])
    for (let j = i + 1; j < bodies.length; j += 1) {
      const rj = componentRect(bodies[j])
      if (aabbOverlap(ri, rj)) {
        overlaps.push([bodies[i].id, bodies[j].id])
        push(bodies[i].id, {
          componentId: bodies[i].id,
          kind: 'overlap',
          partnerId: bodies[j].id,
          message: `Overlaps with ${bodies[j].id}`,
        })
        push(bodies[j].id, {
          componentId: bodies[j].id,
          kind: 'overlap',
          partnerId: bodies[i].id,
          message: `Overlaps with ${bodies[i].id}`,
        })
      }
    }
  }

  // 2. Reach feasibility for every pick/place target.
  const unreachable: ValidationResult['unreachableTargets'] = []
  if (robot) {
    const eff =
      ((robot.dims.effective_reach_mm as number | undefined) ??
        (robot.dims.reach_mm as number | undefined) ??
        2400) * 1
    const center: Point = { x: robot.x_mm, y: robot.y_mm }
    for (const c of proposal.components) {
      for (const t of pickTargetsFor(c)) {
        const { ok, signedMargin } = reachableByRobot(t.point, center, eff)
        if (!ok) {
          unreachable.push({ componentId: t.componentId, signedMargin })
          push(t.componentId, {
            componentId: t.componentId,
            kind: 'unreachable',
            marginMm: signedMargin,
            message: `Unreachable (${(-signedMargin).toFixed(0)} mm beyond effective reach)`,
          })
        }
      }
    }
  }

  // 3. Fence clearance per ISO 13855. Conveyors enter the cell via a muting
  //    zone / light curtain in real installations — separation applies only
  //    to robot + pallets, not the conveyor body.
  const fenceClearance: ValidationResult['fenceClearanceViolations'] = []
  if (fence) {
    const poly = (fence.dims.polyline as number[][] | undefined) ?? []
    const hasCurtain =
      Boolean(fence.dims.has_light_curtain as boolean | undefined) ||
      spec.components.some((c) => c.type === 'fence' && c.has_light_curtain)
    const sSafe = iso13855SafetyDistanceMm(!hasCurtain)
    const fenceCheckBodies = bodies.filter((c) => c.type !== 'conveyor')
    for (const c of fenceCheckBodies) {
      const r: Rect = componentRect(c)
      const corners: Point[] = [
        { x: r.x, y: r.y },
        { x: r.x + r.w, y: r.y },
        { x: r.x + r.w, y: r.y + r.h },
        { x: r.x, y: r.y + r.h },
      ]
      const minD = Math.min(...corners.map((p) => distToPolyline(p, poly)))
      const slack = minD - sSafe
      if (slack < 0) {
        fenceClearance.push({ componentId: c.id, slackMm: slack })
        push(c.id, {
          componentId: c.id,
          kind: 'fence_clearance',
          marginMm: slack,
          message: `ISO 13855 fence clearance short by ${(-slack).toFixed(0)} mm`,
        })
      }
    }
  }

  // 4. Operator zone intrusion: any body bbox touches the operator rect.
  let operatorIntrusion = false
  if (operator) {
    const opRect = componentRect(operator)
    for (const c of bodies) {
      if (aabbOverlap(componentRect(c), opRect)) {
        operatorIntrusion = true
        push(c.id, {
          componentId: c.id,
          kind: 'overlap',
          partnerId: operator.id,
          message: `Intrudes into operator zone`,
        })
      }
    }
  }

  // 5. Inside cell envelope.
  const [cellW, cellH] = spec.cell_envelope_mm
  const outside: string[] = []
  for (const c of bodies) {
    const r = componentRect(c)
    const tl = pointInRect({ x: r.x, y: r.y }, { x: 0, y: 0, w: cellW, h: cellH })
    const br = pointInRect(
      { x: r.x + r.w, y: r.y + r.h },
      { x: 0, y: 0, w: cellW, h: cellH },
    )
    if (!tl || !br) {
      outside.push(c.id)
      push(c.id, {
        componentId: c.id,
        kind: 'outside_envelope',
        message: 'Outside cell envelope',
      })
    }
  }

  const hardViolations =
    overlaps.length + unreachable.length + fenceClearance.length + outside.length +
    (operatorIntrusion ? 1 : 0)

  return {
    perComponent,
    overlaps,
    unreachableTargets: unreachable,
    fenceClearanceViolations: fenceClearance,
    operatorZoneIntrusion: operatorIntrusion,
    outsideEnvelope: outside,
    summary: {
      ok: hardViolations === 0,
      hardViolations,
      softWarnings: 0,
    },
  }
}

export function topViolationLabel(v: ComponentViolation[]): string {
  if (v.length === 0) return ''
  // Prioritize OVERLAP > REACH > FENCE > ENVELOPE
  const order = ['overlap', 'unreachable', 'fence_clearance', 'outside_envelope'] as const
  for (const k of order) {
    if (v.some((x) => x.kind === k)) {
      if (k === 'overlap') return 'OVERLAP'
      if (k === 'unreachable') return 'REACH'
      if (k === 'fence_clearance') return 'FENCE'
      return 'ENVELOPE'
    }
  }
  return ''
}
