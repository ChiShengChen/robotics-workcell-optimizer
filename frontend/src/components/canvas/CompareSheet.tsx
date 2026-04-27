// Compare all variants side-by-side in a Sheet (shadcn).

import { Layout, Plus } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from '@/components/ui/sheet'
import { useLayoutStore } from '@/store/layoutStore'

const SUB_KEYS: { key: string; label: string }[] = [
  { key: 'safety_clearance', label: 'Safety' },
  { key: 'reach_margin', label: 'Reach' },
  { key: 'cycle_efficiency', label: 'Cycle' },
  { key: 'throughput_feasibility', label: 'Throughput' },
  { key: 'compactness', label: 'Compactness' },
]

export function CompareSheet() {
  const proposals = useLayoutStore((s) => s.proposals)
  const scoreByProposal = useLayoutStore((s) => s.scoreByProposal)
  const isGenerating = useLayoutStore((s) => s.isGenerating)
  const runGenerate = useLayoutStore((s) => s.runGenerate)
  const setActive = useLayoutStore((s) => s.setActiveProposal)

  return (
    <Sheet>
      <div className="flex items-center gap-2">
        <SheetTrigger asChild>
          <Button size="sm" variant="outline" disabled={proposals.length === 0}>
            <Layout className="mr-1 h-3.5 w-3.5" /> Compare
          </Button>
        </SheetTrigger>
        <Button
          size="sm"
          variant="outline"
          onClick={() => void runGenerate(0.5)}
          disabled={isGenerating}
        >
          <Plus className="mr-1 h-3.5 w-3.5" /> Generate more
        </Button>
      </div>
      <SheetContent side="right" className="w-[640px] sm:max-w-[640px]">
        <SheetHeader>
          <SheetTitle>Compare variants</SheetTitle>
          <SheetDescription>
            All generated layouts side-by-side. Click a row to make it active.
          </SheetDescription>
        </SheetHeader>
        <div className="mt-4 max-h-[calc(100vh-7rem)] overflow-y-auto pr-1">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-white text-[10px] uppercase tracking-wide text-slate-500">
              <tr>
                <th className="py-1 text-left">Template / Robot</th>
                <th className="py-1 text-right">Cycle</th>
                <th className="py-1 text-right">UPH</th>
                {SUB_KEYS.map((s) => (
                  <th key={s.key} className="py-1 text-right">
                    {s.label}
                  </th>
                ))}
                <th className="py-1 text-right">Aggregate</th>
              </tr>
            </thead>
            <tbody>
              {proposals.map((p) => {
                const sc = scoreByProposal[p.proposal_id]
                const agg = sc?.aggregate ?? null
                const aggColor =
                  agg === null
                    ? 'bg-slate-100 text-slate-500'
                    : agg >= 0.7
                      ? 'bg-emerald-100 text-emerald-700'
                      : agg >= 0.4
                        ? 'bg-amber-100 text-amber-700'
                        : 'bg-red-100 text-red-700'
                return (
                  <tr
                    key={p.proposal_id}
                    onClick={() => setActive(p.proposal_id)}
                    className="cursor-pointer border-b border-slate-100 hover:bg-slate-50"
                  >
                    <td className="py-1.5">
                      <div className="font-medium">{p.template}</div>
                      <div className="text-[10px] text-slate-500">{p.robot_model_id ?? '—'}</div>
                    </td>
                    <td className="py-1.5 text-right tabular-nums">
                      {p.estimated_cycle_time_s.toFixed(2)}s
                    </td>
                    <td className="py-1.5 text-right tabular-nums">
                      {Math.round(p.estimated_uph)}
                    </td>
                    {SUB_KEYS.map((s) => (
                      <td key={s.key} className="py-1.5 text-right tabular-nums">
                        {sc ? (sc as unknown as Record<string, number>)[s.key].toFixed(2) : '…'}
                      </td>
                    ))}
                    <td className="py-1.5 text-right">
                      <Badge className={`tabular-nums ${aggColor}`}>
                        {agg === null ? '…' : (agg * 100).toFixed(0)}
                      </Badge>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </SheetContent>
    </Sheet>
  )
}
