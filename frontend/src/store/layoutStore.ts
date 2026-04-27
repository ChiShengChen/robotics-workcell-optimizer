// Zustand store: prompt + extracted spec + layout proposals + selection +
// flight flags. Persisted to localStorage so refresh doesn't lose work.

import { create } from 'zustand'
import { persist, createJSONStorage } from 'zustand/middleware'
import { ApiError, api } from '@/api/client'
import type { LayoutProposal, WorkcellSpec } from '@/api/types'

const DEFAULT_PROMPT =
  "We're palletizing canned beverage trays on a single packaging line. " +
  'Each tray is 400 x 300 x 220 mm and weighs about 12 kg. We need 500 cases per hour, ' +
  'continuous operation with no line stops. Cell footprint is 8 m by 6 m. Use EUR pallets ' +
  '(1200 x 800 mm) and an interlock pattern. Total cell budget around 160k USD.'

export type Selection =
  | { kind: 'none' }
  | { kind: 'component'; componentId: string }

interface LayoutState {
  // Inputs
  prompt: string
  // Backend results
  spec: WorkcellSpec | null
  proposals: LayoutProposal[]
  activeProposalId: string | null
  selection: Selection
  // In-flight flags
  isExtracting: boolean
  isGenerating: boolean
  // Last errors (string for UI display)
  errors: string[]

  // Actions
  setPrompt: (s: string) => void
  setSelection: (sel: Selection) => void
  clearErrors: () => void
  runExtract: () => Promise<void>
  runGenerate: () => Promise<void>
  setActiveProposal: (id: string) => void
  updateComponentPose: (
    componentId: string,
    pose: { x_mm?: number; y_mm?: number; yaw_deg?: number },
  ) => void
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
      errors: [],

      setPrompt: (s) => set({ prompt: s }),
      setSelection: (selection) => set({ selection }),
      clearErrors: () => set({ errors: [] }),

      runExtract: async () => {
        set({ isExtracting: true, errors: [] })
        try {
          const spec = await api.extract({ prompt: get().prompt })
          set({ spec, proposals: [], activeProposalId: null, selection: { kind: 'none' } })
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
          })
        } catch (err) {
          set({ errors: [describeError(err)] })
        } finally {
          set({ isGenerating: false })
        }
      },

      setActiveProposal: (id) => set({ activeProposalId: id, selection: { kind: 'none' } }),

      updateComponentPose: (componentId, pose) =>
        set((state) => {
          const activeId = state.activeProposalId
          if (!activeId) return state
          const proposals = state.proposals.map((p) => {
            if (p.proposal_id !== activeId) return p
            return {
              ...p,
              components: p.components.map((c) =>
                c.id === componentId
                  ? {
                      ...c,
                      x_mm: pose.x_mm ?? c.x_mm,
                      y_mm: pose.y_mm ?? c.y_mm,
                      yaw_deg: pose.yaw_deg ?? c.yaw_deg,
                    }
                  : c,
              ),
            }
          })
          return { ...state, proposals }
        }),

      resetAll: () =>
        set({
          spec: null,
          proposals: [],
          activeProposalId: null,
          selection: { kind: 'none' },
          errors: [],
        }),
    }),
    {
      name: 'xyz-workcell-layout',
      storage: createJSONStorage(() => localStorage),
      // Don't persist transient flags or errors.
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
