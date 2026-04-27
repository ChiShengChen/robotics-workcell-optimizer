# XYZ Robotics Workcell Layout Optimizer — Project Memory

## What we're building
An LLM-driven palletizing workcell layout optimizer for a take-home interview at XYZ Robotics.

The system takes a natural-language description of a packaging line, extracts a structured spec via LLM, generates an optimized 2D layout (robot + conveyor + pallet stations + safety fence + operator zone), and renders it in an interactive React canvas where the user can drag components, see real-time constraint violations, and re-optimize.

## Core engineering bets (these are non-negotiable design decisions)

1. **Neuro-symbolic split**: LLM picks discrete decisions (template, robot model, infeasibility explanations); numerical solver picks continuous positions. Never let the LLM hallucinate coordinates.

2. **Multi-LLM abstraction**: Provider-agnostic Pydantic schemas with adapter functions for Claude / OpenAI / Gemini. One canonical schema, three schema-shape adapters, repair loop with cross-provider fallback.

3. **Hard/soft constraint discipline**: ISO 13855 separation distance and reach feasibility are HARD (infeasibility = solution rejected). Throughput, compactness, aesthetics are SOFT (squared penalties for SA, weighted aggregation). Never make safety substitutable.

4. **CP-SAT for layout refinement**: Use Google OR-Tools `cp_model.AddNoOverlap2D` for the disjunctive non-overlap reasoning. Avoid MILP from scratch (big-M tuning is fragile, Gurobi requires a license). CP-SAT is integer-only — scale everything to mm.

5. **Assumption-discipline in extraction**: Every Pydantic schema has an `assumptions: list[str]` field. The LLM MUST set ambiguous fields to null and append a note rather than fabricate values. This is the single most important hallucination-control pattern.

## Tech stack — DO NOT deviate

### Backend
- Python 3.11+
- FastAPI + uvicorn
- Pydantic v2 (use `model_config = ConfigDict(extra="forbid")` everywhere)
- ortools (for CP-SAT)
- numpy
- httpx (async HTTP)
- anthropic, openai, google-genai (LLM SDKs)
- pytest

### Frontend
- React 18 + TypeScript (strict mode)
- Vite
- Tailwind CSS v3
- shadcn/ui (Radix primitives)
- Lucide icons
- Zustand (with `persist` middleware)
- react-konva (NOT Fabric, NOT raw SVG, NOT Three)
- recharts (for Pareto scatter and score history)

### Tooling
- pnpm workspace (root, frontend/, backend has its own venv)
- concurrently (for `pnpm dev` to run both)
- ruff + black for Python
- ESLint + Prettier for TypeScript

## Naming conventions

- Python: snake_case for variables/functions, PascalCase for classes, SCREAMING_SNAKE for constants
- TypeScript: camelCase for variables/functions, PascalCase for components/types, kebab-case for filenames in `components/`
- All physical units in field names: `payload_kg`, `reach_mm`, `cycle_time_s` — never bare `payload` or `reach`
- Coordinate convention: cell origin = lower-left corner of workcell rectangle, x → right, y → up, +z up, all units mm. Yaw in DEGREES in schemas, RADIANS inside math kernels (NumPy convention).

## Folder structure

```
.
├── CLAUDE.md
├── README.md
├── package.json              # pnpm workspace root
├── pnpm-workspace.yaml
├── scripts/
│   └── dev.sh                # concurrently runs both
├── backend/
│   ├── pyproject.toml
│   ├── app/
│   │   ├── main.py           # FastAPI app
│   │   ├── api/              # endpoint modules
│   │   │   ├── extract.py
│   │   │   ├── layout.py
│   │   │   ├── score.py
│   │   │   ├── optimize.py
│   │   │   └── chat.py
│   │   ├── schemas/          # Pydantic models
│   │   │   ├── workcell.py
│   │   │   ├── robot.py
│   │   │   ├── layout.py
│   │   │   └── chat.py
│   │   ├── services/
│   │   │   ├── llm.py        # LLMRouter + provider clients
│   │   │   ├── catalog.py
│   │   │   ├── layout.py     # greedy + CP-SAT
│   │   │   ├── scoring.py    # the core scoring functions
│   │   │   └── optimizer.py  # SA loop
│   │   ├── data/
│   │   │   └── robots.json
│   │   └── prompts/          # system prompts as .md files
│   └── tests/
│       ├── test_scoring.py
│       └── test_layout.py
└── frontend/
    ├── package.json
    ├── vite.config.ts
    ├── tsconfig.json
    ├── tailwind.config.ts
    └── src/
        ├── App.tsx
        ├── main.tsx
        ├── components/
        │   ├── canvas/
        │   ├── panels/
        │   └── ui/           # shadcn-generated
        ├── hooks/
        ├── store/            # Zustand
        ├── api/              # client.ts, types.ts
        └── lib/              # geometry.ts, validation.ts, utils.ts
```

