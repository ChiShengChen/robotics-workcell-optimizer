# `xyz_modules` — handoff brief for the receiving coding agent

Self-contained extraction of five sub-systems from the XYZ Robotics workcell
layout optimizer. Intended to be lifted whole into another project's source
tree (preferred), or installed as a wheel via `pip install -e .` from this
directory.

| Sub-system            | Module                          | What it does                                          | Inputs                              | Outputs                                  |
|-----------------------|---------------------------------|-------------------------------------------------------|-------------------------------------|------------------------------------------|
| Cost model + BOM      | `xyz_modules.bom`               | Itemised BOM with capex range + mass + ROI            | Trial-config dict                   | `BomReport` (15 line items)              |
|                       | `xyz_modules.cad_export`        | `build_cost_breakdown()` adapter for placed components| Components list + robot ids         | `CostBreakdown`-shaped dict              |
| Scoring               | `xyz_modules.scoring`           | 5-component layout scoring (compactness, reach margin, cycle, ISO 13855 safety, throughput) | `LayoutProposal`, `WorkcellSpec`, `RobotSpec` | `ScoreBreakdown`                |
| Kinematics            | `xyz_modules.kinematics`        | Trapezoidal motion, ISO 13855 separation              | Robot proto                         | Times in s, distances in mm              |
| CAD generation        | `xyz_modules.export_cad`        | DXF / DWG / STL / STEP writers                        | Trial-config dict + robot dict      | Files on disk                            |
| Floor-plan import     | `xyz_modules.cad_import`        | DXF → polygon obstacles                               | DXF bytes                           | `CadImportResult`                        |
|                       | `xyz_modules.image_floor_plan`  | PNG / JPG → polygon obstacles (OpenCV; optional Gemini Vision hybrid) | Image bytes + floor size in m | `ImageImportResult`               |

