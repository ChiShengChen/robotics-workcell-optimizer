// Selected robot details. Catalog isn't fetched cross-network in Phase 3 —
// we read what's embedded in the proposal's PlacedComponent + robot_model_id.

import { Bot } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useLayoutStore } from '@/store/layoutStore'

export function RobotInfoPanel() {
  const proposals = useLayoutStore((s) => s.proposals)
  const activeId = useLayoutStore((s) => s.activeProposalId)
  const proposal = proposals.find((p) => p.proposal_id === activeId) ?? null

  if (!proposal) return null

  const robots = proposal.components.filter((c) => c.type === 'robot')

  return (
    <Card className="rounded-lg border-slate-200">
      <CardHeader className="space-y-1 pb-2">
        <CardTitle className="flex items-center gap-1 text-sm font-semibold uppercase tracking-wide text-slate-600">
          <Bot className="h-4 w-4" /> Robots ({robots.length || 1})
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-xs">
        {robots.length === 0 || !proposal.robot_model_id ? (
          <Badge variant="destructive" className="text-[10px]">
            No feasible robot
          </Badge>
        ) : (
          robots.map((r, idx) => {
            const modelId =
              (r.dims.model_id as string | undefined) ??
              proposal.robot_model_ids?.[idx] ??
              proposal.robot_model_id
            const reach = r.dims.reach_mm as number | undefined
            const eff = r.dims.effective_reach_mm as number | undefined
            const footL = r.dims.footprint_l_mm as number | undefined
            const footW = r.dims.footprint_w_mm as number | undefined
            const assigned = proposal.task_assignment?.[r.id]
            return (
              <div key={r.id} className="space-y-1 border-l-2 border-slate-200 pl-2">
                <div className="flex items-baseline justify-between">
                  <span className="text-sm font-semibold text-slate-800">{modelId}</span>
                  <span className="text-[10px] text-slate-400">{r.id}</span>
                </div>
                <div className="grid grid-cols-2 gap-y-0.5 text-[11px] text-slate-600">
                  {reach && (
                    <>
                      <span className="text-slate-400">Reach</span>
                      <span>{reach.toFixed(0)} mm</span>
                    </>
                  )}
                  {eff && (
                    <>
                      <span className="text-slate-400">Effective (×0.85)</span>
                      <span>{eff.toFixed(0)} mm</span>
                    </>
                  )}
                  {footL && footW && (
                    <>
                      <span className="text-slate-400">Footprint</span>
                      <span>
                        {footL.toFixed(0)} × {footW.toFixed(0)} mm
                      </span>
                    </>
                  )}
                  {assigned && assigned.length > 0 && (
                    <>
                      <span className="text-slate-400">Tasks</span>
                      <span className="truncate">{assigned.join(', ')}</span>
                    </>
                  )}
                </div>
              </div>
            )
          })
        )}
      </CardContent>
    </Card>
  )
}
