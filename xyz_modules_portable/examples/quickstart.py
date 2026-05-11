"""Five-minute tour of every module in the xyz_modules package.

Run from this folder's parent:
    python xyz_modules_portable/examples/quickstart.py

Each section is independent — copy the bits you need.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

# Make `xyz_modules` importable when running this file directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xyz_modules.bom import build_bom, write_csv, write_markdown
from xyz_modules.cad_export import build_cost_breakdown, proposal_to_trial_config, render
from xyz_modules.cad_import import parse_dxf
from xyz_modules.export_cad import load_robot, write_dwg, write_dxf, write_step, write_stl
from xyz_modules.image_floor_plan import parse_image  # noqa: F401  (importable, not exercised here)
from xyz_modules.kinematics import (
    estimate_cycle_time_s,
    estimate_uph,
    iso13855_safety_distance_mm,
    trapezoidal_time_s,
)

SAMPLE_CONFIG = Path(__file__).parent / "sample_trial_config.json"


def section(title: str) -> None:
    print(f"\n=== {title} ===")


# ---------------------------------------------------------------------------
# 1. Kinematics — pure functions, no schemas
# ---------------------------------------------------------------------------
section("Kinematics")
t_trapezoid = trapezoidal_time_s(distance_mm=2800.0, v_max_mm_s=2500.0, a_mm_s2=8000.0)
s_iso = iso13855_safety_distance_mm(has_hard_guard=False)
print(f"  Trapezoid time over 2.8 m: {t_trapezoid:.3f} s")
print(f"  ISO 13855 light-curtain distance: {s_iso:.0f} mm")


# ---------------------------------------------------------------------------
# 2. BOM from a trial-config dict — same path the company-side coding agent
#    will use when wiring this into a quoting flow
# ---------------------------------------------------------------------------
section("BOM")
cfg = json.loads(SAMPLE_CONFIG.read_text())
report = build_bom(cfg, n_arms=1)
print(f"  Lines: {len(report.lines)}  total mass: {report.total_mass_kg():,.0f} kg")
print(f"  Capex range: ${report.total_price_low():,.0f} – ${report.total_price_high():,.0f}")

# Per-arm scaling — same input, n_arms=3
report3 = build_bom(cfg, n_arms=3)
print(f"  3-arm capex range: ${report3.total_price_low():,.0f} – ${report3.total_price_high():,.0f}")


# ---------------------------------------------------------------------------
# 3. CAD export — DXF / STL / STEP / DWG
# ---------------------------------------------------------------------------
section("CAD export")
robot = load_robot(cfg["robot_id"])
with tempfile.TemporaryDirectory() as td:
    td_p = Path(td)
    write_dxf(cfg, robot, td_p / "scene.dxf")
    write_stl(cfg, robot, td_p / "scene.stl")
    try:
        write_step(cfg, robot, td_p / "scene.step")  # needs cadquery
        print(f"  STEP: {(td_p/'scene.step').stat().st_size:,} bytes")
    except Exception as e:
        print(f"  STEP skipped ({e.__class__.__name__})")
    try:
        write_dwg(cfg, robot, td_p / "scene.dwg")  # needs LibreDWG on PATH
        print(f"  DWG : {(td_p/'scene.dwg').stat().st_size:,} bytes")
    except RuntimeError as e:
        print(f"  DWG skipped — {str(e).splitlines()[0]}")
    print(f"  DXF : {(td_p/'scene.dxf').stat().st_size:,} bytes")
    print(f"  STL : {(td_p/'scene.stl').stat().st_size:,} bytes")
    write_csv(report, td_p / "bom.csv")
    write_markdown(report, cfg, SAMPLE_CONFIG, td_p / "bom.md")
    print(f"  BOM CSV: {(td_p/'bom.csv').stat().st_size:,} bytes")
    print(f"  BOM MD : {(td_p/'bom.md').stat().st_size:,} bytes")


# ---------------------------------------------------------------------------
# 4. CostBreakdown from a LayoutProposal-shaped dict
#    Companies typically have their own placement / layout types — this is
#    the adapter pattern: feed `components` + `robot_model_ids` and get back
#    a CostBreakdown-shaped dict ready to render in a UI.
# ---------------------------------------------------------------------------
section("CostBreakdown adapter")
fake_components = [
    {"id": "r1", "type": "robot",    "x_mm": 1000, "y_mm": 0,   "yaw_deg": 0,
     "dims": {"pedestal_height_mm": 492, "reach_mm": 2100}},
    {"id": "c1", "type": "conveyor", "x_mm": -2850, "y_mm": -300, "yaw_deg": 0,
     "dims": {"length_mm": 3000, "width_mm": 600}},
    {"id": "p1", "type": "pallet",   "x_mm": -98, "y_mm": -1300, "yaw_deg": 0,
     "dims": {"length_mm": 1200, "width_mm": 800, "height_mm": 144,
              "stack_layers": 6, "stack_rows": 4, "stack_columns": 2}},
]
cb = build_cost_breakdown(
    components=fake_components,
    robot_model_ids=["KUKA_KR_30_R2100", "KUKA_KR_30_R2100"],
    primary_robot_id="KUKA_KR_30_R2100",
)
print(f"  bare:  ${cb['bare_total_usd']:>9,.0f}")
print(f"  grand: ${cb['grand_total_usd']:>9,.0f}")
print(f"  payback: {cb['payback_months']:.1f} mo")
print(f"  line items: {len(cb['line_items'])}")


# ---------------------------------------------------------------------------
# 5. DXF → obstacles (input)
# ---------------------------------------------------------------------------
section("DXF import")
# parse_dxf takes bytes; here we synthesise a tiny DXF with ezdxf to keep
# the example self-contained.
try:
    import ezdxf
    doc = ezdxf.new()
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (8000, 0), (8000, 6000), (0, 6000)], close=True)
    msp.add_lwpolyline([(2000, 2000), (2500, 2000), (2500, 2500), (2000, 2500)], close=True)
    dxf_path = Path(tempfile.gettempdir()) / "_xyz_quickstart.dxf"
    doc.saveas(dxf_path)
    result = parse_dxf(dxf_path.read_bytes())
    print(f"  Parsed {result.n_entities_imported} entities ({result.n_entities_skipped} skipped)")
    print(f"  Cell envelope suggestion: {result.suggested_cell_envelope_mm} mm")
except ImportError:
    print("  ezdxf not installed — skipped")


print("\nDone.")
