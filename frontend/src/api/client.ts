// Thin fetch wrapper. Vite proxy maps /api -> backend at localhost:8000.

import type {
  ExtractRequest,
  GenerateLayoutRequest,
  LayoutProposal,
  ScoreBreakdown,
  WorkcellSpec,
} from './types'

const BASE_URL = '/api'

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
}