## Schema canonical reference

### WorkcellSpec (extracted from NL prompt)
```python
class WorkcellSpec(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    cell_envelope_mm: tuple[float, float]    # (W, H) of available floor area
    components: list[Component]              # discriminated union
    constraints: list[Constraint] = []
    throughput: Throughput
    case_dims_mm: tuple[float, float, float] | None = None
    case_mass_kg: float | None = None
    pallet_standard: Literal["EUR", "GMA", "ISO1", "half"] | None = None
    max_stack_height_mm: float | None = None
    budget_usd: float | None = None
    assumptions: list[str] = []              # LLM MUST populate when ambiguous
    notes: str = ""
```

### LayoutProposal (output of layout generation)
```python
class LayoutProposal(BaseModel):
    proposal_id: str
    template: Literal["in_line", "L_shape", "U_shape", "dual_pallet"]
    robot_model_id: str | None
    components: list[PlacedComponent]   # robot, conveyor, pallets, fence, operator zone — each with x/y/yaw
    cell_bounds_mm: tuple[float, float]
    estimated_cycle_time_s: float
    estimated_uph: float
    rationale: str
    assumptions: list[str] = []
```

## Robot catalog source of truth

`backend/app/data/robots.json` contains 22 real palletizing robots from ABB, FANUC, KUKA, Yaskawa, Kawasaki, with:
- payload_kg, reach_mm, vertical_reach_mm
- axes (4, 5, or 6)
- repeatability_mm
- footprint_lw_mm, weight_kg
- cycles_per_hour_std (at 400/2000/400 standard cycle)
- price_usd_range (low, high) — bare arm only; integration adds 50-100%
- ideal_use_case enum: light_case, medium_case, heavy_bag, mixed_sku, layer_picker

Use ONLY real models — see Phase 1 prompt for the exact list. Do NOT invent specs.

## Scoring function (5 components)

1. **Compactness**: bounding-box utilization + perimeter²/area + wasted-space-minus-aisles
2. **Reachability margin**: signed distance from each pick/place to robot's reach annulus boundary, with α=0.85 derate; aggregate min across targets
3. **Cycle efficiency**: trapezoidal motion profile, t = d/v_max + v_max/a if d ≥ v²/a else 2√(d/a); for dual-pallet UPH = 2 · UPH_single · η_overlap
4. **Safety clearance**: ISO 13855: S = K·T + C with K=2000 mm/s, C=850 mm body or 8(d-14) curtain
5. **Throughput feasibility**: UPH_estimated / UPH_target, saturated at 1.1

Aggregation: weighted sum for default; CP-SAT solves lexicographic; mention Pareto in README.

## ISO standards quick reference

- ISO 10218-1/-2 (2025 revised) — robot safety
- ISO 13855 — separation distance: `S = K·T + C` (legacy) or `S = K·T + DDS + Z` (2024+)
- ISO 13857 — fixed-guard reach distances; fence height 2,000-2,200 mm typical
- ANSI/RIA R15.06-2012 — current US install equivalent

## Key vocabulary (USE these terms in code comments and README)

Use: "mixed case palletizing", "random sequence infeed", "pallet planning", "MMR", "RockyOne", "RockyLight", "XYZ Studio Max", "interlock pattern", "reach envelope", "EOAT"

Avoid: "RockPick", "PickPro", "MixedPal", "humanoid", "in-hand dexterous"

## Test discipline

- Pure-function tests (scoring.py, layout.py) are the highest ROI — they encode core engineering claims
- Skip Playwright unless the entire build is done with 90+ minutes to spare
- Frontend: Vitest on lib/validation.ts and lib/geometry.ts only

## What to do when stuck

1. If a phase's acceptance test fails, fix it before moving on — DO NOT stack debt
2. If running out of time at hour 7.5: ship Tier 1, skip everything else, polish README
3. If running out of time at hour 13: skip CP-SAT (Phase 6), polish what's there
4. Always reserve the final hour for README + screen recording — non-negotiable
