// Optimize button + live progress sparkline + delta summary card.
// Sits below the aggregate score in ScoringPanel.

import { Loader2, StopCircle, TrendingUp, Zap } from 'lucide-react'
import { Line, LineChart, ResponsiveContainer, YAxis } from 'recharts'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { useLayoutStore } from '@/store/layoutStore'

const SUB_KEYS: { key: string; label: string }[] = [
  { key: 'safety_clearance', label: 'Safety' },
  { key: 'reach_margin', label: 'Reach' },
  { key: 'cycle_efficiency', label: 'Cycle' },
  { key: 'throughput_feasibility', label: 'Throughput' },
  { key: 'compactness', label: 'Compactness' },
  { key: 'aggregate', label: 'Aggregate' },
]

export function OptimizeBar() {
  const isOptimizing = useLayoutStore((s) => s.isOptimizing)
  const progress = useLayoutStore((s) => s.optimizationProgress)
  const lastOpt = useLayoutStore((s) => s.lastOptimization)
  const runOptimizeSA = useLayoutStore((s) => s.runOptimizeSA)
  const cancelOptimize = useLayoutStore((s) => s.cancelOptimize)
  const proposals = useLayoutStore((s) => s.proposals)
  const activeId = useLayoutStore((s) => s.activeProposalId)
  const hasActive = proposals.find((p) => p.proposal_id === activeId)

  return (
    <div className="space-y-2">
      <Separator />
      <div className="flex items-center gap-2">
        {!isOptimizing ? (
          <Button
            size="sm"
            className="flex-1"
            onClick={() => void runOptimizeSA(600)}
            disabled={!hasActive}
          >
            <Zap className="mr-1 h-3.5 w-3.5" /> Optimize (SA)
          </Button>
        ) : (
          <Button
            size="sm"
            variant="destructive"
            className="flex-1"
            onClick={() => cancelOptimize()}
          >
            <StopCircle className="mr-1 h-3.5 w-3.5" /> Cancel
          </Button>
        )}
        {isOptimizing && progress && (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-slate-400" />
        )}
      </div>

      {progress && (
        <div className="space-y-1">
          <div className="flex items-baseline justify-between text-[10px] text-slate-500">
            <span>iter {progress.iteration} / {progress.totalIterations}</span>
            <span className="tabular-nums">
              best {progress.bestScore.toFixed(3)} · cur {progress.currentScore.toFixed(3)}
            </span>
          </div>
          <div className="h-2 overflow-hidden rounded bg-slate-200">
            <div
              className="h-full bg-teal-500 transition-all"
              style={{ width: `${(progress.iteration / progress.totalIterations) * 100}%` }}
            />
          </div>
          <div className="h-12">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={progress.history}>
                <YAxis hide domain={[0, 1]} />
                <Line
                  type="monotone"
                  dataKey="current"
                  stroke="#94a3b8"
                  strokeWidth={1}
                  dot={false}
                  isAnimationActive={false}
                />
                <Line
                  type="monotone"
                  dataKey="best"
                  stroke="#0d9488"
                  strokeWidth={1.5}
                  dot={false}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {!isOptimizing && lastOpt && (
        <DeltaSummary delta={lastOpt.delta_summary} walltime={lastOpt.walltime_s} accepted={lastOpt.accepted} iterations={lastOpt.iterations} />
      )}
    </div>
  )
}

function DeltaSummary({
  delta,
  walltime,
  accepted,
  iterations,
}: {
  delta: Record<string, number>
  walltime: number
  accepted: number
  iterations: number
}) {
  return (
    <div className="rounded border border-teal-200 bg-teal-50 p-2 text-[11px]">
      <div className="mb-1 flex items-center gap-1 text-teal-700">
        <TrendingUp className="h-3.5 w-3.5" />
        <span className="font-medium">SA result</span>
        <Badge variant="secondary" className="ml-auto h-4 px-1.5 text-[9px]">
          {accepted}/{iterations} acc · {walltime.toFixed(2)}s
        </Badge>
      </div>
      <div className="grid grid-cols-2 gap-x-2 gap-y-0.5">
        {SUB_KEYS.map(({ key, label }) => {
          const v = delta[key] ?? 0
          const sign = v > 0 ? '+' : ''
          const cls = v > 0.001 ? 'text-emerald-600' : v < -0.001 ? 'text-red-600' : 'text-slate-500'
          return (
            <div key={key} className="flex items-baseline justify-between">
              <span className="text-slate-600">{label}</span>
              <span className={`font-medium tabular-nums ${cls}`}>
                {sign}
                {v.toFixed(3)}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
