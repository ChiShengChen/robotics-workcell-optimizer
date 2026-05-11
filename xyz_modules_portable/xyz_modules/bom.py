"""Generate a Bill of Materials from a layout_config_trial_*.json.

Outputs two artefacts next to the source config:
  cad_flow/{stem}_bom.csv   — flat row-per-line BOM, opens in Excel/Numbers
  cad_flow/{stem}_bom.md    — human-readable markdown table + totals

Each line item carries: part_no, category, description, qty, unit_mass_kg,
unit_price_usd_low/high, ext_mass_kg, ext_price_low/high, source.

Pricing rules:
  - Robot prices come from backend/app/data/robots.json (low/high range,
    bare arm only — integration cost is added as a separate line at 75% of
    the bare-arm midpoint, per the catalog meta note).
  - Conveyor / pallet / fence / safety items use rule-of-thumb unit costs
    that are conservative defaults; override via PRICE_OVERRIDES.
  - Every assumption is appended to the markdown report so a reviewer can
    challenge the number rather than trust a black box.

Usage:
  python cad_flow/bom.py
  python cad_flow/bom.py path/to/layout_config_trial_X.json
"""

from __future__ import annotations

import csv
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent
ROBOT_CATALOG = PKG_ROOT / "data" / "robots.json"
# CAD_FLOW alias kept for backward compat with anything that walks the path.
CAD_FLOW = PKG_ROOT

# Rule-of-thumb unit costs (USD). Conservative; override per project.
PRICE_OVERRIDES: dict[str, tuple[float, float]] = {
    "conveyor_per_m": (1500.0, 2800.0),       # belt or roller, integrated
    "pallet_eur": (25.0, 45.0),                # EUR pallet
    "pallet_gma": (20.0, 35.0),                # GMA / 48x40
    "fence_panel_per_m2": (180.0, 320.0),      # mesh perimeter guard
    "fence_post": (75.0, 140.0),               # post per ~2.5 m of fence
    "operator_floor_marking_per_m2": (15.0, 30.0),
    "safety_scanner": (3500.0, 6000.0),        # SICK nanoScan or similar
    "interlocked_gate": (1200.0, 2200.0),
    "gripper_vacuum_basic": (3500.0, 8000.0),  # 1-zone vacuum EOAT
    "controller": (12000.0, 22000.0),          # robot controller + cabinet
    "safety_plc": (4500.0, 8000.0),            # Pilz / SICK Flexi Soft
    "estop_pendant_set": (650.0, 1100.0),
}

# Mass rules-of-thumb (kg)
MASS_RULES = {
    "conveyor_per_m": 35.0,
    "pallet_eur_kg": 25.0,
    "pallet_gma_kg": 22.0,
    "fence_panel_per_m2": 8.5,
    "fence_post_kg": 12.0,
    "controller_kg": 180.0,
    "safety_plc_kg": 4.0,
    "estop_pendant_set_kg": 3.0,
    "gripper_vacuum_basic_kg": 18.0,
    "safety_scanner_kg": 2.5,
    "interlocked_gate_kg": 35.0,
}

INTEGRATION_FACTOR = 0.75   # bare-arm cost × this = robot integration line


@dataclass
class BomLine:
    part_no: str
    category: str
    description: str
    qty: float
    unit_mass_kg: float | None
    unit_price_low_usd: float | None
    unit_price_high_usd: float | None
    source: str
    notes: str = ""

    def ext_mass_kg(self) -> float | None:
        return None if self.unit_mass_kg is None else self.unit_mass_kg * self.qty

    def ext_price_low(self) -> float | None:
        return None if self.unit_price_low_usd is None else self.unit_price_low_usd * self.qty

    def ext_price_high(self) -> float | None:
        return None if self.unit_price_high_usd is None else self.unit_price_high_usd * self.qty


