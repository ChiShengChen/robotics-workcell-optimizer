"""Multi-arm dual_arm_dual_pallet template + per-robot scoring tests."""

from __future__ import annotations

import pytest

from app.schemas.workcell import (
    Conveyor,
    Pallet,
    Robot,
    Throughput,
    WorkcellSpec,
)
from app.services.catalog import RobotCatalogService
from app.services.layout import GreedyLayoutGenerator
from app.services.scoring import score_layout


@pytest.fixture(scope="module")
def catalog() -> RobotCatalogService:
    svc = RobotCatalogService()
    svc.load()
    return svc


@pytest.fixture
def high_throughput_spec() -> WorkcellSpec:
    """High throughput + roomy cell -> dual-arm should be feasible."""
    return WorkcellSpec(
        cell_envelope_mm=(12000.0, 7000.0),
        components=[
            Robot(id="robot_1"),
            Conveyor(id="infeed_1", length_mm=2500.0, width_mm=600.0, flow_direction_deg=0.0),
            Pallet(id="p1", standard="EUR"),
            Pallet(id="p2", standard="EUR"),
        ],
        throughput=Throughput(cases_per_hour_target=2500.0),
        case_dims_mm=(400.0, 300.0, 220.0),
        case_mass_kg=12.0,
        pallet_standard="EUR",
        budget_usd=300_000.0,
    )


def test_greedy_includes_dual_arm_when_throughput_high(catalog, high_throughput_spec):
    """Spec with cph_target >= 1500 should bias dual_arm_dual_pallet first."""
    proposals = GreedyLayoutGenerator(catalog).generate(high_throughput_spec, n_variants=3)
    templates = [p.template for p in proposals]
    assert "dual_arm_dual_pallet" in templates


def test_dual_arm_proposal_has_two_robots_and_task_assignment(catalog, high_throughput_spec):
    proposals = GreedyLayoutGenerator(catalog).generate(high_throughput_spec, n_variants=4)
    dual_arm = next((p for p in proposals if p.template == "dual_arm_dual_pallet"), None)
    assert dual_arm is not None
    robots = [c for c in dual_arm.components if c.type == "robot"]
    assert len(robots) == 2
    assert dual_arm.robot_model_ids and len(dual_arm.robot_model_ids) == 2
    # Each robot owns one pallet + one conveyor.
    assert "robot_1" in dual_arm.task_assignment
    assert "robot_2" in dual_arm.task_assignment
    assigned_1 = dual_arm.task_assignment["robot_1"]
    assigned_2 = dual_arm.task_assignment["robot_2"]
    assert any(a.startswith("pallet") for a in assigned_1)
    assert any(a.startswith("pallet") for a in assigned_2)
    assert any(a.startswith("conveyor") for a in assigned_1)
    assert any(a.startswith("conveyor") for a in assigned_2)


def test_dual_arm_system_uph_roughly_doubles_single_pallet_arm(catalog, high_throughput_spec):
    """vs a SINGLE-PALLET single-arm template (no eta_overlap shortcut),
    dual-arm should give close to 2x system UPH."""
    proposals = GreedyLayoutGenerator(catalog).generate(high_throughput_spec, n_variants=4)
    dual_arm = next(p for p in proposals if p.template == "dual_arm_dual_pallet")
    # in_line / L_shape are single-arm + single-pallet (no /1.9 cycle compression).
    single_simple = next(
        (p for p in proposals if p.template in ("in_line", "L_shape")), None
    )
    if single_simple is None or dual_arm.robot_model_id is None or single_simple.robot_model_id is None:
        pytest.skip("Greedy didn't produce a comparable single-arm proposal.")
    # Dual-arm should be ~2x a single-arm + single-pallet.
    assert dual_arm.estimated_uph > single_simple.estimated_uph * 1.7


def test_score_dual_arm_per_robot_reach(catalog, high_throughput_spec):
    """score_layout walks each robot independently with its task_assignment."""
    proposals = GreedyLayoutGenerator(catalog).generate(high_throughput_spec, n_variants=4)
    dual_arm = next(p for p in proposals if p.template == "dual_arm_dual_pallet")
    robots = [catalog.get_by_id(rid) for rid in dual_arm.robot_model_ids]
    sb = score_layout(dual_arm, high_throughput_spec, robots)
    # All sub-scores in [0, 1].
    for v in (sb.compactness, sb.reach_margin, sb.cycle_efficiency,
              sb.safety_clearance, sb.throughput_feasibility):
        assert 0.0 <= v <= 1.0
    # Each robot's tasks should be reachable in the greedy seed.
    if dual_arm.robot_model_id is None:
        pytest.skip("Greedy didn't pick a robot.")
    assert sb.reach_margin > 0.0  # no hard reach violations


def test_score_layout_accepts_single_robotspec_for_compat(catalog):
    """Backward compat: passing a single RobotSpec (not a list) still works."""
    spec = WorkcellSpec(
        cell_envelope_mm=(8000, 6000),
        components=[Robot(id="robot_1"),
                    Conveyor(id="i", length_mm=2500, width_mm=600, flow_direction_deg=0),
                    Pallet(id="p", standard="EUR")],
        throughput=Throughput(cases_per_hour_target=500),
        case_dims_mm=(400, 300, 220), case_mass_kg=12, pallet_standard="EUR",
    )
    proposal = GreedyLayoutGenerator(catalog).generate(spec, 1)[0]
    if proposal.robot_model_id is None:
        pytest.skip("No robot picked.")
    robot = catalog.get_by_id(proposal.robot_model_id)
    # Single RobotSpec (not list) — must still work.
    sb = score_layout(proposal, spec, robot)
    assert 0.0 <= sb.aggregate <= 1.0
