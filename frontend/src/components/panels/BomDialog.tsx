// Itemised BOM + ROI for the active proposal. Order-of-magnitude estimate
// — meant for proposal-vs-proposal compare, not procurement quotes.

import { Receipt } from 'lucide-react'
import { useState } from 'react'

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

function fmtUSD(v: number): string {
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`
  if (v >= 1_000) return `$${(v / 1_000).toFixed(0)}k`
  return `$${v.toFixed(0)}`
}

export function BomDialog() {
  const [open, setOpen] = useState(false)
  const proposals = useLayoutStore((s) => s.proposals)
  const activeId = useLayoutStore((s) => s.activeProposalId)
  const proposal = proposals.find((p) => p.proposal_id === activeId) ?? null
  const bom = proposal?.cost_breakdown ?? null

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          disabled={!bom}
          className="gap-1"
          title="Itemised BOM + ROI"
        >
          <Receipt className="h-3.5 w-3.5" />
          BOM
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="text-base">
            Cost breakdown · {proposal?.template ?? '—'}
          </DialogTitle>
          <DialogDescription className="text-xs">
            Order-of-magnitude BOM + integration markup + payback estimate.
            Not a procurement quote — vendor RFQs + integrator site visit
            required for actuals.
          </DialogDescription>
        </DialogHeader>

        {!bom ? (
          <div className="py-6 text-center text-sm text-slate-500">
            No cost data available for this proposal.
          </div>
        ) : (
          <div className="space-y-4 text-xs">
            <table className="w-full">
              <thead>
                <tr className="border-b border-slate-200 text-left text-[10px] uppercase tracking-wide text-slate-500">
                  <th className="py-1.5">Line item</th>
                  <th className="py-1.5 text-right">Qty</th>
                  <th className="py-1.5 text-right">Unit</th>
                  <th className="py-1.5 text-right">Subtotal</th>
                </tr>
              </thead>
              <tbody>
                {bom.line_items.map((li, i) => (
                  <tr key={i} className="border-b border-slate-100">
                    <td className="py-1.5 text-slate-700">{li.label}</td>
                    <td className="py-1.5 text-right text-slate-500 tabular-nums">
                      {li.qty}
                    </td>
                    <td className="py-1.5 text-right text-slate-500 tabular-nums">
                      {fmtUSD(li.unit_usd)}
                    </td>
                    <td className="py-1.5 text-right font-medium text-slate-800 tabular-nums">
                      {fmtUSD(li.subtotal_usd)}
                    </td>
                  </tr>
                ))}
                <tr className="border-b border-slate-200">
                  <td className="py-1.5 text-slate-600">Bare hardware total</td>
                  <td colSpan={2} />
                  <td className="py-1.5 text-right font-semibold text-slate-800 tabular-nums">
                    {fmtUSD(bom.bare_total_usd)}
                  </td>
                </tr>
                <tr className="border-b border-slate-200">
                  <td className="py-1.5 text-slate-600">
                    Integration markup ({((bom.integration_multiplier - 1) * 100).toFixed(0)}%)
                    <span className="ml-1 text-[10px] text-slate-400">
                      install · commission · training
                    </span>
                  </td>
                  <td colSpan={2} />
                  <td className="py-1.5 text-right text-slate-700 tabular-nums">
                    +{fmtUSD(bom.integration_usd)}
                  </td>
                </tr>
                <tr>
                  <td className="pt-2 text-sm font-bold text-slate-900">
                    Grand total
                  </td>
                  <td colSpan={2} />
                  <td className="pt-2 text-right text-base font-bold tabular-nums text-slate-900">
                    {fmtUSD(bom.grand_total_usd)}
                  </td>
                </tr>
              </tbody>
            </table>

            <div className="rounded-md border border-emerald-200 bg-emerald-50 p-3">
              <div className="mb-1 text-[10px] uppercase tracking-wide text-emerald-700">
                Payback estimate
              </div>
              <div className="grid grid-cols-3 gap-3 text-[11px]">
                <div>
                  <div className="text-slate-500">Annual labor savings</div>
                  <div className="text-base font-semibold text-emerald-700 tabular-nums">
                    {fmtUSD(bom.annual_labor_savings_usd)}
                  </div>
                  <div className="text-[9px] text-slate-400">
                    $50k/yr × {proposal?.robot_model_ids?.length ?? 0} arms
                  </div>
                </div>
                <div>
                  <div className="text-slate-500">Payback</div>
                  <div className="text-base font-semibold text-emerald-700 tabular-nums">
                    {bom.payback_months > 0
                      ? `${bom.payback_months.toFixed(1)} mo`
                      : '—'}
                  </div>
                  <div className="text-[9px] text-slate-400">
                    {bom.payback_months > 0
                      ? `${(bom.payback_months / 12).toFixed(1)} years`
                      : 'no savings model'}
                  </div>
                </div>
                <div>
                  <div className="text-slate-500">3-yr NPV (rough)</div>
                  <div className="text-base font-semibold text-emerald-700 tabular-nums">
                    {fmtUSD(
                      Math.max(
                        0,
                        bom.annual_labor_savings_usd * 3 - bom.grand_total_usd,
                      ),
                    )}
                  </div>
                  <div className="text-[9px] text-slate-400">
                    no discount, no opex
                  </div>
                </div>
              </div>
            </div>

            <div className="text-[10px] text-slate-400">
              Assumptions: integration ×{bom.integration_multiplier.toFixed(1)};
              labour $50k/yr per displaced manual palletizer; midpoint
              catalogue prices for arms; $4k/m + $3k controls per conveyor;
              $120/m fence + $4k light curtain; $20k cell controller / PLC.
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
