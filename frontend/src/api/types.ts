// Hand-mirrored from backend/app/schemas/*.py.
// Keep names + literal unions in sync; treat backend as the source of truth.

export type PalletStandard = 'EUR' | 'GMA' | 'ISO1' | 'half'
export type IdealUseCase =
  | 'light_case'
  | 'medium_case'
  | 'heavy_bag'
  | 'mixed_sku'
  | 'layer_picker'
export type LayoutTemplate = 'in_line' | 'L_shape' | 'U_shape' | 'dual_pallet'
export type PlacedType = 'robot' | 'conveyor' | 'pallet' | 'fence' | 'operator_zone'
export type ConstraintKind =
  | 'min_clearance'
  | 'max_cycle_time'
  | 'must_reach'
  | 'max_footprint'

// ---- WorkcellSpec components (discriminated union) -------------------------

export interface RobotComponent {
  id: string
  type: 'robot'
  label?: string | null
  payload_kg?: number | null
  reach_mm?: number | null
  preferred_model?: string | null
}

export interface ConveyorComponent {
  id: string
  type: 'conveyor'
  label?: string | null
  length_mm: number
  width_mm: number
  flow_direction_deg: number
  role: 'infeed' | 'outfeed'
  speed_mps?: number | null
}

export interface PalletComponent {
  id: string
  type: 'pallet'
  label?: string | null
  standard?: PalletStandard | null
  length_mm?: number | null
  width_mm?: number | null
  pattern?: 'column' | 'interlock' | 'pinwheel' | null
}

export interface FenceComponent {
  id: string
  type: 'fence'
  label?: string | null
  height_mm: number
  has_light_curtain: boolean
}

export interface OperatorZoneComponent {
  id: string
  type: 'operator_zone'
  label?: string | null
  width_mm: number
  depth_mm: number
}

export type SpecComponent =
  | RobotComponent
  | ConveyorComponent
  | PalletComponent
  | FenceComponent
  | OperatorZoneComponent

export interface Constraint {
  kind: ConstraintKind
  hard: boolean
  target_id?: string | null
  value?: number | null
  description: string
}

export interface Throughput {
  cases_per_hour_target: number
  operating_hours_per_day: number
  sku_count: number
  mixed_sequence: boolean
}

export interface WorkcellSpec {
  schema_version: '1.0'
  cell_envelope_mm: [number, number]
  components: SpecComponent[]
  constraints: Constraint[]
  throughput: Throughput
  case_dims_mm?: [number, number, number] | null
  case_mass_kg?: number | null
  pallet_standard?: PalletStandard | null
  max_stack_height_mm?: number | null
  budget_usd?: number | null
  assumptions: string[]
  notes: string
}

// ---- LayoutProposal --------------------------------------------------------

export interface PlacedComponent {
  id: string
  type: PlacedType
  x_mm: number
  y_mm: number
  yaw_deg: number
  // Per-type geometry. Common keys we read:
  //   robot:    base_radius_mm, reach_mm, effective_reach_mm, footprint_l_mm, footprint_w_mm
  //   conveyor: length_mm, width_mm, role
  //   pallet:   length_mm, width_mm, standard, pattern
  //   fence:    polyline: [[x,y]...], height_mm, safety_margin_mm
  //   operator: width_mm, depth_mm
  dims: Record<string, unknown>
}

export interface LayoutProposal {
  proposal_id: string
  template: LayoutTemplate
  robot_model_id: string | null
  components: PlacedComponent[]
  cell_bounds_mm: [number, number]
  estimated_cycle_time_s: number
  estimated_uph: number
  rationale: string
  assumptions: string[]
}

// ---- Scoring (Phase 4 will populate) --------------------------------------

export interface Violation {
  kind:
    | 'unreachable'
    | 'overlap'
    | 'fence_clearance'
    | 'operator_zone_intrusion'
    | 'iso13855'
    | 'outside_envelope'
  severity: 'hard' | 'soft'
  component_ids: string[]
  message: string
  margin_mm?: number | null
}

export interface ScoreBreakdown {
  compactness: number
  reach_margin: number
  cycle_efficiency: number
  safety_clearance: number
  throughput_feasibility: number
  aggregate: number
  violations: Violation[]
  weights: Record<string, number>
}

// ---- API request/response shapes ------------------------------------------

export interface ExtractRequest {
  prompt: string
}

export interface GenerateLayoutRequest {
  spec: WorkcellSpec
  n_variants?: number
}

export interface ApiError {
  status: number
  detail: unknown
}

// ---- Optimization ----------------------------------------------------------

export interface OptimizeRequest {
  proposal: LayoutProposal
  spec: WorkcellSpec
  robot_model_id?: string | null
  max_iterations?: number
  seed?: number | null
}

export interface OptimizeResponse {
  optimized_proposal: LayoutProposal
  seed_score: ScoreBreakdown
  optimized_score: ScoreBreakdown
  score_history: number[]
  best_history: number[]
  delta_summary: Record<string, number>
  walltime_s: number
  iterations: number
  accepted: number
  rejected: number
}

export interface OptimizeProgressEvent {
  iteration: number
  current_score: number
  best_score: number
}
