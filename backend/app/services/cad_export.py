"""Adapter that turns a `LayoutProposal` into a trial-config dict the
cad_flow/ exporters can consume, then renders DXF / STL / STEP / BOM bytes.

Why an adapter? cad_flow/export_cad.py and cad_flow/bom.py were written to
work standalone from `layout_config_trial_*.json` files (the offline
robotics flow). The interactive system uses `LayoutProposal` objects with
a richer per-component shape (`PlacedComponent.dims`). This module is the
single bridge between the two — anything that needs export bytes calls
`render(proposal_dict, fmt)` and gets `(bytes, content_type, filename)` back.
"""

from __future__ import annotations

import io
import sys
import tempfile
from pathlib import Path
from typing import Literal

# cad_flow/ lives at the repo root, sibling to backend/. Inject it onto
# sys.path so we can import the export functions directly without copying.
_CAD_FLOW = Path(__file__).resolve().parent.parent.parent.parent / "cad_flow"
if str(_CAD_FLOW) not in sys.path:
    sys.path.insert(0, str(_CAD_FLOW))

from export_cad import (  # noqa: E402  (path injection above)
    write_dxf as _write_dxf,
    write_dwg as _write_dwg,
    write_stl as _write_stl,
    write_step as _write_step,
    load_robot,
)
from bom import (  # noqa: E402
    build_bom,
    write_csv as _bom_csv,
    write_markdown as _bom_md,
)


ExportFormat = Literal["dxf", "dwg", "stl", "step", "bom_csv", "bom_md"]

CONTENT_TYPES: dict[str, str] = {
    "dxf": "application/dxf",
    "dwg": "application/acad",
    "stl": "model/stl",
    "step": "application/step",
    "bom_csv": "text/csv",
    "bom_md": "text/markdown",
}

EXTENSIONS: dict[str, str] = {
    "dxf": "dxf",
    "dwg": "dwg",
    "stl": "stl",
    "step": "step",
    "bom_csv": "csv",
    "bom_md": "md",
}


# ---------------------------------------------------------------------------
# LayoutProposal -> trial-config adapter
# ---------------------------------------------------------------------------


