// Zustand store: prompt + extracted spec + layout proposals + selection +
// flight flags. Persisted to localStorage so refresh doesn't lose work.

import { create } from 'zustand'
import { persist, createJSONStorage } from 'zustand/middleware'
import { ApiError, api } from '@/api/client'
import type { LayoutProposal, ScoreBreakdown, WorkcellSpec } from '@/api/types'
import { clampRectInside, snapToGrid, type Rect } from '@/lib/geometry'

const DEFAULT_PROMPT =
  "We're palletizing canned beverage trays on a single packaging line. " +
  'Each tray is 400 x 300 x 220 mm and weighs about 12 kg. We need 500 cases per hour, ' +
  'continuous operation with no line stops. Cell footprint is 8 m by 6 m. Use EUR pallets ' +
  '(1200 x 800 mm) and an interlock pattern. Total cell budget around 160k USD.'

export type Selection =
  | { kind: 'none' }
  | { kind: 'component'; componentId: string }

const SCORE_DEBOUNCE_MS = 150
const SCORE_HISTORY_MAX = 20

interface LayoutState {
  prompt: string
  spec: WorkcellSpec | null
  proposals: LayoutProposal[]
  activeProposalId: string | null
  selection: Selection
  isExtracting: boolean
  isGenerating: boolean
  isScoring: boolean
  errors: string[]
  scoreByProposal: Record<string, ScoreBreakdown>
  scoreHistory: number[] // aggregate score over the last N edits

  setPrompt: (s: string) => void
  setSelection: (sel: Selection) => void
  clearErrors: () => void
  runExtract: () => Promise<void>
  runGenerate: () => Promise<void>
  setActiveProposal: (id: string) => void
  updateComponentPose: (
    componentId: string,
    pose: { x_mm?: number; y_mm?: number; yaw_deg?: number },
    phase: 'move' | 'end',
  ) => void
  rescoreActive: () => Promise<void>
  resetAll: () => void
}

function describeError(err: unknown): string {
  if (err instanceof ApiError) {
    if (typeof err.detail === 'string') return `HTTP ${err.status}: ${err.detail}`
    if (typeof err.detail === 'object' && err.detail !== null) {
      const d = err.detail as Record<string, unknown>
      const msg = d.detail ?? d.message ?? JSON.stringify(d)
      return `HTTP ${err.status}: ${typeof msg === 'string' ? msg : JSON.stringify(msg)}`
    }
    return err.message
  }
  if (err instanceof Error) return err.message
  return String(err)
}

// Module-scoped (not persisted) so debounce + cancellation survive across renders.
let scoreDebounceTimer: ReturnType<typeof setTimeout> | null = null
let scoreAbortController: AbortController | null = null

function clampPose(
  rect: Rect,
  cellW: number,
  cellH: number,
): { x: number; y: number } {
  const clamped = clampRectInside(rect, cellW, cellH)
  return { x: clamped.x, y: clamped.y }
}

function poseSnapAndClamp(
  componentType: string,
  dims: Record<string, unknown>,
  yawDeg: number,
  rawXMm: number,
  rawYMm: number,
  cellW: number,
  cellH: number,
): { x_mm: number; y_mm: number } {
  let xRect = rawXMm
  let yRect = rawYMm
  let w = 0
  let h = 0
  if (componentType === 'robot') {
    const r = (dims.base_radius_mm as number | undefined) ?? 350
    xRect = rawXMm - r
    yRect = rawYMm - r
    w = h = 2 * r
  } else if (componentType === 'conveyor') {
    const length = (dims.length_mm as number | undefined) ?? 0
    const width = (dims.width_mm as number | undefined) ?? 0
    const isVertical = Math.abs(((yawDeg % 180) + 180) % 180 - 90) < 1e-3
    w = isVertical ? width : length
    h = isVertical ? length : width
  } else if (componentType === 'pallet') {
    w = (dims.length_mm as number | undefined) ?? 1200
    h = (dims.width_mm as number | undefined) ?? 800
  } else if (componentType === 'operator_zone') {
    w = (dims.width_mm as number | undefined) ?? 1500
    h = (dims.depth_mm as number | undefined) ?? 1500
  }
  // Snap top-left first, then clamp into envelope.
  const snapX = snapToGrid(xRect, 50)
  const snapY = snapToGrid(yRect, 50)
  const clamped = clampPose({ x: snapX, y: snapY, w, h }, cellW, cellH)
  if (componentType === 'robot') {
    const r = (dims.base_radius_mm as number | undefined) ?? 350
    return { x_mm: clamped.x + r, y_mm: clamped.y + r }
  }
  return { x_mm: clamped.x, y_mm: clamped.y }
}

