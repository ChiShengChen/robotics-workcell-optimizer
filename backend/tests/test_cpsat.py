"""CP-SAT refiner tests — no overlap, reachability, vs SA."""

from __future__ import annotations

import math

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
from app.services.optimizer import CPSATRefiner, SAOptimizer
from app.services.scoring import score_layout


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


def _bbox(c) -> tuple[float, float, float, float]:
    if c.type == "robot":
        r = float(c.dims.get("base_radius_mm", 350))
        return (c.x_mm - r, c.y_mm - r, 2 * r, 2 * r)
    if c.type == "conveyor":
        l = float(c.dims.get("length_mm", 0))
        w = float(c.dims.get("width_mm", 0))
        is_v = abs(((c.yaw_deg % 180.0) + 180.0) % 180.0 - 90.0) < 1e-3
        return (c.x_mm, c.y_mm, w, l) if is_v else (c.x_mm, c.y_mm, l, w)
    if c.type == "pallet":
        return (c.x_mm, c.y_mm, float(c.dims["length_mm"]), float(c.dims["width_mm"]))
    if c.type == "operator_zone":
        return (c.x_mm, c.y_mm, float(c.dims["width_mm"]), float(c.dims["depth_mm"]))
    return (c.x_mm, c.y_mm, 0.0, 0.0)


def _aabb_overlap(a, b) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay)


# ---------------------------------------------------------------------------


def test_cpsat_no_overlap(catalog, beverage_spec):
    """Refine an overlapping seed -> result has no body overlaps."""
    proposals = GreedyLayoutGenerator(catalog).generate(beverage_spec, 4)
    seed = next(p for p in proposals if p.template == "dual_pallet")
    robot = catalog.get_by_id(seed.robot_model_id)

    # Force pallet_2 to overlap pallet_1.
    bad = seed.model_copy(deep=True)
    p1 = next(c for c in bad.components if c.id == "pallet_1")
    p2 = next(c for c in bad.components if c.id == "pallet_2")
    p2.x_mm = p1.x_mm + 100  # heavily overlapping
    p2.y_mm = p1.y_mm

    refiner = CPSATRefiner(time_limit_s=10.0, num_workers=4)
    refined, stats = refiner.refine(bad, beverage_spec, robot)
    assert stats.feasible, f"CP-SAT did not find a feasible solution: {stats.status}"

    bodies = [c for c in refined.components if c.type not in ("fence", "operator_zone")]
    for i, ci in enumerate(bodies):
        for cj in bodies[i + 1 :]:
            assert not _aabb_overlap(_bbox(ci), _bbox(cj)), (
                f"{ci.id} vs {cj.id} still overlap after CP-SAT refinement."
            )


def test_cpsat_reachability(catalog, beverage_spec):
    """Every pick/place target must lie within the 16-gon inscribed in
    the effective reach disk — verified via the conservative disk check."""
    proposals = GreedyLayoutGenerator(catalog).generate(beverage_spec, 4)
    seed = next(p for p in proposals if p.template == "dual_pallet")
    robot = catalog.get_by_id(seed.robot_model_id)
    eff = robot.effective_max_reach_mm

    refiner = CPSATRefiner(time_limit_s=10.0, num_workers=4)
    refined, stats = refiner.refine(seed, beverage_spec, robot)
    assert stats.feasible

    rb = next(c for c in refined.components if c.type == "robot")
    rx, ry = rb.x_mm, rb.y_mm
    # Pallet centers + conveyor pick endpoint must satisfy distance <= eff
    # (the 16-gon is INSCRIBED in the disk, so satisfying its half-planes
    # implies distance <= eff exactly).
    for c in refined.components:
        if c.type == "pallet":
            cx = c.x_mm + float(c.dims["length_mm"]) / 2
            cy = c.y_mm + float(c.dims["width_mm"]) / 2
        elif c.type == "conveyor":
            length = float(c.dims["length_mm"])
            width = float(c.dims["width_mm"])
            is_v = abs(((c.yaw_deg % 180.0) + 180.0) % 180.0 - 90.0) < 1e-3
            cx = c.x_mm + (width / 2 if is_v else length)
            cy = c.y_mm + (length if is_v else width / 2)
        else:
            continue
        d = math.hypot(cx - rx, cy - ry)
        # Allow ~1.5% slack for the 16-gon vs disk approximation.
        assert d <= eff * 1.02, f"{c.id} target at distance {d:.0f} > eff*1.02 ({eff*1.02:.0f})"