@dataclass
class BomReport:
    lines: list[BomLine] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)

    def total_mass_kg(self) -> float:
        return sum((l.ext_mass_kg() or 0.0) for l in self.lines)

    def total_price_low(self) -> float:
        return sum((l.ext_price_low() or 0.0) for l in self.lines)

    def total_price_high(self) -> float:
        return sum((l.ext_price_high() or 0.0) for l in self.lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_robot(model_id: str) -> dict | None:
    if not ROBOT_CATALOG.exists():
        return None
    data = json.loads(ROBOT_CATALOG.read_text())
    target = model_id.replace("_", " ").lower()
    for r in data.get("robots", []):
        key = f"{r.get('manufacturer','')} {r.get('model','')}".lower()
        if target in key or key in target:
            return r
    return None


def m(v: float) -> float:
    """Convert metres → mm for length math; return mm so all geometry stays in mm."""
    return v * 1000.0


# ---------------------------------------------------------------------------
# BOM construction
# ---------------------------------------------------------------------------


def build_bom(cfg: dict, n_arms: int = 1) -> BomReport:
    """Build a Bill of Materials for the cell described by `cfg`.

    `n_arms` scales the items that are inherently per-robot (arm + integration +
    pedestal + EOAT + controller cabinet). Per-cell items (fence, pallet, gates,
    safety PLC, e-stop, floor marking) stay at qty 1 — adding a second arm in
    the same cell doesn't double the perimeter fence or the safety PLC.
    """
    rep = BomReport()
    n_arms = max(1, int(n_arms))

    # ---- Robot ----
    robot_id = cfg.get("robot_id", "UNKNOWN_ROBOT")
    robot = load_robot(robot_id)
    if robot:
        low = float(robot["price_usd_low"])
        high = float(robot["price_usd_high"])
        mass = float(robot["weight_kg"])
        rep.lines.append(BomLine(
            part_no=f"ROBOT-{robot.get('manufacturer','')}-{robot.get('model','').replace(' ','_')}",
            category="Robot",
            description=f"{robot.get('manufacturer')} {robot.get('model')} — {robot.get('axes')}-axis, {robot.get('payload_kg')} kg payload, {robot.get('reach_mm')} mm reach",
            qty=n_arms,
            unit_mass_kg=mass,
            unit_price_low_usd=low,
            unit_price_high_usd=high,
            source="robots.json",
            notes="Bare arm only.",
        ))
        # Integration line (controller + safety + EOAT excluded — itemised separately)
        mid = (low + high) / 2.0
        rep.lines.append(BomLine(
            part_no=f"INT-{robot.get('model','').replace(' ','_')}",
            category="Integration",
            description=f"Robot integration labour & engineering ({int(INTEGRATION_FACTOR*100)}% of bare-arm midpoint)",
            qty=n_arms,
            unit_mass_kg=None,
            unit_price_low_usd=mid * INTEGRATION_FACTOR * 0.8,
            unit_price_high_usd=mid * INTEGRATION_FACTOR * 1.2,
            source="rule_of_thumb",
            notes="Excludes EOAT, controller, safety equipment.",
        ))
    else:
        rep.assumptions.append(
            f"Robot '{robot_id}' not in catalog — used placeholder $50k–80k, 800 kg."
        )
        rep.lines.append(BomLine(
            part_no=f"ROBOT-{robot_id}",
            category="Robot",
            description=f"{robot_id} (specs unknown)",
            qty=n_arms,
            unit_mass_kg=800.0,
            unit_price_low_usd=50000.0,
            unit_price_high_usd=80000.0,
            source="placeholder",
        ))

    # ---- Pedestal (one per arm) ----
    ped_h_mm = m(float(cfg.get("pedestal_height_m", 0.0)))
    if ped_h_mm > 1.0:
        rep.lines.append(BomLine(
            part_no="PED-STEEL-01",
            category="Structural",
            description=f"Steel pedestal, {int(ped_h_mm)} mm tall, ⌀ matching robot footprint",
            qty=n_arms,
            unit_mass_kg=max(40.0, 0.12 * ped_h_mm),    # ~120 g per mm of height
            unit_price_low_usd=600.0,
            unit_price_high_usd=1400.0,
            source="rule_of_thumb",
        ))

    # ---- Conveyor ----
    conv = cfg["conveyor_collision"]
    conv_len_m = float(conv["dimensions"][0])
    conv_w_mm = m(float(conv["dimensions"][1]))
    p_low, p_high = PRICE_OVERRIDES["conveyor_per_m"]
    rep.lines.append(BomLine(
        part_no="CONV-01",
        category="Conveyor",
        description=f"Infeed conveyor, {conv_len_m:.2f} m long × {int(conv_w_mm)} mm wide, integrated drive",
        qty=conv_len_m,
        unit_mass_kg=MASS_RULES["conveyor_per_m"],
        unit_price_low_usd=p_low,
        unit_price_high_usd=p_high,
        source="rule_of_thumb",
        notes="Pricing per linear metre, fully integrated.",
    ))

    # ---- Pallet(s) ----
    standard = (cfg.get("pallet_standard") or "EUR").upper()
    if "GMA" in standard:
        p_low, p_high = PRICE_OVERRIDES["pallet_gma"]
        pallet_mass = MASS_RULES["pallet_gma_kg"]
        std_label = "GMA / ISO1"
    else:
        p_low, p_high = PRICE_OVERRIDES["pallet_eur"]
        pallet_mass = MASS_RULES["pallet_eur_kg"]
        std_label = "EUR EPAL"
    pallet_qty = 1  # single pallet station per the trial config
    rep.lines.append(BomLine(
        part_no=f"PAL-{std_label.split()[0]}",
        category="Pallet",
        description=f"{std_label} pallet, {cfg['pallet_size_mm'][0]:.0f} × {cfg['pallet_size_mm'][1]:.0f} × {cfg['pallet_size_mm'][2]:.0f} mm",
        qty=pallet_qty,
        unit_mass_kg=pallet_mass,
        unit_price_low_usd=p_low,
        unit_price_high_usd=p_high,
        source="rule_of_thumb",
    ))

    # ---- Boxes (consumed product, NOT capex — listed for traceability) ----
    rows = int(cfg.get("stack_rows", 0))
    cols = int(cfg.get("stack_columns", 0))
    layers = int(cfg.get("stack_layers", 0))
    n_boxes = rows * cols * layers
    if n_boxes > 0:
        bx_l, bx_w, bx_h = cfg["box_size_mm"]
        rep.lines.append(BomLine(
            part_no="BOX-CASE-01",
            category="Product (per pallet)",
            description=f"Case {bx_l:.0f}×{bx_w:.0f}×{bx_h:.0f} mm @ {cfg.get('box_weight_kg',0):.1f} kg — {layers} layers × {rows}×{cols} interlock",
            qty=n_boxes,
            unit_mass_kg=float(cfg.get("box_weight_kg", 0.0)),
            unit_price_low_usd=None,
            unit_price_high_usd=None,
            source="trial_config",
            notes="Consumable, shown for cycle/load planning only.",
        ))

    # ---- EOAT (gripper, one per arm) ----
    grip = cfg.get("gripper", {})
    if grip:
        p_low, p_high = PRICE_OVERRIDES["gripper_vacuum_basic"]
        rep.lines.append(BomLine(
            part_no="EOAT-VAC-01",
            category="EOAT",
            description=f"Vacuum gripper, collision envelope {grip.get('collision_size_m','?')}",
            qty=n_arms,
            unit_mass_kg=MASS_RULES["gripper_vacuum_basic_kg"],
            unit_price_low_usd=p_low,
            unit_price_high_usd=p_high,
            source="rule_of_thumb",
            notes="Single-zone vacuum EOAT for case picking.",
        ))

    # ---- Fence (sized from same _safety_polygons logic as export_cad.py) ----
    fence_perimeter_m, fence_height_m = _fence_dimensions(cfg, robot)
    fence_area_m2 = fence_perimeter_m * fence_height_m
    p_low, p_high = PRICE_OVERRIDES["fence_panel_per_m2"]
    rep.lines.append(BomLine(
        part_no="FENCE-MESH-01",
        category="Safety",
        description=f"Perimeter mesh guard, {fence_perimeter_m:.1f} m × {fence_height_m:.1f} m tall (ISO 13857)",
        qty=fence_area_m2,
        unit_mass_kg=MASS_RULES["fence_panel_per_m2"],
        unit_price_low_usd=p_low,
        unit_price_high_usd=p_high,
        source="rule_of_thumb",
        notes="Quantity is m² of panel.",
    ))
    n_posts = max(4, math.ceil(fence_perimeter_m / 2.5))
    p_low, p_high = PRICE_OVERRIDES["fence_post"]
    rep.lines.append(BomLine(
        part_no="FENCE-POST",
        category="Safety",
        description=f"Fence posts, ~2.5 m spacing along {fence_perimeter_m:.1f} m perimeter",
        qty=n_posts,
        unit_mass_kg=MASS_RULES["fence_post_kg"],
        unit_price_low_usd=p_low,
        unit_price_high_usd=p_high,
        source="rule_of_thumb",
    ))
    p_low, p_high = PRICE_OVERRIDES["interlocked_gate"]
    rep.lines.append(BomLine(
        part_no="GATE-INTLK",
        category="Safety",
        description="Interlocked access gate (pallet exit / operator zone)",
        qty=1,
        unit_mass_kg=MASS_RULES["interlocked_gate_kg"],
        unit_price_low_usd=p_low,
        unit_price_high_usd=p_high,
        source="rule_of_thumb",
    ))
    p_low, p_high = PRICE_OVERRIDES["safety_scanner"]
    rep.lines.append(BomLine(
        part_no="SCAN-SAFETY",
        category="Safety",
        description="Type-3 safety laser scanner (operator zone monitoring)",
        qty=1,
        unit_mass_kg=MASS_RULES["safety_scanner_kg"],
        unit_price_low_usd=p_low,
        unit_price_high_usd=p_high,
        source="rule_of_thumb",
        notes="Use ISO 13855 separation: S = K·T + C, K = 2000 mm/s.",
    ))

    # ---- Controls (controller per arm; PLC + e-stop shared) ----
    p_low, p_high = PRICE_OVERRIDES["controller"]
    rep.lines.append(BomLine(
        part_no="CTRL-ROBOT",
        category="Controls",
        description="Robot controller + power cabinet",
        qty=n_arms,
        unit_mass_kg=MASS_RULES["controller_kg"],
        unit_price_low_usd=p_low,
        unit_price_high_usd=p_high,
        source="rule_of_thumb",
    ))
    p_low, p_high = PRICE_OVERRIDES["safety_plc"]
    rep.lines.append(BomLine(
        part_no="PLC-SAFETY",
        category="Controls",
        description="Safety PLC (Cat.3 PLd per ISO 13849)",
        qty=1,
        unit_mass_kg=MASS_RULES["safety_plc_kg"],
        unit_price_low_usd=p_low,
        unit_price_high_usd=p_high,
        source="rule_of_thumb",
    ))
    p_low, p_high = PRICE_OVERRIDES["estop_pendant_set"]
    rep.lines.append(BomLine(
        part_no="ESTOP-SET",
        category="Controls",
        description="E-stop pendant set + cell I/O (4 stations)",
        qty=1,
        unit_mass_kg=MASS_RULES["estop_pendant_set_kg"],
        unit_price_low_usd=p_low,
        unit_price_high_usd=p_high,
        source="rule_of_thumb",
    ))

    # ---- Operator zone marking ----
    op_area_m2 = _operator_zone_area_m2(cfg, robot)
    if op_area_m2 > 0:
        p_low, p_high = PRICE_OVERRIDES["operator_floor_marking_per_m2"]
        rep.lines.append(BomLine(
            part_no="MARK-FLOOR",
            category="Safety",
            description=f"Operator floor marking + epoxy stripes, {op_area_m2:.1f} m²",
            qty=op_area_m2,
            unit_mass_kg=None,
            unit_price_low_usd=p_low,
            unit_price_high_usd=p_high,
            source="rule_of_thumb",
        ))

    rep.assumptions.append(
        f"Fence sized from reach + conveyor + pallet AABB inflated by {int(_FENCE_OFFSET_MM)} mm; height {int(_FENCE_HEIGHT_MM)} mm (ISO 13857 typical)."
    )
    rep.assumptions.append(
        f"Robot integration estimated at {int(INTEGRATION_FACTOR*100)}% of bare-arm midpoint ± 20%."
    )
    rep.assumptions.append(
        "Boxes listed per pallet for cycle/payload planning; not part of capex totals (no price)."
    )
    return rep


# Mirror constants in export_cad.py so we can size the fence without importing it.
_FENCE_OFFSET_MM = 500.0
_FENCE_HEIGHT_MM = 2100.0
_OPERATOR_ZONE_DEPTH_MM = 1200.0


def _fence_dimensions(cfg: dict, robot: dict | None) -> tuple[float, float]:
    rx = m(cfg["robot_position_xy"][0])
    ry = m(cfg["robot_position_xy"][1])
    reach = float(robot["reach_mm"]) if robot else 2100.0
    conv = cfg["conveyor_collision"]
    cv_cx, cv_cy = m(conv["center"][0]), m(conv["center"][1])
    cv_w, cv_h = m(conv["dimensions"][0]), m(conv["dimensions"][1])
    px, py = m(cfg["pallet_position"][0]), m(cfg["pallet_position"][1])
    pl_l, pl_w = cfg["pallet_size_mm"][0], cfg["pallet_size_mm"][1]
    xs = [rx - reach, rx + reach, cv_cx - cv_w / 2, cv_cx + cv_w / 2, px - pl_l / 2, px + pl_l / 2]
    ys = [ry - reach, ry + reach, cv_cy - cv_h / 2, cv_cy + cv_h / 2, py - pl_w / 2, py + pl_w / 2]
    w = (max(xs) - min(xs)) + 2 * _FENCE_OFFSET_MM
    h = (max(ys) - min(ys)) + 2 * _FENCE_OFFSET_MM
    perimeter_m = 2 * (w + h) / 1000.0
    return perimeter_m, _FENCE_HEIGHT_MM / 1000.0


def _operator_zone_area_m2(cfg: dict, robot: dict | None) -> float:
    perimeter_m, _ = _fence_dimensions(cfg, robot)
    # Operator zone runs along one short side ≈ (perimeter / 4) wide × depth.
    side_m = perimeter_m / 4.0
    return side_m * (_OPERATOR_ZONE_DEPTH_MM / 1000.0)


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------


def write_csv(rep: BomReport, out_path: Path) -> None:
    with out_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "part_no", "category", "description", "qty",
            "unit_mass_kg", "unit_price_low_usd", "unit_price_high_usd",
            "ext_mass_kg", "ext_price_low_usd", "ext_price_high_usd",
            "source", "notes",
        ])
        for l in rep.lines:
            w.writerow([
                l.part_no, l.category, l.description, _fmt(l.qty),
                _fmt(l.unit_mass_kg), _fmt(l.unit_price_low_usd), _fmt(l.unit_price_high_usd),
                _fmt(l.ext_mass_kg()), _fmt(l.ext_price_low()), _fmt(l.ext_price_high()),
                l.source, l.notes,
            ])
        w.writerow([])
        w.writerow(["TOTAL", "", "", "",
                    "", "", "",
                    _fmt(rep.total_mass_kg()), _fmt(rep.total_price_low()), _fmt(rep.total_price_high()),
                    "", ""])


