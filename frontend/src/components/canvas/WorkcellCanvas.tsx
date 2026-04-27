// Top-level Konva Stage. Two layers:
//   - Static (grid + cell border + fence)         listening: false
//   - Dynamic (robot, conveyors, pallets, ops)    listening: true
// Y-axis flip: spec stores y-up (mm), Konva uses y-down (px). We flip by
// applying scaleY(-1) and translating by stageHeight to keep origin LL.

import { Layer, Line, Rect, Stage } from 'react-konva'
import { useMemo } from 'react'

import type { LayoutProposal, PlacedComponent } from '@/api/types'
import { useStageSize, computeMmPerPx } from '@/hooks/useStageSize'

import { ConveyorShape } from './ConveyorShape'
import { FenceShape } from './FenceShape'
import { OperatorZoneShape } from './OperatorZoneShape'
import { PalletShape } from './PalletShape'
import { RobotShape } from './RobotShape'

interface Props {
  proposal: LayoutProposal | null
  selectedId: string | null
  onSelect: (id: string | null) => void
  onComponentMove?: (
    componentId: string,
    pose: { x_mm: number; y_mm: number },
  ) => void
}

const PADDING_PX = 16

export function WorkcellCanvas({ proposal, selectedId, onSelect, onComponentMove }: Props) {
  const { ref, size } = useStageSize<HTMLDivElement>()

  const cellW = proposal?.cell_bounds_mm[0] ?? 8000
  const cellH = proposal?.cell_bounds_mm[1] ?? 6000

  const mmPerPx = useMemo(
    () => computeMmPerPx([cellW, cellH], size, PADDING_PX),
    [cellW, cellH, size.width, size.height],
  )
  const cellWPx = cellW / mmPerPx
  const cellHPx = cellH / mmPerPx

  // Center the cell in the stage.
  const offsetXPx = Math.max(PADDING_PX, (size.width - cellWPx) / 2)
  const offsetYPx = Math.max(PADDING_PX, (size.height - cellHPx) / 2)

  const mmToPx = (mm: number) => mm / mmPerPx

  const components = proposal?.components ?? []
  const fence = components.find((c) => c.type === 'fence')
  const dynamic = components.filter((c) => c.type !== 'fence')

  // Grid: 1m lines.
  const gridLines: number[][] = []
  for (let x = 0; x <= cellW; x += 1000) gridLines.push([mmToPx(x), 0, mmToPx(x), cellHPx])
  for (let y = 0; y <= cellH; y += 1000) gridLines.push([0, mmToPx(y), cellWPx, mmToPx(y)])

  return (
    <div ref={ref} className="relative h-full w-full bg-slate-50">
      <Stage
        width={size.width}
        height={size.height}
        onMouseDown={(e) => {
          // Click on stage background → clear selection
          if (e.target === e.target.getStage()) onSelect(null)
        }}
      >
        {/* Group that flips y so spec coords (y-up) render correctly */}
        <Layer
          listening={false}
          x={offsetXPx}
          y={offsetYPx + cellHPx}
          scaleY={-1}
        >
          {/* Cell border */}
          <Rect
            x={0}
            y={0}
            width={cellWPx}
            height={cellHPx}
            fill="#ffffff"
            stroke="#cbd5e1"
            strokeWidth={1}
          />
          {/* 1m grid */}
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
          {dynamic.map((c) => renderComponent(c, mmToPx, selectedId, onSelect, onComponentMove, mmPerPx))}
        </Layer>
      </Stage>
      {/* Scale chip */}
      <div className="pointer-events-none absolute bottom-2 right-3 rounded bg-white/80 px-2 py-1 text-[11px] text-slate-600 shadow-sm">
        Cell {(cellW / 1000).toFixed(1)} × {(cellH / 1000).toFixed(1)} m
        {' · '}
        {(1 / mmPerPx * 1000).toFixed(0)} px / m
      </div>
    </div>
  )
}

function renderComponent(
  c: PlacedComponent,
  mmToPx: (mm: number) => number,
  selectedId: string | null,
  onSelect: (id: string | null) => void,
  onMove: Props['onComponentMove'],
  mmPerPx: number,
) {
  const selected = selectedId === c.id

  // Konva pose (px) — when scaleY=-1 is on the layer, drag deltas in y come back negated.
  // We convert back to mm by reading the px and undoing the flip via mmPerPx and the layer transform.
  const onDragMove = (xPx: number, yPx: number) => {
    if (!onMove) return
    onMove(c.id, { x_mm: xPx * mmPerPx, y_mm: yPx * mmPerPx })
  }
  const onDragEnd = onDragMove

  if (c.type === 'robot') {
    const baseR = (c.dims.base_radius_mm as number | undefined) ?? 350
    const reach = (c.dims.reach_mm as number | undefined) ?? 2400
    const eff = (c.dims.effective_reach_mm as number | undefined) ?? reach * 0.85
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
        onClick={() => onSelect(c.id)}
        onDragMove={onDragMove}
        onDragEnd={onDragEnd}
      />
    )
  }
  if (c.type === 'conveyor') {
    const length = (c.dims.length_mm as number) ?? 2000
    const width = (c.dims.width_mm as number) ?? 600
    const isVertical = Math.abs(((c.yaw_deg % 180) + 180) % 180 - 90) < 1e-3
    const widthPx = mmToPx(isVertical ? width : length)
    const heightPx = mmToPx(isVertical ? length : width)
    const role = (c.dims.role as 'infeed' | 'outfeed' | undefined) ?? 'infeed'
    return (
      <ConveyorShape
        key={c.id}
        xPx={mmToPx(c.x_mm)}
        yPx={mmToPx(c.y_mm)}
        widthPx={widthPx}
        heightPx={heightPx}
        yawDeg={c.yaw_deg}
        label={c.id}
        role={role}
        selected={selected}
        onClick={() => onSelect(c.id)}
        onDragMove={onDragMove}
        onDragEnd={onDragEnd}
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
        onClick={() => onSelect(c.id)}
        onDragMove={onDragMove}
        onDragEnd={onDragEnd}
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
        onClick={() => onSelect(c.id)}
      />
    )
  }
  return null
}
