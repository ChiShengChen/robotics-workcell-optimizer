// Prompt textarea + Extract / Generate buttons.

import { Loader2, Wand2, Workflow } from 'lucide-react'

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

  return (
    <Card className="rounded-lg border-slate-200">
      <CardHeader className="space-y-1">
        <CardTitle className="text-sm font-semibold uppercase tracking-wide text-slate-600">
          1 · Describe the line
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
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
