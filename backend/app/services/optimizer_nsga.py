"""NSGA-II multi-objective optimizer for layout proposals.

Where SA collapses everything into a single weighted aggregate and CP-SAT
deals with hard non-overlap, NSGA-II asks the harder question: what set of
layouts is *non-dominated* across multiple sub-scores? The output is a
Pareto front rather than a single "best" — the user picks the trade-off.

Decision variables:
    For each MOVABLE component (conveyor / pallet / operator_zone):
      * x_mm continuous in [0, cell_w]
      * y_mm continuous in [0, cell_h]

    Robot pose is held fixed (same convention as SAOptimizer). Yaw is held
    fixed too — yaw flips would require an integer var; pymoo's mixed-int
    handling complicates the population init. SA already covers yaw moves.

Objectives (all minimised — pymoo convention):
    f1 = 1 - compactness            (higher compactness = tighter cell)
    f2 = 1 - reach_margin           (higher reach margin = robot can reach)
    f3 = 1 - throughput_feasibility (higher = more headroom vs target UPH)

We deliberately do NOT include safety_clearance and cycle_efficiency:
    - safety is a HARD constraint (handled via penalty + violation check),
      not a trade-off knob worth Pareto-exploring.
    - cycle is highly correlated with reach + throughput; including it
      makes the front noisier without revealing new trade-offs.

Hard-violation handling:
    Sub-scores stay honest — compactness / reach / throughput are well
    defined in [0,1] regardless of feasibility, and we *want* the front
    to expose trade-offs between "feasible-but-cramped" and "spacious-
    but-violating" so the user can judge. Each Pareto entry carries its
    full ScoreBreakdown including violations; the frontend renders
    feasibility status as a badge. This matters because the seed itself
    can have a hard violation that NSGA-II's decision space (movable
    components only) cannot resolve — penalising would collapse the
    front to empty.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
from pymoo.algorithms.moo.nsga2 import NSGA2
from pymoo.core.problem import Problem
from pymoo.operators.crossover.sbx import SBX
from pymoo.operators.mutation.pm import PM
from pymoo.operators.sampling.lhs import LHS
from pymoo.optimize import minimize
from pymoo.util.ref_dirs import get_reference_directions  # noqa: F401  (future NSGA-III)

from app.schemas.layout import LayoutProposal, PlacedComponent, ScoreBreakdown
from app.schemas.robot import RobotSpec
from app.schemas.workcell import WorkcellSpec
from app.services.scoring import score_layout

MOVABLE_TYPES: set[str] = {"conveyor", "pallet", "operator_zone"}
# Pairs that must not bbox-overlap. operator_zone is allowed to touch the
# fence, since the scoring already treats fence-zone proximity as soft.
NO_OVERLAP_TYPES: set[str] = {"robot", "conveyor", "pallet"}


def _bbox_for(c: PlacedComponent) -> tuple[float, float, float, float]:
    """(min_x, min_y, max_x, max_y) AABB for a component, ignoring yaw rotation."""
    if c.type == "robot":
        r = float(c.dims.get("base_radius_mm", 350))
        return c.x_mm - r, c.y_mm - r, c.x_mm + r, c.y_mm + r
    if c.type == "conveyor":
        length = float(c.dims.get("length_mm", 0))
        width = float(c.dims.get("width_mm", 0))
        is_vertical = abs(((c.yaw_deg % 180.0) + 180.0) % 180.0 - 90.0) < 1e-3
        w, h = (width, length) if is_vertical else (length, width)
        return c.x_mm, c.y_mm, c.x_mm + w, c.y_mm + h
    if c.type == "pallet":
        w = float(c.dims.get("length_mm", 1200))
        h = float(c.dims.get("width_mm", 800))
        return c.x_mm, c.y_mm, c.x_mm + w, c.y_mm + h
    if c.type == "operator_zone":
        w = float(c.dims.get("width_mm", 1500))
        h = float(c.dims.get("depth_mm", 1500))
        return c.x_mm, c.y_mm, c.x_mm + w, c.y_mm + h
    return c.x_mm, c.y_mm, c.x_mm, c.y_mm


def _overlap_area(a: PlacedComponent, b: PlacedComponent) -> float:
    """AABB overlap area in mm². 0 if no overlap."""
    ax0, ay0, ax1, ay1 = _bbox_for(a)
    bx0, by0, bx1, by1 = _bbox_for(b)
    dx = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    dy = max(0.0, min(ay1, by1) - max(ay0, by0))
    return dx * dy


@dataclass
class NSGAStats:
    n_evaluations: int = 0
    n_generations: int = 0
    n_pareto: int = 0
    n_feasible: int = 0
    walltime_s: float = 0.0
    hypervolume: float | None = None
    # Per-generation min/avg of each objective (for a convergence chart).
    history: list[dict[str, float]] = field(default_factory=list)


@dataclass
class NSGAResult:
    pareto_proposals: list[LayoutProposal]
    pareto_scores: list[ScoreBreakdown]
    seed_proposal: LayoutProposal
    seed_score: ScoreBreakdown
    stats: NSGAStats


class _LayoutProblem(Problem):
    """pymoo Problem that rebuilds a LayoutProposal from a real-valued vector
    of component (x, y) positions and scores it via the existing scoring
    pipeline. Each row of `X` is one individual; we evaluate them serially.
    pymoo will call this once per generation with the whole population.
    """

    def __init__(
        self,
        seed_proposal: LayoutProposal,
        spec: WorkcellSpec,
        robot_spec: RobotSpec | list[RobotSpec] | None,
    ):
        self.seed_proposal = seed_proposal
        self.spec = spec
        self.robot_spec = robot_spec
        self.cell_w, self.cell_h = spec.cell_envelope_mm

        self.movable_indices = [
            i for i, c in enumerate(seed_proposal.components) if c.type in MOVABLE_TYPES
        ]
        n_movable = len(self.movable_indices)
        n_var = 2 * n_movable  # (x, y) per movable

        # Pre-compute the index pairs we constrain on non-overlap. Robot is
        # fixed but participates in the pairing — moving a pallet onto the
        # robot is what we're guarding against most.
        no_overlap_indices = [
            i for i, c in enumerate(seed_proposal.components)
            if c.type in NO_OVERLAP_TYPES
        ]
        self.constraint_pairs: list[tuple[int, int]] = [
            (a, b)
            for i, a in enumerate(no_overlap_indices)
            for b in no_overlap_indices[i + 1:]
        ]
        n_constr = len(self.constraint_pairs)

        # Per-variable bounds.
        xl = np.zeros(n_var, dtype=float)
        xu = np.tile([self.cell_w, self.cell_h], n_movable).astype(float)

        super().__init__(n_var=n_var, n_obj=3, n_constr=n_constr, xl=xl, xu=xu)
        self.n_evaluations = 0

    def seed_vector(self) -> np.ndarray:
        """Pack the seed proposal's current movable positions into a decision
        vector. Used to inject the seed as one initial population member so
        the front spans (seed → explored) instead of starting from random."""
        v = np.zeros(self.n_var, dtype=float)
        for k, comp_idx in enumerate(self.movable_indices):
            comp = self.seed_proposal.components[comp_idx]
            v[2 * k] = float(comp.x_mm)
            v[2 * k + 1] = float(comp.y_mm)
        return v

    def _vector_to_proposal(self, x: np.ndarray) -> LayoutProposal:
        new_components = list(self.seed_proposal.components)
        for k, comp_idx in enumerate(self.movable_indices):
            comp = new_components[comp_idx]
            new_x = float(x[2 * k])
            new_y = float(x[2 * k + 1])
            new_components[comp_idx] = comp.model_copy(update={"x_mm": new_x, "y_mm": new_y})
        return self.seed_proposal.model_copy(update={"components": new_components})

    def _score_to_objectives(self, sb: ScoreBreakdown) -> tuple[float, float, float]:
        """Pack 3 sub-scores into pymoo's minimisation form. Sub-scores
        stay honest regardless of violations — see module docstring for
        why we don't penalise here."""
        return (
            1.0 - sb.compactness,
            1.0 - sb.reach_margin,
            1.0 - sb.throughput_feasibility,
        )

    def _evaluate(self, X: np.ndarray, out: dict, *args, **kwargs) -> None:
        objs = np.zeros((X.shape[0], 3), dtype=float)
        n_g = max(1, len(self.constraint_pairs))
        cons = np.zeros((X.shape[0], n_g), dtype=float)
        for i, x in enumerate(X):
            cand = self._vector_to_proposal(x)
            sb = score_layout(cand, self.spec, self.robot_spec)
            objs[i] = self._score_to_objectives(sb)
            # G ≤ 0 means feasible. We use overlap area in m² so the magnitudes
            # are O(1) and pymoo's constraint-handling stays well-scaled
            # against the [0,1] objectives.
            for j, (a_idx, b_idx) in enumerate(self.constraint_pairs):
                area_mm2 = _overlap_area(
                    cand.components[a_idx], cand.components[b_idx]
                )
                cons[i, j] = area_mm2 / 1_000_000.0  # m²
            self.n_evaluations += 1
        out["F"] = objs
        if self.constraint_pairs:
            out["G"] = cons


