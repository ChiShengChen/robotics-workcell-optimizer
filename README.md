# XYZ Robotics — LLM-Driven Workcell Layout Optimizer

> Take-home submission for XYZ Robotics. End-to-end pipeline:
> natural language → structured spec → robot selection → optimized 2D layout
> → interactive editing → re-optimization (SA + CP-SAT).

![demo placeholder](docs/architecture.svg)

## TL;DR

I built this around three engineering bets:

1. **Multi-LLM abstraction** — provider-agnostic Pydantic schemas with a single
   schema-shape adapter per provider (Claude tool use / OpenAI strict
   `json_schema` / Gemini `response_schema`), structured outputs via constrained
   decoding, a validation-repair loop that feeds the failure back to the model,
   and cross-provider fallback (Claude → OpenAI → Gemini). A `CostLedger`
   accumulates per-provider USD usage for telemetry.

2. **Hard / soft constraint discipline** — ISO 13855 separation distance and
   reach feasibility are inviolable; throughput, compactness, and aesthetics are
   weighted soft objectives. Any hard violation zeroes the aggregate score.
   Safety is never substitutable.

3. **CP-SAT for layout refinement** — Google OR-Tools' native
   `add_no_overlap_2d` handles disjunctive non-overlap reasoning without big-M
   tuning. The LLM picks discrete decisions (template, robot model); CP-SAT
   picks continuous positions. SA is also available for soft-objective
   exploration.

## Architecture

```
┌────────────────────────┐     ┌──────────────────────────────────────┐
│   React + Konva UI     │     │           FastAPI gateway            │
│   (Vite, Tailwind,     │     │                                      │
│    Zustand, recharts)  │     │   /api/extract       /api/score      │
│                        │ ──► │   /api/generate-     /api/optimize   │
│   3-column layout:     │     │       layout         /api/optimize/  │
│   • Input + Spec       │     │   /api/examples         stream (SSE) │
│   • Canvas + variants  │     │                      /api/optimize/  │
│   • Score + stacker    │     │                         cpsat        │
└─────────┬──────────────┘     └─────┬─────────────────┬──────────────┘
          │ SSE / fetch              │                 │
          ▼                          ▼                 ▼
   localStorage              ┌──────────────┐   ┌──────────────┐
   (Zustand persist)         │ LLMRouter    │   │ CatalogService│
                             │  · Claude    │   │   robots.json │
                             │  · OpenAI    │   │   22 models   │
                             │  · Gemini    │   └──────────────┘
                             │  · CostLedger│   ┌──────────────┐
                             └──────┬───────┘   │ ScoringService│
                                    ▼           │ 5 components  │
                             Anthropic /        │  + violations │
                             OpenAI /           └──────────────┘
                             Google             ┌──────────────┐
                                                │ OptimizerService│
                                                │  · SAOptimizer  │
                                                │  · CPSATRefiner │
                                                └──────────────┘
                                                ┌──────────────┐
                                                │ Pydantic v2   │
                                                │  WorkcellSpec │
                                                │  LayoutProposal│
                                                │  ScoreBreakdown│
                                                └──────────────┘
```

Pydantic schemas are the canonical contract through the whole pipeline — TS
types in `frontend/src/api/types.ts` are hand-mirrored from
`backend/app/schemas/`.

## Setup

```bash
# Backend
cd backend
python3.13 -m venv .venv && source .venv/bin/activate
pip install -e .[dev]
cp .env.example .env  # add at least one of ANTHROPIC_API_KEY / OPENAI_API_KEY / GOOGLE_API_KEY

# Frontend
cd ../frontend
pnpm install

# Run both
cd ..
bash scripts/dev.sh
# → http://localhost:5173
```

Without an LLM key, you can still:
- Click **Load example…** in the left panel — the bundled JSON examples ship
  with a pre-extracted `WorkcellSpec`, so the demo skips `/api/extract` and
  goes straight to layout generation, scoring, SA, and CP-SAT.
- Hit `/api/generate-layout`, `/api/score`, `/api/optimize`,
  `/api/optimize/cpsat` directly with any pre-built spec.

## Pipeline

1. **Extract** — `/api/extract` runs the LLM with two few-shot examples and a
   strict system prompt; fields the input doesn't pin down become `null` and
   land in `assumptions[]`. Validation-repair loop catches schema drift.
