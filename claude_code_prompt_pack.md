# XYZ Robotics Take-Home — Claude Code Prompt Pack

> 給 Chisheng 用的 Claude Code 完整作戰包。**使用方式:**
> 1. 先把第一章的 `CLAUDE.md` 內容存到專案 root 的 `CLAUDE.md`
> 2. 把第二章的 Bootstrap Prompt 貼進 Claude Code 開啟一個新會話
> 3. 完成 Bootstrap 後,依序貼 Phase 1 → Phase 7 的 prompts
> 4. 每個 phase 完成時,跑該 phase 的 acceptance test 確認通過再進下一個

---

## 一、CLAUDE.md(存到專案 root,作為持續性記憶)

```markdown
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

✅ Use: "mixed case palletizing", "random sequence infeed", "pallet planning", "MMR", "RockyOne", "RockyLight", "XYZ Studio Max", "interlock pattern", "reach envelope", "EOAT"

❌ Avoid: "RockPick", "PickPro", "MixedPal", "humanoid", "in-hand dexterous"

## Test discipline

- Pure-function tests (scoring.py, layout.py) are the highest ROI — they encode core engineering claims
- Skip Playwright unless the entire build is done with 90+ minutes to spare
- Frontend: Vitest on lib/validation.ts and lib/geometry.ts only

## What to do when stuck

1. If a phase's acceptance test fails, fix it before moving on — DO NOT stack debt
2. If running out of time at hour 7.5: ship Tier 1, skip everything else, polish README
3. If running out of time at hour 13: skip CP-SAT (Phase 6), polish what's there
4. Always reserve the final hour for README + screen recording — non-negotiable
```

---

## 二、Bootstrap Prompt(Phase 0,~30-90 分鐘)

把這段貼進 Claude Code 的第一個訊息:

