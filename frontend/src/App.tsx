// 3-column layout:
//   left  (320 px) : InputPanel + SpecPanel
//   center (flex)  : WorkcellCanvas + VariantsStrip
//   right (320 px) : ScoringPanel + RobotInfoPanel

import { useMemo } from 'react'
import { AlertCircle, X } from 'lucide-react'

import { WorkcellCanvas } from '@/components/canvas/WorkcellCanvas'
import { VariantsStrip } from '@/components/canvas/VariantsStrip'
import { InputPanel } from '@/components/panels/InputPanel'
import { RobotInfoPanel } from '@/components/panels/RobotInfoPanel'
import { ScoringPanel } from '@/components/panels/ScoringPanel'
import { SpecPanel } from '@/components/panels/SpecPanel'
import { Button } from '@/components/ui/button'
import { validateLayout } from '@/lib/validation'
import { getActiveProposal, useLayoutStore } from '@/store/layoutStore'

function App() {
  const errors = useLayoutStore((s) => s.errors)
  const clearErrors = useLayoutStore((s) => s.clearErrors)
  const proposal = useLayoutStore(getActiveProposal)
  const spec = useLayoutStore((s) => s.spec)
  const selection = useLayoutStore((s) => s.selection)
  const setSelection = useLayoutStore((s) => s.setSelection)
  const updatePose = useLayoutStore((s) => s.updateComponentPose)
  const resetAll = useLayoutStore((s) => s.resetAll)

  // Client-side validation runs synchronously on every render.
  // Cheap (<1ms for ~10 components); paints red strokes the moment a drag moves.
  const validation = useMemo(
    () => (proposal && spec ? validateLayout(proposal, spec) : null),
    [proposal, spec],
  )

  return (
    <div className="flex h-screen flex-col bg-slate-100 text-slate-900">
      <header className="flex items-center justify-between border-b border-slate-200 bg-white px-4 py-2">
        <div>
          <h1 className="text-sm font-semibold tracking-tight">
            XYZ Robotics — Workcell Layout Optimizer
          </h1>
          <p className="text-[11px] text-slate-500">
            NL → spec → robot selection → optimized 2D layout
          </p>
        </div>
        <Button size="sm" variant="ghost" onClick={resetAll}>
          Reset
        </Button>
      </header>

      {errors.length > 0 && (
        <div className="flex items-start justify-between border-b border-red-200 bg-red-50 px-4 py-2 text-xs text-red-700">
          <div className="flex gap-2">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <ul className="space-y-0.5">
              {errors.map((e, i) => (
                <li key={i}>{e}</li>
              ))}
            </ul>
          </div>
          <button onClick={clearErrors} type="button" className="ml-2">
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      <main className="grid flex-1 grid-cols-[320px_1fr_320px] gap-3 overflow-hidden p-3">
        <aside className="flex min-h-0 flex-col gap-3 overflow-y-auto">
          <InputPanel />
          <SpecPanel />
        </aside>

        <section className="flex min-h-0 flex-col overflow-hidden rounded-lg border border-slate-200 bg-white">
          <div className="flex-1 overflow-hidden">
            <WorkcellCanvas
              proposal={proposal}
              spec={spec}
              selectedId={selection.kind === 'component' ? selection.componentId : null}
              validation={validation}
              onSelect={(id) =>
                setSelection(id ? { kind: 'component', componentId: id } : { kind: 'none' })
              }
              onComponentMove={(id, pose, phase) => updatePose(id, pose, phase)}
            />
          </div>
          <VariantsStrip />
        </section>

        <aside className="flex min-h-0 flex-col gap-3 overflow-y-auto">
          <ScoringPanel />
          <RobotInfoPanel />
        </aside>
      </main>
    </div>
  )
}

export default App