def test_cpsat_avoids_obstacle(catalog, beverage_spec):
    """When the spec has an obstacle, CP-SAT places bodies clear of it.
    The obstacle's AABB is added as a fixed obstacle in add_no_overlap_2d."""
    from app.schemas.obstacle import Obstacle

    obstructed_spec = beverage_spec.model_copy(deep=True)
    obstructed_spec.obstacles = [
        Obstacle(
            id="cad_block",
            polygon=[[5000, 2400], [6500, 2400], [6500, 3600], [5000, 3600], [5000, 2400]],
            closed=True,
            source_entity="LWPOLYLINE",
        )
    ]
    proposals = GreedyLayoutGenerator(catalog).generate(obstructed_spec, 4)
    seed = next(p for p in proposals if p.template == "in_line")
    robot = catalog.get_by_id(seed.robot_model_id)

    refiner = CPSATRefiner(time_limit_s=10.0, num_workers=4)
    refined, stats = refiner.refine(seed, obstructed_spec, robot)
    assert stats.feasible, f"CP-SAT INFEASIBLE: {stats.status}"

    # After refinement, every movable body's AABB must be disjoint from the
    # obstacle's AABB.
    obstacle_aabb = (5000, 2400, 1500, 1200)
    for c in refined.components:
        if c.type in ("fence", "robot"):
            continue
        bb = _bbox(c)
        ox, oy, ow, oh = obstacle_aabb
        if not (bb[0] + bb[2] <= ox or ox + ow <= bb[0] or bb[1] + bb[3] <= oy or oy + oh <= bb[1]):
            assert False, f"{c.id} bbox {bb} still overlaps obstacle {obstacle_aabb}"


def test_cpsat_at_least_as_compact_as_sa(catalog, beverage_spec):
    """On the same seed, CP-SAT's bbox surrogate (sum of right + top edges)
    should be no worse than SA's. CP-SAT explicitly minimises this objective;
    SA optimises a soft-aggregate score. CP-SAT typically wins on compactness.
    """
    proposals = GreedyLayoutGenerator(catalog).generate(beverage_spec, 4)
    seed = next(p for p in proposals if p.template == "dual_pallet")
    robot = catalog.get_by_id(seed.robot_model_id)

    sa_best, _ = SAOptimizer(max_iterations=300, seed=11).optimize(
        seed, beverage_spec, robot
    )
    cpsat_best, stats = CPSATRefiner(time_limit_s=15.0, num_workers=4).refine(
        seed, beverage_spec, robot
    )
    assert stats.feasible

    def bbox_objective(p):
        bx = max(
            (
                _bbox(c)[0] + _bbox(c)[2]
                for c in p.components
                if c.type not in ("fence", "operator_zone")
            ),
            default=0.0,
        )
        by = max(
            (
                _bbox(c)[1] + _bbox(c)[3]
                for c in p.components
                if c.type not in ("fence", "operator_zone")
            ),
            default=0.0,
        )
        return bx + by

    sa_obj = bbox_objective(sa_best)
    cpsat_obj = bbox_objective(cpsat_best)
    # CP-SAT objective should be <= SA's (it explicitly minimises this).
    # Allow a 1% slack for tie cases / numerical wobble.
    assert cpsat_obj <= sa_obj * 1.01, (
        f"Expected CP-SAT bbox objective <= SA's, got CP-SAT={cpsat_obj:.0f}, SA={sa_obj:.0f}"
    )

    # And the result should still be feasible per scoring (no hard violations).
    sb = score_layout(cpsat_best, beverage_spec, robot)
    hard = [v for v in sb.violations if v.severity == "hard"]
    assert hard == [], f"CP-SAT result has hard violations: {hard}"
