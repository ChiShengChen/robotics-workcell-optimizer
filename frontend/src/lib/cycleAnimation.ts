// Pick-and-place cycle state machine + 4-axis palletizer IK.
// Coordinates: world-space metres; +y is up; (x, z) is the floor plane.
// J1 (base yaw), J2 (lower-arm pitch from horizontal), J3 (upper-arm pitch
// from horizontal). The parallelogram link keeps the EOAT vertical.

import * as THREE from 'three'

export type Phase =
  | 'home'
  | 'descend_pick'
  | 'lift_after_pick'
  | 'transport'
  | 'descend_place'
  | 'lift_after_place'

// Phase fractions of the total cycle (sum = 1).
const PHASE_FRACTIONS: Record<Phase, number> = {
  home: 0.05,
  descend_pick: 0.15,
  lift_after_pick: 0.10,
  transport: 0.30,
  descend_place: 0.15,
  lift_after_place: 0.25,
}
export const PHASE_ORDER: Phase[] = [
  'home',
  'descend_pick',
  'lift_after_pick',
  'transport',
  'descend_place',
  'lift_after_place',
]

export interface CycleConfig {
  cycleSeconds: number      // simulation seconds for one full pick-place cycle
  pickPoint: THREE.Vector3   // world position where the case is picked from
  placePointsByPallet: { palletId: string; basePoint: THREE.Vector3 }[]
  homePoint: THREE.Vector3   // EOAT idle position above robot
  hoverHeight: number        // how high above pick/place to hover during transport
  caseHeight: number         // case stack height per case (m); used to lift the place point as cases stack
  // Cap on cases per pallet — when reached, the count wraps to 0 to simulate
  // the full pallet being rolled out and replaced. Without this the place
  // height climbs forever and the renderer caps visible cases, making the
  // demo look like it "stopped generating".
  maxPerPallet?: Record<string, number>
}

export interface CycleState {
  t: number                 // simulation time accumulated
  phaseIdx: number
  cycleCount: number
  carrying: boolean
  currentPalletIdx: number  // which pallet (cycles through placePointsByPallet)
  placedPerPallet: Record<string, number>
}

export function initialCycleState(cfg: CycleConfig): CycleState {
  const placedPerPallet: Record<string, number> = {}
  for (const p of cfg.placePointsByPallet) placedPerPallet[p.palletId] = 0
  return {
    t: 0,
    phaseIdx: 0,
    cycleCount: 0,
    carrying: false,
    currentPalletIdx: 0,
    placedPerPallet,
  }
}

export interface FrameOutput {
  state: CycleState
  eoatPosition: THREE.Vector3
  carrying: boolean
}

/** Advance the cycle state by `dtSimSeconds`, returning the new state +
 *  the EOAT world position to render. Does NOT mutate the input state. */
export function step(state: CycleState, cfg: CycleConfig, dtSimSeconds: number): FrameOutput {
  const t2 = state.t + dtSimSeconds
  let { phaseIdx, cycleCount, carrying, currentPalletIdx, placedPerPallet } = state

  // Determine which phase t2 falls in.
  const phase = PHASE_ORDER[phaseIdx]
  const phaseDuration = PHASE_FRACTIONS[phase] * cfg.cycleSeconds
  const phaseStartT = sumDurationsBeforePhase(phaseIdx, cfg.cycleSeconds) +
    cycleCount * cfg.cycleSeconds
  const phaseElapsed = t2 - phaseStartT
  const u = clamp01(phaseElapsed / phaseDuration)

  // Compute EOAT position based on the phase + interpolation u.
  const placeTarget = cfg.placePointsByPallet[currentPalletIdx]
  const placedCountForCurrent = placedPerPallet[placeTarget.palletId] ?? 0
  const placeTopY = placeTarget.basePoint.y + placedCountForCurrent * cfg.caseHeight
  const placePos = new THREE.Vector3(placeTarget.basePoint.x, placeTopY, placeTarget.basePoint.z)
  const placeHover = placePos.clone().setY(placeTarget.basePoint.y + cfg.hoverHeight)
  const pickHover = cfg.pickPoint.clone().setY(cfg.pickPoint.y + cfg.hoverHeight)

  let eoat = cfg.homePoint.clone()
  switch (phase) {
    case 'home':
      eoat = cfg.homePoint.clone()
      break
    case 'descend_pick':
      eoat = lerpV3(cfg.homePoint, cfg.pickPoint, easeInOut(u))
      break
    case 'lift_after_pick':
      eoat = lerpV3(cfg.pickPoint, pickHover, easeInOut(u))
      break
    case 'transport':
      eoat = lerpV3(pickHover, placeHover, easeInOut(u))
      break
    case 'descend_place':
      eoat = lerpV3(placeHover, placePos, easeInOut(u))
      break
    case 'lift_after_place':
      eoat = lerpV3(placePos, cfg.homePoint, easeInOut(u))
      break
  }

  // Phase transition triggers.
  if (phaseElapsed >= phaseDuration) {
    // Transition out of current phase.
    if (phase === 'descend_pick') carrying = true
    if (phase === 'descend_place') {
      carrying = false
      placedPerPallet = { ...placedPerPallet }
      const next = (placedPerPallet[placeTarget.palletId] ?? 0) + 1
      const cap = cfg.maxPerPallet?.[placeTarget.palletId]
      placedPerPallet[placeTarget.palletId] = cap && next >= cap ? 0 : next
    }
    if (phase === 'lift_after_place') {
      // End of cycle — switch to next pallet for round-robin dual-pallet.
      currentPalletIdx = (currentPalletIdx + 1) % cfg.placePointsByPallet.length
      cycleCount += 1
      phaseIdx = 0
    } else {
      phaseIdx += 1
    }
  }

  return {
    state: { t: t2, phaseIdx, cycleCount, carrying, currentPalletIdx, placedPerPallet },
    eoatPosition: eoat,
    carrying,
  }
}