```
Read the CLAUDE.md file in this directory in full before doing anything else.

Now bootstrap the project per the folder structure in CLAUDE.md. Specifically:

1. Initialize a pnpm workspace at the repo root with `pnpm-workspace.yaml` listing `frontend/*`. Backend uses its own venv.

2. Create `backend/pyproject.toml` using `[project]` syntax (PEP 621). Dependencies:
   - fastapi, uvicorn[standard], pydantic>=2.5, python-dotenv
   - ortools, numpy
   - anthropic, openai, google-genai, httpx
   - dev: pytest, pytest-asyncio, ruff, black, mypy
   Use `requires-python = ">=3.11"`.

3. Initialize the frontend with `pnpm create vite@latest frontend -- --template react-ts`. Then add: tailwindcss@3, postcss, autoprefixer, zustand, react-konva, konva, recharts, lucide-react, clsx, tailwind-merge. Set up Tailwind per their docs.

4. Set up shadcn/ui in frontend: `pnpm dlx shadcn@latest init` with defaults (Slate base color, CSS variables yes). Add these components: button, card, input, textarea, label, tabs, badge, separator, dialog, sheet, progress.

5. Create `scripts/dev.sh` that uses `concurrently` to run uvicorn (port 8000) and Vite (port 5173). Configure Vite proxy to forward `/api` to localhost:8000.

6. Create the empty Python package structure under `backend/app/` matching CLAUDE.md exactly. Each package needs `__init__.py`. Each `api/*.py` should have a stub `router = APIRouter()`. `main.py` should mount all routers under `/api`.

7. Create `backend/.env.example` with `ANTHROPIC_API_KEY=`, `OPENAI_API_KEY=`, `GOOGLE_API_KEY=`. The README will tell users to copy to `.env`.

8. Add `.gitignore` covering: node_modules, dist, __pycache__, .venv, .env, .pytest_cache, .ruff_cache, *.pyc.

9. Create a placeholder `README.md` with just the project title and "WIP" — we'll fill it in at the end.

ACCEPTANCE TEST:
- `cd backend && python -m uvicorn app.main:app --reload` starts without errors and `curl http://localhost:8000/api/health` returns `{"ok": true}`. (Add a /health endpoint in main.py for this.)
- `cd frontend && pnpm dev` starts Vite without errors and shows the default Vite + React landing page at localhost:5173.
- `bash scripts/dev.sh` starts both concurrently.

DO NOT write any business logic yet. Just scaffolding. Confirm acceptance test passes before stopping.
```

---

## 三、Phase 1 — Schema + Robot Catalog + LLM Abstraction(~2.5 小時)

```
Read CLAUDE.md again to refresh context.

This phase builds the type foundation and multi-LLM router. Three deliverables:

DELIVERABLE 1A: Pydantic schemas in backend/app/schemas/

Create workcell.py with:
- ComponentBase, Robot, Conveyor, Pallet, Fence, OperatorZone (discriminated union via type Literal field)
- Component = Annotated[Union[...], Field(discriminator="type")]
- Constraint (kind enum: min_clearance, max_cycle_time, must_reach, max_footprint; hard: bool)
- Throughput
- WorkcellSpec (matches the canonical schema in CLAUDE.md)

Create robot.py with:
- RobotSpec (model, manufacturer, axes, payload_kg, reach_mm, vertical_reach_mm_min, vertical_reach_mm_max, repeatability_mm, footprint_l_mm, footprint_w_mm, weight_kg, cycles_per_hour_std, price_usd_low, price_usd_high, ideal_use_case enum, manufacturer_url, notes)
- Computed property: effective_max_reach_mm = 0.85 * reach_mm

Create layout.py with:
- PlacedComponent (id, type, x_mm, y_mm, yaw_deg, dims dict — flexible per type)
- LayoutProposal (matches canonical schema)
- ScoreBreakdown (compactness, reach_margin, cycle_efficiency, safety_clearance, throughput_feasibility — each as a sub-score 0-1, plus violations list, plus aggregate score)

Create chat.py with:
- JsonPatchOp (op, path, value)
- ChatRefinementResponse (patch: list[JsonPatchOp], rationale: str, assumptions: list[str])

ALL Pydantic models must use `model_config = ConfigDict(extra="forbid")`. Every field needs a Field(description=...) — these descriptions feed into LLM tool schemas.

DELIVERABLE 1B: Robot catalog at backend/app/data/robots.json

Populate with these 22 real models — DO NOT invent specs, use the exact values:

ABB IRB 460 — 4-axis, 110 kg, 2400 mm reach, 925 kg, ~2190 cph @ 60 kg, $45-70k bare arm
ABB IRB 660-250 — 4-axis, 250 kg, 3150 mm, ~1650 kg, ~1000 cph, $60-85k
ABB IRB 760 — 4-axis, 450 kg, 3180 mm, 2300 kg, 880 cph, $90-130k
ABB IRB 6700-300/2.70 — 6-axis, 300 kg, 2700 mm, ~1310 kg, mixed-SKU use, $70-120k
FANUC M-410iC/110 — 4-axis, 110 kg, 2403 mm, 1030 kg, 2200 cph, $50-70k
FANUC M-410iC/185 — 4-axis, 185 kg, 3143 mm, 1600 kg, ~1600 cph, $65-85k
FANUC M-410iC/315 — 4-axis, 315 kg, 3143 mm, ~1600 kg, ~900 cph, $80-110k
FANUC M-410iB/700 — 4-axis, 700 kg, 3143 mm, 2700 kg, ~600 cph, $110-160k
FANUC M-410iB/140H — 5-axis, 140 kg, 2850 mm, ~1500 kg, 1900 cph, $60-80k
FANUC M-710iC/50 — 6-axis, 50 kg, 2050 mm, ~560 kg, mixed-SKU, $45-60k
KUKA KR 240 R3200 PA — 5-axis, 240 kg, 3195 mm, ~1150 kg, ~1400 cph, $80-105k
KUKA KR 470-2 PA — 5-axis, 470 kg, 3150 mm, ~2000 kg, ~1000 cph, $100-140k
KUKA KR 700 PA — 4-axis, 700 kg, 3320 mm, ~2800 kg, 1020 cph, $120-170k
Yaskawa MPL80II — 5-axis, 80 kg, 2061 mm, ~550 kg, ~1800 cph, $40-60k
Yaskawa MPL160II — 4-axis, 160 kg, 3159 mm, ~1100 kg, 1400 cph, $55-75k
Yaskawa MPL300II — 4-axis, 300 kg, 3159 mm, ~1500 kg, 1200 cph, $75-100k
Yaskawa MPL500II — 4-axis, 500 kg, 3159 mm, ~2200 kg, ~900 cph, $95-130k
Yaskawa MPL800II — 4-axis, 800 kg, 3159 mm, 2550 kg, ~600 cph, $130-180k
Kawasaki CP180L — 4-axis, 180 kg, 3255 mm, 1600 kg, 2050 cph @ 130, $55-75k
Kawasaki CP300L — 4-axis, 300 kg, 3255 mm, ~1650 kg, ~1800 cph, $70-95k
Kawasaki CP500L — 4-axis, 500 kg, 3255 mm, 1650 kg, 1000 cph, $90-125k
Kawasaki CP700L — 4-axis, 700 kg, 3255 mm, 1750 kg, 900 cph, $115-155k

For vertical_reach_mm_min/max: most palletizers' vertical envelope is roughly Z_min = -1200 mm below base, Z_max = +2000 mm to +2400 mm above base. Use sensible defaults (e.g., -1200 to +2200) and add a `notes` field flagging "approximate; see manufacturer dimension drawing for exact envelope."

For repeatability_mm: use ±0.5 mm (FANUC datasheet conservative value) for all FANUC/Kawasaki, ±0.07 mm for ABB and Yaskawa, ±0.06 mm for KUKA — these match published datasheets.

For ideal_use_case: assign by payload — light_case for ≤120 kg 4-axis, medium_case for 150-300 kg, heavy_bag for ≥400 kg, mixed_sku for ALL 6-axis (because they can re-orient cases).

Add a top-level `_meta` field with version, source_note: "Specs from manufacturer datasheets and distributor catalogs. Prices triangulated from RobotWorx, Robots.com, Surplus Record. Bare arm only; integrated cell costs 50-100% more."

Then create backend/app/services/catalog.py:
- RobotCatalogService class
- load() method reads robots.json into list[RobotSpec]
- find(min_payload_kg, min_reach_mm, max_price_usd, axes_filter, use_case_filter) returns ranked candidates
- get_by_id(model) returns single spec or raises 404

DELIVERABLE 1C: Multi-LLM abstraction in backend/app/services/llm.py

Design:
- LLMResult dataclass: text, parsed (typed Pydantic), usage_in_tokens, usage_out_tokens, cost_usd, model, latency_ms, provider
- LLMClient ABC with methods: chat(messages, ...) -> LLMResult, extract(messages, schema, ...) -> LLMResult, extract_with_repair(messages, schema, max_retries=2) -> LLMResult
- ClaudeClient, OpenAIClient, GeminiClient subclasses
- Schema adapter functions: for_openai_strict(pydantic_model), for_claude(pydantic_model), for_gemini(pydantic_model). Each takes a Pydantic model and returns a JSON schema dict shaped for that provider's structured-output API. Key differences:
  - OpenAI strict: every object needs additionalProperties: false; every property in required (use Optional unions for nullable); strip minLength/pattern/format/minimum
  - Claude: input_schema for tool use, with "strict": true at the top
  - Gemini: response_schema, strip additionalProperties (older SDK issue)
- LLMRouter class: routes by tier (extraction → fast model, layout → smart model) and provider (env-var driven default + per-call override). Has CostLedger that accumulates cost_usd per request.
- extract_with_repair: validate response against Pydantic; on ValidationError or refusal or empty parsed, append the failed turn + a user message "your previous output failed validation: {errors}; return ONLY valid JSON matching the schema" and retry. After max_retries, fall back to a different provider (Claude → OpenAI → Gemini → raise).

Model mapping:
- Fast tier: claude-haiku-4-5, gpt-5-nano, gemini-2.5-flash
- Smart tier: claude-sonnet-4-5, gpt-4.1, gemini-2.5-pro
(If a model name isn't recognized by the SDK, log a warning and use the closest available — don't crash.)

Pricing dict (per 1M tokens, as of 2026):
- claude-haiku-4-5: $1 / $5
- claude-sonnet-4-5: $3 / $15
- gpt-5-nano: $0.05 / $0.40
- gpt-4.1: $2.50 / $10
- gemini-2.5-flash: $0.30 / $2.50
- gemini-2.5-pro: $1.25 / $10

Use httpx-based async clients where possible. All public methods should be `async def`.

ACCEPTANCE TESTS:
- `pytest backend/tests/test_schemas.py` — write a minimal test that constructs a WorkcellSpec, RobotSpec, LayoutProposal and validates round-trip JSON serialization
- `pytest backend/tests/test_catalog.py` — write a test that loads robots.json, asserts 22 robots, and that find(min_payload_kg=200, axes_filter=[4]) returns the right subset
- `pytest backend/tests/test_llm.py` — write a unit test with a mocked HTTP client (use `respx` or just monkeypatch httpx) that verifies the schema-adapter output for each provider matches expected shape (especially additionalProperties: false for OpenAI). Don't make real API calls in tests.

When all three tests pass, stop. Do not move on to Phase 2.
```

---

## 四、Phase 2 — Spec Extraction + Greedy Layout Generation(~3 小時)

```
Read CLAUDE.md to refresh context. Confirm Phase 1 acceptance tests pass.

This phase delivers two endpoints: POST /api/extract and POST /api/generate-layout.

DELIVERABLE 2A: Spec extraction at backend/app/api/extract.py

System prompt at backend/app/prompts/extract_spec.md (use this verbatim):

```
You are a senior workcell layout engineer for an industrial palletizing robot.
You translate informal customer descriptions of a packaging line into precise,
schema-valid JSON specifications.

OUTPUT CONTRACT
1. Return ONLY JSON matching the supplied schema. No prose, no markdown fences, no preamble.
2. Use SI units throughout (millimeters for length, kilograms for mass, seconds, degrees Celsius).
   Convert from imperial; record the conversion in `assumptions`.
3. If a value is missing or ambiguous in the input, set the field to null AND append a
   short string to the `assumptions` array describing the assumption or missing data.
   NEVER fabricate measurements, weights, throughput, or part numbers.
4. When a numeric value is explicitly stated, copy it verbatim — do not round.
5. If the input describes more than one cell, return only the first; mention the others
   in `assumptions`.
6. Use industry vocabulary: "mixed case palletizing", "random sequence infeed",
   "interlock pattern", "EUR pallet", "ISO 13855 separation distance".

EDGE CASES
- "Continuous operation" or "no line stops" → infer dual-pallet stations; note in assumptions.
- Imperial pallet (48x40 in) → GMA standard (1219x1016 mm).
- Cycle rate given in cases/min → convert to cases_per_hour_target.
- Payload not stated but case mass given → leave robot_payload null; assumption:
  "robot payload should be at least case mass × max_pick_count + EOAT mass (typ. 30 kg)".
```

Endpoint:
- POST /api/extract takes { prompt: str } returns WorkcellSpec
- Use LLMRouter with tier="extraction", temperature=0
- Include 2 few-shot examples in the message array (one beverage line, one with ambiguous pallet type forcing nulls). Examples must show populated `assumptions` arrays.
- Call extract_with_repair so the response is guaranteed valid or surfaces an error
- Wrap in try/except — on irrecoverable failure, return 422 with the LLM's last raw output for debugging
- Log every call: prompt hash, model, latency, cost (use Python `logging` to a file at backend/logs/llm.jsonl)

DELIVERABLE 2B: Greedy layout generation at backend/app/services/layout.py and api/layout.py

In services/layout.py, implement:

class GreedyLayoutGenerator:
    def __init__(self, catalog: RobotCatalogService): ...

    def generate(self, spec: WorkcellSpec, n_variants: int = 3) -> list[LayoutProposal]:
        """Returns up to 3 proposals using different templates."""

    def _template_in_line(self, spec, robot) -> LayoutProposal: ...
    def _template_l_shape(self, spec, robot) -> LayoutProposal: ...
    def _template_u_shape(self, spec, robot) -> LayoutProposal: ...
    def _template_dual_pallet(self, spec, robot) -> LayoutProposal: ...

Algorithm for each template:
1. Anchor robot at workcell origin or center per template
2. Place infeed conveyor at 0.7 * R_max distance from robot, on the upstream side
3. Place pallet stations within reach annulus on the appropriate side(s)
4. Add operator zone adjacent to the open side
5. Generate fence as offset polyline around components, at S_safe = K*T + C distance from reach envelope (K=2000, T=0.3s, C=850 → 1450 mm if no curtain; use 600 mm with hard guard if spec.has_hard_guard)

Robot selection logic:
- Find catalog robots where:
  - payload_kg ≥ (spec.case_mass_kg or 15) × pick_count_estimate (default 1) + 30 kg EOAT
  - effective_max_reach_mm ≥ farthest pallet corner distance from robot base for the template
  - cycles_per_hour_std ≥ spec.throughput.cases_per_hour_target × 1.1 (10% headroom)
- Rank by price, select cheapest. If none feasible, return a proposal with robot_model_id=None and assumption explaining the bottleneck.

Each proposal must compute estimated_cycle_time_s using the trapezoidal motion profile (see CLAUDE.md). For dual_pallet, multiply UPH by 1.9 (η_overlap=0.95).

In api/layout.py:
- POST /api/generate-layout takes WorkcellSpec, returns list[LayoutProposal]
- Wraps GreedyLayoutGenerator
- Records assumptions at proposal level (e.g., "no robot satisfies 4500 cph at 25 kg payload with 3.5 m reach in $150k budget — relaxed budget to $185k")

DELIVERABLE 2C: Sample input fixtures at backend/tests/fixtures/

Create three sample NL prompts as .txt files:
- beverage_eur.txt (the "canned beverage trays, 500/hr, EUR, $160k, dual pallet" prompt from the brief)
- mixed_sku.txt (a description involving 3 SKUs with different masses needing repacking)
- ambiguous.txt (deliberately missing pallet type and case dimensions to force assumptions)

ACCEPTANCE TESTS:
- pytest backend/tests/test_layout.py:
  - test_greedy_in_line: build a simple spec, generate, assert robot is selected, fence wraps everything, no overlaps in PlacedComponent bounding boxes
  - test_greedy_dual_pallet: assert two pallet stations on opposite sides
  - test_no_feasible_robot: spec demanding 1000 kg payload at 5 m reach → returns proposal with robot_model_id=None and a clear assumption
  - test_cycle_time_trapezoidal: verify the math against the M-410iC/110 datasheet (2200 cph at standard cycle ≈ 1.636 s/cycle)

- Manual smoke test:
  ```
  uvicorn app.main:app --reload &
  curl -X POST http://localhost:8000/api/extract -H "Content-Type: application/json" -d '{"prompt": "<beverage_eur.txt content>"}'
  ```
  Should return a valid WorkcellSpec JSON. If you don't have API keys, use a mocked LLM client for the smoke test.

When tests pass and the manual smoke test works, stop.
```

---

## 五、Phase 3 — Frontend Canvas + Konva Visualization(~2 小時)

```
Read CLAUDE.md. Confirm Phase 2 acceptance tests pass.

This phase puts a working canvas in front of the user. No drag-edit yet — that's Phase 4.

DELIVERABLE 3A: Zustand store at frontend/src/store/layoutStore.ts

State:
- prompt: string
- spec: WorkcellSpec | null
- proposals: LayoutProposal[]
- activeProposalId: string | null
- isExtracting: boolean
- isGenerating: boolean
- errors: string[]

Actions:
- setPrompt(s: string)
- runExtract() — async, calls /api/extract, sets spec
- runGenerate() — async, calls /api/generate-layout with current spec, sets proposals, sets activeProposalId to first
- setActiveProposal(id: string)
- updateComponentPose(componentId, x, y, yaw) — for Phase 4 use later

Use `persist` middleware to save state to localStorage so refreshing doesn't lose work.

DELIVERABLE 3B: API client at frontend/src/api/

- types.ts: TypeScript types mirroring the Pydantic schemas. Use unions and discriminated types matching the Python ones. Generate or hand-write — your call. For 15 hours, hand-write is fine.
- client.ts: thin wrapper around fetch() with baseUrl="/api" (Vite proxy handles routing)

DELIVERABLE 3C: Konva canvas at frontend/src/components/canvas/

Files:
- WorkcellCanvas.tsx — top-level Stage with two Layers: a static layer (grid, fence, listening=false) and a dynamic layer (robot, conveyor, pallets, operator zone)
- RobotShape.tsx — Group containing a Circle (robot base footprint) + dashed Circle (max reach) + light-shaded Arc (vertical reach band annotation if needed) + Text label
- ConveyorShape.tsx — Rect with arrow indicating flow direction
- PalletShape.tsx — Rect with thin border, label showing standard (EUR/GMA), pattern badge
- FenceShape.tsx — Line (polyline, closed) with stroke
- OperatorZoneShape.tsx — Rect with diagonal hatching
- ReachEnvelope.tsx — overlay showing the reach annulus, toggleable

Use a fixed scale: 1 mm = 0.05 px (so a 10 m × 10 m cell fits in 500 × 500 px canvas). Add a useStageSize hook that adjusts to viewport.

Coordinate conversion utility at frontend/src/lib/geometry.ts:
- mmToPx(mm: number): number
- pxToMm(px: number): number
- All geometry functions: rectangle overlap, point-in-rect, distance to polyline, etc.

DELIVERABLE 3D: App layout at frontend/src/App.tsx

3-column layout (Tailwind grid):
- Left (320px): InputPanel (prompt textarea + Extract button) + SpecPanel (formatted JSON with collapsible assumptions section)
- Center (flex-1): WorkcellCanvas + below it a small "Variants" strip showing thumbnail of each proposal (clicking switches active)
- Right (320px): ScoringPanel (placeholder for now — will populate in Phase 5) + RobotInfoPanel (selected robot specs)

Use shadcn/ui components: Card for panels, Tabs if needed, Button, Textarea, Label, Badge, Separator.

DELIVERABLE 3E: Spec extraction & generation flow

Wire up:
1. User types in prompt textarea
2. Clicks "Extract" → runExtract() runs, SpecPanel populates, "Generate Layout" button enables
3. Clicks "Generate Layout" → runGenerate() runs, canvas populates with first proposal, variants strip shows thumbnails
4. Clicking a variant thumbnail switches the canvas to that proposal

Loading states: skeleton loaders, disable buttons while in flight, show errors via shadcn Alert.

ACCEPTANCE TEST:
- Manual: type the beverage_eur prompt, click Extract → spec panel populates with assumptions visible; click Generate → canvas shows robot at center with reach circle, conveyor on one side, pallets on the other, fence wrapping everything, operator zone visible. Variants strip shows 2-3 thumbnails. Clicking each switches the layout.

When this works end-to-end visually, stop.
```

---

## 六、Phase 4 — Drag-to-Edit + Real-Time Validation + Scoring(~3 小時)

```
Read CLAUDE.md. Confirm Phase 3 demo works.

This is the heart of the "interactivity" score (30% weight). Take it seriously.

DELIVERABLE 4A: Client-side validation at frontend/src/lib/validation.ts

Pure functions, fast (<1ms for ~10 components):
- aabbOverlap(a, b): boolean — axis-aligned bounding box overlap
- componentRect(c: PlacedComponent): {x, y, w, h} — accounts for yaw via rotation; for Phase 4 keep yaw=0 (axis-aligned only) and add rotated AABB if time permits
- distToPolyline(point, polyline): number
- reachableByRobot(target: Point, robot: PlacedComponent, robotSpec: RobotSpec): { ok: boolean, signedMargin: number }
- validateLayout(proposal: LayoutProposal, spec: WorkcellSpec, catalog: RobotSpec[]): {
    overlaps: [string, string][],     // pairs of component IDs that overlap
    unreachableTargets: { componentId, signedMargin }[],
    fenceClearanceViolations: { componentId, slack_mm }[],
    operatorZoneIntrusion: boolean,
    summary: { ok: boolean, hardViolations: number, softWarnings: number }
  }

DELIVERABLE 4B: Drag-edit in canvas components

For each shape (RobotShape, ConveyorShape, PalletShape):
- Set draggable={true}
- onDragMove: compute new (x, y), call validateLayout, update store with new pose, update violation set
- onDragEnd: same as above plus debounced backend call (Phase 4D)
- Use Konva Transformer for rotation when component is selected (click to select)
- dragBoundFunc: snap to grid every 50 mm; clamp inside cell envelope

When a component has any violation, render its shape with a red stroke (3 px) and a small red badge (use Konva.Text or HTML overlay) showing "REACH" or "OVERLAP" or "FENCE".

Add a selection ring (subtle blue outline) for the currently selected component.

DELIVERABLE 4C: Backend scoring service at backend/app/services/scoring.py

Implement all 5 scoring components (see CLAUDE.md):

```python
def score_compactness(proposal, spec) -> float:
    """Returns 0-1 (higher is better)."""

def score_reach_margin(proposal, spec, robot_spec) -> dict:
    """Returns { score: 0-1, min_margin_mm, target_margins: [...] }"""

def score_cycle_efficiency(proposal, robot_spec, target_uph) -> dict:
    """Returns { score: 0-1, estimated_cycle_s, estimated_uph }"""

def score_safety_clearance(proposal, spec) -> dict:
    """Returns { score: 0-1, fence_slack_mm, iso13855_pass: bool }"""

def score_throughput_feasibility(estimated_uph, target_uph) -> float:
    """Returns 0-1, saturated at 1.1*target."""

def score_layout(proposal, spec, robot_spec, weights=None) -> ScoreBreakdown:
    """Aggregate. Default weights: c=0.20, r=0.30, t=0.20, s=0.30."""
```

Use sigmoid normalization for safety and reach (k=0.005, x0=500 for safety; k=0.003, x0=300 for reach), min-max for compactness and throughput. Hard infeasibility (any unreachable target, ISO 13855 violation) yields aggregate score = 0 with a violations list populated.

Trapezoidal motion profile: v_xy=2.5 m/s, a_xy=8 m/s² for 4-axis; multiply by 0.85 for 6-axis.

POST /api/score takes { proposal, spec, robot_model_id } returns ScoreBreakdown.

DELIVERABLE 4D: Hybrid validation flow

In WorkcellCanvas:
- onDragMove: client-side validateLayout only (instant red highlights)
- onDragEnd: client-side + debounced (150ms) call to /api/score with AbortController to cancel stale requests

In ScoringPanel (right column):
- Five horizontal progress bars, one per score component, with the value 0-1 and a tooltip explaining what that score measures
- An aggregate score circle (large, prominent) at the top
- A violations list below (red if hard, yellow if soft)
- A small "history" sparkline (recharts) showing aggregate score over the last 20 changes

DELIVERABLE 4E: Scoring tests at backend/tests/test_scoring.py

- test_compactness_perfect: tightly packed layout returns near-1
- test_compactness_sparse: spread-out layout returns lower
- test_reach_margin_at_boundary: target exactly at 0.85*R_max returns margin=0, score=0.5
- test_reach_margin_unreachable: target at 1.1*R_max returns negative signed margin, score=0, hard violation
- test_cycle_efficiency_m410ic110: standard cycle should give ~36 cpm matching FANUC datasheet
- test_safety_iso13855_no_curtain: 1450 mm clearance passes; 600 mm fails
- test_aggregate_zeroes_on_hard_violation: any unreachable target → aggregate=0

ACCEPTANCE TEST:
- All scoring tests pass
- Manual demo: drag a pallet outside the reach envelope → it turns red instantly; ScoringPanel shows reach_margin score drop and a hard violation badge; drag back inside → returns green and score recovers. Drag the conveyor to overlap the robot footprint → both turn red, OVERLAP badge appears.

When this is smooth and the score updates feel snappy, stop.
```

---

## 七、Phase 5 — SA Optimizer + Multi-Variant Comparison(~2.5 小時)

```
Read CLAUDE.md. Confirm Phase 4 demo is responsive.

This phase adds the "Optimize" button (Tier 2 deliverable) and side-by-side variant comparison.

DELIVERABLE 5A: Simulated Annealing optimizer at backend/app/services/optimizer.py

```python
class SAOptimizer:
    def __init__(self, scoring_service, max_iterations=400, T0=1.0, T_min=0.001):
        ...

    def optimize(
        self,
        seed_proposal: LayoutProposal,
        spec: WorkcellSpec,
        robot_spec: RobotSpec,
        on_step: Callable[[int, LayoutProposal, float], None] | None = None,
    ) -> tuple[LayoutProposal, list[float]]:
        """Returns (best_proposal, score_history)."""
```

Algorithm:
- Geometric cooling: T(i) = T0 * (T_min/T0)^(i/n)
- Perturbation: pick a random component (excluding robot — robot is fixed for this phase), Gaussian (sigma=80mm) on (x,y), 10% chance of 90° yaw flip
- Accept if Δscore > 0 OR random() < exp(Δscore / T)
- Constrain proposed positions to remain inside cell envelope; reject (don't even score) any move that would violate this
- Track best proposal seen; return best, not last

Add post-processing: after SA converges, run one greedy "snap to grid 50mm" pass that only accepts strictly-better moves. This makes the result look clean.

DELIVERABLE 5B: Optimization endpoint at backend/app/api/optimize.py

POST /api/optimize takes:
- { proposal, spec, robot_model_id, max_iterations? }
returns:
- { optimized_proposal, score_history: float[], delta_summary: { compactness: +0.12, reach: +0.05, ...} }

For demo polish: stream progress via Server-Sent Events. Endpoint POST /api/optimize/stream returns text/event-stream with events:
- `progress`: { iteration, current_score, best_score }
- `done`: { optimized_proposal, score_history, delta_summary }

In FastAPI use StreamingResponse + an asyncio.Queue pattern.

DELIVERABLE 5C: Optimize button UI

In ScoringPanel:
- "Optimize" button below the aggregate score circle
- On click: open an EventSource to /api/optimize/stream
- Show a progress bar (recharts LineChart with the score history streaming in)
- When done, animate the components moving to their new positions over ~600ms (use Konva's `to()` tween)
- Show a delta summary: "Compactness +12%, Reach +5%, Cycle +0%, Safety unchanged"

If user drags during optimization, cancel the stream.

DELIVERABLE 5D: Multi-variant comparison

Below the canvas (the "Variants strip" stub from Phase 3):
- Render each LayoutProposal as a 200×150 px Konva mini-canvas at small scale
- Show the aggregate score below each thumbnail with a colored badge
- Highlight the active one
- Add a "Generate more" button that re-calls /api/generate-layout with temperature=0.5 to get more diverse alternatives

Add a dedicated comparison view: click "Compare" → opens a Sheet (shadcn) showing all proposals in a 2×N grid with full ScoreBreakdown side-by-side.

DELIVERABLE 5E: Pareto thumbnail in ScoringPanel

Below the score history sparkline:
- Recharts ScatterChart with x=footprint_m², y=estimated_uph
- Each historical proposal as a dot (max 20 dots, FIFO)
- Pareto frontier highlighted (compute non-dominated set on the client)

ACCEPTANCE TEST:
- Generate a layout, click Optimize → progress streams, components animate to new positions, score visibly improves
- Multiple variants visible in strip; clicking each switches active proposal
- Manual: introduce a deliberately bad initial proposal (drag a pallet halfway out of reach), click Optimize → SA should pull it back into reach and the violation should clear

When optimization feels snappy and the visual feedback is satisfying, stop.
```

---

## 八、Phase 6 — CP-SAT Showcase(~1 小時)

```
Read CLAUDE.md. Confirm Phase 5 demo works. Decision point: if SA produces good-looking results AND README+recording can fit in 1 hour, do this phase. If Tier 2 still feels rough, skip to Phase 7 to polish.

This is the engineering-depth showcase. Goal: a CP-SAT refinement that visibly produces cleaner, more aligned layouts than SA, with the code clearly demonstrating constraint programming.

DELIVERABLE 6A: CP-SAT solver in backend/app/services/optimizer.py

```python
class CPSATRefiner:
    def __init__(self, scoring_service): ...

    def refine(
        self,
        seed_proposal: LayoutProposal,
        spec: WorkcellSpec,
        robot_spec: RobotSpec,
        time_limit_s: float = 15.0,
    ) -> tuple[LayoutProposal, dict]:
        """Returns (refined_proposal, solver_stats: {status, objective, walltime_s, num_branches})"""
```

Implementation:
1. Use ortools.sat.python.cp_model. All dimensions integer (mm).
2. For each component except robot (robot stays at seed position), create x and y IntVars within (0, cell_W - w) and (0, cell_H - h).
3. Create IntervalVars and call `model.add_no_overlap_2d(x_intervals, y_intervals)` — this is THE killer constraint.
4. Reachability: 16-half-plane polygon approximation of the reach annulus around the robot. For each pick/place point (e.g., conveyor exit, each pallet center), enforce 16 linear constraints.
5. Fence clearance: encode as no-overlap between component bounding boxes (expanded by S_safe) and a phantom "robot-reach + safety" rectangle.
6. Objective: lexicographic — first minimize hard violations to zero (it's actually constraints), then minimize the bbox surrogate `bx + by` where `bx ≥ x_i + w_i`, `by ≥ y_i + h_i` for all i.
7. Set max_time_in_seconds, num_search_workers=4.

Document every non-obvious modeling choice as a comment in the code (these become README design decisions). Specifically explain:
- Why CP-SAT over MILP (cite the AddNoOverlap2D advantage)
- Why 16-gon approximation (linear constraints, not quadratic — CP-SAT can't natively do circles)
- Why integer-only and the mm scale choice
- Why lexicographic vs weighted-sum here (because we have a clear hard-vs-soft hierarchy)

DELIVERABLE 6B: Endpoint POST /api/optimize/cpsat

Same input shape as /api/optimize, returns refined_proposal + solver_stats. Don't stream — CP-SAT runs fast enough (< 15s) that polling or just awaiting is fine.

DELIVERABLE 6C: UI toggle

In ScoringPanel below the Optimize button:
- Add a Tabs component: "SA" | "CP-SAT"
- Each tab has its own Optimize button
- After CP-SAT runs, show a small "Solver stats" card: status (OPTIMAL / FEASIBLE), objective value, wall time, num branches explored
- Side-by-side comparison: a "Compare SA vs CP-SAT" button that opens a Dialog showing both refined layouts and their score breakdowns

DELIVERABLE 6D: Test at backend/tests/test_cpsat.py

- test_cpsat_no_overlap: refine a deliberately-overlapping seed, assert no overlaps in result
- test_cpsat_reachability: assert all pick/place targets are within 0.85*R_max horizontal in the result
- test_cpsat_finds_better_compactness: compare SA result vs CP-SAT result on the same seed; CP-SAT should be at least as compact (objective ≤ SA's bx+by)

ACCEPTANCE TEST:
- Click "CP-SAT" tab → Optimize → cleaner, more grid-aligned layout appears, solver stats show OPTIMAL status, comparison dialog shows side-by-side bbox sizes (CP-SAT typically ≥5% more compact than SA on cluttered seeds).

When the CP-SAT visibly produces tighter layouts than SA on a couple of test prompts, stop.
```

---

## 九、Phase 7 — Tier 3 + README + Polish + Recording(~1 小時)

```
Read CLAUDE.md. This is the final phase. Time budget is tight.

Pick ONE Tier 3 feature based on what's most polished:
- (a) Chat refinement — only if multi-LLM is rock solid
- (b) Code-driven parametric design — only if you want to showcase symbolic generation
- (c) Stacking pattern visualizer — pure frontend, zero risk, highest visual impact for time invested

RECOMMENDATION: do (c) unless you have ≥45 minutes left.

DELIVERABLE 7A (option c): Stacking pattern visualizer

In frontend, add a new panel below the canvas (or as a Tab in the ScoringPanel sheet):
- Render the pallet's top-down view at 2×–3× zoom
- Draw cases per layer using the chosen pattern (column / interlock / pinwheel)
- A layer slider to scrub through layers
- Show metrics: cases per layer, total cases, total stack height, load efficiency (volume utilization), CoG offset from pallet center

Implementation:
- Create lib/stacking.ts with pure functions:
  - columnPattern(palletDims, caseDims, count) → CaseRect[]
  - interlockPattern(...) → CaseRect[][] (one per alternating layer)
  - pinwheelPattern(...) → CaseRect[]
- Render with Konva on a separate small Stage

DELIVERABLE 7B: README.md

Top-down structure (use this exact outline, fill in content):

```markdown
# XYZ Robotics — LLM-Driven Workcell Layout Optimizer

> Take-home submission for XYZ Robotics. End-to-end pipeline: natural language → structured spec → robot selection → optimized 2D layout → interactive editing → re-optimization.

## TL;DR

I built this around three engineering bets:

1. **Multi-LLM abstraction** — provider-agnostic Pydantic schemas with per-provider adapters (Claude / OpenAI / Gemini), structured outputs via constrained decoding, and a validation-repair loop with cross-provider fallback.

2. **Hard/soft constraint discipline** — ISO 13855 separation distance and reach feasibility are inviolable; throughput and compactness are weighted soft objectives with squared penalties. Safety is never substitutable.

3. **CP-SAT for layout refinement** — Google OR-Tools' native `AddNoOverlap2D` handles the disjunctive non-overlap reasoning without big-M tuning. The LLM picks discrete decisions (template, robot model); CP-SAT picks continuous positions.

## Architecture

[Insert architecture diagram — make this in Excalidraw, export SVG, embed]

User → React + Konva canvas + Zustand → FastAPI gateway → {LLMRouter (Claude/OpenAI/Gemini), CatalogService, LayoutService (greedy templates + CP-SAT), ScoringService, OptimizerService (SA + CP-SAT)} → Pydantic schemas as the canonical contract throughout.

## Setup

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env  # add your API keys

# Frontend
cd ../frontend
pnpm install

# Run both
cd .. && bash scripts/dev.sh
# → http://localhost:5173
```

You need at least one of: ANTHROPIC_API_KEY, OPENAI_API_KEY, GOOGLE_API_KEY.

## Pipeline

1. **Extract** — LLM parses NL prompt into `WorkcellSpec` with `assumptions` array tracking inferred values
2. **Select** — `RobotCatalogService` filters 22 real palletizing robots (ABB / FANUC / KUKA / Yaskawa / Kawasaki) by payload, reach, throughput, cost
3. **Layout** — Four-template greedy generator (in-line / L / U / dual-pallet) seeds initial proposals
4. **Score** — Five components: compactness, reach margin, cycle efficiency, safety clearance, throughput feasibility
5. **Refine** — SA (continuous) or CP-SAT (combinatorial) optimization

## Key design decisions

[For each, state the decision and the rejected alternative — the "why not" matters]

### Why CP-SAT over MILP from scratch
[OR-Tools native AddNoOverlap2D handles disjunctive constraints without big-M; lazy clause generation; integer-only forced explicit mm scale; free vs Gurobi license]

### Why react-konva over Fabric / SVG / Three
[Declarative React + native Transformer + multi-layer perf for static-vs-dynamic split]

### Why a hand-rolled multi-LLM abstraction over Instructor or LiteLLM
[Take-home visibility — production would use Instructor + add the repair loop and cost telemetry on top]

### Why discriminated-union Pydantic with `assumptions` field
[Hallucination control: forces LLM to admit uncertainty rather than fabricate; type-mirrors cleanly to TypeScript]

### Why sigmoid normalization for safety/reach but min-max for compactness
[Saturation matches physical reality — more clearance ≠ better past a threshold; compactness is genuinely linear-better]

### LLM/solver split
[LLM handles discrete decisions (template choice, robot model selection, infeasibility explanations); numerical solver handles continuous geometry. This neuro-symbolic pattern mirrors XYZ Robotics' own contact-rich-manipulation lineage (Mason at CMU, Rodriguez at MIT MCube) where physical constraints are encoded explicitly rather than learned implicitly. Same pattern scales from layout planning to mixed case palletizing's pallet-planning solver.]

## Schema

[Brief WorkcellSpec excerpt with annotations]

## Multi-LLM details

[Provider-specific structured-output paths: Claude tool use with strict input_schema, OpenAI strict response_format, Gemini response_schema. Each provider gets a schema-shape adapter (~20 lines each).]

## Scoring math

[Brief formulas: ISO 13855 S = K·T + C; trapezoidal cycle time; reach margin signed distance]

## What I'd improve with more time

- Full ISO 13855:2024 dynamic separation formula `S = K·T + DDS + Z`
- Real 6-axis IK reach checks (currently approximated with truncated-sphere envelope)
- CMA-ES vs SA comparison
- Learned scoring weights from human feedback (Bradley-Terry)
- 3D preview pane via react-three-fiber
- WebSocket for collaborative multi-engineer editing
- Robot path planning visualization (CHOMP / RRT-Connect)
- Layer-by-layer pallet build animation tied to the stacking visualizer

## Project structure

[paste tree from CLAUDE.md]

## Testing

```bash
cd backend && pytest
cd frontend && pnpm test
```

## Acknowledgments

- Robot specs from manufacturer datasheets (ABB, FANUC, KUKA, Yaskawa, Kawasaki)
- Safety distances per ISO 13855 / EN ISO 13855:2024
- OR-Tools CP-SAT for the no-overlap-2d formulation
```

DELIVERABLE 7C: Architecture diagram

Use Excalidraw at https://excalidraw.com:
- Three columns: Frontend / Backend / External
- Frontend: WorkcellCanvas → Zustand store → API client
- Backend: API routes → Services (LLM, Catalog, Layout, Scoring, Optimizer) → Schemas → robots.json
- External: Anthropic / OpenAI / Gemini APIs
- Draw arrows showing data flow; annotate the LLM/solver split clearly
- Export as SVG, save to `docs/architecture.svg`, reference from README

DELIVERABLE 7D: Final polish pass

- Run `ruff check --fix backend/` and `pnpm lint` in frontend; fix all warnings
- Verify the dev script starts both services with one command and that demo flow works end-to-end
- Add a sample prompt in the input textarea as placeholder text (the beverage_eur prompt)
- Verify error states are graceful (no API key → show banner with instructions; LLM failure → show retry button)
- Save 2-3 sample WorkcellSpec JSON files in backend/app/data/examples/ that load via a "Load Example" dropdown in the UI

DELIVERABLE 7E: Screen recording (separate task — record yourself, not Claude Code)

3-minute structure:
- 0:00–0:30 Type beverage prompt, click Extract, highlight assumptions
- 0:30–1:15 Show 3 variants, click into one, drag conveyor to break reach → red, drag back → green
- 1:15–2:00 Click Optimize (SA), watch score climb; click CP-SAT tab, optimize again, see cleaner layout
- 2:00–2:30 Show ScoringPanel breakdown, Pareto scatter; brief look at chat refinement OR stacking visualizer
- 2:30–3:00 Cut to README architecture diagram + CP-SAT code snippet; voiceover summarizes the three engineering bets

ACCEPTANCE TEST (FINAL):
- Fresh clone, follow README setup, `bash scripts/dev.sh` → working demo in <2 minutes setup
- All pytest tests pass
- README renders cleanly on GitHub
- Recording uploaded to a private link

When the demo cleanly walks through the recording script without bugs, ship it.
```

---

## 四、執行小貼士

**動工前 5 分鐘做的事:**

1. 把 `CLAUDE.md` 內容存到專案 root,Claude Code 每次 session 開始會自動讀
2. 申請好 API keys(至少其中一個):Anthropic / OpenAI / Google AI Studio
3. 確認本機有 Python 3.11+ 和 Node 18+ 和 pnpm

**執行 phases 的節奏:**

- 每個 phase 開新對話(`/clear` 或開新 session),只貼那個 phase 的 prompt
- Phase 完成後,**親自跑一次 acceptance test**,別只看 Claude Code 說「done」
- 每個 phase 結束都 git commit,保有 rollback 能力
- 卡關超過 30 分鐘 → 把錯誤訊息 + 相關檔案內容貼回 Claude Code,要它先診斷再改

**最重要的紀律:**

- **永遠不要跳過 acceptance test**。半破的 phase 帶到下個 phase 是最大的 anti-pattern。
- **時間到 hour 7.5 就決定**:Tier 1 沒過就 ship Tier 1;Tier 1 過了就繼續 Tier 2。
- **時間到 hour 13 再決定一次**:CP-SAT 跑得動就做 Phase 6;不行就直接跳 Phase 7。
- **最後 1 小時無論如何只做 README 和錄影** — 這是 reviewer 第一眼看的東西。

**如果 phase 太大裝不下 Claude Code 的 context:** 把 phase 內的 deliverables 各自切成獨立 prompt,但仍然先讓 Claude Code 讀 CLAUDE.md 重新對齊 context。

祝順利,有任何 phase 卡住記得回來找我。
