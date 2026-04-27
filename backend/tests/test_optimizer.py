"""SA optimizer tests."""

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
from app.services.optimizer import SAOptimizer
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


def _greedy(catalog, spec):
    gen = GreedyLayoutGenerator(catalog)
    return gen.generate(spec, n_variants=4)


def test_sa_does_not_regress_clean_seed(catalog, beverage_spec):
    """Starting from a feasible greedy proposal, SA must end at ≥ seed score."""
    proposals = _greedy(catalog, beverage_spec)
    seed = next(p for p in proposals if p.template == "dual_pallet")
    robot = catalog.get_by_id(seed.robot_model_id)
    seed_score = score_layout(seed, beverage_spec, robot).aggregate

    sa = SAOptimizer(max_iterations=200, seed=42)
    best, stats = sa.optimize(seed, beverage_spec, robot)
    best_score = score_layout(best, beverage_spec, robot).aggregate

    assert stats.iterations == 200
    assert best_score >= seed_score - 1e-6
    # History length = iterations + 1 (initial state).
    assert len(stats.score_history) == 201
    assert len(stats.best_history) == 201
    # Best history is non-decreasing.
    for prev, cur in zip(stats.best_history, stats.best_history[1:]):
        assert cur >= prev - 1e-9


def test_sa_recovers_halfway_out_of_reach(catalog, beverage_spec):
    """Halfway out of reach (per CLAUDE.md acceptance test): SA should pull
    the pallet back into reach and clear the hard violation."""
    proposals = _greedy(catalog, beverage_spec)
    seed = next(p for p in proposals if p.template == "dual_pallet")
    robot = catalog.get_by_id(seed.robot_model_id)

    # Push pallet_2 ~1100 mm further out so its center sits beyond effective
    # reach but only by ~half the reach margin (the "halfway out" scenario).
    bad = seed.model_copy(deep=True)
    pallet = next(c for c in bad.components if c.id == "pallet_2")
    pallet.x_mm += 1100.0
    bad_score = score_layout(bad, beverage_spec, robot).aggregate
    assert bad_score == 0.0  # hard reach violation -> aggregate zero

    sa = SAOptimizer(max_iterations=1500, seed=7)
    best, _ = sa.optimize(bad, beverage_spec, robot)
    best_score = score_layout(best, beverage_spec, robot).aggregate
    # SA should recover the violation -> aggregate > 0.
    assert best_score > 0.0