def proposal_to_trial_config(proposal: dict) -> dict:
    """Translate a LayoutProposal dict into the `layout_config_trial_*.json`
    shape that cad_flow/ scripts expect.

    Conventions:
      - cad_flow positions are in METRES, sizes in MM (matches the existing
        trial JSON). LayoutProposal stores everything in MM, so we /1000.
      - For multi-arm proposals only the first robot/conveyor/pallet is
        emitted today. Multi-pallet export is a follow-up.
      - Missing dims fall back to sensible defaults (matches the cad_flow
        DEFAULTS in export_cad.py).
    """
    components = proposal.get("components", [])

    def first(kind: str) -> dict | None:
        for c in components:
            if c.get("type") == kind:
                return c
        return None

    robot_c = first("robot")
    conveyor_c = first("conveyor")
    pallet_c = first("pallet")

    if robot_c is None:
        raise ValueError("Proposal has no robot component — cannot export.")
    if conveyor_c is None:
        raise ValueError("Proposal has no conveyor component — cannot export.")
    if pallet_c is None:
        raise ValueError("Proposal has no pallet component — cannot export.")

    robot_id = proposal.get("robot_model_id") or "UNKNOWN_ROBOT"
    rdims = robot_c.get("dims", {})
    cdims = conveyor_c.get("dims", {})
    pdims = pallet_c.get("dims", {})

    pallet_l = float(pdims.get("length_mm", 1200))
    pallet_w = float(pdims.get("width_mm", 800))
    pallet_h = float(pdims.get("height_mm", 144))

    conv_l_mm = float(cdims.get("length_mm", 3000))
    conv_w_mm = float(cdims.get("width_mm", 600))
    # cad_flow conveyor_collision dims are in METRES (see trial JSON):
    conv_dims_m = (conv_l_mm / 1000.0, conv_w_mm / 1000.0, 0.30)
    # Centre the conveyor on its midpoint (PlacedComponent.x_mm/y_mm is its
    # anchor; LayoutProposal anchors conveyor at the LL corner of its bbox).
    yaw = float(conveyor_c.get("yaw_deg", 0.0))
    is_vertical = abs(((yaw % 180) + 180) % 180 - 90) < 1e-3
    if is_vertical:
        cx_mm = float(conveyor_c["x_mm"]) + conv_w_mm / 2.0
        cy_mm = float(conveyor_c["y_mm"]) + conv_l_mm / 2.0
    else:
        cx_mm = float(conveyor_c["x_mm"]) + conv_l_mm / 2.0
        cy_mm = float(conveyor_c["y_mm"]) + conv_w_mm / 2.0

    cfg: dict = {
        "robot_id": robot_id,
        "robot_position_xy": [
            float(robot_c["x_mm"]) / 1000.0,
            float(robot_c["y_mm"]) / 1000.0,
        ],
        "robot_yaw_deg": float(robot_c.get("yaw_deg", 0.0)),
        "pedestal_height_m": float(rdims.get("pedestal_height_mm", 492)) / 1000.0,
        "conveyor_end_xy_m": [cx_mm / 1000.0, cy_mm / 1000.0],
        "conveyor_height_m": 0.30,
        "conveyor_collision": {
            "center": [cx_mm / 1000.0, cy_mm / 1000.0, 0.15],
            "dimensions": list(conv_dims_m),
        },
        "pallet_position": [
            (float(pallet_c["x_mm"]) + pallet_l / 2.0) / 1000.0,
            (float(pallet_c["y_mm"]) + pallet_w / 2.0) / 1000.0,
            pallet_h / 2000.0,
        ],
        "pallet_yaw_deg": float(pallet_c.get("yaw_deg", 0.0)),
        "pallet_size_mm": [pallet_l, pallet_w, pallet_h],
        "box_size_mm": list(pdims.get("box_size_mm", [300.0, 400.0, 225.0])),
        "box_weight_kg": float(pdims.get("box_weight_kg", 11.5)),
        "stack_layers": int(pdims.get("stack_layers", 6)),
        "stack_rows": int(pdims.get("stack_rows", 4)),
        "stack_columns": int(pdims.get("stack_columns", 2)),
        "gripper": {
            "collision_size_m": list(rdims.get("gripper_size_m", [0.30, 0.18, 0.105])),
        },
    }
    return cfg


# ---------------------------------------------------------------------------
# Render to bytes
# ---------------------------------------------------------------------------


def render(proposal: dict, fmt: ExportFormat) -> tuple[bytes, str, str]:
    """Render the proposal in the requested format. Returns
    (data, content_type, suggested_filename).

    Uses tempfiles for the file-only writers (ezdxf / trimesh STL / cadquery
    STEP) — they don't all support in-memory writes and the temp dance is
    cheaper than reimplementing each backend.
    """
    if fmt not in CONTENT_TYPES:
        raise ValueError(f"Unsupported export format: {fmt}")

    cfg = proposal_to_trial_config(proposal)
    robot = load_robot(cfg.get("robot_id", ""))
    proposal_id = proposal.get("proposal_id", "layout")
    base = f"{proposal_id}.{EXTENSIONS[fmt]}"

    n_arms = max(1, len(proposal.get("robot_model_ids") or []))

    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / base
        if fmt == "dxf":
            _write_dxf(cfg, robot, out)
        elif fmt == "dwg":
            _write_dwg(cfg, robot, out)
        elif fmt == "stl":
            _write_stl(cfg, robot, out)
        elif fmt == "step":
            _write_step(cfg, robot, out)
        elif fmt == "bom_csv":
            rep = build_bom(cfg, n_arms=n_arms)
            _bom_csv(rep, out)
        elif fmt == "bom_md":
            rep = build_bom(cfg, n_arms=n_arms)
            # bom.write_markdown signature: (rep, cfg, cfg_path, out_path).
            # cfg_path is only used for header text + relative_to display;
            # synthesise a stable virtual path.
            virtual = Path(td) / f"{proposal_id}.json"
            virtual.write_bytes(b"{}")
            _bom_md(rep, cfg, virtual, out)
        data = out.read_bytes()

    return data, CONTENT_TYPES[fmt], base


def stream(proposal: dict, fmt: ExportFormat) -> tuple[io.BytesIO, str, str]:
    """Same as `render` but returns a BytesIO ready for FastAPI StreamingResponse."""
    data, ct, name = render(proposal, fmt)
    return io.BytesIO(data), ct, name


