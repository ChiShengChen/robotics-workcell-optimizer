// Top-level Konva Stage. Two layers:
//   - Static (grid + cell border + fence)         listening: false
//   - Dynamic (robot, conveyors, pallets, ops)    listening: true
// Y-axis flip: spec stores y-up (mm), Konva uses y-down (px). We flip by
// applying scaleY(-1) on the layer; child Group local coords stay y-up px,
// so onDrag callbacks return values that map directly to mm.

import { Layer, Line, Rect, Stage } from 'react-konva'
import { useMemo } from 'react'

import type { LayoutProposal, PlacedComponent, WorkcellSpec } from '@/api/types'
import { useStageSize, computeMmPerPx } from '@/hooks/useStageSize'
import { snapToGrid } from '@/lib/geometry'
import { topViolationLabel, type ValidationResult } from '@/lib/validation'

import { ConveyorShape } from './ConveyorShape'
import { FenceShape } from './FenceShape'
import { OperatorZoneShape } from './OperatorZoneShape'
import { PalletShape } from './PalletShape'
import { RobotShape } from './RobotShape'

interface Props {
  proposal: LayoutProposal | null
  spec: WorkcellSpec | null
  selectedId: string | null
  validation: ValidationResult | null
  onSelect: (id: string | null) => void
  onComponentMove?: (
    componentId: string,
    pose: { x_mm: number; y_mm: number },
    phase: 'move' | 'end',
  ) => void
}

const PADDING_PX = 16
const GRID_STEP_MM = 50

export function WorkcellCanvas({
  proposal,
  spec,
  selectedId,
  validation,
  onSelect,
  onComponentMove,
}: Props) {
  const { ref, size } = useStageSize<HTMLDivElement>()

  const cellW = proposal?.cell_bounds_mm[0] ?? spec?.cell_envelope_mm[0] ?? 8000
  const cellH = proposal?.cell_bounds_mm[1] ?? spec?.cell_envelope_mm[1] ?? 6000

  const mmPerPx = useMemo(
    () => computeMmPerPx([cellW, cellH], size, PADDING_PX),
    [cellW, cellH, size.width, size.height],
  )
  const cellWPx = cellW / mmPerPx
  const cellHPx = cellH / mmPerPx

  const offsetXPx = Math.max(PADDING_PX, (size.width - cellWPx) / 2)
  const offsetYPx = Math.max(PADDING_PX, (size.height - cellHPx) / 2)

  const mmToPx = (mm: number) => mm / mmPerPx

  const components = proposal?.components ?? []
  const fence = components.find((c) => c.type === 'fence')
  const dynamic = components.filter((c) => c.type !== 'fence')

  // 1m grid lines (visual reference); store snap is 50 mm.
  const gridLines: number[][] = []
  for (let x = 0; x <= cellW; x += 1000) gridLines.push([mmToPx(x), 0, mmToPx(x), cellHPx])
  for (let y = 0; y <= cellH; y += 1000) gridLines.push([0, mmToPx(y), cellWPx, mmToPx(y)])

  return (
    <div ref={ref} className="relative h-full w-full bg-slate-50">
      <Stage
        width={size.width}
        height={size.height}
        onMouseDown={(e) => {
          if (e.target === e.target.getStage()) onSelect(null)
        }}
      >
        {/* Static layer */}
        <Layer listening={false} x={offsetXPx} y={offsetYPx + cellHPx} scaleY={-1}>
          <Rect
            x={0}
            y={0}
            width={cellWPx}
            height={cellHPx}
            fill="#ffffff"
            stroke="#cbd5e1"
            strokeWidth={1}
          />
          {gridLines.map((pts, i) => (
            <Line key={i} points={pts} stroke="#e2e8f0" strokeWidth={0.5} />
          ))}
          {fence && (
            <FenceShape
              pointsPx={
                ((fence.dims.polyline as number[][]) ?? []).flatMap(([x, y]) => [
                  mmToPx(x),
                  mmToPx(y),
                ])
              }
            />
          )}
        </Layer>

        {/* Dynamic layer */}
        <Layer x={offsetXPx} y={offsetYPx + cellHPx} scaleY={-1}>
          {dynamic.map((c) =>
            renderComponent({
              c,
              mmToPx,
              mmPerPx,
              cellW,
              cellH,
              selectedId,
              validation,
              onSelect,
              onMove: onComponentMove,
            }),
          )}
        </Layer>
      </Stage>
      <div className="pointer-events-none absolute bottom-2 right-3 rounded bg-white/80 px-2 py-1 text-[11px] text-slate-600 shadow-sm">
        Cell {(cellW / 1000).toFixed(1)} × {(cellH / 1000).toFixed(1)} m
        {' · '}
        {(1 / mmPerPx * 1000).toFixed(0)} px / m · grid {GRID_STEP_MM} mm
      </div>
    </div>
  )
}

interface RenderArgs {
  c: PlacedComponent
  mmToPx: (mm: number) => number
  mmPerPx: number
  cellW: number
  cellH: number
  selectedId: string | null
  validation: ValidationResult | null
  onSelect: (id: string | null) => void
  onMove: Props['onComponentMove']
}

