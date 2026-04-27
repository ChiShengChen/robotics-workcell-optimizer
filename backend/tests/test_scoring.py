"""Scoring service tests — pure-function checks of the five components."""

from __future__ import annotations

import math

import pytest

from app.schemas.layout import LayoutProposal, PlacedComponent
from app.schemas.workcell import (
    Conveyor,
    Pallet,
    Robot,
    Throughput,
    WorkcellSpec,
)
from app.services.catalog import RobotCatalogService
from app.services.layout import GreedyLayoutGenerator, iso13855_safety_distance_mm
from app.services.scoring import (
    score_compactness,
    score_cycle_efficiency,
    score_layout,
    score_reach_margin,
    score_safety_clearance,
)


@pytest.fixture(scope="module")
def catalog() -> RobotCatalogService:
    svc = RobotCatalogService()
    svc.load()
    return svc


@pytest.fixture
def beverage_spec() -> WorkcellSpec:
    return WorkcellSpec(
        cell_envelope_mm=(8000.0, 6000.0),
        components=[
            Robot(id="robot_1"),
            Conveyor(id="infeed_1", length_mm=2500.0, width_mm=600.0, flow_direction_deg=0.0),
            Pallet(id="pallet_a", standard="EUR"),
            Pallet(id="pallet_b", standard="EUR"),
        ],
        throughput=Throughput(cases_per_hour_target=500.0),
        case_dims_mm=(400.0, 300.0, 220.0),
        case_mass_kg=12.0,
        pallet_standard="EUR",
        budget_usd=160_000.0,
    )


@pytest.fixture
def beverage_proposal(catalog, beverage_spec) -> LayoutProposal:
    gen = GreedyLayoutGenerator(catalog)
    proposals = gen.generate(beverage_spec, n_variants=4)
    return next(p for p in proposals if p.template == "dual_pallet")


# ---------------------------------------------------------------------------


def test_compactness_perfect_vs_sparse(beverage_spec):
    """A tightly-packed bbox scores higher than a spread-out one."""
    cell = beverage_spec.cell_envelope_mm
    # Tight: three pallets stacked next to each other in a corner.
    tight = LayoutProposal(
        proposal_id="tight",
        template="in_line",
        robot_model_id=None,
        components=[
            PlacedComponent(id="p1", type="pallet", x_mm=100, y_mm=100, yaw_deg=0,
                            dims={"length_mm": 1200, "width_mm": 800}),
            PlacedComponent(id="p2", type="pallet", x_mm=1300, y_mm=100, yaw_deg=0,
                            dims={"length_mm": 1200, "width_mm": 800}),
        ],
        cell_bounds_mm=cell, estimated_cycle_time_s=0, estimated_uph=0, rationale="",
    )
    sparse = LayoutProposal(
        proposal_id="sparse",
        template="in_line",
        robot_model_id=None,
        components=[
            PlacedComponent(id="p1", type="pallet", x_mm=100, y_mm=100, yaw_deg=0,
                            dims={"length_mm": 1200, "width_mm": 800}),
            PlacedComponent(id="p2", type="pallet", x_mm=cell[0] - 1300, y_mm=cell[1] - 900,
                            yaw_deg=0, dims={"length_mm": 1200, "width_mm": 800}),
        ],
        cell_bounds_mm=cell, estimated_cycle_time_s=0, estimated_uph=0, rationale="",
    )
    s_tight = score_compactness(tight, beverage_spec)
    s_sparse = score_compactness(sparse, beverage_spec)
    assert s_tight > s_sparse