def write_markdown(rep: BomReport, cfg: dict, cfg_path: Path, out_path: Path) -> None:
    rows = [
        "| # | Part No | Category | Description | Qty | Unit kg | Unit Price USD | Ext kg | Ext Price USD |",
        "|---|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for i, l in enumerate(rep.lines, 1):
        unit_price = _price_range(l.unit_price_low_usd, l.unit_price_high_usd)
        ext_price = _price_range(l.ext_price_low(), l.ext_price_high())
        rows.append(
            f"| {i} | `{l.part_no}` | {l.category} | {l.description} | "
            f"{_fmt(l.qty)} | {_fmt(l.unit_mass_kg)} | {unit_price} | "
            f"{_fmt(l.ext_mass_kg())} | {ext_price} |"
        )

    body = [
        f"# Bill of Materials — {cfg_path.name}",
        "",
        f"- Source config: `{_safe_relative(cfg_path)}`",
        f"- Robot: **{cfg.get('robot_id','UNKNOWN')}**",
        f"- Pallet: {cfg.get('pallet_size_mm')} mm, stack {cfg.get('stack_layers')}L × {cfg.get('stack_rows')}×{cfg.get('stack_columns')}",
        "",
        "## Line items",
        "",
        *rows,
        "",
        "## Totals",
        "",
        f"- **Total mass (cell, no product)**: {rep.total_mass_kg():,.0f} kg",
        f"- **Total capex range**: ${rep.total_price_low():,.0f} – ${rep.total_price_high():,.0f} USD",
        f"- Midpoint estimate: **${(rep.total_price_low()+rep.total_price_high())/2:,.0f} USD**",
        "",
        "## Assumptions",
        "",
        *[f"- {a}" for a in rep.assumptions],
    ]
    out_path.write_text("\n".join(body) + "\n")


def _safe_relative(p: Path) -> str:
    """Path relative to repo root if possible, otherwise just the basename.
    The backend export pipeline calls write_markdown with a tempfile path
    outside the repo — relative_to() would raise."""
    try:
        return str(p.relative_to(CAD_FLOW.parent))
    except ValueError:
        return p.name


def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        if abs(v) >= 1000:
            return f"{v:,.0f}"
        if v == int(v):
            return f"{int(v)}"
        return f"{v:.2f}"
    return str(v)


def _price_range(low, high) -> str:
    if low is None and high is None:
        return ""
    if low == high:
        return f"${_fmt(low)}"
    return f"${_fmt(low)} – ${_fmt(high)}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    if len(argv) >= 2:
        cfg_path = Path(argv[1]).resolve()
    else:
        candidates = sorted(CAD_FLOW.glob("layout_config_trial_*.json"))
        if not candidates:
            print(f"no layout_config_trial_*.json in {CAD_FLOW}", file=sys.stderr)
            return 1
        cfg_path = candidates[0]

    cfg = json.loads(cfg_path.read_text())
    rep = build_bom(cfg)

    stem = cfg_path.stem
    csv_out = CAD_FLOW / f"{stem}_bom.csv"
    md_out = CAD_FLOW / f"{stem}_bom.md"
    write_csv(rep, csv_out)
    write_markdown(rep, cfg, cfg_path, md_out)
    print(f"wrote {csv_out.relative_to(CAD_FLOW.parent)}  ({len(rep.lines)} lines)")
    print(f"wrote {md_out.relative_to(CAD_FLOW.parent)}")
    print(f"capex range: ${rep.total_price_low():,.0f} – ${rep.total_price_high():,.0f} USD")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