The original project that produced this bundle lives at
[ChiShengChen/robotics-workcell-optimizer](https://github.com/ChiShengChen/robotics-workcell-optimizer).
Treat the rest of that repo as reference material — only the files in
`xyz_modules/` are needed.

---

## 1. Quickstart (5 minutes)

```bash
# From the directory containing xyz_modules_portable/
cd xyz_modules_portable
python -m venv .venv && source .venv/bin/activate
pip install -e .                 # core deps; or `pip install -e .[full]` for cadquery + gemini
python examples/quickstart.py    # exercises every public entry point
```

Expected output ends with `Done.` and reports byte sizes for the 6 export
formats and a Pareto-style cost summary. If it fails, **read which section
fails first** — sections are independent.

---

## 2. Public API surface

### 2.1 `xyz_modules.kinematics` — pure helpers

```python
from xyz_modules.kinematics import (
    trapezoidal_time_s, estimate_cycle_time_s, estimate_uph,
    iso13855_safety_distance_mm,
)

# Single trapezoidal move
t = trapezoidal_time_s(distance_mm=2800, v_max_mm_s=2500, a_mm_s2=8000)

# ISO 13855: S = K·T + C
s = iso13855_safety_distance_mm(has_hard_guard=False)   # 1450 mm
```

`estimate_cycle_time_s` takes any object with `.axes` and
`.cycles_per_hour_std` attributes — the bundled `RobotSpec` works, or your
own type duck-typed to that contract.

### 2.2 `xyz_modules.scoring` — `score_layout(proposal, spec, robot_spec) -> ScoreBreakdown`

Pure function. Five sub-scores in `[0, 1]`, each independently meaningful:

| Sub-score              | Hard-violation triggers                          |
|------------------------|--------------------------------------------------|
| `compactness`          | None (always soft)                               |
| `reach_margin`         | Unreachable pick/place (`kind: unreachable`)     |
| `cycle_efficiency`     | None                                             |
| `safety_clearance`     | ISO 13855 distance < `K·T + C` (`kind: iso13855`)|
| `throughput_feasibility` | None                                           |

Hard violations zero the `aggregate` field but leave the sub-scores intact —
this is the **discipline** the system is built around: don't let safety
become substitutable. Re-create that discipline in any UI you build on top.

```python
from xyz_modules.scoring import score_layout
sb = score_layout(proposal, spec, robot_spec, weights={
    "safety_clearance": 0.30,
    "reach_margin":      0.25,
    "cycle_efficiency":  0.20,
    "throughput_feasibility": 0.15,
    "compactness":       0.10,
})
print(sb.aggregate, sb.violations)
```

### 2.3 `xyz_modules.cad_import` — `parse_dxf(data: bytes) -> CadImportResult`

```python
from xyz_modules.cad_import import parse_dxf
result = parse_dxf(open("floor.dxf", "rb").read(),
                   scale_to_mm=1.0,        # 1000 if drawing is in metres
                   margin_mm=200.0,        # shift origin to (200, 200)
                   treat_largest_as_boundary=True)
# result.obstacles: list[CadObstacle]      — polygons (mm)
# result.bounding_box_mm: (x0, y0, x1, y1) — assumed cell extents
# result.suggested_cell_envelope_mm: (W, H) — bbox × 1.05
```

Helper `aabb_intersects_polygon(rect_x, rect_y, rect_w, rect_h, polygon)`
is used by scoring to test placement against obstacles; expose it in your
validation code too.

### 2.4 `xyz_modules.image_floor_plan` — `parse_image(...)`

```python
from xyz_modules.image_floor_plan import parse_image
result = parse_image(open("floor.png", "rb").read(),
                     floor_w_m=12.0, floor_h_m=8.0,
                     mode="cv",         # 'cv' | 'auto' | 'hough' | 'hybrid'
                     margin_mm=200.0)
# result.rects: list[ImageRect]          — wall | obstacle classification
# result.bounding_box_mm, suggested_cell_envelope_mm: same as DXF importer
```

Modes:
- **`cv`** (default) — Otsu threshold + contour bounding boxes. Best for
  clean vector floor plans.
- **`auto`** — per-contour dispatch: thin rects → `hough`, blocky →
  `cv`. Reasonable default for mixed plans.
- **`hough`** — `HoughLinesP` + angle/offset clustering. Better for
  thin walls in scanned/hand-drawn plans.
- **`hybrid`** — `cv` geometry + Gemini Vision judgement (needs
  `GOOGLE_API_KEY` env var). The LLM relabels rectangles
  (wall / column / equipment / door); CV stays in charge of geometry.
  Drop the `google-genai` dependency to remove this mode.

Both importers emit the *same* `CadImportResult`-shaped object, so the
downstream pipeline doesn't care which produced the obstacles.

### 2.5 `xyz_modules.export_cad` — file writers

```python
from xyz_modules.export_cad import (
    write_dxf, write_dwg, write_stl, write_step, load_robot,
)
cfg = json.load(open("trial_config.json"))     # see §3 for shape
robot = load_robot(cfg["robot_id"])            # midpoint pricing from robots.json
write_dxf (cfg, robot, "scene.dxf")            # 2D top-down, 9 layers
write_stl (cfg, robot, "scene.stl")            # 3D mesh
write_step(cfg, robot, "scene.step")           # 3D BREP    (needs `cadquery`)
write_dwg (cfg, robot, "scene.dwg")            # 2D binary  (needs `dxf2dwg` on PATH)
```

### 2.6 `xyz_modules.bom` — BOM CSV / Markdown

```python
from xyz_modules.bom import build_bom, write_csv, write_markdown
rep = build_bom(cfg, n_arms=3)                 # scales per-arm items
write_csv(rep, "bom.csv")
write_markdown(rep, cfg, Path("trial.json"), "bom.md")
print(rep.total_price_low(), rep.total_price_high())
```

### 2.7 `xyz_modules.cad_export` — bytes renderer + cost-breakdown adapter

```python
from xyz_modules.cad_export import render, build_cost_breakdown

# Render any format to bytes (no tempfile bookkeeping in your code):
data, content_type, filename = render(proposal_dict, fmt="dwg")
# data: bytes  ->  stream as Response(content=data, media_type=content_type)

# Build a CostBreakdown-shaped dict from any layout-like data structure:
cb = build_cost_breakdown(
    components=[c.model_dump() for c in placed],   # list[PlacedComponent dict]
    robot_model_ids=["KUKA_KR_30_R2100", "KUKA_KR_30_R2100"],
    primary_robot_id="KUKA_KR_30_R2100",
)
# cb["grand_total_usd"], cb["payback_months"], cb["line_items"][...]
```

`build_cost_breakdown()` is the bridge that made the original system's
in-canvas BOM dialog show the same numbers as its export bundle.

---

## 3. Data contracts

### 3.1 Trial-config dict (input to `export_cad` + `bom`)

The compact shape these two modules consume. Coordinates in **metres** unless
the suffix says `_mm`. Example:

```json
{
  "robot_id": "KUKA_KR_30_R2100",
  "robot_position_xy": [1.0014, 0.0],
  "robot_yaw_deg": 180.0,
  "pedestal_height_m": 0.492,
  "conveyor_end_xy_m": [0.0, 0.0],
  "conveyor_height_m": 0.3,
  "conveyor_collision": {
    "center": [-1.35, 0.0, 0.15],
    "dimensions": [3.0, 0.6, 0.3]
  },
  "pallet_position": [0.5014, -0.9, 0.144],
  "pallet_yaw_deg": 0.0,
  "pallet_size_mm": [1200, 800, 144],
  "box_size_mm": [300.0, 400.0, 225.0],
  "box_weight_kg": 11.5,
  "stack_layers": 6,
  "stack_rows": 4,
  "stack_columns": 2,
  "gripper": {"collision_size_m": [0.3, 0.18, 0.105]}
}
```

A working sample lives at
[`examples/sample_trial_config.json`](examples/sample_trial_config.json).

### 3.2 `LayoutProposal` / `PlacedComponent` (input to scoring + cost-breakdown adapter)

Pydantic v2 models in [`xyz_modules/schemas/layout.py`](xyz_modules/schemas/layout.py).
The shape that matters for the modules in this bundle:

```python
class PlacedComponent(BaseModel):
    id: str
    type: Literal["robot", "conveyor", "pallet", "fence", "operator_zone"]
    x_mm: float
    y_mm: float
    yaw_deg: float
    dims: dict[str, Any]   # see below
```

Per-type `dims` keys the modules read:

| type             | required keys                                       |
|------------------|-----------------------------------------------------|
| `robot`          | `base_radius_mm`, `reach_mm`, `effective_reach_mm`, `footprint_l_mm`, `footprint_w_mm` |
| `conveyor`       | `length_mm`, `width_mm`, `role` ("infeed"/"outfeed")|
| `pallet`         | `length_mm`, `width_mm`, `standard` ("EUR"/"GMA"/...)|
| `fence`          | `polyline: [[x,y]...]`, `height_mm`, `safety_margin_mm`, optional `has_light_curtain` |
| `operator_zone`  | `width_mm`, `depth_mm`                              |

For the BOM/CAD path the pallet `dims` should additionally include
`box_size_mm`, `box_weight_kg`, `stack_layers`, `stack_rows`,
`stack_columns` so per-layer pricing and stack geometry work.

### 3.3 `WorkcellSpec` + `RobotSpec`

`WorkcellSpec` is the canonical extracted spec — see
[`xyz_modules/schemas/workcell.py`](xyz_modules/schemas/workcell.py).
`RobotSpec` mirrors the rows in
[`xyz_modules/data/robots.json`](xyz_modules/data/robots.json); 23 real
palletizing arms from ABB, FANUC, KUKA, Yaskawa, Kawasaki, with prices,
reach, payload, cph, footprint, weight, and `ideal_use_case` tag.

### 3.4 `CostBreakdown` (output of `build_cost_breakdown`)

```python
{
    "robots_usd": 250000.0,
    "eoat_usd": 17000.0,
    "conveyors_usd": 8000.0,
    "fence_usd": 22000.0,
    "cell_controller_usd": 24000.0,
    "bare_total_usd": 248605.0,
    "integration_multiplier": 1.5,        # effective ratio, not flat 1.6
    "integration_usd": 112500.0,
    "grand_total_usd": 361105.0,
    "annual_labor_savings_usd": 150000.0,
    "payback_months": 28.9,
    "line_items": [                       # 15 detailed entries
        {"label": "Robot · Yaskawa MPL80II — ...", "qty": 3,
         "unit_usd": 50000.0, "subtotal_usd": 150000.0},
        ...
    ],
}
```

---

## 4. DWG install (only if you need DWG output)

DWG is Autodesk-proprietary so there is no pip-installable native Python
writer. `write_dwg()` writes the DXF via `ezdxf`, then subprocesses
`dxf2dwg` (LibreDWG) or `ODAFileConverter`. The lookup tries:

1. Anything named `dxf2dwg` on `$PATH`
2. `ODAFileConverter` / `OdaFileConverter` on `$PATH`
3. `~/.local/bin/dxf2dwg`, `/opt/homebrew/bin/dxf2dwg`, `/usr/local/bin/dxf2dwg`
4. macOS app bundle `/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter`

If none is found, `write_dwg()` raises `RuntimeError` with a helpful hint —
the other 5 formats keep working.

### macOS — LibreDWG from source (no Homebrew formula)

```bash
brew install autoconf automake libtool pkg-config
curl -L https://ftp.gnu.org/gnu/libredwg/libredwg-0.13.3.tar.xz | tar xJ -C /tmp
cd /tmp/libredwg-0.13.3
./configure --prefix=$HOME/.local --disable-bindings --disable-python
make -j8 && make install prefix=$HOME/.local
# macOS only — patch the dylib path baked into the binary at link time:
install_name_tool -change /usr/local/lib/libredwg.0.dylib \
  $HOME/.local/lib/libredwg.0.dylib $HOME/.local/bin/dxf2dwg
install_name_tool -id $HOME/.local/lib/libredwg.0.dylib \
  $HOME/.local/lib/libredwg.0.dylib
```

### Debian / Ubuntu

```bash
sudo apt install libredwg-tools
```

### Windows / "I refuse to build"

Grab [ODA File Converter](https://www.opendesign.com/guestfiles/oda_file_converter)
(free, registration required), drop the binary onto `PATH`.

---

## 5. Integration patterns

### 5.1 Drop-in adapter for an existing layout type

If your project already has `MyLayout` / `MyComponent` classes, write one
adapter and reuse every module:

```python
def my_layout_to_proposal_dict(layout: MyLayout) -> dict:
    """Translate to the LayoutProposal-shaped dict that cad_export + scoring
    accept. Match field names exactly; types are documented in §3.2."""
    return {
        "proposal_id": layout.id,
        "template": layout.template_name,
        "robot_model_id": layout.robots[0].model,
        "robot_model_ids": [r.model for r in layout.robots],
        "task_assignment": {},
        "components": [
            {"id": c.id, "type": c.kind, "x_mm": c.x, "y_mm": c.y,
             "yaw_deg": c.yaw, "dims": c.dims}
            for c in layout.components
        ],
        "cell_bounds_mm": layout.cell_size,
        "estimated_cycle_time_s": layout.cycle_time,
        "estimated_uph": layout.uph,
        "rationale": "",
        "assumptions": [],
        "estimated_cost_usd": 0.0,
        "cost_breakdown": None,
    }

# Now both /api/export-style flows work:
data, ct, fname = render(my_layout_to_proposal_dict(layout), "dwg")
cb = build_cost_breakdown(
    components=[c.__dict__ for c in layout.components],
    robot_model_ids=[r.model for r in layout.robots],
    primary_robot_id=layout.robots[0].model,
)
```

### 5.2 Replacing the robot catalogue

`xyz_modules/data/robots.json` ships with 23 real palletizers. To swap in
your own:

1. Replace the JSON in place — keep the schema (the keys `model`,
   `manufacturer`, `payload_kg`, `reach_mm`, `cycles_per_hour_std`,
   `price_usd_low`, `price_usd_high`, `weight_kg`, `axes` are mandatory).
2. Or point `xyz_modules.bom.ROBOT_CATALOG` (module-level path) at a
   different file at import time.

### 5.3 Overriding rule-of-thumb prices

Every unit cost in `bom.py` lives in `PRICE_OVERRIDES: dict[str, tuple[float, float]]`
at module scope. Mutate it (with low, high USD tuples) at import time to
match your local vendor quotes; the rules then propagate to every BOM and to
`build_cost_breakdown()` automatically.

```python
import xyz_modules.bom as bm
bm.PRICE_OVERRIDES["conveyor_per_m"] = (2200.0, 3400.0)   # your supplier
bm.PRICE_OVERRIDES["safety_scanner"] = (4200.0, 7800.0)
```

### 5.4 Non-Pydantic projects

Every module that takes a Pydantic schema also accepts the `model_dump()`'d
dict — the schemas are convenient, not load-bearing. If you don't want
Pydantic, build the dicts directly to the shape documented in §3.

---

## 6. Known caveats

- **`build_bom()` assumes one cell, one pallet station.** Multi-pallet
  topologies (dual_pallet, dual_arm_dual_pallet) work but report only the
  *primary* pallet's box stack. Boxes are tagged "Product (per pallet)" and
  excluded from capex — they're consumables, shown for cycle/load planning.
- **DXF→DWG warnings are normal.** LibreDWG emits warnings for unknown
  header variables (`HEADER.TEXTSTYLE OpenSans dxf:7` etc.) — these are
  ezdxf custom dictionary entries the DWG R2000 format doesn't have a slot
  for. The DWG opens fine in AutoCAD / Bricscad.
- **Scoring `aggregate` is `0.0` whenever any hard violation exists.** This
  is intentional. If your UI wants a "feasibility-soft" gradient (e.g. for
  a SA cooling schedule), look at the per-sub-score values directly and
  build your own soft penalty from `violations[i].margin_mm`.
- **Gemini Vision hybrid mode requires `GOOGLE_API_KEY`.** The `hybrid`
  parser silently falls back to `cv` if the key isn't set. Log inspection:
  `logger.warning("google-genai not installed; hybrid mode unavailable")`.
- **`cadquery` install is heavy** (~200 MB wheel including OCP geometry
  kernel). Drop it from `requirements.txt` if you don't need STEP.
- **LibreDWG is GPLv3.** If you ship `dxf2dwg` as part of your product,
  you take on the GPLv3 distribution obligations. The Python code in
  `xyz_modules` only *calls* `dxf2dwg` via subprocess, which is generally
  considered "mere aggregation" — but check with legal if it matters.

---

## 7. File map

```
xyz_modules_portable/
├── README_HANDOFF.md           ← this file
├── pyproject.toml              ← `pip install -e .` makes the package available
├── requirements.txt            ← if you don't use pip install -e
├── xyz_modules/                ← the importable package
│   ├── __init__.py
│   ├── kinematics.py           ← pure helpers (no schemas)
│   ├── scoring.py              ← score_layout + 5 sub-scores
│   ├── cad_import.py           ← DXF parser (input)
│   ├── image_floor_plan.py     ← PNG/JPG parser (input; OpenCV + optional Gemini)
│   ├── export_cad.py           ← DXF/DWG/STL/STEP writers (output)
│   ├── bom.py                  ← BOM CSV/Markdown
│   ├── cad_export.py           ← LayoutProposal↔trial-config adapter + bytes renderer
│   ├── schemas/                ← Pydantic data contracts
│   │   ├── __init__.py
│   │   ├── workcell.py
│   │   ├── robot.py
│   │   ├── layout.py
│   │   └── obstacle.py
│   └── data/
│       └── robots.json         ← 23 real palletizer specs
└── examples/
    ├── sample_trial_config.json
    └── quickstart.py           ← run this first
```

---

## 8. Smoke test (must pass before merging)

```bash
python examples/quickstart.py
```

Expected: every section prints output. The DXF / STL / BOM CSV / BOM MD
lines always produce bytes. STEP needs `cadquery` installed. DWG needs
`dxf2dwg` or `ODAFileConverter` on PATH — both of those will print a
"skipped — install hint" line instead of failing if missing, which is the
correct graceful-degradation behaviour.

## 9. Versioning

`__version__ = "0.1.0"` in `xyz_modules/__init__.py`. The original project's
upstream commit reference is recorded inside `xyz_modules/scoring.py`
(top docstring) so you can diff against the source repo if you ever need to
pick up bug fixes — see
[ChiShengChen/robotics-workcell-optimizer](https://github.com/ChiShengChen/robotics-workcell-optimizer).
