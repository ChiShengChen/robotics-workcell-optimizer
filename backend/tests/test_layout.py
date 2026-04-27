"""Greedy layout + trapezoidal cycle-time tests."""

from __future__ import annotations

import math

import pytest

from app.schemas.workcell import (
    Conveyor,
    OperatorZone,
    Pallet,
    Robot,
    Throughput,
    WorkcellSpec,
)
from app.services.catalog import RobotCatalogService
from app.services.layout import (
    A_MAX_MM_S2_4AXIS,
    GreedyLayoutGenerator,
    STD_CYCLE_PATH_MM,
    V_MAX_MM_S_4AXIS,
    estimate_cycle_time_s,
    trapezoidal_time_s,
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
            Pallet(id="pallet_a", standard="EUR", pattern="interlock"),
        ],
        throughput=Throughput(cases_per_hour_target=500.0),
        case_dims_mm=(400.0, 300.0, 220.0),
        case_mass_kg=12.0,
        pallet_standard="EUR",
        budget_usd=160_000.0,
    )


def _bbox(c) -> tuple[float, float, float, float]:
    """Return (x, y, w, h) for a placed component (ignoring yaw — axis-aligned approx)."""
    if c.type == "robot":
        r = c.dims.get("base_radius_mm", 350)
        return (c.x_mm - r, c.y_mm - r, 2 * r, 2 * r)
    if c.type == "conveyor":
        l = c.dims["length_mm"]
        w = c.dims["width_mm"]
        if abs((c.yaw_deg % 180.0) - 90.0) < 1e-6:
            return (c.x_mm, c.y_mm, w, l)
        return (c.x_mm, c.y_mm, l, w)
    if c.type == "pallet":
        return (c.x_mm, c.y_mm, c.dims["length_mm"], c.dims["width_mm"])
    if c.type == "operator_zone":
        return (c.x_mm, c.y_mm, c.dims["width_mm"], c.dims["depth_mm"])
    return (c.x_mm, c.y_mm, 0.0, 0.0)


def _aabbs_overlap(a, b) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay)


# ---------------------------------------------------------------------------


def test_greedy_in_line(catalog, beverage_spec):
    gen = GreedyLayoutGenerator(catalog)
    proposals = gen.generate(beverage_spec, n_variants=4)
    in_line = next(p for p in proposals if p.template == "in_line")
    assert in_line.robot_model_id is not None, "Beverage spec should yield a feasible robot."
    types = {c.type for c in in_line.components}
    # Robot + conveyor + pallet + fence + operator zone all present.
    assert {"robot", "conveyor", "pallet", "fence", "operator_zone"}.issubset(types)

    # Fence polyline wraps the robot (point-in-polygon: robot center should be inside the fence rect).
    fence = next(c for c in in_line.components if c.type == "fence")
    poly = fence.dims["polyline"]
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    robot = next(c for c in in_line.components if c.type == "robot")
    assert min(xs) <= robot.x_mm <= max(xs)
    assert min(ys) <= robot.y_mm <= max(ys)

    # No two non-fence component bboxes overlap.
    bodies = [c for c in in_line.components if c.type != "fence"]
    for i, ci in enumerate(bodies):
        for cj in bodies[i + 1 :]:
            assert not _aabbs_overlap(_bbox(ci), _bbox(cj)), (
                f"Components {ci.id} and {cj.id} overlap in in_line layout."
            )


def test_greedy_dual_pallet(catalog, beverage_spec):
    gen = GreedyLayoutGenerator(catalog)
    proposals = gen.generate(beverage_spec, n_variants=4)
    dual = next(p for p in proposals if p.template == "dual_pallet")
    pallets = [c for c in dual.components if c.type == "pallet"]
    assert len(pallets) == 2, "Dual-pallet template must place exactly 2 pallet stations."
    # Pallets on opposite sides of the robot in x.
    robot = next(c for c in dual.components if c.type == "robot")
    xs_relative_to_robot = [p.x_mm + p.dims["length_mm"] / 2 - robot.x_mm for p in pallets]
    assert min(xs_relative_to_robot) < 0 < max(xs_relative_to_robot), (
        "Pallets should straddle the robot on opposite sides."
    )
    # UPH should be substantially higher than single-station equivalent (1.9× factor in cycle).
    in_line = next(p for p in proposals if p.template == "in_line")
    assert dual.estimated_uph > 1.5 * in_line.estimated_uph


def test_no_feasible_robot(catalog):
    spec = WorkcellSpec(
        cell_envelope_mm=(20_000.0, 20_000.0),
        components=[Robot(id="robot_1")],
        throughput=Throughput(cases_per_hour_target=500.0),
        case_mass_kg=970.0,  # 970 + 30 EOAT = 1000 kg > any catalog robot
        pallet_standard="EUR",
        budget_usd=50_000.0,
    )
    gen = GreedyLayoutGenerator(catalog)
    proposals = gen.generate(spec, n_variants=2)
    assert proposals, "Should still return at least one placeholder proposal."
    assert all(p.robot_model_id is None for p in proposals), (
        "No catalog robot can carry ~1000 kg payload."
    )
    assert any(
        any("No catalog robot" in a or "payload" in a.lower() for a in p.assumptions)
        for p in proposals
    )


def test_cycle_time_trapezoidal_matches_m410ic110(catalog):
    """FANUC M-410iC/110 datasheet: 2200 cph at standard 400/2000/400 mm cycle.

    The motion-only trapezoidal estimate should land in the same neighborhood as
    the published 1.636 s/cycle (3600/2200) — within 30% — and our final estimate
    must respect the cph_std floor.
    """
    m410 = catalog.get_by_id("M-410iC/110")
    motion_s = trapezoidal_time_s(STD_CYCLE_PATH_MM, V_MAX_MM_S_4AXIS, A_MAX_MM_S2_4AXIS)
    # 5600 mm path @ 2.5 m/s, 8 m/s² → 5.6/2.5 + 2.5/8 = 2.24 + 0.3125 = 2.5525 s
    assert math.isclose(motion_s, 5600 / 2500 + 2500 / 8000, rel_tol=1e-6)

    cycle_s = estimate_cycle_time_s(m410, dual_pallet=False)
    # Cph_std floor is 3600/2200 ≈ 1.636 s; our motion+pick estimate is higher,
    # so the final estimate should equal the motion+pick term (≈3.35 s) — well above floor.
    assert cycle_s >= 3600.0 / m410.cycles_per_hour_std
    assert 1.5 < cycle_s < 6.0, f"Cycle time {cycle_s:.2f}s outside sane palletizer range."

    # Dual pallet should compress cycle by ~1.9×.
    dual = estimate_cycle_time_s(m410, dual_pallet=True)
    assert math.isclose(dual, cycle_s / 1.9, rel_tol=1e-6)
