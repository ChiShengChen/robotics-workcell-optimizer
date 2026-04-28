// Strip of thumbnail mini-canvases — clicking switches active proposal.

import { Layer, Line, Rect, Stage } from 'react-konva'

import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import type { LayoutProposal, PlacedComponent } from '@/api/types'
import { useLayoutStore } from '@/store/layoutStore'
import { CompareSheet } from './CompareSheet'
import { SideBySideCompare } from './SideBySideCompare'
import { BomDialog } from '@/components/panels/BomDialog'

const THUMB_W = 200
const THUMB_H = 130
const THUMB_PAD = 8

export function VariantsStrip() {
  const proposals = useLayoutStore((s) => s.proposals)
  const activeId = useLayoutStore((s) => s.activeProposalId)
  const setActive = useLayoutStore((s) => s.setActiveProposal)
  const scoreByProposal = useLayoutStore((s) => s.scoreByProposal)

  if (proposals.length === 0) return null

  return (
    <div className="border-t border-slate-200 bg-white px-3 py-2">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-[11px] font-medium uppercase tracking-wide text-slate-500">
          Variants ({proposals.length})
        </span>
        <div className="flex items-center gap-2">
          <BomDialog />
          <SideBySideCompare />
          <CompareSheet />
        </div>
      </div>
      <div className="flex gap-3 overflow-x-auto pb-1">
        {proposals.map((p) => {
          const score = scoreByProposal[p.proposal_id]?.aggregate ?? null
          const badgeVariant = score === null ? 'secondary' : score >= 0.7 ? 'default' : score >= 0.4 ? 'secondary' : 'destructive'
          return (
            <button
              key={p.proposal_id}
              type="button"
              onClick={() => setActive(p.proposal_id)}
              className={cn(
                'shrink-0 rounded border bg-slate-50 p-1 transition',
                p.proposal_id === activeId
                  ? 'border-blue-500 ring-2 ring-blue-300'
                  : 'border-slate-200 hover:border-slate-400',
              )}
            >
              <Thumbnail proposal={p} />
              <div className="flex items-center justify-between px-1 pt-1">
                <span className="text-[10px] font-medium text-slate-700">{p.template}</span>
                <Badge variant={badgeVariant} className="h-4 px-1.5 text-[9px]">
                  {score === null ? `${Math.round(p.estimated_uph)} UPH` : `${Math.round(score * 100)}`}
                </Badge>
              </div>
              <div className="px-1 text-[9px] text-slate-500">
                {p.robot_model_id ?? '—'}
              </div>
            </button>
          )
        })}
      </div>
    </div>
  )
}

function Thumbnail({ proposal }: { proposal: LayoutProposal }) {
  const [cellW, cellH] = proposal.cell_bounds_mm
  const usableW = THUMB_W - 2 * THUMB_PAD
  const usableH = THUMB_H - 2 * THUMB_PAD
  const mmPerPx = Math.max(cellW / usableW, cellH / usableH)
  const cellWPx = cellW / mmPerPx
  const cellHPx = cellH / mmPerPx
  const offX = (THUMB_W - cellWPx) / 2
  const offY = (THUMB_H - cellHPx) / 2

  return (
    <Stage width={THUMB_W} height={THUMB_H}>
      <Layer x={offX} y={offY + cellHPx} scaleY={-1} listening={false}>
        <Rect width={cellWPx} height={cellHPx} fill="#fff" stroke="#cbd5e1" strokeWidth={0.5} />
        {proposal.components.map((c) => renderThumbComponent(c, mmPerPx))}
      </Layer>
    </Stage>
  )
}

function renderThumbComponent(c: PlacedComponent, mmPerPx: number) {
  const toPx = (mm: number) => mm / mmPerPx
  const key = `${c.id}`
  if (c.type === 'fence') {
    const poly = (c.dims.polyline as number[][] | undefined) ?? []
    if (poly.length < 2) return null
    const points = poly.flatMap(([x, y]) => [toPx(x), toPx(y)])
    return (
      <Line key={key} points={points} stroke="#dc2626" strokeWidth={0.8} dash={[3, 2]} closed />
    )
  }
  if (c.type === 'robot') {
    const r = (c.dims.base_radius_mm as number | undefined) ?? 350
    const reach = (c.dims.reach_mm as number | undefined) ?? 2400
    return (
      <Layer key={key} listening={false}>
        <Rect
          x={toPx(c.x_mm) - toPx(r)}
          y={toPx(c.y_mm) - toPx(r)}
          width={toPx(2 * r)}
          height={toPx(2 * r)}
          fill="#1f2937"
          cornerRadius={toPx(r)}
        />
        <Rect
          x={toPx(c.x_mm) - toPx(reach)}
          y={toPx(c.y_mm) - toPx(reach)}
          width={toPx(2 * reach)}
          height={toPx(2 * reach)}
          stroke="#94a3b8"
          strokeWidth={0.3}
          dash={[3, 3]}
          cornerRadius={toPx(reach)}
        />
      </Layer>
    )
  }
  if (c.type === 'conveyor') {
    const length = (c.dims.length_mm as number) ?? 0
    const width = (c.dims.width_mm as number) ?? 0
    const isVertical = Math.abs(((c.yaw_deg % 180) + 180) % 180 - 90) < 1e-3
    const wPx = toPx(isVertical ? width : length)
    const hPx = toPx(isVertical ? length : width)
    return (
      <Rect key={key} x={toPx(c.x_mm)} y={toPx(c.y_mm)} width={wPx} height={hPx} fill="#dbeafe" />
    )
  }
  if (c.type === 'pallet') {
    const length = (c.dims.length_mm as number) ?? 1200
    const width = (c.dims.width_mm as number) ?? 800
    return (
      <Rect
        key={key}
        x={toPx(c.x_mm)}
        y={toPx(c.y_mm)}
        width={toPx(length)}
        height={toPx(width)}
        fill="#fef3c7"
        stroke="#a16207"
        strokeWidth={0.4}
      />
    )
  }
  if (c.type === 'operator_zone') {
    const w = (c.dims.width_mm as number) ?? 1500
    const d = (c.dims.depth_mm as number) ?? 1500
    return (
      <Rect
        key={key}
        x={toPx(c.x_mm)}
        y={toPx(c.y_mm)}
        width={toPx(w)}
        height={toPx(d)}
        fill="rgba(34, 197, 94, 0.18)"
        stroke="#16a34a"
        strokeWidth={0.4}
      />
    )
  }
  return null
}