2. **Select** — `RobotCatalogService` filters 22 real palletizers (ABB / FANUC
   / KUKA / Yaskawa / Kawasaki) by payload, reach, throughput, and budget;
   ranks cheapest-first; relaxes constraints in order if nothing fits and
   records each relaxation as a layout-level assumption.
3. **Layout** — Four-template greedy generator (in_line / L_shape / U_shape /
   dual_pallet). Continuous-operation heuristic biases dual_pallet first.
4. **Score** — Five sub-scores (compactness / reach margin / cycle efficiency /
   ISO 13855 safety / throughput feasibility) aggregated with hard/soft
   discipline.
5. **Refine** — SA (continuous, soft-objective gradient descent with cross-
   basin large jumps) or CP-SAT (combinatorial, lexicographic objective with
   `add_no_overlap_2d` and a 16-half-plane reach polygon).

## Key design decisions

### Why CP-SAT over MILP from scratch
OR-Tools' `add_no_overlap_2d` handles disjunctive non-overlap with native
lazy clause generation; the same in MILP needs big-M constants whose tuning
is fragile. CP-SAT's portfolio search is free, and CP-SAT solves the demo
problem to OPTIMAL in ~30 ms.

### Why react-konva over Fabric / SVG / Three
Declarative React makes the multi-layer split (static fence + grid on
`listening: false`, dynamic bodies above) cheap. Native Konva drag with
`scaleY={-1}` cleanly handles the spec's y-up convention without inverting
math everywhere.

### Why a hand-rolled multi-LLM abstraction over Instructor or LiteLLM
This take-home rewards visibility. Production would layer Instructor on top
of OpenAI/Anthropic SDKs and add the repair loop + cost telemetry as
middleware. Here every line of the abstraction is in one file under 500
lines so an interviewer can see exactly what each provider needs.

### Why discriminated-union Pydantic with `assumptions: list[str]`
Hallucination control: forces the LLM to *admit* uncertainty rather than
fabricate measurements. Every Pydantic model uses
`model_config = ConfigDict(extra="forbid")` so unknown keys explode rather
than silently masking schema drift. The TS side mirrors the same
discriminated unions, so type changes propagate via `tsc -b` failures.

### Why sigmoid normalization for safety/reach but min-max for compactness
Saturation matches physical reality: more clearance ≠ better past a knee
(safety distance is an OK/not-OK threshold), so a sigmoid (k=0.005,
x₀=500 mm for safety; k=0.003, x₀=300 mm for reach) gives a smooth pass/
fail with diminishing returns. Compactness is genuinely linear-better
within the cell envelope.

### Why a soft penalty for hard violations *inside* SA but not in scoring
The user-facing aggregate is zero whenever any hard constraint is violated
(this is the contract). But SA's internal landscape needs a *gradient* to
descend out of violations, so the optimizer replaces the zero with
`-Σ|margin_mm| / 10000` *only inside the SA loop*. Without this, SA random-
walks across the whole infeasible plateau and never escapes.

### LLM/solver split (neuro-symbolic)
LLM handles discrete decisions (template choice, robot model selection,
infeasibility explanations); the numerical solver handles continuous
geometry. This is the same neuro-symbolic pattern XYZ Robotics' own
contact-rich-manipulation lineage (Mason at CMU, Rodriguez at MIT MCube)
encodes physical constraints explicitly rather than learning them
implicitly. The same template scales from layout planning to mixed case
palletizing's pallet-planning solver.

## Schema (excerpt)

```python
class WorkcellSpec(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    cell_envelope_mm: tuple[float, float]
    components: list[Component]      # discriminated union: Robot|Conveyor|Pallet|Fence|OperatorZone
    constraints: list[Constraint] = []
    throughput: Throughput
    case_dims_mm: tuple[float, float, float] | None = None
    case_mass_kg: float | None = None
    pallet_standard: Literal["EUR", "GMA", "ISO1", "half"] | None = None
    max_stack_height_mm: float | None = None
    budget_usd: float | None = None
    assumptions: list[str] = []      # LLM MUST populate when ambiguous
    notes: str = ""
```

## Multi-LLM details

