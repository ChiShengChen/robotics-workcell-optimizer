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
  const robot = proposal?.components.find((c) => c.type === 'robot') ?? null

  if (!proposal) return null

  const reach = robot?.dims.reach_mm as number | undefined
  const eff = robot?.dims.effective_reach_mm as number | undefined
  const footL = robot?.dims.footprint_l_mm as number | undefined
  const footW = robot?.dims.footprint_w_mm as number | undefined

  return (
    <Card className="rounded-lg border-slate-200">
      <CardHeader className="space-y-1 pb-2">
        <CardTitle className="flex items-center gap-1 text-sm font-semibold uppercase tracking-wide text-slate-600">
          <Bot className="h-4 w-4" /> Selected robot
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-1 text-xs">
        {!proposal.robot_model_id ? (
          <Badge variant="destructive" className="text-[10px]">
            No feasible robot
          </Badge>
        ) : (
          <>
            <div className="text-sm font-semibold text-slate-800">{proposal.robot_model_id}</div>
            <div className="grid grid-cols-2 gap-y-1 text-[11px] text-slate-600">
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
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}
