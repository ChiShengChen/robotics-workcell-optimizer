// Pareto scatter of footprint (m²) vs UPH; non-dominated points highlighted.

import { useMemo } from 'react'
import { CartesianGrid, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from 'recharts'

import { useLayoutStore } from '@/store/layoutStore'

interface ParetoPoint {
  id: string
  template: string
  footprint_m2: number
  uph: number
  pareto: boolean
}

function computeFootprintM2(cellBounds: [number, number]): number {
  return (cellBounds[0] / 1000) * (cellBounds[1] / 1000)
}

/** Non-dominated set: minimize footprint AND maximize UPH. A point is on the
 *  Pareto frontier if no other point has BOTH smaller footprint AND larger UPH. */
function computeFrontier(points: Omit<ParetoPoint, 'pareto'>[]): boolean[] {
  return points.map((p, i) =>
    points.every((q, j) =>
      j === i ||
      // q does NOT strictly dominate p
      !(q.footprint_m2 <= p.footprint_m2 && q.uph >= p.uph &&
        (q.footprint_m2 < p.footprint_m2 || q.uph > p.uph)),
    ),
  )
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
    }))
    const frontier = computeFrontier(base)
    return base.map((p, i) => ({ ...p, pareto: frontier[i] }))
  }, [proposals])

  if (data.length === 0) return null

  const dominated = data.filter((d) => !d.pareto)
  const pareto = data.filter((d) => d.pareto)
  const activePt = data.find((d) => d.id === activeId)

  return (
    <div>
      <div className="mb-1 text-[10px] uppercase tracking-wide text-slate-500">
        Pareto · footprint vs UPH
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
              label={{ value: 'footprint (m²)', position: 'insideBottom', offset: -2, fontSize: 9 }}
            />
            <YAxis
              type="number"
              dataKey="uph"
              name="UPH"
              tick={{ fontSize: 9 }}
              width={40}
            />
            <Tooltip
              cursor={{ strokeDasharray: '3 3' }}
              contentStyle={{ fontSize: 11 }}
              formatter={(v) => (typeof v === 'number' ? v.toFixed(1) : String(v))}
              labelFormatter={() => ''}
            />
            <Scatter data={dominated} fill="#94a3b8" />
            <Scatter data={pareto} fill="#0d9488" />
            {activePt && <Scatter data={[activePt]} fill="#3b82f6" shape="star" />}
          </ScatterChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}