# ---------------------------------------------------------------------------
# CostBreakdown (in-canvas BomDialog) — built from the same cad_flow rules
# so the table in BomDialog matches the BOM CSV/MD downloads line-for-line.
# ---------------------------------------------------------------------------


# Map each cad_flow BOM category onto a slot in the legacy CostBreakdown
# named totals. Categories that have no slot (Product, Integration) are
# excluded from the bare hardware total but kept in line_items for visibility.
_CATEGORY_TO_TOTAL: dict[str, str] = {
    "Robot": "robots_usd",
    "EOAT": "eoat_usd",
    "Conveyor": "conveyors_usd",
    "Safety": "fence_usd",
    "Structural": "fence_usd",       # pedestal lumped with cell-structural total
    "Pallet": "fence_usd",           # pallet capex is small; folded into "other hardware"
    "Controls": "cell_controller_usd",
}


def build_cost_breakdown(
    components: list[dict],
    robot_model_ids: list[str],
    primary_robot_id: str | None,
) -> dict:
    """Build a CostBreakdown-shaped dict from placed components + arm robots.

    Uses cad_flow.bom.build_bom() under the hood so the totals here match
    what /api/export -> bom_csv / bom_md emit. Returns a dict (not a
    Pydantic instance) so callers in either codebase can construct their
    own typed object.

    Conventions:
      - `components`: list of PlacedComponent dicts (model_dump'd).
      - `robot_model_ids`: in order; len() == number of arms.
      - `primary_robot_id`: used by load_robot() to attach catalogue pricing;
        usually == robot_model_ids[0].
    """
    n_arms = max(1, len(robot_model_ids))
    fake_proposal = {
        "components": components,
        "robot_model_id": primary_robot_id or (robot_model_ids[0] if robot_model_ids else None),
    }
    cfg = proposal_to_trial_config(fake_proposal)
    rep = build_bom(cfg, n_arms=n_arms)

    # Aggregate to legacy named totals (use midpoint of low/high range).
    totals = {
        "robots_usd": 0.0,
        "eoat_usd": 0.0,
        "conveyors_usd": 0.0,
        "fence_usd": 0.0,
        "cell_controller_usd": 0.0,
    }
    integration_usd = 0.0
    bare_total = 0.0
    line_items: list[dict[str, str | float]] = []
    for ln in rep.lines:
        if ln.unit_price_low_usd is None or ln.unit_price_high_usd is None:
            # Non-capex line (e.g. boxes — consumable). Keep visible but
            # don't count toward totals.
            line_items.append({
                "label": f"{ln.category} · {ln.description}",
                "qty": float(ln.qty),
                "unit_usd": 0.0,
                "subtotal_usd": 0.0,
            })
            continue
        unit_mid = (ln.unit_price_low_usd + ln.unit_price_high_usd) / 2.0
        subtotal = unit_mid * ln.qty
        line_items.append({
            "label": f"{ln.category} · {ln.description}",
            "qty": float(ln.qty),
            "unit_usd": float(unit_mid),
            "subtotal_usd": float(subtotal),
        })
        if ln.category == "Integration":
            integration_usd += subtotal
            continue
        bare_total += subtotal
        slot = _CATEGORY_TO_TOTAL.get(ln.category)
        if slot:
            totals[slot] += subtotal

    grand_total = bare_total + integration_usd
    # Report an effective multiplier so the existing BomDialog field stays
    # meaningful — for the cad_flow model integration scales per arm, not
    # as a flat % of bare_total, but the ratio is still informative.
    integration_multiplier = (grand_total / bare_total) if bare_total > 0 else 1.0

    # ROI: 1 displaced manual palletizer per arm × $50k fully-loaded labour.
    annual_savings = 50_000.0 * n_arms
    payback_months = (
        grand_total / (annual_savings / 12.0) if annual_savings > 0 else 0.0
    )

    return {
        **totals,
        "bare_total_usd": bare_total,
        "integration_multiplier": integration_multiplier,
        "integration_usd": integration_usd,
        "grand_total_usd": grand_total,
        "annual_labor_savings_usd": annual_savings,
        "payback_months": payback_months,
        "line_items": line_items,
    }
