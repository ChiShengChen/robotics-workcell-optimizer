// Side-by-side compare: two proposals as larger Konva thumbnails + score
// delta table. Triggered from the variants-strip toolbar.

import { Columns2 } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Layer, Line, Rect, Stage } from 'react-konva'

import type { LayoutProposal, PlacedComponent, ScoreBreakdown } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { useLayoutStore } from '@/store/layoutStore'

const CANVAS_W = 380
const CANVAS_H = 280
const PAD = 12

const SUB_KEYS: { key: keyof ScoreBreakdown; label: string }[] = [
  { key: 'safety_clearance', label: 'Safety' },
  { key: 'reach_margin', label: 'Reach' },
  { key: 'cycle_efficiency', label: 'Cycle' },
  { key: 'throughput_feasibility', label: 'Throughput' },
  { key: 'compactness', label: 'Compactness' },
]

function fmtUSD(v: number): string {
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`
  if (v >= 1_000) return `$${(v / 1_000).toFixed(0)}k`
  return `$${v.toFixed(0)}`
}

function CompareCanvas({ proposal }: { proposal: LayoutProposal }) {
  const [cellW, cellH] = proposal.cell_bounds_mm
  const usableW = CANVAS_W - 2 * PAD
  const usableH = CANVAS_H - 2 * PAD
  const mmPerPx = Math.max(cellW / usableW, cellH / usableH)
  const cellWPx = cellW / mmPerPx
  const cellHPx = cellH / mmPerPx
  const offX = (CANVAS_W - cellWPx) / 2
  const offY = (CANVAS_H - cellHPx) / 2
  return (
    <Stage width={CANVAS_W} height={CANVAS_H}>
      <Layer x={offX} y={offY + cellHPx} scaleY={-1} listening={false}>
        <Rect
          width={cellWPx}
          height={cellHPx}
          fill="#fff"
          stroke="#cbd5e1"
          strokeWidth={1}
        />
        {proposal.components.map((c) => renderCmp(c, mmPerPx))}
      </Layer>
    </Stage>
  )
}

function renderCmp(c: PlacedComponent, mmPerPx: number) {
  const toPx = (mm: number) => mm / mmPerPx
  if (c.type === 'fence') {
    const poly = (c.dims.polyline as number[][] | undefined) ?? []
    if (poly.length < 2) return null
    const points = poly.flatMap(([x, y]) => [toPx(x), toPx(y)])
    return (
      <Line
        key={c.id}
        points={points}
        stroke="#dc2626"
        strokeWidth={1.4}
        dash={[5, 4]}
        closed
      />
    )
  }
  if (c.type === 'robot') {
    const r = (c.dims.base_radius_mm as number | undefined) ?? 350
    const reach = (c.dims.reach_mm as number | undefined) ?? 2400
    return (
      <Layer key={c.id} listening={false}>
        <Rect
          x={toPx(c.x_mm) - toPx(reach)}
          y={toPx(c.y_mm) - toPx(reach)}
          width={toPx(2 * reach)}
          height={toPx(2 * reach)}
          stroke="#94a3b8"
          strokeWidth={0.6}
          dash={[5, 4]}
          cornerRadius={toPx(reach)}
        />
        <Rect
          x={toPx(c.x_mm) - toPx(r)}
          y={toPx(c.y_mm) - toPx(r)}
          width={toPx(2 * r)}
          height={toPx(2 * r)}
          fill="#1f2937"
          cornerRadius={toPx(r)}
        />
      </Layer>
    )
  }
  if (c.type === 'conveyor') {
    const length = (c.dims.length_mm as number) ?? 0
    const width = (c.dims.width_mm as number) ?? 0
    const isVertical =
      Math.abs((((c.yaw_deg as number) % 180) + 180) % 180 - 90) < 1e-3
    return (
      <Rect
        key={c.id}
        x={toPx(c.x_mm)}
        y={toPx(c.y_mm)}
        width={toPx(isVertical ? width : length)}
        height={toPx(isVertical ? length : width)}
        fill="#dbeafe"
        stroke="#3b82f6"
        strokeWidth={0.6}
      />
    )
  }
  if (c.type === 'pallet') {
    const length = (c.dims.length_mm as number) ?? 1200
    const width = (c.dims.width_mm as number) ?? 800
    return (
      <Rect
        key={c.id}
        x={toPx(c.x_mm)}
        y={toPx(c.y_mm)}
        width={toPx(length)}
        height={toPx(width)}
        fill="#fef3c7"
        stroke="#a16207"
        strokeWidth={0.6}
      />
    )
  }
  if (c.type === 'operator_zone') {
    return (
      <Rect
        key={c.id}
        x={toPx(c.x_mm)}
        y={toPx(c.y_mm)}
        width={toPx((c.dims.width_mm as number) ?? 1500)}
        height={toPx((c.dims.depth_mm as number) ?? 1500)}
        fill="rgba(34, 197, 94, 0.18)"
        stroke="#16a34a"
        strokeWidth={0.6}
      />
    )
  }
  return null
}

interface RowProps {
  label: string
  left: number | string
  right: number | string
  format?: (v: number) => string
  betterDirection?: 'higher' | 'lower'
}

function CompareRow({
  label,
  left,
  right,
  format = (v) => v.toFixed(2),
  betterDirection,
}: RowProps) {
  const lNum = typeof left === 'number' ? left : null
  const rNum = typeof right === 'number' ? right : null
  let leftWins = false
  let rightWins = false
  if (lNum !== null && rNum !== null && betterDirection && lNum !== rNum) {
    if (betterDirection === 'higher') {
      leftWins = lNum > rNum
      rightWins = rNum > lNum
    } else {
      leftWins = lNum < rNum
      rightWins = rNum < lNum
    }
  }
  return (
    <tr className="border-b border-slate-100">
      <td className="py-1 text-[10px] uppercase tracking-wide text-slate-500">
        {label}
      </td>
      <td
        className={`py-1 text-right tabular-nums ${leftWins ? 'font-bold text-emerald-700' : 'text-slate-700'}`}
      >
        {lNum !== null ? format(lNum) : left}
      </td>
      <td
        className={`py-1 text-right tabular-nums ${rightWins ? 'font-bold text-emerald-700' : 'text-slate-700'}`}
      >
        {rNum !== null ? format(rNum) : right}
      </td>
    </tr>
  )
}

export function SideBySideCompare() {
  const proposals = useLayoutStore((s) => s.proposals)
  const scoreByProposal = useLayoutStore((s) => s.scoreByProposal)
  const activeId = useLayoutStore((s) => s.activeProposalId)

  const [open, setOpen] = useState(false)
  const [leftId, setLeftId] = useState<string | null>(null)
  const [rightId, setRightId] = useState<string | null>(null)

  const left = useMemo(
    () =>
      proposals.find((p) => p.proposal_id === leftId) ??
      proposals.find((p) => p.proposal_id === activeId) ??
      proposals[0] ??
      null,
    [proposals, leftId, activeId],
  )
  const right = useMemo(
    () =>
      proposals.find((p) => p.proposal_id === rightId) ??
      proposals.find((p) => p.proposal_id !== left?.proposal_id) ??
      null,
    [proposals, rightId, left],
  )

  const lScore = left ? scoreByProposal[left.proposal_id] : null
  const rScore = right ? scoreByProposal[right.proposal_id] : null

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          size="sm"
          variant="outline"
          disabled={proposals.length < 2}
          className="gap-1"
          title="Side-by-side compare"
        >
          <Columns2 className="h-3.5 w-3.5" />
          A vs B
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-4xl">
        <DialogHeader>
          <DialogTitle className="text-base">Side-by-side compare</DialogTitle>
          <DialogDescription className="text-xs">
            Pick two proposals; the better-on-each-axis cell is highlighted
            in green.
          </DialogDescription>
        </DialogHeader>
        {!left || !right ? (
          <div className="py-6 text-center text-sm text-slate-500">
            Need at least two proposals to compare.
          </div>
        ) : (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <select
                  value={left.proposal_id}
                  onChange={(e) => setLeftId(e.target.value)}
                  className="w-full rounded border border-input bg-background px-2 py-1 text-xs"
                >
                  {proposals.map((p) => (
                    <option key={p.proposal_id} value={p.proposal_id}>
                      {p.template} · {p.robot_model_ids?.join(', ') || '—'}
                    </option>
                  ))}
                </select>
                <div className="rounded border border-slate-200 bg-slate-50">
                  <CompareCanvas proposal={left} />
                </div>
              </div>
              <div className="space-y-1">
                <select
                  value={right.proposal_id}
                  onChange={(e) => setRightId(e.target.value)}
                  className="w-full rounded border border-input bg-background px-2 py-1 text-xs"
                >
                  {proposals.map((p) => (
                    <option key={p.proposal_id} value={p.proposal_id}>
                      {p.template} · {p.robot_model_ids?.join(', ') || '—'}
                    </option>
                  ))}
                </select>
                <div className="rounded border border-slate-200 bg-slate-50">
                  <CompareCanvas proposal={right} />
                </div>
              </div>
            </div>

            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-slate-200 text-[10px] uppercase tracking-wide text-slate-500">
                  <th className="py-1.5 text-left">Metric</th>
                  <th className="py-1.5 text-right">Left</th>
                  <th className="py-1.5 text-right">Right</th>
                </tr>
              </thead>
              <tbody>
                <CompareRow
                  label="Template"
                  left={left.template}
                  right={right.template}
                />
                <CompareRow
                  label="Arms"
                  left={left.robot_model_ids?.length ?? 0}
                  right={right.robot_model_ids?.length ?? 0}
                  format={(v) => v.toFixed(0)}
                />
                <CompareRow
                  label="Cycle (s)"
                  left={left.estimated_cycle_time_s}
                  right={right.estimated_cycle_time_s}
                  betterDirection="lower"
                />
                <CompareRow
                  label="System UPH"
                  left={left.estimated_uph}
                  right={right.estimated_uph}
                  format={(v) => v.toFixed(0)}
                  betterDirection="higher"
                />
                <CompareRow
                  label="Bare-arm cost"
                  left={left.estimated_cost_usd}
                  right={right.estimated_cost_usd}
                  format={fmtUSD}
                  betterDirection="lower"
                />
                {left.cost_breakdown && right.cost_breakdown && (
                  <>
                    <CompareRow
                      label="Total cell cost"
                      left={left.cost_breakdown.grand_total_usd}
                      right={right.cost_breakdown.grand_total_usd}
                      format={fmtUSD}
                      betterDirection="lower"
                    />
                    <CompareRow
                      label="Payback"
                      left={left.cost_breakdown.payback_months}
                      right={right.cost_breakdown.payback_months}
                      format={(v) => `${v.toFixed(1)} mo`}
                      betterDirection="lower"
                    />
                  </>
                )}
                {lScore && rScore && (
                  <>
                    {SUB_KEYS.map((s) => (
                      <CompareRow
                        key={s.key as string}
                        label={s.label}
                        left={lScore[s.key] as number}
                        right={rScore[s.key] as number}
                        betterDirection="higher"
                      />
                    ))}
                    <tr className="border-t-2 border-slate-300">
                      <td className="py-1.5 text-[11px] font-bold uppercase tracking-wide text-slate-700">
                        Aggregate
                      </td>
                      <td className="py-1.5 text-right">
                        <Badge className="tabular-nums">
                          {(lScore.aggregate * 100).toFixed(0)}
                        </Badge>
                      </td>
                      <td className="py-1.5 text-right">
                        <Badge className="tabular-nums">
                          {(rScore.aggregate * 100).toFixed(0)}
                        </Badge>
                      </td>
                    </tr>
                  </>
                )}
              </tbody>
            </table>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
