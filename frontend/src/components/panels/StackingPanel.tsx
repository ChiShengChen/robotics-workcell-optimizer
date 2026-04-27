// Tier 3 deliverable: pallet stacking pattern visualizer.
// Top-down Konva mini-canvas + pattern selector + layer slider + metrics.

import { useMemo, useState } from 'react'
import { Layer as KonvaLayer, Rect, Stage, Text } from 'react-konva'
import { Boxes } from 'lucide-react'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { buildStack, type CaseDims, type PalletDims } from '@/lib/stacking'
import { useLayoutStore } from '@/store/layoutStore'

type Pattern = 'column' | 'interlock' | 'pinwheel'

const STAGE_W = 264
const STAGE_H = 200
const PADDING = 12

export function StackingPanel() {
  const spec = useLayoutStore((s) => s.spec)
  const [pattern, setPattern] = useState<Pattern>('interlock')
  const [layerIdx, setLayerIdx] = useState(0)

  const palletStandard = spec?.pallet_standard ?? 'EUR'
  const pallet: PalletDims =
    palletStandard === 'GMA'
      ? { length_mm: 1219, width_mm: 1016 }
      : palletStandard === 'half'
        ? { length_mm: 800, width_mm: 600 }
        : palletStandard === 'ISO1'
          ? { length_mm: 1200, width_mm: 1000 }
          : { length_mm: 1200, width_mm: 800 }

  const caseDims: CaseDims =
    spec?.case_dims_mm
      ? {
          length_mm: spec.case_dims_mm[0],
          width_mm: spec.case_dims_mm[1],
          height_mm: spec.case_dims_mm[2],
        }
      : { length_mm: 400, width_mm: 300, height_mm: 220 }

  const maxStack = spec?.max_stack_height_mm ?? 1800
  const nLayers = Math.max(1, Math.floor(maxStack / caseDims.height_mm))
  const stack = useMemo(
    () => buildStack(pallet, caseDims, pattern, nLayers),
    [pallet.length_mm, pallet.width_mm, caseDims.length_mm, caseDims.width_mm, caseDims.height_mm, pattern, nLayers],
  )

  const safeLayer = Math.min(layerIdx, stack.perLayer.length - 1)
  const layerRects = stack.perLayer[safeLayer] ?? []

  // Scale pallet to fit stage with padding.
  const usableW = STAGE_W - 2 * PADDING
  const usableH = STAGE_H - 2 * PADDING
  const mmPerPx = Math.max(pallet.length_mm / usableW, pallet.width_mm / usableH)
  const palletWPx = pallet.length_mm / mmPerPx
  const palletHPx = pallet.width_mm / mmPerPx
  const offX = (STAGE_W - palletWPx) / 2
  const offY = (STAGE_H - palletHPx) / 2

  return (
    <Card className="rounded-lg border-slate-200">
      <CardHeader className="space-y-1 pb-2">
        <CardTitle className="flex items-center gap-1 text-sm font-semibold uppercase tracking-wide text-slate-600">
          <Boxes className="h-4 w-4" /> Stacking pattern
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-xs">
        <Tabs value={pattern} onValueChange={(v) => setPattern(v as Pattern)}>
          <TabsList className="h-7 w-full">
            <TabsTrigger value="column" className="h-6 text-[10px]">column</TabsTrigger>
            <TabsTrigger value="interlock" className="h-6 text-[10px]">interlock</TabsTrigger>
            <TabsTrigger value="pinwheel" className="h-6 text-[10px]">pinwheel</TabsTrigger>
          </TabsList>
        </Tabs>

        <div className="rounded bg-slate-50 p-1">
          <Stage width={STAGE_W} height={STAGE_H}>
            <KonvaLayer x={offX} y={offY + palletHPx} scaleY={-1} listening={false}>
              {/* Pallet outline */}
              <Rect
                width={palletWPx}
                height={palletHPx}
                fill="#fef3c7"
                stroke="#a16207"
                strokeWidth={1.5}
              />
              {layerRects.map((r, i) => (
                <Rect
                  key={i}
                  x={r.x_mm / mmPerPx}
                  y={r.y_mm / mmPerPx}
                  width={r.w_mm / mmPerPx}
                  height={r.h_mm / mmPerPx}
                  fill="rgba(59, 130, 246, 0.4)"
                  stroke="#1e40af"
                  strokeWidth={0.6}
                />
              ))}
            </KonvaLayer>
            <KonvaLayer listening={false}>
              <Text
                text={`Layer ${safeLayer + 1} / ${stack.perLayer.length}`}
                x={6}
                y={6}
                fontSize={10}
                fill="#475569"
              />
              <Text
                text={`${layerRects.length} cases`}
                x={6}
                y={STAGE_H - 16}
                fontSize={10}
                fill="#475569"
              />
            </KonvaLayer>
          </Stage>
        </div>

        <div>
          <Label htmlFor="layer-slider" className="text-[10px] text-slate-500">
            Layer {safeLayer + 1} of {stack.perLayer.length}
          </Label>
          <input
            id="layer-slider"
            type="range"
            min={0}
            max={Math.max(0, stack.perLayer.length - 1)}
            value={safeLayer}
            onChange={(e) => setLayerIdx(parseInt(e.target.value, 10))}
            className="mt-1 w-full accent-blue-600"
          />
        </div>

        <div className="grid grid-cols-2 gap-x-2 gap-y-0.5 text-[11px]">
          <span className="text-slate-500">Pallet</span>
          <span className="font-medium tabular-nums">
            {pallet.length_mm}×{pallet.width_mm} mm
          </span>
          <span className="text-slate-500">Case</span>
          <span className="font-medium tabular-nums">
            {caseDims.length_mm}×{caseDims.width_mm}×{caseDims.height_mm} mm
          </span>
          <span className="text-slate-500">Cases / layer</span>
          <span className="font-medium tabular-nums">
            {layerRects.length}
          </span>
          <span className="text-slate-500">Total cases</span>
          <span className="font-medium tabular-nums">{stack.total}</span>
          <span className="text-slate-500">Stack height</span>
          <span className="font-medium tabular-nums">
            {(stack.totalHeightMm / 1000).toFixed(2)} m
          </span>
          <span className="text-slate-500">Load efficiency</span>
          <span className="font-medium tabular-nums">
            {(stack.loadEfficiency * 100).toFixed(1)}%
          </span>
          <span className="text-slate-500">CoG offset</span>
          <span className="font-medium tabular-nums">
            {stack.cogOffsetMm.magnitude.toFixed(0)} mm
          </span>
        </div>
      </CardContent>
    </Card>
  )
}
