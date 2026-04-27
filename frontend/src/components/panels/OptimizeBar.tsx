// Optimize panel: Tabs SA | CP-SAT.
// SA tab: streamed progress + delta summary.
// CP-SAT tab: synchronous solve + solver stats card.
// Bottom: "Compare SA vs CP-SAT" dialog showing both runs side by side.

import { useState } from 'react'
import { GitCompareArrows, Loader2, Rocket, StopCircle, TrendingUp, Zap } from 'lucide-react'
import { Line, LineChart, ResponsiveContainer, YAxis } from 'recharts'

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
import { Separator } from '@/components/ui/separator'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
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
  const isCPSATRunning = useLayoutStore((s) => s.isCPSATRunning)
  const progress = useLayoutStore((s) => s.optimizationProgress)
  const lastOpt = useLayoutStore((s) => s.lastOptimization)
  const lastCPSAT = useLayoutStore((s) => s.lastCPSAT)
  const runSA = useLayoutStore((s) => s.runOptimizeSA)
  const runCPSAT = useLayoutStore((s) => s.runOptimizeCPSAT)
  const cancelOptimize = useLayoutStore((s) => s.cancelOptimize)
  const proposals = useLayoutStore((s) => s.proposals)
  const activeId = useLayoutStore((s) => s.activeProposalId)
  const hasActive = proposals.find((p) => p.proposal_id === activeId)

  return (
    <div className="space-y-2">
      <Separator />
      <Tabs defaultValue="sa">
        <TabsList className="h-7 w-full">
          <TabsTrigger value="sa" className="h-6 text-[11px]">
            <Zap className="mr-1 h-3 w-3" /> SA
          </TabsTrigger>
          <TabsTrigger value="cpsat" className="h-6 text-[11px]">
            <Rocket className="mr-1 h-3 w-3" /> CP-SAT
          </TabsTrigger>
        </TabsList>

        <TabsContent value="sa" className="mt-2 space-y-2">
          {!isOptimizing ? (
            <Button
              size="sm"
              className="w-full"
              onClick={() => void runSA(600)}
              disabled={!hasActive}
            >
              <Zap className="mr-1 h-3.5 w-3.5" /> Optimize (SA)
            </Button>
          ) : (
            <Button
              size="sm"
              variant="destructive"
              className="w-full"
              onClick={() => cancelOptimize()}
            >
              <StopCircle className="mr-1 h-3.5 w-3.5" /> Cancel
            </Button>
          )}

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
                    <Line type="monotone" dataKey="current" stroke="#94a3b8" strokeWidth={1} dot={false} isAnimationActive={false} />
                    <Line type="monotone" dataKey="best" stroke="#0d9488" strokeWidth={1.5} dot={false} isAnimationActive={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}

          {!isOptimizing && lastOpt && (
            <DeltaSummary
              kind="SA"
              delta={lastOpt.delta_summary}
              walltime={lastOpt.walltime_s}
              footer={`${lastOpt.accepted}/${lastOpt.iterations} acc`}
            />
          )}
        </TabsContent>

        <TabsContent value="cpsat" className="mt-2 space-y-2">
          <Button
            size="sm"
            className="w-full"
            onClick={() => void runCPSAT(10)}
            disabled={!hasActive || isCPSATRunning}
          >
            {isCPSATRunning ? (
              <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Rocket className="mr-1 h-3.5 w-3.5" />
            )}
            Refine (CP-SAT, 10s)
          </Button>

          {lastCPSAT && (
            <SolverStatsCard stats={lastCPSAT.solver_stats} />
          )}

          {lastCPSAT && (
            <DeltaSummary
              kind="CP-SAT"
              delta={lastCPSAT.delta_summary}
              walltime={lastCPSAT.solver_stats.walltime_s}
              footer={lastCPSAT.solver_stats.status}
            />
          )}
        </TabsContent>
      </Tabs>

      <CompareDialog />
    </div>
  )
}

function SolverStatsCard({ stats }: { stats: NonNullable<ReturnType<typeof useLayoutStore.getState>['lastCPSAT']>['solver_stats'] }) {
  const variant = stats.status === 'OPTIMAL' ? 'default' : stats.feasible ? 'secondary' : 'destructive'
  return (
    <div className="rounded border border-violet-200 bg-violet-50 p-2 text-[11px]">
      <div className="mb-1 flex items-center justify-between">
        <span className="font-medium text-violet-700">Solver stats</span>
        <Badge variant={variant} className="h-4 px-1.5 text-[9px]">
          {stats.status}
        </Badge>
      </div>
      <div className="grid grid-cols-2 gap-x-2 gap-y-0.5">
        <span className="text-slate-500">Objective</span>
        <span className="font-medium tabular-nums">{stats.objective.toFixed(0)} mm</span>
        <span className="text-slate-500">Walltime</span>
        <span className="font-medium tabular-nums">{stats.walltime_s.toFixed(2)} s</span>
        <span className="text-slate-500">Branches</span>
        <span className="font-medium tabular-nums">{stats.num_branches.toLocaleString()}</span>
        <span className="text-slate-500">Conflicts</span>
        <span className="font-medium tabular-nums">{stats.num_conflicts.toLocaleString()}</span>
      </div>
    </div>
  )
}