function renderComponent({
  c,
  mmToPx,
  mmPerPx,
  cellW,
  cellH,
  selectedId,
  validation,
  onSelect,
  onMove,
}: RenderArgs) {
  const selected = selectedId === c.id
  const compViolations = validation?.perComponent.get(c.id) ?? []
  const violated = compViolations.length > 0
  const vlabel = topViolationLabel(compViolations)

  const dragHandler = (phase: 'move' | 'end') => (xPx: number, yPx: number) => {
    if (!onMove) return
    onMove(c.id, { x_mm: xPx * mmPerPx, y_mm: yPx * mmPerPx }, phase)
  }

  if (c.type === 'robot') {
    const baseR = (c.dims.base_radius_mm as number | undefined) ?? 350
    const reach = (c.dims.reach_mm as number | undefined) ?? 2400
    const eff = (c.dims.effective_reach_mm as number | undefined) ?? reach * 0.85
    const dragBoundFunc = makeRobotDragBound(baseR, mmPerPx, cellW, cellH)
    return (
      <RobotShape
        key={c.id}
        xPx={mmToPx(c.x_mm)}
        yPx={mmToPx(c.y_mm)}
        baseRadiusPx={mmToPx(baseR)}
        reachPx={mmToPx(reach)}
        effectiveReachPx={mmToPx(eff)}
        label={c.id}
        selected={selected}
        violated={violated}
        violatedLabel={vlabel}
        onClick={() => onSelect(c.id)}
        onDragMove={dragHandler('move')}
        onDragEnd={dragHandler('end')}
        dragBoundFunc={dragBoundFunc}
      />
    )
  }
  if (c.type === 'conveyor') {
    const length = (c.dims.length_mm as number) ?? 2000
    const width = (c.dims.width_mm as number) ?? 600
    const isVertical = Math.abs(((c.yaw_deg % 180) + 180) % 180 - 90) < 1e-3
    const wMm = isVertical ? width : length
    const hMm = isVertical ? length : width
    return (
      <ConveyorShape
        key={c.id}
        xPx={mmToPx(c.x_mm)}
        yPx={mmToPx(c.y_mm)}
        widthPx={mmToPx(wMm)}
        heightPx={mmToPx(hMm)}
        yawDeg={c.yaw_deg}
        label={c.id}
        role={(c.dims.role as 'infeed' | 'outfeed' | undefined) ?? 'infeed'}
        selected={selected}
        violated={violated}
        violatedLabel={vlabel}
        onClick={() => onSelect(c.id)}
        onDragMove={dragHandler('move')}
        onDragEnd={dragHandler('end')}
        dragBoundFunc={makeBoxDragBound(wMm, hMm, mmPerPx, cellW, cellH)}
      />
    )
  }
  if (c.type === 'pallet') {
    const length = (c.dims.length_mm as number) ?? 1200
    const width = (c.dims.width_mm as number) ?? 800
    const standard = (c.dims.standard as string) ?? 'EUR'
    const pattern = (c.dims.pattern as string) ?? 'interlock'
    return (
      <PalletShape
        key={c.id}
        xPx={mmToPx(c.x_mm)}
        yPx={mmToPx(c.y_mm)}
        widthPx={mmToPx(length)}
        heightPx={mmToPx(width)}
        label={c.id}
        standard={standard}
        pattern={pattern}
        selected={selected}
        violated={violated}
        violatedLabel={vlabel}
        onClick={() => onSelect(c.id)}
        onDragMove={dragHandler('move')}
        onDragEnd={dragHandler('end')}
        dragBoundFunc={makeBoxDragBound(length, width, mmPerPx, cellW, cellH)}
      />
    )
  }
  if (c.type === 'operator_zone') {
    const w = (c.dims.width_mm as number) ?? 1500
    const d = (c.dims.depth_mm as number) ?? 1500
    return (
      <OperatorZoneShape
        key={c.id}
        xPx={mmToPx(c.x_mm)}
        yPx={mmToPx(c.y_mm)}
        widthPx={mmToPx(w)}
        heightPx={mmToPx(d)}
        label={c.id}
        selected={selected}
        violated={violated}
        violatedLabel={vlabel}
        onClick={() => onSelect(c.id)}
      />
    )
  }
  return null
}

// dragBoundFunc receives ABSOLUTE stage pixel coords; we must respect that.
// We can't easily clamp absolute coords without knowing the layer transform,
// so we pass through and let the move handler snap+clamp on the parent side.
// However for snap-to-grid we can apply rounding here using a per-shape
// closure that knows the layer's offset and scale, derived from the parent.
//
// Simpler: snap is applied in the store update handler; dragBoundFunc just
// returns the input. We keep the function for future hard-clamp behavior.
function makeBoxDragBound(
  _wMm: number,
  _hMm: number,
  _mmPerPx: number,
  _cellW: number,
  _cellH: number,
) {
  return (pos: { x: number; y: number }) => pos
}

function makeRobotDragBound(
  _radiusMm: number,
  _mmPerPx: number,
  _cellW: number,
  _cellH: number,
) {
  return (pos: { x: number; y: number }) => pos
}

// Helper exposed for callers wanting snap behavior on raw mm.
export function snapMm(mm: number): number {
  return snapToGrid(mm, GRID_STEP_MM)
}
