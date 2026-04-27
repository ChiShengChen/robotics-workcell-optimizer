// Pallet stacking pattern generation.
// Each pattern returns the case rectangles for a SINGLE layer; interlock
// returns alternating-orientation layers (returned by `interlockPatternLayers`).
// All math in mm; pallet origin = lower-left of the pallet (relative coords).

export interface PalletDims {
  length_mm: number
  width_mm: number
}

export interface CaseDims {
  length_mm: number
  width_mm: number
  height_mm: number
}

export interface CaseRect {
  /** Lower-left corner of the case footprint, mm relative to pallet origin. */
  x_mm: number
  y_mm: number
  w_mm: number
  h_mm: number
  yaw_deg: 0 | 90
}

export interface StackResult {
  /** Cases per layer. */
  perLayer: CaseRect[][]
  /** Total cases (perLayer[i].length summed across layers). */
  total: number
  /** Total stack height (cases × case height) in mm. */
  totalHeightMm: number
  /** Volume utilization: sum of case volumes / pallet area × stack height. */
  loadEfficiency: number
  /** Center-of-gravity offset from pallet center (mm) — assumes uniform mass. */
  cogOffsetMm: { dx: number; dy: number; magnitude: number }
}

/** Pack cases in a regular grid with a single orientation (column pattern). */
export function columnPattern(
  pallet: PalletDims,
  cas: Pick<CaseDims, 'length_mm' | 'width_mm'>,
  yaw: 0 | 90 = 0,
): CaseRect[] {
  const w = yaw === 0 ? cas.length_mm : cas.width_mm
  const h = yaw === 0 ? cas.width_mm : cas.length_mm
  if (w <= 0 || h <= 0) return []
  const cols = Math.floor(pallet.length_mm / w)
  const rows = Math.floor(pallet.width_mm / h)
  const usedW = cols * w
  const usedH = rows * h
  const offX = (pallet.length_mm - usedW) / 2
  const offY = (pallet.width_mm - usedH) / 2
  const rects: CaseRect[] = []
  for (let r = 0; r < rows; r += 1) {
    for (let c = 0; c < cols; c += 1) {
      rects.push({
        x_mm: offX + c * w,
        y_mm: offY + r * h,
        w_mm: w,
        h_mm: h,
        yaw_deg: yaw,
      })
    }
  }
  return rects
}

/** Interlock pattern: alternate layer orientation (0° / 90°). Returns the
 *  two layer templates; full stack is just both repeated. */
export function interlockPatternLayers(
  pallet: PalletDims,
  cas: Pick<CaseDims, 'length_mm' | 'width_mm'>,
): [CaseRect[], CaseRect[]] {
  const a = columnPattern(pallet, cas, 0)
  const b = columnPattern(pallet, cas, 90)
  return [a, b]
}

/** Pinwheel pattern: classic 4-around-1 brick layout for square-ish cases.
 *  Falls back to column when cases don't fit the 2L+W constraint. */
export function pinwheelPattern(
  pallet: PalletDims,
  cas: Pick<CaseDims, 'length_mm' | 'width_mm'>,
): CaseRect[] {
  const cl = cas.length_mm
  const cw = cas.width_mm
  if (cl + cw > pallet.length_mm || cl + cw > pallet.width_mm) {
    return columnPattern(pallet, cas)
  }
  // Build a single 4-case "windmill" cluster of (cl+cw, cl+cw) and tile it.
  const blockW = cl + cw
  const blockH = cl + cw
  const cols = Math.floor(pallet.length_mm / blockW)
  const rows = Math.floor(pallet.width_mm / blockH)
  const usedW = cols * blockW
  const usedH = rows * blockH
  const offX = (pallet.length_mm - usedW) / 2
  const offY = (pallet.width_mm - usedH) / 2
  const rects: CaseRect[] = []
  for (let r = 0; r < rows; r += 1) {
    for (let c = 0; c < cols; c += 1) {
      const bx = offX + c * blockW
      const by = offY + r * blockH
      // Top-left horizontal, top-right vertical, bottom-right horizontal, bottom-left vertical.
      rects.push({ x_mm: bx, y_mm: by + cw, w_mm: cl, h_mm: cw, yaw_deg: 0 })
      rects.push({ x_mm: bx + cl, y_mm: by + cl, w_mm: cw, h_mm: cl, yaw_deg: 90 })
      rects.push({ x_mm: bx + cw, y_mm: by, w_mm: cl, h_mm: cw, yaw_deg: 0 })
      rects.push({ x_mm: bx, y_mm: by, w_mm: cw, h_mm: cl, yaw_deg: 90 })
    }
  }
  return rects
}

/** Build a stack of `nLayers` for the given pattern + dims. */
export function buildStack(
  pallet: PalletDims,
  cas: CaseDims,
  pattern: 'column' | 'interlock' | 'pinwheel',
  nLayers: number,
): StackResult {
  const perLayer: CaseRect[][] = []
  if (pattern === 'interlock') {
    const [a, b] = interlockPatternLayers(pallet, cas)
    for (let i = 0; i < nLayers; i += 1) perLayer.push(i % 2 === 0 ? a : b)
  } else if (pattern === 'pinwheel') {
    const layer = pinwheelPattern(pallet, cas)
    for (let i = 0; i < nLayers; i += 1) perLayer.push(layer)
  } else {
    const layer = columnPattern(pallet, cas)
    for (let i = 0; i < nLayers; i += 1) perLayer.push(layer)
  }

  const total = perLayer.reduce((acc, l) => acc + l.length, 0)
  const totalHeightMm = nLayers * cas.height_mm

  const palletArea = pallet.length_mm * pallet.width_mm
  const caseFootprint = cas.length_mm * cas.width_mm
  const layerArea = perLayer.reduce(
    (acc, l) => acc + l.reduce((a, r) => a + r.w_mm * r.h_mm, 0),
    0,
  )
  const loadEfficiency =
    nLayers > 0 && palletArea > 0
      ? (layerArea / nLayers) / palletArea
      : 0

  const cogOffset = computeCoGOffset(perLayer, pallet, cas)
  void caseFootprint

  return { perLayer, total, totalHeightMm, loadEfficiency, cogOffsetMm: cogOffset }
}

function computeCoGOffset(
  perLayer: CaseRect[][],
  pallet: PalletDims,
  cas: CaseDims,
): { dx: number; dy: number; magnitude: number } {
  // Uniform-mass cases. Each case at z = (layer_idx + 0.5) * h. We track
  // x/y centroid weighted by mass (proportional to volume == footprint area
  // since cases share the same height).
  let mTotal = 0
  let xWeighted = 0
  let yWeighted = 0
  for (const layer of perLayer) {
    for (const r of layer) {
      const m = r.w_mm * r.h_mm * cas.height_mm // unit-density volume
      mTotal += m
      xWeighted += m * (r.x_mm + r.w_mm / 2)
      yWeighted += m * (r.y_mm + r.h_mm / 2)
    }
  }
  if (mTotal === 0) return { dx: 0, dy: 0, magnitude: 0 }
  const cogX = xWeighted / mTotal
  const cogY = yWeighted / mTotal
  const dx = cogX - pallet.length_mm / 2
  const dy = cogY - pallet.width_mm / 2
  return { dx, dy, magnitude: Math.hypot(dx, dy) }
}
