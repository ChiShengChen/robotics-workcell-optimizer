// Export the active LayoutProposal to DXF / STL / STEP / BOM (CSV / Markdown).
// Backend route: POST /api/export — adapter in app/services/cad_export.py
// translates the proposal into the cad_flow trial-config shape so the same
// exporters used by the offline cad_flow/ scripts produce the bytes.

import { Download, FileBox, FileSpreadsheet, FileText, Loader2 } from 'lucide-react'
import { useState } from 'react'

import { api, ApiError } from '@/api/client'
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

type Fmt = 'dxf' | 'stl' | 'step' | 'bom_csv' | 'bom_md'

const FORMATS: { fmt: Fmt; label: string; sub: string; icon: typeof FileBox }[] = [
  { fmt: 'dxf', label: 'DXF (2D top-down)', sub: 'AutoCAD plan view, layered', icon: FileText },
  { fmt: 'stl', label: 'STL (3D mesh)', sub: 'For Blender / 3D viewers', icon: FileBox },
  { fmt: 'step', label: 'STEP (3D BREP)', sub: 'SolidWorks / Fusion / FreeCAD', icon: FileBox },
  { fmt: 'bom_csv', label: 'BOM CSV', sub: 'Excel / Numbers, line items', icon: FileSpreadsheet },
  { fmt: 'bom_md', label: 'BOM Markdown', sub: 'Human-readable, with totals', icon: FileText },
]

export function ExportMenu() {
  const proposals = useLayoutStore((s) => s.proposals)
  const activeId = useLayoutStore((s) => s.activeProposalId)
  const proposal = proposals.find((p) => p.proposal_id === activeId) ?? null
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState<Fmt | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function handleExport(fmt: Fmt) {
    if (!proposal) return
    setBusy(fmt)
    setError(null)
    try {
      const { blob, filename } = await api.exportProposal(proposal, fmt)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = filename
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? typeof e.detail === 'string'
            ? e.detail
            : JSON.stringify(e.detail)
          : e instanceof Error
            ? e.message
            : String(e)
      setError(`${fmt}: ${msg}`)
    } finally {
      setBusy(null)
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          disabled={!proposal}
          className="gap-1"
          title="Export CAD / BOM files"
        >
          <Download className="h-3.5 w-3.5" />
          Export
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="text-base">
            Export · {proposal?.template ?? '—'}
          </DialogTitle>
          <DialogDescription className="text-xs">
            Render the active proposal as CAD or BOM files. Adapter maps the
            in-canvas LayoutProposal to the cad_flow trial-config shape, so
            geometry stays consistent with the offline pipeline.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-1.5">
          {FORMATS.map(({ fmt, label, sub, icon: Icon }) => (
            <button
              key={fmt}
              type="button"
              disabled={!proposal || busy !== null}
              onClick={() => void handleExport(fmt)}
              className="flex w-full items-center gap-3 rounded border border-slate-200 bg-white px-3 py-2 text-left transition hover:border-slate-400 disabled:opacity-50"
            >
              <Icon className="h-4 w-4 shrink-0 text-slate-500" />
              <div className="flex-1">
                <div className="text-xs font-medium text-slate-800">{label}</div>
                <div className="text-[10px] text-slate-500">{sub}</div>
              </div>
              {busy === fmt && <Loader2 className="h-3.5 w-3.5 animate-spin text-slate-400" />}
            </button>
          ))}
        </div>

        {error && (
          <div className="rounded border border-red-200 bg-red-50 px-3 py-2 text-[11px] text-red-700">
            {error}
          </div>
        )}

        <div className="text-[10px] text-slate-500">
          STEP requires <code className="rounded bg-slate-100 px-1">cadquery</code> on
          the backend. DXF/STL/BOM have no extra dependencies.
        </div>
      </DialogContent>
    </Dialog>
  )
}
