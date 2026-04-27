"""Simulated annealing optimizer + (Phase 6) CP-SAT refiner.

The neuro-symbolic split: LLM picked discrete decisions (template, robot
model). Continuous geometry (component poses) is optimized here against the
deterministic scoring.py. Robot is held fixed; we perturb conveyor / pallets
/ operator zone.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Callable

from app.schemas.layout import LayoutProposal, PlacedComponent, ScoreBreakdown
from app.schemas.robot import RobotSpec
from app.schemas.workcell import WorkcellSpec
from app.services.scoring import score_layout


@dataclass
class SAStats:
    iterations: int = 0
    accepted: int = 0
    rejected: int = 0
    best_score: float = 0.0
    walltime_s: float = 0.0
    score_history: list[float] = field(default_factory=list)
    best_history: list[float] = field(default_factory=list)


# Components we will perturb (keep robot + fence fixed).
MOVABLE_TYPES: set[str] = {"conveyor", "pallet"}

# Perturbation params.
SIGMA_MM = 80.0
YAW_FLIP_PROB = 0.10
GRID_MM = 50.0
# Once in a while take a much bigger step so we can escape deep basins
# (e.g. a pallet stranded in a corner). σ scaled by 6×.
LARGE_JUMP_PROB = 0.10
LARGE_JUMP_SIGMA_MM = 480.0

# When hard violations exist, the public aggregate is zero — flat landscape.
# For SA's internal landscape, replace it with a soft penalty proportional to
# the worst violation magnitude so SA has a gradient to descend.
VIOLATION_PENALTY_DENOM_MM = 10_000.0


def _sa_internal_score(sb: ScoreBreakdown) -> float:
    """SA's internal score: aggregate when feasible; soft penalty otherwise."""
    if sb.aggregate > 0:
        return sb.aggregate
    penalty = 0.0
    for v in sb.violations:
        if v.severity == "hard":
            mag = abs(v.margin_mm) if v.margin_mm is not None else 1000.0
            penalty += mag
    return -penalty / VIOLATION_PENALTY_DENOM_MM


def _bbox_for(c: PlacedComponent) -> tuple[float, float]:
    """Return (w, h) of axis-aligned bbox; ignores yaw rotation."""
    if c.type == "robot":
        r = float(c.dims.get("base_radius_mm", 350))
        return 2 * r, 2 * r
    if c.type == "conveyor":
        length = float(c.dims.get("length_mm", 0))
        width = float(c.dims.get("width_mm", 0))
        is_vertical = abs(((c.yaw_deg % 180.0) + 180.0) % 180.0 - 90.0) < 1e-3
        return (width, length) if is_vertical else (length, width)
    if c.type == "pallet":
        return float(c.dims.get("length_mm", 1200)), float(c.dims.get("width_mm", 800))
    if c.type == "operator_zone":
        return float(c.dims.get("width_mm", 1500)), float(c.dims.get("depth_mm", 1500))
    return 0.0, 0.0


def _clamp_to_envelope(
    c: PlacedComponent, cell_w: float, cell_h: float
) -> PlacedComponent:
    w, h = _bbox_for(c)
    if c.type == "robot":
        r = float(c.dims.get("base_radius_mm", 350))
        x = max(r, min(cell_w - r, c.x_mm))
        y = max(r, min(cell_h - r, c.y_mm))
    else:
        x = max(0.0, min(cell_w - w, c.x_mm))
        y = max(0.0, min(cell_h - h, c.y_mm))
    return c.model_copy(update={"x_mm": x, "y_mm": y})


def _snap_component(c: PlacedComponent) -> PlacedComponent:
    return c.model_copy(
        update={
            "x_mm": round(c.x_mm / GRID_MM) * GRID_MM,
            "y_mm": round(c.y_mm / GRID_MM) * GRID_MM,
        }
    )


def _rebuild_fence_from(
    proposal: LayoutProposal, cell_w: float, cell_h: float
) -> LayoutProposal:
    """After moving the robot, regenerate the fence polyline so it stays
    centered on the (possibly new) robot position with the same offset.

    For the SA in this phase we don't move the robot, so this is essentially
    a no-op — kept for forward-compat with CP-SAT/Phase 6.
    """
    return proposal


def _perturb(
    proposal: LayoutProposal, cell_w: float, cell_h: float, rng: random.Random
) -> LayoutProposal:
    """Pick one movable component, jitter (x, y) ~ N(0, sigma), 10% chance
    flip yaw 0↔90 (only for components where it's meaningful: conveyor)."""
    movable_indices = [
        i for i, c in enumerate(proposal.components) if c.type in MOVABLE_TYPES
    ]
    if not movable_indices:
        return proposal
    idx = rng.choice(movable_indices)
    target = proposal.components[idx]

    sigma = LARGE_JUMP_SIGMA_MM if rng.random() < LARGE_JUMP_PROB else SIGMA_MM
    dx = rng.gauss(0.0, sigma)
    dy = rng.gauss(0.0, sigma)
    new_x = target.x_mm + dx
    new_y = target.y_mm + dy
    new_yaw = target.yaw_deg
    if target.type == "conveyor" and rng.random() < YAW_FLIP_PROB:
        new_yaw = (target.yaw_deg + 90.0) % 180.0

    moved = target.model_copy(update={"x_mm": new_x, "y_mm": new_y, "yaw_deg": new_yaw})
    moved = _clamp_to_envelope(moved, cell_w, cell_h)

    new_components = list(proposal.components)
    new_components[idx] = moved
    return proposal.model_copy(update={"components": new_components})


