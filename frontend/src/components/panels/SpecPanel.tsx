// Renders the extracted WorkcellSpec — summary + collapsible assumptions + raw JSON.

import { useState } from 'react'
import { ChevronDown, ChevronRight, FileJson } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import { useLayoutStore } from '@/store/layoutStore'

export function SpecPanel() {
  const spec = useLayoutStore((s) => s.spec)
  const [showJson, setShowJson] = useState(false)
  const [showAssumptions, setShowAssumptions] = useState(true)

  if (!spec) {
    return (
      <Card className="rounded-lg border-dashed border-slate-300">
        <CardContent className="py-6 text-center text-xs text-slate-400">
          Extracted WorkcellSpec will appear here.
        </CardContent>
      </Card>
    )
  }

  const [w, h] = spec.cell_envelope_mm
  return (
    <Card className="rounded-lg border-slate-200">
      <CardHeader className="space-y-1 pb-2">
        <CardTitle className="text-sm font-semibold uppercase tracking-wide text-slate-600">
          2 · Extracted spec
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-xs">
        <div className="grid grid-cols-2 gap-y-1">
          <span className="text-slate-500">Envelope</span>
          <span className="font-medium">
            {(w / 1000).toFixed(1)} × {(h / 1000).toFixed(1)} m
          </span>
          <span className="text-slate-500">Throughput</span>
          <span className="font-medium">{spec.throughput.cases_per_hour_target} cph</span>
          {spec.case_dims_mm && (
            <>
              <span className="text-slate-500">Case</span>
              <span className="font-medium">
                {spec.case_dims_mm[0]}×{spec.case_dims_mm[1]}×{spec.case_dims_mm[2]} mm
              </span>
            </>
          )}
          {spec.case_mass_kg != null && (
            <>
              <span className="text-slate-500">Mass</span>
              <span className="font-medium">{spec.case_mass_kg} kg</span>
            </>
          )}
          {spec.pallet_standard && (
            <>
              <span className="text-slate-500">Pallet</span>
              <span className="font-medium">{spec.pallet_standard}</span>
            </>
          )}
          {spec.budget_usd != null && (
            <>
              <span className="text-slate-500">Budget</span>
              <span className="font-medium">${spec.budget_usd.toLocaleString()}</span>
            </>
          )}
          <span className="text-slate-500">SKUs</span>
          <span className="font-medium">
            {spec.throughput.sku_count}
            {spec.throughput.mixed_sequence ? ' (random sequence)' : ''}
          </span>
        </div>

        <Separator />

        <div>
          <button
            type="button"
            className="flex items-center gap-1 text-xs font-medium text-slate-600"
            onClick={() => setShowAssumptions((v) => !v)}
          >
            {showAssumptions ? (
              <ChevronDown className="h-3.5 w-3.5" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5" />
            )}
            Assumptions{' '}
            <Badge variant="secondary" className="ml-1 h-4 px-1.5 text-[10px]">
              {spec.assumptions.length}
            </Badge>
          </button>
          {showAssumptions && (
            <ul className="mt-1 list-disc space-y-1 pl-5 text-[11px] text-slate-600">
              {spec.assumptions.length === 0 && (
                <li className="list-none italic text-slate-400">No assumptions recorded.</li>
              )}
              {spec.assumptions.map((a, i) => (
                <li key={i}>{a}</li>
              ))}
            </ul>
          )}
        </div>

        {spec.notes && (
          <>
            <Separator />
            <div className="text-[11px] text-slate-600">
              <div className="font-medium text-slate-500">Notes</div>
              <div>{spec.notes}</div>
            </div>
          </>
        )}

        <Separator />

        <button
          type="button"
          className="flex items-center gap-1 text-xs font-medium text-slate-600"
          onClick={() => setShowJson((v) => !v)}
        >
          {showJson ? (
            <ChevronDown className="h-3.5 w-3.5" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5" />
          )}
          <FileJson className="h-3.5 w-3.5" /> Raw JSON
        </button>
        {showJson && (
          <pre className="max-h-64 overflow-auto rounded bg-slate-900 p-2 text-[10px] leading-snug text-slate-100">
            {JSON.stringify(spec, null, 2)}
          </pre>
        )}
      </CardContent>
    </Card>
  )
}
