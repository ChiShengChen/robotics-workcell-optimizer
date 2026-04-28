// Pareto scatter — three dimensions encoded in one chart:
//   X = footprint (m²)        — minimise
//   Y = system UPH            — maximise
//   Z = bare-arm cost (USD)   — bubble size, minimise
// Pareto frontier is computed across all three axes.

import { useMemo } from 'react'
import {
  CartesianGrid,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from 'recharts'

import { useLayoutStore } from '@/store/layoutStore'

interface ParetoPoint {
  id: string
  template: string
  footprint_m2: number
  uph: number
  cost_usd: number
  pareto: boolean
}

function computeFootprintM2(cellBounds: [number, number]): number {
  return (cellBounds[0] / 1000) * (cellBounds[1] / 1000)
}

/** Non-dominated set across (minimise footprint, maximise UPH, minimise cost).
 *  A point is on the Pareto frontier if no other point is at least as good
 *  on every axis AND strictly better on at least one. */
function computeFrontier(points: Omit<ParetoPoint, 'pareto'>[]): boolean[] {
  return points.map((p, i) =>
    points.every((q, j) => {
      if (j === i) return true
      const dominates =
        q.footprint_m2 <= p.footprint_m2 &&
        q.uph >= p.uph &&
        q.cost_usd <= p.cost_usd &&
        (q.footprint_m2 < p.footprint_m2 ||
          q.uph > p.uph ||
          q.cost_usd < p.cost_usd)
      return !dominates
    }),
  )
}

function fmtCost(v: number): string {
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000) return `$${(v / 1_000).toFixed(0)}k`
  return `$${v.toFixed(0)}`
}

export function ParetoScatter() {
  const proposals = useLayoutStore((s) => s.proposals)
  const activeId = useLayoutStore((s) => s.activeProposalId)

  const data = useMemo<ParetoPoint[]>(() => {
    const base = proposals.slice(-20).map((p) => ({
      id: p.proposal_id,
      template: p.template,
      footprint_m2: computeFootprintM2(p.cell_bounds_mm),
      uph: p.estimated_uph,
      cost_usd: p.estimated_cost_usd ?? 0,
    }))
    const frontier = computeFrontier(base)
    return base.map((p, i) => ({ ...p, pareto: frontier[i] }))
  }, [proposals])

  if (data.length === 0) return null

  const dominated = data.filter((d) => !d.pareto)
  const pareto = data.filter((d) => d.pareto)
  const activePt = data.find((d) => d.id === activeId)

  const minCost = Math.min(...data.map((d) => d.cost_usd))
  const maxCost = Math.max(...data.map((d) => d.cost_usd))

  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-[10px] uppercase tracking-wide text-slate-500">
        <span>Pareto · footprint × UPH × cost</span>
        <span className="text-[9px] normal-case tracking-normal text-slate-400">
          bubble size = cost ({fmtCost(minCost)}–{fmtCost(maxCost)})
        </span>
      </div>
      <div className="h-32">
        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart margin={{ top: 4, right: 4, bottom: 14, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
            <XAxis
              type="number"
              dataKey="footprint_m2"
              name="footprint"
              unit=" m²"
              tick={{ fontSize: 9 }}
              label={{
                value: 'footprint (m²)',
                position: 'insideBottom',
                offset: -2,
                fontSize: 9,
              }}
            />
            <YAxis
              type="number"
              dataKey="uph"
              name="UPH"
              tick={{ fontSize: 9 }}
              width={40}
            />
            <ZAxis
              type="number"
              dataKey="cost_usd"
              range={[40, 400]}
              name="cost"
            />
            <Tooltip
              cursor={{ strokeDasharray: '3 3' }}
              contentStyle={{ fontSize: 11 }}
              formatter={(v, name) => {
                if (typeof v !== 'number') return String(v)
                if (name === 'cost') return fmtCost(v)
                if (name === 'UPH') return v.toFixed(0)
                if (name === 'footprint') return `${v.toFixed(1)} m²`
                return v.toFixed(1)
              }}
              labelFormatter={() => ''}
            />
            <Scatter data={dominated} fill="#94a3b8" fillOpacity={0.55} />
            <Scatter data={pareto} fill="#0d9488" fillOpacity={0.85} />
            {activePt && (
              <Scatter data={[activePt]} fill="#3b82f6" shape="star" />
            )}
          </ScatterChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}
