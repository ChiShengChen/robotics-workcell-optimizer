"""Simulated annealing optimizer + (Phase 6) CP-SAT refiner.

The neuro-symbolic split: LLM picked discrete decisions (template, robot
model). Continuous geometry (component poses) is optimized here against the
deterministic scoring.py. Robot is held fixed; we perturb conveyor / pallets
/ operator zone.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from dataclasses import dataclass, field

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


# ---------------------------------------------------------------------------
# CP-SAT refiner (Phase 6 — engineering depth showcase)
# ---------------------------------------------------------------------------
#
# Why CP-SAT over MILP from scratch:
#   - OR-Tools' AddNoOverlap2D handles disjunctive non-overlap with native
#     lazy clause generation; modelling the same in MILP needs big-M
#     constants whose tuning is fragile.
#   - CP-SAT's portfolio search is free; Gurobi is licensed.
#
# Why 16-half-plane reach approximation:
#   - CP-SAT is integer-LP-flavoured; quadratic distance constraints aren't
#     supported natively. A regular polygon inscribed in the reach disk
#     (16 sides ≈ 1.5% radial error) gives exact linear constraints.
#
# Why integer-only mm scale:
#   - CP-SAT operates on integer variables; we scale all distances to mm
#     and trig coefficients to a 1000x scale (3 decimal precision is
#     enough for layout positioning).
#
# Why lexicographic vs weighted-sum objective:
#   - Hard constraints (no overlap, reachability inside the 16-gon, fence
#     clearance) are encoded as model constraints — the solver returns
#     INFEASIBLE if violated. The objective minimises the bbox surrogate
#     `bx + by` (perimeter proxy) so the solver chases compactness only
#     after feasibility is guaranteed. This is a clean lexicographic
#     hierarchy without juggling arbitrary penalty weights.

import math as _math
from dataclasses import dataclass as _dataclass

try:  # pragma: no cover — import shielded so unit tests don't all crash if ortools isn't installed
    from ortools.sat.python import cp_model as _cp_model  # type: ignore
except ImportError:  # pragma: no cover
    _cp_model = None


@_dataclass
class CPSATStats:
    status: str  # "OPTIMAL" / "FEASIBLE" / "INFEASIBLE" / "MODEL_INVALID" / "UNKNOWN"
    objective: float
    walltime_s: float
    num_branches: int
    num_conflicts: int
    feasible: bool


N_REACH_DIRECTIONS = 16
SCALE = 1000  # multiply trig coefficients by this to keep things integer
# Pull the reach polygon in by this many mm so the integer-rounding error
# from the SCALE=1000 trig coefficients can't push a target past the disk.
REACH_SLACK_MM = 20


class CPSATRefiner:
    """Refine a layout's continuous geometry with OR-Tools CP-SAT.

    Robot + fence are held fixed; only conveyor / pallet / operator-zone
    positions are decision variables. All math is done in mm scaled to
    integers; trig coefficients are scaled by 1000.
    """

    def __init__(self, time_limit_s: float = 15.0, num_workers: int = 4) -> None:
        if _cp_model is None:
            raise RuntimeError(
                "ortools is not installed; pip install ortools to use CPSATRefiner."
            )
        self.time_limit_s = time_limit_s
        self.num_workers = num_workers

    def refine(
        self,
        seed_proposal: LayoutProposal,
        spec: WorkcellSpec,
        robot_spec: RobotSpec | None,
    ) -> tuple[LayoutProposal, CPSATStats]:
        cp_model = _cp_model
        cell_w = int(round(spec.cell_envelope_mm[0]))
        cell_h = int(round(spec.cell_envelope_mm[1]))

        movables: list[tuple[int, PlacedComponent, int, int]] = []
        for idx, c in enumerate(seed_proposal.components):
            if c.type in {"robot", "fence"}:
                continue
            w_f, h_f = _bbox_for(c)
            if w_f <= 0 or h_f <= 0:
                continue
            movables.append((idx, c, int(round(w_f)), int(round(h_f))))

        robot = next(
            (c for c in seed_proposal.components if c.type == "robot"), None
        )
        rx = int(round(robot.x_mm)) if robot is not None else cell_w // 2
        ry = int(round(robot.y_mm)) if robot is not None else cell_h // 2
        eff = (
            int(round(robot_spec.effective_max_reach_mm))
            if robot_spec is not None
            else int(round(robot.dims.get("effective_reach_mm", 1750)))
            if robot is not None
            else 1750
        )

        model = cp_model.CpModel()
        x_vars: list[Any] = []
        y_vars: list[Any] = []
        x_intervals: list[Any] = []
        y_intervals: list[Any] = []
        widths: list[int] = []
        heights: list[int] = []

        for _idx, c, w, h in movables:
            x = model.new_int_var(0, max(0, cell_w - w), f"x_{c.id}")
            y = model.new_int_var(0, max(0, cell_h - h), f"y_{c.id}")
            xi = model.new_fixed_size_interval_var(x, w, f"xi_{c.id}")
            yi = model.new_fixed_size_interval_var(y, h, f"yi_{c.id}")
            x_vars.append(x)
            y_vars.append(y)
            x_intervals.append(xi)
            y_intervals.append(yi)
            widths.append(w)
            heights.append(h)

        # Add the robot footprint as a STATIC obstacle so movable bodies
        # can't overlap the robot base.
        if robot is not None:
            base_r = float(robot.dims.get("base_radius_mm", 350))
            r_w = int(round(2 * base_r))
            r_h = int(round(2 * base_r))
            r_x0 = int(round(robot.x_mm - base_r))
            r_y0 = int(round(robot.y_mm - base_r))
            rx_const = model.new_constant(r_x0)
            ry_const = model.new_constant(r_y0)
            x_intervals.append(model.new_fixed_size_interval_var(rx_const, r_w, "xi_robot"))
            y_intervals.append(model.new_fixed_size_interval_var(ry_const, r_h, "yi_robot"))

        # No-overlap among non-robot bodies (plus robot footprint as obstacle).
        if x_intervals:
            model.add_no_overlap_2d(x_intervals, y_intervals)

        # 16-half-plane reach constraint per pick/place target.
        # Each pallet center + each conveyor pick endpoint must lie inside the
        # regular 16-gon inscribed in the disk of radius eff around the robot.
        for (i, (_idx, c, w, h)), x, y in zip(enumerate(movables), x_vars, y_vars):
            # Skip operator zone — operator doesn't need to be inside reach.
            if c.type == "operator_zone":
                continue
            target_offset_x, target_offset_y = self._target_offset(c, w, h)
            for k in range(N_REACH_DIRECTIONS):
                theta = 2 * _math.pi * k / N_REACH_DIRECTIONS
                cos_k = int(round(_math.cos(theta) * SCALE))
                sin_k = int(round(_math.sin(theta) * SCALE))
                # cos·tx + sin·ty ≤ eff*SCALE + cos·rx + sin·ry, where
                # (tx, ty) = (x + offset_x, y + offset_y).
                # Rearranged for IntVar terms:
                #   cos·x + sin·y ≤ eff*SCALE + cos·(rx - offset_x) + sin·(ry - offset_y)
                rhs = (
                    (eff - REACH_SLACK_MM) * SCALE
                    + cos_k * (rx - target_offset_x)
                    + sin_k * (ry - target_offset_y)
                )
                model.add(cos_k * x + sin_k * y <= rhs)

        # Fence clearance via the same 16-gon — body bbox CORNERS must lie
        # inside the eff-disk so they automatically clear the fence sitting at
        # eff + S_safe. (Operator zone exempt.)
        for (i, (_idx, c, w, h)), x, y in zip(enumerate(movables), x_vars, y_vars):
            if c.type in {"operator_zone", "conveyor"}:
                # Real cells route the conveyor through a muting zone /
                # light curtain, so it isn't part of the ISO 13855 envelope.
                continue
            for ox, oy in [(0, 0), (w, 0), (0, h), (w, h)]:
                for k in range(N_REACH_DIRECTIONS):
                    theta = 2 * _math.pi * k / N_REACH_DIRECTIONS
                    cos_k = int(round(_math.cos(theta) * SCALE))
                    sin_k = int(round(_math.sin(theta) * SCALE))
                    rhs = (
                        (eff - REACH_SLACK_MM) * SCALE
                        + cos_k * (rx - ox)
                        + sin_k * (ry - oy)
                    )
                    model.add(cos_k * x + sin_k * y <= rhs)

        # Objective: minimise bbox surrogate bx + by where bx ≥ x_i + w_i,
        # by ≥ y_i + h_i. Smaller bbox -> tighter, more compact layout.
        if x_vars:
            bx = model.new_int_var(0, cell_w * 2, "bx")
            by = model.new_int_var(0, cell_h * 2, "by")
            for x, w in zip(x_vars, widths):
                model.add(bx >= x + w)
            for y, h in zip(y_vars, heights):
                model.add(by >= y + h)
            model.minimize(bx + by)
        else:
            bx = by = None

        # Warm start from the seed pose.
        for (_idx, c, w, h), x, y in zip(movables, x_vars, y_vars):
            seed_x = max(0, min(cell_w - w, int(round(c.x_mm))))
            seed_y = max(0, min(cell_h - h, int(round(c.y_mm))))
            model.add_hint(x, seed_x)
            model.add_hint(y, seed_y)

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.time_limit_s
        solver.parameters.num_search_workers = self.num_workers
        status = solver.solve(model)
        status_name = solver.status_name(status)

        feasible = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
        if not feasible:
            return seed_proposal, CPSATStats(
                status=status_name,
                objective=0.0,
                walltime_s=solver.wall_time,
                num_branches=solver.num_branches,
                num_conflicts=solver.num_conflicts,
                feasible=False,
            )

        # Reassemble the proposal with refined positions.
        new_components = list(seed_proposal.components)
        for (idx, c, _w, _h), x_var, y_var in zip(movables, x_vars, y_vars):
            new_x = solver.value(x_var)
            new_y = solver.value(y_var)
            new_components[idx] = c.model_copy(
                update={"x_mm": float(new_x), "y_mm": float(new_y)}
            )
        refined = seed_proposal.model_copy(update={"components": new_components})

        objective = solver.objective_value if x_vars else 0.0
        return refined, CPSATStats(
            status=status_name,
            objective=float(objective),
            walltime_s=solver.wall_time,
            num_branches=solver.num_branches,
            num_conflicts=solver.num_conflicts,
            feasible=True,
        )

    @staticmethod
    def _target_offset(c: PlacedComponent, w: int, h: int) -> tuple[int, int]:
        """Pick/place target inside the bbox, expressed as (offset_x, offset_y)
        from the bbox top-left."""
        if c.type == "pallet":
            return w // 2, h // 2
        if c.type == "conveyor":
            length = float(c.dims.get("length_mm", 0))
            width = float(c.dims.get("width_mm", 0))
            is_vertical = abs(((c.yaw_deg % 180.0) + 180.0) % 180.0 - 90.0) < 1e-3
            # Pick at the END of the belt — opposite side from the robot.
            if is_vertical:
                # bbox is (width, length); pick at top.
                return int(round(width / 2)), int(round(length))
            return int(round(length)), int(round(width / 2))
        return w // 2, h // 2


from typing import Any  # noqa: E402  (kept here so the optional ortools import lives at top)
