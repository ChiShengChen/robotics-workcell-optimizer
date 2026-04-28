// Thin fetch wrapper. Vite proxy maps /api -> backend at localhost:8000.

import type {
  CadImportResponse,
  CPSATOptimizeRequest,
  CPSATOptimizeResponse,
  ExampleSpec,
  ExtractRequest,
  GenerateLayoutRequest,
  LayoutProposal,
  OptimizeProgressEvent,
  OptimizeRequest,
  OptimizeResponse,
  ScoreBreakdown,
  WorkcellSpec,
} from './types'

// In dev: '/api' is proxied by Vite to localhost:8000 (see vite.config.ts).
// In prod (Vercel): set VITE_API_URL=https://your-backend.onrender.com/api
// so the frontend talks to the deployed backend directly. CORS on the
// backend already allows *.vercel.app origins (see backend/app/main.py).
const BASE_URL = import.meta.env.VITE_API_URL ?? '/api'

export class ApiError extends Error {
  status: number
  detail: unknown
  constructor(status: number, detail: unknown, message?: string) {
    super(message ?? `HTTP ${status}`)
    this.status = status
    this.detail = detail
  }
}

async function request<T>(
  path: string,
  init: RequestInit & { signal?: AbortSignal },
): Promise<T> {
  const resp = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init.headers ?? {}) },
  })
  if (!resp.ok) {
    let detail: unknown = await resp.text()
    try {
      detail = JSON.parse(detail as string)
    } catch {
      // keep raw text
    }
    throw new ApiError(resp.status, detail, `HTTP ${resp.status}`)
  }
  return (await resp.json()) as T
}

export const api = {
  health: (signal?: AbortSignal) =>
    request<{ ok: boolean }>('/health', { method: 'GET', signal }),

  extract: (req: ExtractRequest, signal?: AbortSignal) =>
    request<WorkcellSpec>('/extract', {
      method: 'POST',
      body: JSON.stringify(req),
      signal,
    }),

  generateLayout: (req: GenerateLayoutRequest, signal?: AbortSignal) =>
    request<LayoutProposal[]>('/generate-layout', {
      method: 'POST',
      body: JSON.stringify(req),
      signal,
    }),

  score: (
    body: {
      proposal: LayoutProposal
      spec: WorkcellSpec
      robot_model_id?: string | null
      weights?: Record<string, number>
    },
    signal?: AbortSignal,
  ) =>
    request<ScoreBreakdown>('/score', {
      method: 'POST',
      body: JSON.stringify(body),
      signal,
    }),

  optimize: (req: OptimizeRequest, signal?: AbortSignal) =>
    request<OptimizeResponse>('/optimize', {
      method: 'POST',
      body: JSON.stringify(req),
      signal,
    }),

  optimizeCPSAT: (req: CPSATOptimizeRequest, signal?: AbortSignal) =>
    request<CPSATOptimizeResponse>('/optimize/cpsat', {
      method: 'POST',
      body: JSON.stringify(req),
      signal,
    }),

  examples: (signal?: AbortSignal) =>
    request<ExampleSpec[]>('/examples', { method: 'GET', signal }),

  importDxf: async (
    file: File,
    opts: { scale_to_mm?: number; margin_mm?: number } = {},
    signal?: AbortSignal,
  ): Promise<CadImportResponse> => {
    const form = new FormData()
    form.append('file', file)
    const params = new URLSearchParams()
    if (opts.scale_to_mm !== undefined) params.set('scale_to_mm', String(opts.scale_to_mm))
    if (opts.margin_mm !== undefined) params.set('margin_mm', String(opts.margin_mm))
    const qs = params.toString()
    const resp = await fetch(`${BASE_URL}/cad/import-dxf${qs ? `?${qs}` : ''}`, {
      method: 'POST',
      body: form,
      signal,
    })
    if (!resp.ok) {
      let detail: unknown = await resp.text()
      try { detail = JSON.parse(detail as string) } catch { /* keep raw */ }
      throw new ApiError(resp.status, detail)
    }
    return (await resp.json()) as CadImportResponse
  },
}

/** Stream SA progress via fetch + SSE parsing.
 *  Calls onProgress for each `progress` event and onDone with the final payload.
 *  Use `signal` to cancel mid-stream.
 */
export async function optimizeStream(
  req: OptimizeRequest,
  handlers: {
    onProgress?: (e: OptimizeProgressEvent) => void
    onDone: (r: OptimizeResponse) => void
    onError?: (msg: string) => void
  },
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch(`${BASE_URL}/optimize/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(req),
    signal,
  })
  if (!resp.ok || !resp.body) {
    handlers.onError?.(`HTTP ${resp.status}`)
    return
  }
  const reader = resp.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buf = ''
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let idx
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const block = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      const lines = block.split('\n')
      let event = 'message'
      const dataLines: string[] = []
      for (const line of lines) {
        if (line.startsWith('event: ')) event = line.slice(7).trim()
        else if (line.startsWith('data: ')) dataLines.push(line.slice(6))
      }
      if (dataLines.length === 0) continue
      const dataStr = dataLines.join('\n')
      try {
        const data = JSON.parse(dataStr)
        if (event === 'progress') handlers.onProgress?.(data)
        else if (event === 'done') handlers.onDone(data)
        else if (event === 'error') handlers.onError?.(data.message ?? 'unknown error')
      } catch {
        handlers.onError?.(`malformed SSE payload: ${dataStr.slice(0, 200)}`)
      }
    }
  }
}