function DeltaSummary({
  kind,
  delta,
  walltime,
  footer,
}: {
  kind: 'SA' | 'CP-SAT'
  delta: Record<string, number>
  walltime: number
  footer: string
}) {
  const tone = kind === 'SA' ? 'border-teal-200 bg-teal-50 text-teal-700' : 'border-violet-200 bg-violet-50 text-violet-700'
  return (
    <div className={`rounded border p-2 text-[11px] ${tone}`}>
      <div className="mb-1 flex items-center gap-1">
        <TrendingUp className="h-3.5 w-3.5" />
        <span className="font-medium">{kind} result</span>
        <Badge variant="secondary" className="ml-auto h-4 px-1.5 text-[9px]">
          {footer} · {walltime.toFixed(2)}s
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
              <span className={`font-medium tabular-nums ${cls}`}>{sign}{v.toFixed(3)}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function CompareDialog() {
  const lastOpt = useLayoutStore((s) => s.lastOptimization)
  const lastCPSAT = useLayoutStore((s) => s.lastCPSAT)
  const [open, setOpen] = useState(false)
  const both = lastOpt && lastCPSAT
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" variant="outline" className="w-full" disabled={!both}>
          <GitCompareArrows className="mr-1 h-3.5 w-3.5" /> Compare SA vs CP-SAT
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-[640px]">
        <DialogHeader>
          <DialogTitle>SA vs CP-SAT — last runs</DialogTitle>
          <DialogDescription>
            Side-by-side score breakdown and solver telemetry.
          </DialogDescription>
        </DialogHeader>
        {both && (
          <div className="grid grid-cols-2 gap-3 text-xs">
            <div className="rounded border border-teal-200 bg-teal-50 p-2">
              <div className="mb-1 font-medium text-teal-700">SA</div>
              <ScoreTable seed={lastOpt.seed_score} optimized={lastOpt.optimized_score} />
              <div className="mt-2 grid grid-cols-2 gap-x-2 text-[11px]">
                <span className="text-slate-500">Walltime</span>
                <span className="tabular-nums">{lastOpt.walltime_s.toFixed(2)} s</span>
                <span className="text-slate-500">Iterations</span>
                <span className="tabular-nums">{lastOpt.iterations}</span>
                <span className="text-slate-500">Accepted</span>
                <span className="tabular-nums">{lastOpt.accepted}</span>
              </div>
            </div>
            <div className="rounded border border-violet-200 bg-violet-50 p-2">
              <div className="mb-1 font-medium text-violet-700">CP-SAT</div>
              <ScoreTable seed={lastCPSAT.seed_score} optimized={lastCPSAT.optimized_score} />
              <div className="mt-2 grid grid-cols-2 gap-x-2 text-[11px]">
                <span className="text-slate-500">Status</span>
                <span className="tabular-nums">{lastCPSAT.solver_stats.status}</span>
                <span className="text-slate-500">Objective</span>
                <span className="tabular-nums">{lastCPSAT.solver_stats.objective.toFixed(0)} mm</span>
                <span className="text-slate-500">Walltime</span>
                <span className="tabular-nums">{lastCPSAT.solver_stats.walltime_s.toFixed(2)} s</span>
                <span className="text-slate-500">Branches</span>
                <span className="tabular-nums">{lastCPSAT.solver_stats.num_branches.toLocaleString()}</span>
              </div>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

function ScoreTable({ seed, optimized }: { seed: { aggregate: number; compactness: number; reach_margin: number; cycle_efficiency: number; safety_clearance: number; throughput_feasibility: number }; optimized: { aggregate: number; compactness: number; reach_margin: number; cycle_efficiency: number; safety_clearance: number; throughput_feasibility: number } }) {
  const rows: { key: keyof typeof seed; label: string }[] = [
    { key: 'aggregate', label: 'Aggregate' },
    { key: 'compactness', label: 'Compactness' },
    { key: 'reach_margin', label: 'Reach' },
    { key: 'cycle_efficiency', label: 'Cycle' },
    { key: 'safety_clearance', label: 'Safety' },
    { key: 'throughput_feasibility', label: 'Throughput' },
  ]
  return (
    <table className="w-full text-[10px]">
      <thead>
        <tr className="text-slate-500">
          <th className="text-left">Metric</th>
          <th className="text-right">Seed</th>
          <th className="text-right">After</th>
          <th className="text-right">Δ</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(({ key, label }) => {
          const d = optimized[key] - seed[key]
          const cls = d > 0.001 ? 'text-emerald-700' : d < -0.001 ? 'text-red-700' : 'text-slate-500'
          return (
            <tr key={key}>
              <td>{label}</td>
              <td className="text-right tabular-nums">{seed[key].toFixed(2)}</td>
              <td className="text-right tabular-nums">{optimized[key].toFixed(2)}</td>
              <td className={`text-right tabular-nums ${cls}`}>
                {d > 0 ? '+' : ''}{d.toFixed(2)}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