class SAOptimizer:
    """Simulated annealing on the continuous pose of conveyor + pallets."""

    def __init__(
        self,
        max_iterations: int = 400,
        T0: float = 1.0,
        T_min: float = 0.001,
        seed: int | None = None,
    ) -> None:
        self.max_iterations = max_iterations
        self.T0 = T0
        self.T_min = T_min
        self.rng = random.Random(seed)

    def _temperature(self, i: int, n: int) -> float:
        if n <= 1:
            return self.T_min
        # Geometric cooling: T(i) = T0 * (T_min/T0)^(i/n)
        return self.T0 * (self.T_min / self.T0) ** (i / (n - 1))

    def optimize(
        self,
        seed_proposal: LayoutProposal,
        spec: WorkcellSpec,
        robot_spec: RobotSpec | None,
        on_step: Callable[[int, LayoutProposal, float, float], None] | None = None,
    ) -> tuple[LayoutProposal, SAStats]:
        """Run SA. on_step receives (iteration, current_proposal, current_score, best_score)."""
        import time

        cell_w, cell_h = spec.cell_envelope_mm
        current = seed_proposal
        current_sb = score_layout(current, spec, robot_spec)
        current_score = _sa_internal_score(current_sb)
        best = current
        best_score = current_score

        stats = SAStats(best_score=current_sb.aggregate)
        # History uses the public aggregate so the UI sparkline is meaningful.
        stats.score_history.append(current_sb.aggregate)
        stats.best_history.append(current_sb.aggregate)
        best_public = current_sb.aggregate
        if on_step is not None:
            on_step(0, current, current_sb.aggregate, best_public)

        t0 = time.perf_counter()
        for i in range(1, self.max_iterations + 1):
            T = self._temperature(i, self.max_iterations)
            candidate = _perturb(current, cell_w, cell_h, self.rng)
            cand_sb = score_layout(candidate, spec, robot_spec)
            cand_score = _sa_internal_score(cand_sb)
            delta = cand_score - current_score

            accept = False
            if delta > 0:
                accept = True
            elif T > 0:
                # delta is in [-1, 1]; divide by T to scale.
                p = math.exp(delta / T)
                if self.rng.random() < p:
                    accept = True

            if accept:
                current = candidate
                current_score = cand_score
                current_sb = cand_sb
                stats.accepted += 1
                if cand_score > best_score:
                    best = candidate
                    best_score = cand_score
                    best_public = cand_sb.aggregate
            else:
                stats.rejected += 1

            stats.iterations = i
            stats.score_history.append(current_sb.aggregate)
            stats.best_history.append(best_public)
            if on_step is not None:
                on_step(i, current, current_sb.aggregate, best_public)

        # Greedy snap pass: snap each movable to 50 mm grid; only accept if score holds or improves.
        snapped = self._snap_pass(best, spec, robot_spec, best_score)
        snap_sb = score_layout(snapped, spec, robot_spec)
        snap_score = _sa_internal_score(snap_sb)
        if snap_score >= best_score:
            best = snapped
            best_public = snap_sb.aggregate

        stats.best_score = best_public
        stats.walltime_s = time.perf_counter() - t0
        return best, stats

    def _snap_pass(
        self,
        proposal: LayoutProposal,
        spec: WorkcellSpec,
        robot_spec: RobotSpec | None,
        baseline: float,
    ) -> LayoutProposal:
        """Per-component grid snap; revert any snap that worsens score."""
        cell_w, cell_h = spec.cell_envelope_mm
        result = proposal
        for idx, c in enumerate(result.components):
            if c.type not in MOVABLE_TYPES:
                continue
            snapped = _snap_component(c)
            snapped = _clamp_to_envelope(snapped, cell_w, cell_h)
            new_components = list(result.components)
            new_components[idx] = snapped
            candidate = result.model_copy(update={"components": new_components})
            cand_score = _sa_internal_score(score_layout(candidate, spec, robot_spec))
            if cand_score >= baseline:
                result = candidate
                baseline = cand_score
        return result


def delta_summary(
    seed: LayoutProposal,
    seed_score,
    optimized: LayoutProposal,
    optimized_score,
) -> dict[str, float]:
    """Per-sub-score deltas, plus aggregate."""
    return {
        "compactness": optimized_score.compactness - seed_score.compactness,
        "reach_margin": optimized_score.reach_margin - seed_score.reach_margin,
        "cycle_efficiency": optimized_score.cycle_efficiency - seed_score.cycle_efficiency,
        "safety_clearance": optimized_score.safety_clearance - seed_score.safety_clearance,
        "throughput_feasibility": (
            optimized_score.throughput_feasibility - seed_score.throughput_feasibility
        ),
        "aggregate": optimized_score.aggregate - seed_score.aggregate,
    }