def test_reach_margin_at_boundary_vs_unreachable(catalog):
    """Target exactly inside effective reach scores well; beyond is HARD violation → 0."""
    robot_spec = catalog.get_by_id("M-410iC/110")
    # M-410iC/110: reach=2400 mm → effective=2040 mm.
    eff = robot_spec.effective_max_reach_mm
    spec = WorkcellSpec(
        cell_envelope_mm=(8000, 6000), components=[Robot(id="r1")],
        throughput=Throughput(cases_per_hour_target=500.0),
    )
    # Pallet center exactly at eff+1 mm (just out of reach).
    rx, ry = 4000.0, 3000.0
    pal_center_x = rx + eff + 1.0  # 1 mm past
    proposal_far = LayoutProposal(
        proposal_id="far", template="in_line", robot_model_id="M-410iC/110",
        components=[
            PlacedComponent(id="r1", type="robot", x_mm=rx, y_mm=ry,
                            dims={"base_radius_mm": 435, "reach_mm": 2400, "effective_reach_mm": eff}),
            PlacedComponent(id="p1", type="pallet",
                            x_mm=pal_center_x - 600, y_mm=ry - 400,
                            dims={"length_mm": 1200, "width_mm": 800}),
        ],
        cell_bounds_mm=(8000, 6000), estimated_cycle_time_s=0, estimated_uph=0, rationale="",
    )
    res_far = score_reach_margin(proposal_far, spec, robot_spec)
    assert res_far["score"] == 0.0
    assert any(v.kind == "unreachable" and v.severity == "hard" for v in res_far["violations"])

    # Pallet at eff/2 → comfortably inside.
    proposal_close = LayoutProposal(
        proposal_id="close", template="in_line", robot_model_id="M-410iC/110",
        components=[
            PlacedComponent(id="r1", type="robot", x_mm=rx, y_mm=ry,
                            dims={"base_radius_mm": 435, "reach_mm": 2400, "effective_reach_mm": eff}),
            PlacedComponent(id="p1", type="pallet",
                            x_mm=rx + eff / 2 - 600, y_mm=ry - 400,
                            dims={"length_mm": 1200, "width_mm": 800}),
        ],
        cell_bounds_mm=(8000, 6000), estimated_cycle_time_s=0, estimated_uph=0, rationale="",
    )
    res_close = score_reach_margin(proposal_close, spec, robot_spec)
    assert res_close["score"] > 0.5
    assert all(v.severity != "hard" for v in res_close["violations"])


def test_cycle_efficiency_m410ic110(catalog):
    """Estimated cycle should be near catalog cph (3600/2200 ≈ 1.636 s) when target distance is short."""
    robot_spec = catalog.get_by_id("M-410iC/110")
    spec = WorkcellSpec(
        cell_envelope_mm=(8000, 6000), components=[Robot(id="r1")],
        throughput=Throughput(cases_per_hour_target=2000.0),
    )
    # Robot at center, single pallet 800 mm away → very short reach → cycle should hit cph_std floor.
    proposal = LayoutProposal(
        proposal_id="x", template="in_line", robot_model_id="M-410iC/110",
        components=[
            PlacedComponent(id="r1", type="robot", x_mm=4000, y_mm=3000,
                            dims={"base_radius_mm": 435, "reach_mm": 2400, "effective_reach_mm": 2040}),
            PlacedComponent(id="p1", type="pallet", x_mm=4400, y_mm=2600,
                            dims={"length_mm": 1200, "width_mm": 800}),
        ],
        cell_bounds_mm=(8000, 6000), estimated_cycle_time_s=0, estimated_uph=0, rationale="",
    )
    res = score_cycle_efficiency(proposal, robot_spec, target_uph=2000.0)
    # Should be at least the catalog floor (1.636s).
    assert res["estimated_cycle_s"] >= 3600.0 / robot_spec.cycles_per_hour_std - 1e-3
    # UPH should be > 0 and score in (0, 1].
    assert res["estimated_uph"] > 0
    assert 0.0 < res["score"] <= 1.0