function sumDurationsBeforePhase(idx: number, cycleSeconds: number): number {
  let s = 0
  for (let i = 0; i < idx; i += 1) {
    s += PHASE_FRACTIONS[PHASE_ORDER[i]] * cycleSeconds
  }
  return s
}

function clamp01(x: number): number {
  return Math.max(0, Math.min(1, x))
}

function easeInOut(u: number): number {
  // Smooth cubic ease — good enough surrogate for trapezoidal motion;
  // visible accel/decel without perceptible jitter.
  return u * u * (3 - 2 * u)
}

function lerpV3(a: THREE.Vector3, b: THREE.Vector3, u: number): THREE.Vector3 {
  return a.clone().lerp(b, u)
}

// ---------------------------------------------------------------------------
// 4-axis palletizer IK
// ---------------------------------------------------------------------------

export interface ArmPose {
  /** Base yaw (rad), J1. */
  j1: number
  /** Lower-arm pitch from horizontal (rad), J2. */
  j2: number
  /** Upper-arm pitch from horizontal (rad), J3. The parallelogram link keeps
   *  the EOAT plate vertical regardless of (j2, j3). */
  j3: number
  /** Resolved elbow + wrist world positions for rendering. */
  hipWorld: THREE.Vector3
  elbowWorld: THREE.Vector3
  wristWorld: THREE.Vector3
}

export interface ArmGeometry {
  baseWorld: THREE.Vector3   // world position of the base centre on the floor
  hipHeight: number          // y of the hip joint above the base
  l1: number                 // lower-arm length
  l2: number                 // upper-arm length
}

/** Solve 2-link IK in the vertical plane through (base, target).
 *  Always picks the elbow-up solution (palletizer convention) so the
 *  arm doesn't dip toward the floor. Returns clamped pose if target
 *  is beyond reach. */
export function solveArmIK(geom: ArmGeometry, target: THREE.Vector3): ArmPose {
  const { baseWorld, hipHeight, l1, l2 } = geom
  // J1 yaw: rotate to face the target horizontally.
  const dx = target.x - baseWorld.x
  const dz = target.z - baseWorld.z
  const j1 = Math.atan2(dz, dx)

  // 2D IK in the vertical plane: r horizontal, h vertical.
  const r = Math.hypot(dx, dz)
  const hipWorld = new THREE.Vector3(baseWorld.x, baseWorld.y + hipHeight, baseWorld.z)
  const dr = r
  const dh = target.y - hipWorld.y
  const D = Math.hypot(dr, dh)

  let q2: number
  let q3: number
  if (D >= l1 + l2 - 1e-4) {
    // Out of reach -> fully extended toward target.
    const angle = Math.atan2(dh, dr)
    q2 = angle
    q3 = angle
  } else if (D <= Math.abs(l1 - l2) + 1e-4) {
    // Inside the dead zone (folded). Point straight up.
    q2 = Math.PI / 2
    q3 = -Math.PI / 2
  } else {
    // Standard 2-link IK. cos(angle at elbow) via law of cosines.
    const cosBeta = clamp(-1, 1, (l1 * l1 + D * D - l2 * l2) / (2 * l1 * D))
    const beta = Math.acos(cosBeta)
    const cosGamma = clamp(-1, 1, (l1 * l1 + l2 * l2 - D * D) / (2 * l1 * l2))
    const gamma = Math.acos(cosGamma)
    const alpha = Math.atan2(dh, dr)
    // Elbow-up branch.
    q2 = alpha + beta
    q3 = q2 - (Math.PI - gamma)
  }

  // Convert (q2, q3) in the rotated vertical plane back to 3D world points.
  // The plane direction (forward) is the unit vector (cos(j1), 0, sin(j1)).
  const fx = Math.cos(j1)
  const fz = Math.sin(j1)
  const elbowR = l1 * Math.cos(q2)
  const elbowY = hipWorld.y + l1 * Math.sin(q2)
  const elbowWorld = new THREE.Vector3(
    hipWorld.x + fx * elbowR,
    elbowY,
    hipWorld.z + fz * elbowR,
  )
  const wristR = elbowR + l2 * Math.cos(q3)
  const wristY = elbowY + l2 * Math.sin(q3)
  const wristWorld = new THREE.Vector3(
    hipWorld.x + fx * wristR,
    wristY,
    hipWorld.z + fz * wristR,
  )

  return { j1, j2: q2, j3: q3, hipWorld, elbowWorld, wristWorld }
}

function clamp(lo: number, hi: number, v: number): number {
  return Math.max(lo, Math.min(hi, v))
}