Each provider gets a one-function adapter that translates the canonical
Pydantic schema into that provider's structured-output shape:

| Provider | Endpoint               | Key transformation                                         |
|----------|------------------------|------------------------------------------------------------|
| Claude   | `messages.create` tool | `input_schema` from Pydantic; `tool_choice` forces tool    |
| OpenAI   | `response_format`      | `additionalProperties:false` everywhere; every prop in `required`; strip `format`/`min`/`max`; inline `$defs` |
| Gemini   | `response_schema`      | strip `additionalProperties`; inline `$defs`               |

`extract_with_repair` validates each response against Pydantic; on
`ValidationError` it appends the failure as a user turn and retries. After
`max_retries` it falls back to the next provider in the chain.

## Scoring math

- **ISO 13855**: `S = K·T + C` with `K=2000 mm/s`, `T=0.3 s`, `C=850 mm` body
  (or `C=600 mm` with hard guard). Default `S = 1450 mm` (body) or `1200 mm`
  (hard guard).
- **Trapezoidal cycle**: `t = d/v + v/a` if `d ≥ v²/a`, else `2·sqrt(d/a)`.
  4-axis defaults `v=2.5 m/s`, `a=8 m/s²`; 6-axis derate ×0.85; dual-pallet
  divides cycle by `2·η_overlap = 1.9`.
- **Reach margin**: signed distance = `0.85·R_max - target_distance`;
  negative = HARD violation.
- **CP-SAT 16-gon**: regular 16-sided polygon inscribed in the disk of radius
  `0.85·R_max`; ~1.5% radial error, fully linear.

## What I'd improve with more time

- Full ISO 13855:2024 dynamic separation formula `S = K·T + DDS + Z`.
- Real 6-axis IK reach checks (currently approximated by the truncated-
  sphere envelope).
- CMA-ES vs SA comparison.
- Learned scoring weights from human feedback (Bradley-Terry).
- 3D preview pane via react-three-fiber.
- WebSocket for collaborative multi-engineer editing.
- Robot path planning visualization (CHOMP / RRT-Connect).
- Layer-by-layer pallet build animation tied to the stacking visualizer.
- Konva tween animation for the SA / CP-SAT pose update (currently snaps
  instantly).

## Project structure

```
.
├── CLAUDE.md
├── README.md
├── package.json              # pnpm workspace root
├── pnpm-workspace.yaml
├── docs/architecture.svg     # architecture diagram
├── scripts/dev.sh            # concurrently runs both
├── backend/
│   ├── pyproject.toml
│   ├── app/
│   │   ├── main.py           # FastAPI app (CORS + 6 routers)
│   │   ├── api/              # extract / layout / score / optimize / chat / examples
│   │   ├── schemas/          # WorkcellSpec, LayoutProposal, RobotSpec, etc.
│   │   ├── services/         # llm, catalog, layout, scoring, optimizer (SA + CP-SAT), extraction
│   │   ├── data/             # robots.json + examples/*.json
│   │   └── prompts/          # extract_spec.md
│   └── tests/                # 31 tests: schemas, catalog, layout, llm, scoring, optimizer, cpsat
└── frontend/
    ├── package.json
    ├── vite.config.ts        # /api proxy to localhost:8000
    ├── tailwind.config.ts
    └── src/
        ├── App.tsx           # 3-column layout
        ├── components/
        │   ├── canvas/       # WorkcellCanvas, Variants, Compare, shapes
        │   └── panels/       # Input, Spec, Scoring, Optimize, Pareto, Stacking, RobotInfo
        ├── store/            # Zustand + persist
        ├── api/              # client + types (mirror Pydantic)
        ├── hooks/
        └── lib/              # geometry, validation, stacking, utils
```

## Testing

```bash
cd backend && .venv/bin/python -m pytest tests/   # 31 passed
cd frontend && pnpm exec tsc -b                   # 0 type errors
cd frontend && pnpm build                         # production bundle
```

## Acknowledgments

- Robot specs from manufacturer datasheets (ABB, FANUC, KUKA, Yaskawa,
  Kawasaki); prices triangulated from RobotWorx, Robots.com, Surplus Record.
- Safety distances per ISO 13855 / EN ISO 13855:2024.
- OR-Tools CP-SAT for the no-overlap-2d formulation.
