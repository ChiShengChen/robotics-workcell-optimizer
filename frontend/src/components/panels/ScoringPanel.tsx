// Five sub-score progress bars + aggregate circle + violations list + history sparkline.

import { Activity, AlertTriangle, Clock, Gauge, Loader2 } from 'lucide-react'
import { Line, LineChart, ResponsiveContainer, YAxis } from 'recharts'

import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import { cn } from '@/lib/utils'
import {
  getActiveProposal,
  getActiveScore,
  useLayoutStore,
} from '@/store/layoutStore'
import type { ScoreBreakdown } from '@/api/types'

const SUB_SCORES: {
  key: keyof Pick<
    ScoreBreakdown,
    'compactness' | 'reach_margin' | 'cycle_efficiency' | 'safety_clearance' | 'throughput_feasibility'
  >
  label: string
  hint: string
}[] = [
  { key: 'safety_clearance', label: 'Safety (ISO 13855)', hint: 'Fence ≥ S=K·T+C from any body' },
  { key: 'reach_margin', label: 'Reach margin', hint: 'Targets within 0.85·R_max envelope' },
  { key: 'cycle_efficiency', label: 'Cycle efficiency', hint: 'Trapezoidal motion vs cph_std' },
  { key: 'throughput_feasibility', label: 'Throughput', hint: 'UPH / target, sat at 1.1×' },
  { key: 'compactness', label: 'Compactness', hint: 'Bbox utilization + aspect penalty' },
]

export function ScoringPanel() {
  const proposal = useLayoutStore(getActiveProposal)
  const score = useLayoutStore(getActiveScore)
  const isScoring = useLayoutStore((s) => s.isScoring)
  const history = useLayoutStore((s) => s.scoreHistory)

  return (
    <Card className="rounded-lg border-slate-200">
      <CardHeader className="space-y-1 pb-2">
        <CardTitle className="flex items-center justify-between text-sm font-semibold uppercase tracking-wide text-slate-600">
          <span>3 · Scoring</span>
          {isScoring && <Loader2 className="h-3.5 w-3.5 animate-spin text-slate-400" />}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-xs">
        {!proposal ? (
          <p className="text-center text-xs text-slate-400">
            Generate a layout to see scoring.
          </p>
        ) : (
          <>
            <AggregateCircle score={score?.aggregate ?? null} />

            <div className="space-y-2">
              {SUB_SCORES.map(({ key, label, hint }) => (
                <ScoreBar
                  key={key}
                  label={label}
                  hint={hint}
                  value={score ? score[key] : 0}
                  loading={!score}
                />
              ))}
            </div>

            <Separator />

            <div className="grid grid-cols-2 gap-y-1 text-[11px]">
              <span className="flex items-center gap-1 text-slate-500">
                <Clock className="h-3.5 w-3.5" /> Cycle
              </span>
              <span className="font-medium">
                {proposal.estimated_cycle_time_s.toFixed(2)} s
              </span>
              <span className="flex items-center gap-1 text-slate-500">
                <Gauge className="h-3.5 w-3.5" /> UPH
              </span>
              <span className="font-medium">{Math.round(proposal.estimated_uph)}</span>
              <span className="flex items-center gap-1 text-slate-500">
                <Activity className="h-3.5 w-3.5" /> Template
              </span>
              <span className="font-medium">{proposal.template}</span>
            </div>

            <ViolationsList violations={score?.violations ?? []} />

            {history.length > 1 && (
              <>
                <Separator />
                <div>
                  <div className="mb-1 text-[10px] uppercase tracking-wide text-slate-500">
                    History (last {history.length})
                  </div>
                  <div className="h-12">
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart data={history.map((v, i) => ({ i, v }))}>
                        <YAxis hide domain={[0, 1]} />
                        <Line
                          type="monotone"
                          dataKey="v"
                          stroke="#0d9488"
                          strokeWidth={1.5}
                          dot={false}
                          isAnimationActive={false}
                        />
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                </div>
              </>
            )}
          </>
        )}
      </CardContent>
    </Card>
  )
}

function AggregateCircle({ score }: { score: number | null }) {
  const v = score ?? 0
  const color = v >= 0.7 ? '#16a34a' : v >= 0.4 ? '#f59e0b' : '#dc2626'
  const r = 28
  const c = 2 * Math.PI * r
  const dash = c * v
  return (
    <div className="flex items-center justify-center pb-1">
      <div className="relative h-20 w-20">
        <svg viewBox="0 0 80 80" className="h-full w-full -rotate-90">
          <circle cx="40" cy="40" r={r} stroke="#e2e8f0" strokeWidth="6" fill="none" />
          <circle
            cx="40"
            cy="40"
            r={r}
            stroke={color}
            strokeWidth="6"
            fill="none"
            strokeDasharray={`${dash} ${c}`}
            strokeLinecap="round"
            style={{ transition: 'stroke-dasharray 220ms ease-out, stroke 220ms' }}
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-base font-semibold text-slate-800">
            {score === null ? '—' : `${Math.round(v * 100)}`}
          </span>
          <span className="text-[9px] uppercase tracking-wide text-slate-400">Score</span>
        </div>
      </div>
    </div>
  )
}

function ScoreBar({
  label,
  hint,
  value,
  loading,
}: {
  label: string
  hint: string
  value: number
  loading?: boolean
}) {
  const pct = Math.max(0, Math.min(1, value)) * 100
  const color = value >= 0.7 ? 'bg-green-500' : value >= 0.4 ? 'bg-amber-500' : 'bg-red-500'
  return (
    <div title={hint}>
      <div className="flex items-baseline justify-between text-[11px]">
        <span className="text-slate-700">{label}</span>
        <span className={cn('tabular-nums', loading ? 'text-slate-400' : 'text-slate-700')}>
          {loading ? '…' : value.toFixed(2)}
        </span>
      </div>
      <div className="mt-0.5 h-1.5 overflow-hidden rounded bg-slate-200">
        <div
          className={cn('h-full transition-all duration-200', color)}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

function ViolationsList({ violations }: { violations: ScoreBreakdown['violations'] }) {
  if (violations.length === 0) return null
  const hard = violations.filter((v) => v.severity === 'hard')
  const soft = violations.filter((v) => v.severity === 'soft')
  return (
    <>
      <Separator />
      <div>
        <div className="mb-1 flex items-center gap-1 text-slate-500">
          <AlertTriangle className="h-3.5 w-3.5 text-amber-500" />
          Violations
          <Badge variant="destructive" className="h-4 px-1.5 text-[10px]">
            {hard.length} hard
          </Badge>
          {soft.length > 0 && (
            <Badge variant="secondary" className="h-4 px-1.5 text-[10px]">
              {soft.length} soft
            </Badge>
          )}
        </div>
        <ul className="space-y-1 text-[10px]">
          {violations.map((v, i) => (
            <li
              key={i}
              className={cn(
                'rounded px-1.5 py-1',
                v.severity === 'hard'
                  ? 'border border-red-200 bg-red-50 text-red-700'
                  : 'border border-amber-200 bg-amber-50 text-amber-700',
              )}
            >
              <span className="font-medium uppercase">{v.kind.replace('_', ' ')}</span>
              {' · '}
              {v.message}
              {v.component_ids.length > 0 && (
                <span className="ml-1 text-slate-500">[{v.component_ids.join(', ')}]</span>
              )}
            </li>
          ))}
        </ul>
      </div>
    </>
  )
}
