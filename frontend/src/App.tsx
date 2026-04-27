// 3-column layout:
//   left  (320 px) : InputPanel + SpecPanel
//   center (flex)  : 2D / 3D canvas tabs + VariantsStrip
//   right (320 px) : ScoringPanel + RobotInfoPanel + StackingPanel

import { useMemo } from 'react'
import { AlertCircle, Box, Square, X } from 'lucide-react'

import { WorkcellCanvas } from '@/components/canvas/WorkcellCanvas'
import { Workcell3DCanvas } from '@/components/canvas/Workcell3DCanvas'
import { VariantsStrip } from '@/components/canvas/VariantsStrip'
import { InputPanel } from '@/components/panels/InputPanel'
import { RobotInfoPanel } from '@/components/panels/RobotInfoPanel'
import { ScoringPanel } from '@/components/panels/ScoringPanel'
import { SpecPanel } from '@/components/panels/SpecPanel'
import { StackingPanel } from '@/components/panels/StackingPanel'
import { Button } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
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
          <Tabs defaultValue="2d" className="flex flex-1 flex-col overflow-hidden">
            <div className="flex items-center justify-between border-b border-slate-200 bg-white px-2 py-1">
              <TabsList className="h-7">
                <TabsTrigger value="2d" className="h-6 text-[11px]">
                  <Square className="mr-1 h-3 w-3" /> 2D layout
                </TabsTrigger>
                <TabsTrigger value="3d" className="h-6 text-[11px]">
                  <Box className="mr-1 h-3 w-3" /> 3D preview
                </TabsTrigger>
              </TabsList>
            </div>
            <TabsContent value="2d" className="m-0 flex-1 overflow-hidden">
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
            </TabsContent>
            <TabsContent value="3d" className="relative m-0 flex-1 overflow-hidden">
              <Workcell3DCanvas proposal={proposal} spec={spec} />
            </TabsContent>
          </Tabs>
          <VariantsStrip />
        </section>

        <aside className="flex min-h-0 flex-col gap-3 overflow-y-auto">
          <ScoringPanel />
          <RobotInfoPanel />
          <StackingPanel />
        </aside>
      </main>
    </div>
  )
}

export default App