export const useLayoutStore = create<LayoutState>()(
  persist(
    (set, get) => ({
      prompt: DEFAULT_PROMPT,
      spec: null,
      proposals: [],
      activeProposalId: null,
      selection: { kind: 'none' },
      isExtracting: false,
      isGenerating: false,
      isScoring: false,
      errors: [],
      scoreByProposal: {},
      scoreHistory: [],

      setPrompt: (s) => set({ prompt: s }),
      setSelection: (selection) => set({ selection }),
      clearErrors: () => set({ errors: [] }),

      runExtract: async () => {
        set({ isExtracting: true, errors: [] })
        try {
          const spec = await api.extract({ prompt: get().prompt })
          set({
            spec,
            proposals: [],
            activeProposalId: null,
            selection: { kind: 'none' },
            scoreByProposal: {},
            scoreHistory: [],
          })
        } catch (err) {
          set({ errors: [describeError(err)] })
        } finally {
          set({ isExtracting: false })
        }
      },

      runGenerate: async () => {
        const spec = get().spec
        if (!spec) {
          set({ errors: ['Run Extract first to produce a WorkcellSpec.'] })
          return
        }
        set({ isGenerating: true, errors: [] })
        try {
          const proposals = await api.generateLayout({ spec, n_variants: 3 })
          set({
            proposals,
            activeProposalId: proposals[0]?.proposal_id ?? null,
            selection: { kind: 'none' },
            scoreByProposal: {},
            scoreHistory: [],
          })
          // Trigger first scoring pass.
          void get().rescoreActive()
        } catch (err) {
          set({ errors: [describeError(err)] })
        } finally {
          set({ isGenerating: false })
        }
      },

      setActiveProposal: (id) => {
        set({ activeProposalId: id, selection: { kind: 'none' }, scoreHistory: [] })
        void get().rescoreActive()
      },

      updateComponentPose: (componentId, pose, phase) =>
        set((state) => {
          const activeId = state.activeProposalId
          const spec = state.spec
          if (!activeId || !spec) return state
          const [cellW, cellH] = spec.cell_envelope_mm
          const proposals = state.proposals.map((p) => {
            if (p.proposal_id !== activeId) return p
            return {
              ...p,
              components: p.components.map((c) => {
                if (c.id !== componentId) return c
                const yawDeg = pose.yaw_deg ?? c.yaw_deg
                const rawX = pose.x_mm ?? c.x_mm
                const rawY = pose.y_mm ?? c.y_mm
                const { x_mm, y_mm } = poseSnapAndClamp(
                  c.type,
                  c.dims,
                  yawDeg,
                  rawX,
                  rawY,
                  cellW,
                  cellH,
                )
                return { ...c, x_mm, y_mm, yaw_deg: yawDeg }
              }),
            }
          })

          // Schedule a debounced /api/score call on drag-end.
          if (phase === 'end') {
            if (scoreDebounceTimer) clearTimeout(scoreDebounceTimer)
            scoreDebounceTimer = setTimeout(() => {
              void useLayoutStore.getState().rescoreActive()
            }, SCORE_DEBOUNCE_MS)
          }

          return { ...state, proposals }
        }),

      rescoreActive: async () => {
        const state = get()
        const activeId = state.activeProposalId
        const spec = state.spec
        if (!activeId || !spec) return
        const proposal = state.proposals.find((p) => p.proposal_id === activeId)
        if (!proposal) return
        if (scoreAbortController) scoreAbortController.abort()
        scoreAbortController = new AbortController()
        set({ isScoring: true })
        try {
          const score = await api.score(
            { proposal, spec, robot_model_id: proposal.robot_model_id },
            scoreAbortController.signal,
          )
          set((s) => ({
            scoreByProposal: { ...s.scoreByProposal, [activeId]: score },
            scoreHistory: [...s.scoreHistory, score.aggregate].slice(-SCORE_HISTORY_MAX),
            isScoring: false,
          }))
        } catch (err) {
          // Aborted requests are benign.
          if ((err as Error).name === 'AbortError') return
          set({ errors: [describeError(err)], isScoring: false })
        }
      },

      resetAll: () => {
        if (scoreDebounceTimer) clearTimeout(scoreDebounceTimer)
        if (scoreAbortController) scoreAbortController.abort()
        set({
          spec: null,
          proposals: [],
          activeProposalId: null,
          selection: { kind: 'none' },
          errors: [],
          scoreByProposal: {},
          scoreHistory: [],
        })
      },
    }),
    {
      name: 'xyz-workcell-layout',
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({
        prompt: state.prompt,
        spec: state.spec,
        proposals: state.proposals,
        activeProposalId: state.activeProposalId,
      }),
    },
  ),
)

export function getActiveProposal(state: LayoutState): LayoutProposal | null {
  if (!state.activeProposalId) return null
  return state.proposals.find((p) => p.proposal_id === state.activeProposalId) ?? null
}

export function getActiveScore(state: LayoutState): ScoreBreakdown | null {
  if (!state.activeProposalId) return null
  return state.scoreByProposal[state.activeProposalId] ?? null
}
