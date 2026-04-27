// Phase 4 will fill this in. For now, show the cycle/UPH summary from the
// active proposal so the right column isn't empty.

import { Activity, Clock, Gauge } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import { useLayoutStore } from '@/store/layoutStore'

export function ScoringPanel() {
  const proposals = useLayoutStore((s) => s.proposals)
  const activeId = useLayoutStore((s) => s.activeProposalId)
  const proposal = proposals.find((p) => p.proposal_id === activeId) ?? null

  return (
    <Card className="rounded-lg border-slate-200">
      <CardHeader className="space-y-1 pb-2">
        <CardTitle className="text-sm font-semibold uppercase tracking-wide text-slate-600">
          3 · Scoring
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-xs">
        {!proposal ? (
          <p className="text-center text-xs text-slate-400">
            Generate a layout to see scoring.
          </p>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-y-1">
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
            <Separator />
            <div>
              <div className="text-slate-500">Rationale</div>
              <div className="mt-1 text-[11px] text-slate-700">{proposal.rationale}</div>
            </div>
            {proposal.assumptions.length > 0 && (
              <>
                <Separator />
                <div>
                  <div className="flex items-center gap-1 text-slate-500">
                    Layout assumptions{' '}
                    <Badge variant="secondary" className="h-4 px-1.5 text-[10px]">
                      {proposal.assumptions.length}
                    </Badge>
                  </div>
                  <ul className="mt-1 list-disc space-y-1 pl-5 text-[11px] text-slate-600">
                    {proposal.assumptions.map((a, i) => (
                      <li key={i}>{a}</li>
                    ))}
                  </ul>
                </div>
              </>
            )}
            <Separator />
            <p className="text-[10px] italic text-slate-400">
              Phase 4: 5-component score breakdown + drag-violation feedback will live here.
            </p>
          </>
        )}
      </CardContent>
    </Card>
  )
}