def test_safety_iso13855_pass_and_fail(beverage_spec):
    """1450 mm clearance passes (no curtain); 600 mm fails."""
    s_safe = iso13855_safety_distance_mm(has_hard_guard=False)
    assert math.isclose(s_safe, 2000 * 0.3 + 850, rel_tol=1e-9)  # 1450 mm

    # Passing: fence sits 200 mm beyond robot reach + S_safe.
    rx, ry = 4000, 3000
    margin = 2400 + s_safe + 200
    poly = [
        [rx - margin, ry - margin], [rx + margin, ry - margin],
        [rx + margin, ry + margin], [rx - margin, ry + margin],
        [rx - margin, ry - margin],
    ]
    proposal_ok = LayoutProposal(
        proposal_id="ok", template="in_line", robot_model_id="IRB 460",
        components=[
            PlacedComponent(id="r1", type="robot", x_mm=rx, y_mm=ry,
                            dims={"base_radius_mm": 435, "reach_mm": 2400, "effective_reach_mm": 2040}),
            PlacedComponent(id="fence_main", type="fence", x_mm=0, y_mm=0,
                            dims={"polyline": poly, "height_mm": 2000}),
        ],
        cell_bounds_mm=(20000, 20000), estimated_cycle_time_s=0, estimated_uph=0, rationale="",
    )
    res_ok = score_safety_clearance(proposal_ok, beverage_spec)
    assert res_ok["iso13855_pass"]
    assert res_ok["score"] > 0.5

    # Failing: fence is only 600 mm from robot center — violates S_safe=1450.
    tight = 600
    poly_bad = [
        [rx - tight, ry - tight], [rx + tight, ry - tight],
        [rx + tight, ry + tight], [rx - tight, ry + tight],
        [rx - tight, ry - tight],
    ]
    proposal_fail = LayoutProposal(
        proposal_id="fail", template="in_line", robot_model_id="IRB 460",
        components=[
            PlacedComponent(id="r1", type="robot", x_mm=rx, y_mm=ry,
                            dims={"base_radius_mm": 435, "reach_mm": 2400, "effective_reach_mm": 2040}),
            PlacedComponent(id="fence_main", type="fence", x_mm=0, y_mm=0,
                            dims={"polyline": poly_bad, "height_mm": 2000}),
        ],
        cell_bounds_mm=(20000, 20000), estimated_cycle_time_s=0, estimated_uph=0, rationale="",
    )
    res_fail = score_safety_clearance(proposal_fail, beverage_spec)
    assert res_fail["score"] == 0.0
    assert any(v.kind == "iso13855" and v.severity == "hard" for v in res_fail["violations"])


def test_aggregate_zeroes_on_hard_violation(catalog, beverage_spec, beverage_proposal):
    """Add an unreachable target → aggregate must drop to 0."""
    robot_spec = catalog.get_by_id(beverage_proposal.robot_model_id)
    # Move pallet far past reach.
    far_proposal = beverage_proposal.model_copy(deep=True)
    pallet = next(c for c in far_proposal.components if c.type == "pallet")
    pallet.x_mm = beverage_spec.cell_envelope_mm[0] - 1300  # far edge
    pallet.y_mm = beverage_spec.cell_envelope_mm[1] - 900

    sb = score_layout(far_proposal, beverage_spec, robot_spec)
    if any(v.severity == "hard" for v in sb.violations):
        assert sb.aggregate == 0.0


def test_full_aggregate_clean_proposal(catalog, beverage_spec, beverage_proposal):
    """Greedy proposal on a feasible spec should yield aggregate > 0."""
    robot_spec = catalog.get_by_id(beverage_proposal.robot_model_id)
    sb = score_layout(beverage_proposal, beverage_spec, robot_spec)
    # All five sub-scores in [0,1].
    for v in (sb.compactness, sb.reach_margin, sb.cycle_efficiency,
              sb.safety_clearance, sb.throughput_feasibility):
        assert 0.0 <= v <= 1.0
    if not any(v.severity == "hard" for v in sb.violations):
        assert sb.aggregate > 0.0
        assert sb.aggregate <= 1.0


def test_throughput_saturation():
    """UPH/target ratio saturates at 1.1× = score 1."""
    from app.services.scoring import score_throughput_feasibility

    assert score_throughput_feasibility(500, 500) == pytest.approx(1.0 / 1.1, abs=1e-3)
    assert score_throughput_feasibility(1000, 500) == 1.0  # capped at 1.1×
    assert score_throughput_feasibility(0, 500) == 0.0
    assert score_throughput_feasibility(500, 0) == 1.0  # no target → trivially feasible
