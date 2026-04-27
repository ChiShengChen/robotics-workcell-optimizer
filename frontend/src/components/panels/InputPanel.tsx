// Prompt textarea + Extract / Generate buttons + Load Example dropdown.

import { useEffect, useState } from 'react'
import { ChevronDown, Loader2, Sparkles, Wand2, Workflow } from 'lucide-react'

import { api } from '@/api/client'
import type { ExampleSpec } from '@/api/types'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { useLayoutStore } from '@/store/layoutStore'

export function InputPanel() {
  const prompt = useLayoutStore((s) => s.prompt)
  const setPrompt = useLayoutStore((s) => s.setPrompt)
  const isExtracting = useLayoutStore((s) => s.isExtracting)
  const isGenerating = useLayoutStore((s) => s.isGenerating)
  const spec = useLayoutStore((s) => s.spec)
  const runExtract = useLayoutStore((s) => s.runExtract)
  const runGenerate = useLayoutStore((s) => s.runGenerate)
  const loadExample = useLayoutStore((s) => s.loadExample)

  const [examples, setExamples] = useState<ExampleSpec[]>([])
  const [loadingId, setLoadingId] = useState<string | null>(null)
  const [open, setOpen] = useState(false)

  useEffect(() => {
    let alive = true
    api.examples().then((xs) => {
      if (alive) setExamples(xs)
    }).catch(() => { /* examples are optional */ })
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
        <div className="flex gap-2">
          <Button
            onClick={() => void runExtract()}
            disabled={isExtracting || prompt.trim().length === 0}
            className="flex-1"
            size="sm"
          >
            {isExtracting ? (
              <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Wand2 className="mr-1 h-3.5 w-3.5" />
            )}
            Extract Spec
          </Button>
          <Button
            onClick={() => void runGenerate()}
            disabled={isGenerating || !spec}
            variant="secondary"
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
        </div>
      </CardContent>
    </Card>
  )
}
