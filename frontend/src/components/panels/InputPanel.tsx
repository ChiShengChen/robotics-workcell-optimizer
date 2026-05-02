// Prompt textarea + Extract / Generate buttons + Load Example dropdown.

import { useEffect, useMemo, useRef, useState } from 'react'
import { BookOpen, ChevronDown, Layers, Loader2, Map, Sparkles, Wand2, Workflow, X } from 'lucide-react'

import { api } from '@/api/client'
import type { CadSample, ExampleSpec } from '@/api/types'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { useLayoutStore } from '@/store/layoutStore'
import { PROMPT_LIBRARY, promptLibraryByCategory, type PromptLibraryEntry } from '@/data/promptLibrary'

export function InputPanel() {
  const prompt = useLayoutStore((s) => s.prompt)
  const setPrompt = useLayoutStore((s) => s.setPrompt)
  const isExtracting = useLayoutStore((s) => s.isExtracting)
  const isGenerating = useLayoutStore((s) => s.isGenerating)
  const spec = useLayoutStore((s) => s.spec)
  const runExtract = useLayoutStore((s) => s.runExtract)
  const runGenerate = useLayoutStore((s) => s.runGenerate)
  const nVariants = useLayoutStore((s) => s.nVariants)
  const setNVariants = useLayoutStore((s) => s.setNVariants)
  const loadExample = useLayoutStore((s) => s.loadExample)
  const importCadFloorPlan = useLayoutStore((s) => s.importCadFloorPlan)
  const importCadImage = useLayoutStore((s) => s.importCadImage)
  const loadCadSample = useLayoutStore((s) => s.loadCadSample)
  const clearObstacles = useLayoutStore((s) => s.clearObstacles)
  const obstacleCount = useLayoutStore((s) => s.spec?.obstacles?.length ?? 0)

  const [examples, setExamples] = useState<ExampleSpec[]>([])
  const [cadSamples, setCadSamples] = useState<CadSample[]>([])
  const [loadingId, setLoadingId] = useState<string | null>(null)
  const [open, setOpen] = useState(false)
  const [cadOpen, setCadOpen] = useState(false)
  const [promptOpen, setPromptOpen] = useState(false)
  const [loadingSampleId, setLoadingSampleId] = useState<string | null>(null)
  const [importingCad, setImportingCad] = useState(false)
  const [importingImage, setImportingImage] = useState(false)
  const [pendingImage, setPendingImage] = useState<File | null>(null)
  const [imgFloorW, setImgFloorW] = useState('10')
  const [imgFloorH, setImgFloorH] = useState('8')
  const fileInputRef = useRef<HTMLInputElement>(null)
  const imageInputRef = useRef<HTMLInputElement>(null)

  const promptCategories = useMemo(() => promptLibraryByCategory(), [])

  const handlePickPrompt = async (entry: PromptLibraryEntry) => {
    setPromptOpen(false)
    // If the entry pairs with a CAD sample, load it first.
    if (entry.cadSampleId) {
      setLoadingSampleId(entry.cadSampleId)
      try {
        await loadCadSample(entry.cadSampleId)
      } catch {
        /* error already in store.errors */
      } finally {
        setLoadingSampleId(null)
      }
    }
    setPrompt(entry.prompt)
  }

  useEffect(() => {
    let alive = true
    api.examples().then((xs) => {
      if (alive) setExamples(xs)
    }).catch(() => { /* examples are optional */ })
    api.cadSamples().then((xs) => {
      if (alive) setCadSamples(xs)
    }).catch(() => { /* cad samples are optional */ })
    return () => { alive = false }
  }, [])

  return (
    <Card className="rounded-lg border-slate-200">
      <CardHeader className="space-y-1">
        <CardTitle className="text-sm font-semibold uppercase tracking-wide text-slate-600">
          1 · Describe the line
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="space-y-1">
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="w-full justify-start"
            disabled={importingCad}
            onClick={() => fileInputRef.current?.click()}
          >
            {importingCad ? (
              <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Map className="mr-1 h-3.5 w-3.5" />
            )}
            Import floor plan (.dxf)…
            {obstacleCount > 0 && (
              <span className="ml-auto inline-flex items-center gap-1 rounded bg-slate-100 px-1.5 text-[10px] text-slate-600">
                {obstacleCount} obstacles
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation()
                    clearObstacles()
                  }}
                  className="hover:text-red-600"
                  title="Clear obstacles"
                >
                  <X className="h-3 w-3" />
                </button>
              </span>
            )}
          </Button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".dxf,application/dxf"
            className="hidden"
            onChange={async (e) => {
              const f = e.target.files?.[0]
              if (!f) return
              setImportingCad(true)
              try {
                await importCadFloorPlan(f, { margin_mm: 200 })
              } catch {
                /* error already surfaced via store.errors */
              } finally {
                setImportingCad(false)
                if (fileInputRef.current) fileInputRef.current.value = ''
              }
            }}
          />

          <Button
            type="button"
            size="sm"
            variant="outline"
            className="w-full justify-start"
            disabled={importingImage}
            onClick={() => imageInputRef.current?.click()}
            title="Upload a top-down floor-plan PNG / JPG; OpenCV detects walls + obstacles"
          >
            {importingImage ? (
              <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Map className="mr-1 h-3.5 w-3.5" />
            )}
            Import floor plan (.png / .jpg)…
          </Button>
          <input
            ref={imageInputRef}
            type="file"
            accept="image/png,image/jpeg"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0]
              if (!f) return
              setPendingImage(f)
              if (imageInputRef.current) imageInputRef.current.value = ''
            }}
          />
          {pendingImage && (
            <div className="space-y-2 rounded border border-input bg-muted/30 p-2 text-[11px]">
              <div className="text-slate-700">
                <span className="font-medium">{pendingImage.name}</span>
                <span className="ml-1 text-slate-400">
                  ({(pendingImage.size / 1024).toFixed(0)} KB)
                </span>
              </div>
              <div className="text-[10px] text-slate-500">
                Real-world floor size this image represents (metres):
              </div>
              <div className="flex items-center gap-1.5">
                <input
                  type="number"
                  min={0.5}
                  max={100}
                  step={0.1}
                  value={imgFloorW}
                  onChange={(e) => setImgFloorW(e.target.value)}
                  className="w-14 rounded border border-input bg-background px-1.5 py-0.5 text-xs"
                />
                <span className="text-slate-400">×</span>
                <input
                  type="number"
                  min={0.5}
                  max={100}
                  step={0.1}
                  value={imgFloorH}
                  onChange={(e) => setImgFloorH(e.target.value)}
                  className="w-14 rounded border border-input bg-background px-1.5 py-0.5 text-xs"
                />
                <span className="text-slate-400">m</span>
                <Button
                  type="button"
                  size="sm"
                  className="ml-auto h-6"
                  disabled={importingImage}
                  onClick={async () => {
                    const w = parseFloat(imgFloorW)
                    const h = parseFloat(imgFloorH)
                    if (!Number.isFinite(w) || !Number.isFinite(h) || w <= 0 || h <= 0) return
                    setImportingImage(true)
                    try {
                      await importCadImage(pendingImage, { floor_w_m: w, floor_h_m: h })
                      setPendingImage(null)
                    } catch {
                      /* error already in store */
                    } finally {
                      setImportingImage(false)
                    }
                  }}
                >
                  {importingImage ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    'Detect'
                  )}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="h-6 px-1.5"
                  onClick={() => setPendingImage(null)}
                  title="Cancel"
                >
                  <X className="h-3 w-3" />
                </Button>
              </div>
            </div>
          )}
        </div>

        {cadSamples.length > 0 && (
          <div className="relative">
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="w-full justify-between"
              onClick={() => setCadOpen((v) => !v)}
              disabled={loadingSampleId !== null}
            >
              <span className="flex items-center gap-1">
                <Layers className="h-3.5 w-3.5" />
                Try a sample floor plan…
              </span>
              <ChevronDown className="h-3.5 w-3.5" />
            </Button>
            {cadOpen && (
              <ul
                className="absolute z-20 mt-1 w-full overflow-hidden rounded border border-slate-200 bg-white shadow-md"
                onMouseLeave={() => setCadOpen(false)}
              >
                {cadSamples.map((s) => (
                  <li key={s.id}>
                    <button
                      type="button"
                      className="block w-full px-2 py-1.5 text-left text-xs hover:bg-slate-50 disabled:opacity-50"
                      disabled={loadingSampleId !== null}
                      onClick={async () => {
                        setLoadingSampleId(s.id)
                        setCadOpen(false)
                        try {
                          await loadCadSample(s.id)
                        } finally {
                          setLoadingSampleId(null)
                        }
                      }}
                    >
                      <div className="flex items-center gap-1 font-medium text-slate-700">
                        {loadingSampleId === s.id && (
                          <Loader2 className="h-3 w-3 animate-spin" />
                        )}
                        {s.label}
                      </div>
                      <div className="text-[10px] text-slate-500">{s.description}</div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {examples.length > 0 && (
          <div className="relative">
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="w-full justify-between"
              onClick={() => setOpen((v) => !v)}
            >
              <span className="flex items-center gap-1">
                <Sparkles className="h-3.5 w-3.5" />
                Load example…
              </span>
              <ChevronDown className="h-3.5 w-3.5" />
            </Button>
            {open && (
              <ul
                className="absolute z-20 mt-1 w-full overflow-hidden rounded border border-slate-200 bg-white shadow-md"
                onMouseLeave={() => setOpen(false)}
              >
                {examples.map((e) => (
                  <li key={e.id}>
                    <button
                      type="button"
                      className="block w-full px-2 py-1.5 text-left text-xs hover:bg-slate-50 disabled:opacity-50"
                      disabled={loadingId !== null}
                      onClick={async () => {
                        setLoadingId(e.id)
                        setOpen(false)
                        try {
                          await loadExample(e)
                        } finally {
                          setLoadingId(null)
                        }
                      }}
                    >
                      <div className="flex items-center gap-1 font-medium text-slate-700">
                        {loadingId === e.id && (
                          <Loader2 className="h-3 w-3 animate-spin" />
                        )}
                        {e.label}
                      </div>
                      <div className="text-[10px] text-slate-500">{e.description}</div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {PROMPT_LIBRARY.length > 0 && (
          <div className="relative">
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="w-full justify-between"
              onClick={() => setPromptOpen((v) => !v)}
            >
              <span className="flex items-center gap-1">
                <BookOpen className="h-3.5 w-3.5" />
                Prompt library… ({PROMPT_LIBRARY.length} prompts)
              </span>
              <ChevronDown className="h-3.5 w-3.5" />
            </Button>
            {promptOpen && (
              <div
                className="absolute z-30 mt-1 max-h-[480px] w-[420px] overflow-y-auto rounded border border-slate-200 bg-white shadow-lg"
                onMouseLeave={() => setPromptOpen(false)}
              >
                {Array.from(promptCategories.entries()).map(([cat, entries]) => (
                  <div key={cat} className="border-b border-slate-100 last:border-b-0">
                    <div className="sticky top-0 bg-slate-100 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-slate-600">
                      {cat}
                    </div>
                    <ul>
                      {entries.map((e) => (
                        <li key={e.id}>
                          <button
                            type="button"
                            className="block w-full px-2 py-1.5 text-left text-xs hover:bg-blue-50"
                            onClick={() => void handlePickPrompt(e)}
                          >
                            <div className="flex items-baseline justify-between gap-2">
                              <span className="font-medium text-slate-700">
                                {e.title}
                              </span>
                              <span className="shrink-0 text-[10px] text-slate-400 tabular-nums">
                                {e.cph} cph
                              </span>
                            </div>
                            <div className="text-[10px] text-slate-500">
                              {e.description}
                            </div>
                            {e.badge && (
                              <span className="mt-1 inline-block rounded bg-emerald-100 px-1.5 py-0.5 text-[9px] font-medium text-emerald-700">
                                {e.badge}
                              </span>
                            )}
                          </button>
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        <Label htmlFor="prompt" className="text-xs text-slate-500">
          Natural-language description (case sizes, throughput, pallet, budget…)
        </Label>
        <Textarea
          id="prompt"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          rows={9}
          className="resize-y text-xs"
        />
        <div className="flex flex-col gap-2">
          <div className="flex gap-2">
            <Button
              onClick={() => void runGenerate()}
              disabled={isGenerating || !spec}
              className="flex-1"
              size="sm"
            >
              {isGenerating ? (
                <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
              ) : (
                <Workflow className="mr-1 h-3.5 w-3.5" />
              )}
              Generate Layout
            </Button>
            <div className="flex items-center gap-1.5 rounded-md border border-input bg-background px-2 text-xs">
              <Label htmlFor="n-variants" className="text-muted-foreground">
                Variants
              </Label>
              <select
                id="n-variants"
                value={nVariants}
                onChange={(e) => setNVariants(Number(e.target.value))}
                disabled={isGenerating}
                className="h-7 cursor-pointer bg-transparent text-xs font-medium outline-none"
                title="How many layout proposals to generate (1-6)."
              >
                {[1, 2, 3, 4, 5, 6].map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <Button
            onClick={() => void runExtract()}
            disabled={isExtracting || prompt.trim().length === 0}
            variant="outline"
            className="w-full"
            size="sm"
          >
            {isExtracting ? (
              <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Wand2 className="mr-1 h-3.5 w-3.5" />
            )}
            Extract Spec
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