class NSGAIIOptimizer:
    """Real-coded NSGA-II with SBX + polynomial mutation. Defaults are
    appropriate for ~30 vars, fast `score_layout` calls, demo time budget.
    """

    def __init__(
        self,
        population_size: int = 32,
        n_generations: int = 30,
        seed: int | None = None,
    ) -> None:
        self.population_size = population_size
        self.n_generations = n_generations
        self.seed = seed

    def optimize(
        self,
        seed_proposal: LayoutProposal,
        spec: WorkcellSpec,
        robot_spec: RobotSpec | list[RobotSpec] | None,
    ) -> NSGAResult:
        problem = _LayoutProblem(seed_proposal, spec, robot_spec)

        # If there's nothing to move, just score the seed and return.
        if problem.n_var == 0:
            sb = score_layout(seed_proposal, spec, robot_spec)
            return NSGAResult(
                pareto_proposals=[seed_proposal],
                pareto_scores=[sb],
                seed_proposal=seed_proposal,
                seed_score=sb,
                stats=NSGAStats(
                    n_evaluations=1, n_generations=0, n_pareto=1, n_feasible=1,
                    walltime_s=0.0,
                ),
            )

        # Seed-aware initial population: LHS for diversity, but slot the
        # seed proposal's actual decision vector into row 0 so NSGA-II
        # always knows about the human-chosen baseline. Without this, LHS
        # tends to drive the front to corners (e.g. compactness=high but
        # reach=0 by parking pallets in the corner away from the robot).
        rng = np.random.default_rng(self.seed)
        lhs = LHS().do(problem, self.population_size).get("X")
        # Force row 0 to be the seed.
        lhs[0] = problem.seed_vector()
        # Sprinkle ±200 mm jitter around the seed for a few more rows so the
        # local basin gets explored before the LHS exploration kicks in.
        n_jitter = max(1, self.population_size // 8)
        for i in range(1, n_jitter + 1):
            jitter = rng.normal(0.0, 200.0, size=problem.n_var)
            v = problem.seed_vector() + jitter
            lhs[i] = np.clip(v, problem.xl, problem.xu)

        algorithm = NSGA2(
            pop_size=self.population_size,
            sampling=lhs,
            crossover=SBX(prob=0.9, eta=15),
            mutation=PM(prob=1.0 / problem.n_var, eta=20),
            eliminate_duplicates=True,
        )

        t0 = time.perf_counter()
        res = minimize(
            problem,
            algorithm,
            ("n_gen", self.n_generations),
            seed=self.seed,
            verbose=False,
            save_history=True,
        )
        walltime = time.perf_counter() - t0

        # Rebuild proposals + score for every Pareto-optimal individual.
        pareto_proposals: list[LayoutProposal] = []
        pareto_scores: list[ScoreBreakdown] = []
        if res.X is not None and len(res.X) > 0:
            X = res.X if res.X.ndim == 2 else np.atleast_2d(res.X)
            for i, x in enumerate(X):
                proposal = problem._vector_to_proposal(x)
                # Tag the variant so the UI can distinguish NSGA outputs.
                proposal = proposal.model_copy(update={
                    "proposal_id": f"{seed_proposal.proposal_id}_nsga_{i}",
                    "rationale": f"{seed_proposal.rationale} (NSGA-II Pareto #{i+1})",
                })
                sb = score_layout(proposal, spec, robot_spec)
                pareto_proposals.append(proposal)
                pareto_scores.append(sb)

        # Per-generation history for a convergence panel.
        history: list[dict[str, float]] = []
        if res.history:
            for gen_i, gen in enumerate(res.history):
                F = gen.pop.get("F")
                if F is None or len(F) == 0:
                    continue
                history.append({
                    "generation": gen_i,
                    "min_f1": float(F[:, 0].min()),
                    "min_f2": float(F[:, 1].min()),
                    "min_f3": float(F[:, 2].min()),
                    "avg_f1": float(F[:, 0].mean()),
                    "avg_f2": float(F[:, 1].mean()),
                    "avg_f3": float(F[:, 2].mean()),
                })

        seed_score = score_layout(seed_proposal, spec, robot_spec)

        n_feasible = sum(
            1 for sb in pareto_scores
            if not any(v.severity == "hard" for v in sb.violations)
        )
        stats = NSGAStats(
            n_evaluations=problem.n_evaluations,
            n_generations=self.n_generations,
            n_pareto=len(pareto_proposals),
            n_feasible=n_feasible,
            walltime_s=walltime,
            history=history,
        )

        return NSGAResult(
            pareto_proposals=pareto_proposals,
            pareto_scores=pareto_scores,
            seed_proposal=seed_proposal,
            seed_score=seed_score,
            stats=stats,
        )
