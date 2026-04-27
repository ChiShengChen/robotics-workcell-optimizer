"""Pydantic schema round-trip tests."""

from __future__ import annotations

from app.schemas.layout import LayoutProposal, PlacedComponent, ScoreBreakdown
from app.schemas.robot import IdealUseCase, RobotSpec
from app.schemas.workcell import (
    Conveyor,
    OperatorZone,
    Pallet,
    Robot,
    Throughput,
    WorkcellSpec,
)


def test_workcell_spec_roundtrip():
    spec = WorkcellSpec(
        cell_envelope_mm=(8000.0, 6000.0),
        components=[
            Robot(id="r1", payload_kg=80.0, reach_mm=2400.0),
            Conveyor(id="c1", length_mm=2500.0, width_mm=600.0, flow_direction_deg=0.0),
            Pallet(id="p1", standard="EUR", pattern="interlock"),
            OperatorZone(id="op", width_mm=1500.0, depth_mm=1500.0),
        ],
        throughput=Throughput(cases_per_hour_target=500.0),
        case_dims_mm=(400.0, 300.0, 200.0),
        case_mass_kg=12.0,
        pallet_standard="EUR",
        budget_usd=160000.0,
        assumptions=["Operator zone width inferred (1500 mm typical)."],
    )
    payload = spec.model_dump_json()
    restored = WorkcellSpec.model_validate_json(payload)
    assert restored == spec
    assert restored.components[0].type == "robot"
    assert restored.components[2].standard == "EUR"


def test_robot_spec_roundtrip_and_derate():
    r = RobotSpec(
        model="M-410iC/110",
        manufacturer="FANUC",
        axes=4,
        payload_kg=110.0,
        reach_mm=2400.0,
        vertical_reach_mm_min=-1200.0,
        vertical_reach_mm_max=2200.0,
        repeatability_mm=0.5,
        footprint_l_mm=870.0,
        footprint_w_mm=870.0,
        weight_kg=1030.0,
        cycles_per_hour_std=2200.0,
        price_usd_low=50000.0,
        price_usd_high=70000.0,
        ideal_use_case=IdealUseCase.LIGHT_CASE,
    )
    assert abs(r.effective_max_reach_mm - 0.85 * 2400.0) < 1e-6
    blob = r.model_dump_json()
    r2 = RobotSpec.model_validate_json(blob)
    assert r2 == r


def test_layout_proposal_roundtrip():
    proposal = LayoutProposal(
        proposal_id="abc",
        template="dual_pallet",
        robot_model_id="IRB 460",
        components=[
            PlacedComponent(
                id="r1", type="robot", x_mm=4000.0, y_mm=3000.0,
                dims={"base_radius_mm": 500.0, "reach_mm": 2400.0},
            ),
            PlacedComponent(
                id="p1", type="pallet", x_mm=2000.0, y_mm=4500.0,
                dims={"length_mm": 1200.0, "width_mm": 800.0, "standard": "EUR"},
            ),
        ],
        cell_bounds_mm=(8000.0, 6000.0),
        estimated_cycle_time_s=1.7,
        estimated_uph=1900.0,
        rationale="Dual-pallet doubles throughput.",
    )
    j = proposal.model_dump_json()
    restored = LayoutProposal.model_validate_json(j)
    assert restored == proposal


def test_score_breakdown_roundtrip():
    sb = ScoreBreakdown(
        compactness=0.7,
        reach_margin=0.95,
        cycle_efficiency=0.8,
        safety_clearance=1.0,
        throughput_feasibility=0.9,
        aggregate=0.86,
        violations=[],
        weights={"c": 0.2, "r": 0.3, "t": 0.2, "s": 0.3},
    )
    assert ScoreBreakdown.model_validate_json(sb.model_dump_json()) == sb
