"""Layout scoring — five components, aggregated into a single 0-1 score.

Hard/soft discipline (CLAUDE.md):
- HARD violations (unreachable target, ISO 13855 fence breach, AABB overlap)
  zero the aggregate score and populate `violations` with severity="hard".
- SOFT components (compactness, cycle efficiency, throughput feasibility)
  contribute proportional sub-scores.

Normalization:
- Reach + safety use sigmoid (saturates: more clearance ≠ better past a knee).
- Compactness + throughput use min-max linear (genuinely linear-better).

All math in mm; trapezoidal motion profile per CLAUDE.md / layout.py.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

from app.schemas.layout import LayoutProposal, PlacedComponent, ScoreBreakdown, Violation
from app.schemas.robot import RobotSpec
from app.schemas.workcell import WorkcellSpec
from app.services.layout import (
    A_MAX_MM_S2_4AXIS,
    SIX_AXIS_DERATE,
    V_MAX_MM_S_4AXIS,
    estimate_cycle_time_s,
    iso13855_safety_distance_mm,
    trapezoidal_time_s,
)

DEFAULT_WEIGHTS: dict[str, float] = {
    "compactness": 0.20,
    "reach_margin": 0.30,
    "cycle_efficiency": 0.20,
    "safety_clearance": 0.30,
    "throughput_feasibility": 0.0,  # rolled into cycle_efficiency in default weighting
}

# Sigmoid knees (per the prompt pack).
SAFETY_K = 0.005
SAFETY_X0 = 500.0
REACH_K = 0.003
REACH_X0 = 300.0


# ---------------------------------------------------------------------------
# Geometry helpers (axis-aligned; ignore yaw for Phase 4)
# ---------------------------------------------------------------------------


@dataclass
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


def _bbox_for(c: PlacedComponent) -> Rect:
    if c.type == "robot":
        r = float(c.dims.get("base_radius_mm", 350))
        return Rect(c.x_mm - r, c.y_mm - r, 2 * r, 2 * r)
    if c.type == "conveyor":
        length = float(c.dims.get("length_mm", 0))
        width = float(c.dims.get("width_mm", 0))
        is_vertical = abs(((c.yaw_deg % 180.0) + 180.0) % 180.0 - 90.0) < 1e-3
        return (
            Rect(c.x_mm, c.y_mm, width, length)
            if is_vertical
            else Rect(c.x_mm, c.y_mm, length, width)
        )
    if c.type == "pallet":
        return Rect(
            c.x_mm,
            c.y_mm,
            float(c.dims.get("length_mm", 1200)),
            float(c.dims.get("width_mm", 800)),
        )
    if c.type == "operator_zone":
        return Rect(
            c.x_mm,
            c.y_mm,
            float(c.dims.get("width_mm", 1500)),
            float(c.dims.get("depth_mm", 1500)),
        )
    return Rect(c.x_mm, c.y_mm, 0.0, 0.0)


def _aabb_overlap(a: Rect, b: Rect) -> bool:
    return not (a.x + a.w <= b.x or b.x + b.w <= a.x or a.y + a.h <= b.y or b.y + b.h <= a.y)


def _sigmoid(x: float, k: float, x0: float) -> float:
    """Standard logistic. As x → +∞ → 1, as x → -∞ → 0, x0 = 0.5."""
    z = -k * (x - x0)
    if z > 50:
        return 0.0
    if z < -50:
        return 1.0
    return 1.0 / (1.0 + math.exp(z))


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


# ---------------------------------------------------------------------------
# Component scoring
# ---------------------------------------------------------------------------


def score_compactness(proposal: LayoutProposal, spec: WorkcellSpec) -> float:
    """Bounding-box utilization with a perimeter penalty.

    score = util · (1 - perimeter_penalty)
        util = sum(component_areas) / bbox_area  (capped 1.0)
        perim_penalty = (perimeter² / area − 16) / 32  (square baseline = 16, capped 1)
    Returns a value in [0, 1].
    """
    bodies = [c for c in proposal.components if c.type != "fence"]
    if not bodies:
        return 0.0
    rects = [_bbox_for(c) for c in bodies]
    xs = [r.x for r in rects] + [r.x + r.w for r in rects]
    ys = [r.y for r in rects] + [r.y + r.h for r in rects]
    bbox_w = max(xs) - min(xs)
    bbox_h = max(ys) - min(ys)
    bbox_area = bbox_w * bbox_h
    if bbox_area <= 0:
        return 0.0
    body_area = sum(r.w * r.h for r in rects)
    util = _clamp(body_area / bbox_area)
    # Cell envelope penalty — wasted floor that *isn't* inside our bbox.
    cell_w, cell_h = spec.cell_envelope_mm
    cell_area = cell_w * cell_h
    envelope_use = _clamp(bbox_area / cell_area)
    # Aspect-ratio penalty: square == best.
    perimeter = 2 * (bbox_w + bbox_h)
    perim_ratio = (perimeter * perimeter) / bbox_area  # square = 16
    aspect_penalty = _clamp((perim_ratio - 16.0) / 32.0)
    return _clamp(0.6 * util + 0.4 * envelope_use - 0.2 * aspect_penalty)


def _reach_targets(proposal: LayoutProposal) -> list[tuple[str, float, float]]:
    """Pick/place targets the robot must reach: conveyor tip + pallet centers."""
    targets: list[tuple[str, float, float]] = []
    for c in proposal.components:
        if c.type == "conveyor":
            length = float(c.dims.get("length_mm", 0))
            width = float(c.dims.get("width_mm", 0))
            is_vertical = abs(((c.yaw_deg % 180.0) + 180.0) % 180.0 - 90.0) < 1e-3
            if is_vertical:
                # Pick at top of belt (largest y).
                tx = c.x_mm + width / 2
                ty = c.y_mm + length
            else:
                # Pick at far end along +x.
                tx = c.x_mm + length
                ty = c.y_mm + width / 2
            targets.append((c.id, tx, ty))
        elif c.type == "pallet":
            length = float(c.dims.get("length_mm", 1200))
            width = float(c.dims.get("width_mm", 800))
            targets.append((c.id, c.x_mm + length / 2, c.y_mm + width / 2))
    return targets


def _robot_center(proposal: LayoutProposal) -> tuple[float, float] | None:
    robot = next((c for c in proposal.components if c.type == "robot"), None)
    if robot is None:
        return None
    return robot.x_mm, robot.y_mm


def score_reach_margin(
    proposal: LayoutProposal, spec: WorkcellSpec, robot_spec: RobotSpec | None
) -> dict:
    """Signed margin = effective_reach - distance_to_target.
    Negative = HARD violation (target unreachable).
    Score: sigmoid of min margin around REACH_X0.
    """
    if robot_spec is None:
        return {
            "score": 0.0,
            "min_margin_mm": -math.inf,
            "target_margins": [],
            "violations": [
                Violation(
                    kind="unreachable",
                    severity="hard",
                    component_ids=[],
                    message="No robot selected — cannot compute reach margin.",
                    margin_mm=None,
                )
            ],
        }
    rc = _robot_center(proposal)
    if rc is None:
        return {
            "score": 0.0,
            "min_margin_mm": -math.inf,
            "target_margins": [],
            "violations": [
                Violation(
                    kind="unreachable",
                    severity="hard",
                    component_ids=[],
                    message="Layout has no robot component.",
                    margin_mm=None,
                )
            ],
        }
    rx, ry = rc
    eff = robot_spec.effective_max_reach_mm
    margins: list[tuple[str, float]] = []
    violations: list[Violation] = []
    targets = _reach_targets(proposal)
    if not targets:
        return {"score": 0.5, "min_margin_mm": 0.0, "target_margins": [], "violations": []}
    for tid, tx, ty in targets:
        d = math.hypot(tx - rx, ty - ry)
        margin = eff - d
        margins.append((tid, margin))
        if margin < 0:
            violations.append(
                Violation(
                    kind="unreachable",
                    severity="hard",
                    component_ids=[tid],
                    message=(
                        f"Target {tid} is {-margin:.0f} mm beyond effective reach "
                        f"({eff:.0f} mm)."
                    ),
                    margin_mm=margin,
                )
            )
    min_margin = min(m for _, m in margins)
    if min_margin < 0:
        score = 0.0
    else:
        score = _sigmoid(min_margin, REACH_K, REACH_X0)
    return {
        "score": _clamp(score),
        "min_margin_mm": min_margin,
        "target_margins": margins,
        "violations": violations,
    }


def score_cycle_efficiency(
    proposal: LayoutProposal, robot_spec: RobotSpec | None, target_uph: float
) -> dict:
    """Use trapezoidal estimate at the actual robot↔target distance, not just std cycle.

    Score = cycle_floor / actual_cycle, capped at 1.
    """
    if robot_spec is None or _robot_center(proposal) is None:
        return {"score": 0.0, "estimated_cycle_s": 0.0, "estimated_uph": 0.0}
    rx, ry = _robot_center(proposal)  # type: ignore[misc]
    targets = _reach_targets(proposal)
    if not targets:
        return {"score": 0.0, "estimated_cycle_s": 0.0, "estimated_uph": 0.0}

    derate = SIX_AXIS_DERATE if robot_spec.axes == 6 else 1.0
    v = V_MAX_MM_S_4AXIS * derate
    a = A_MAX_MM_S2_4AXIS * derate

    # Average pick→place→home loop: 2× the average target distance.
    avg_d = sum(math.hypot(tx - rx, ty - ry) for _, tx, ty in targets) / len(targets)
    motion_s = trapezoidal_time_s(2 * avg_d, v, a)
    cycle_s = motion_s + 0.8  # pick + place dwell
    # Respect cph_std as a floor (manufacturer-tuned best case).
    cycle_s = max(cycle_s, 3600.0 / robot_spec.cycles_per_hour_std)
    n_pallets = sum(1 for c in proposal.components if c.type == "pallet")
    if n_pallets >= 2 or proposal.template == "dual_pallet":
        cycle_s = cycle_s / (2.0 * 0.95)
    uph = 3600.0 / cycle_s if cycle_s > 0 else 0.0

    # Score: how close UPH gets to target_uph (saturated at 1.1×).
    if target_uph <= 0:
        return {"score": 1.0, "estimated_cycle_s": cycle_s, "estimated_uph": uph}
    ratio = uph / target_uph
    score = _clamp(min(ratio, 1.1) / 1.1)
    # Use the catalogue-tuned cycle as a sanity for the score floor.
    cat_cycle = estimate_cycle_time_s(robot_spec, dual_pallet=(n_pallets >= 2))
    if cycle_s < cat_cycle:
        cycle_s = cat_cycle  # never report a cycle better than catalog
    return {"score": score, "estimated_cycle_s": cycle_s, "estimated_uph": uph}


def score_safety_clearance(proposal: LayoutProposal, spec: WorkcellSpec) -> dict:
    """ISO 13855 separation distance: fence must lie ≥ S_safe outside reach envelope.

    `fence_slack_mm` = min distance from fence polyline to nearest body bbox edge,
    minus S_safe. Negative = hard violation.
    """
    fence = next((c for c in proposal.components if c.type == "fence"), None)
    if fence is None:
        return {
            "score": 0.0,
            "fence_slack_mm": -math.inf,
            "iso13855_pass": False,
            "violations": [
                Violation(
                    kind="iso13855", severity="hard", component_ids=[],
                    message="No safety fence in layout.",
                )
            ],
        }
    has_curtain = any(
        c.type == "fence" and bool(c.dims.get("has_light_curtain", False))
        for c in proposal.components
    ) or any(
        # Spec-level hint
        getattr(comp, "has_light_curtain", False)
        for comp in spec.components
        if comp.type == "fence"
    )
    s_safe = iso13855_safety_distance_mm(has_hard_guard=not has_curtain)
    poly = fence.dims.get("polyline", []) or []
    # Conveyor enters the cell through a muting zone / light curtain in real
    # cells, so ISO 13855 separation does not apply to its body — only the
    # robot + pallets need to stay clear of the safety fence.
    bodies = [
        c for c in proposal.components
        if c.type not in ("fence", "operator_zone", "conveyor")
    ]
    min_slack = math.inf
    for c in bodies:
        rect = _bbox_for(c)
        # Distance from rect EDGE to closest fence segment.
        rect_corners = [
            (rect.x, rect.y),
            (rect.x + rect.w, rect.y),
            (rect.x + rect.w, rect.y + rect.h),
            (rect.x, rect.y + rect.h),
        ]
        d = min(_distance_point_to_polyline(corner, poly) for corner in rect_corners)
        slack = d - s_safe
        if slack < min_slack:
            min_slack = slack
    violations: list[Violation] = []
    if min_slack < 0:
        violations.append(
            Violation(
                kind="iso13855", severity="hard", component_ids=[fence.id],
                message=(
                    f"Fence is only {(s_safe + min_slack):.0f} mm from a body component; "
                    f"ISO 13855 requires ≥ {s_safe:.0f} mm (S = K·T + C)."
                ),
                margin_mm=min_slack,
            )
        )
        score = 0.0
    else:
        # Sigmoid on positive slack.
        score = _sigmoid(min_slack, SAFETY_K, SAFETY_X0)
    return {
        "score": _clamp(score),
        "fence_slack_mm": min_slack if min_slack != math.inf else 0.0,
        "iso13855_pass": min_slack >= 0,
        "violations": violations,
    }


def _distance_point_to_polyline(p: tuple[float, float], poly: list[list[float]]) -> float:
    if len(poly) < 2:
        return math.inf
    best = math.inf
    px, py = p
    for i in range(len(poly) - 1):
        ax, ay = poly[i]
        bx, by = poly[i + 1]
        dx, dy = bx - ax, by - ay
        ll2 = dx * dx + dy * dy
        if ll2 == 0:
            t = 0.0
        else:
            t = ((px - ax) * dx + (py - ay) * dy) / ll2
            t = max(0.0, min(1.0, t))
        cx, cy = ax + t * dx, ay + t * dy
        best = min(best, math.hypot(px - cx, py - cy))
    return best


def score_throughput_feasibility(estimated_uph: float, target_uph: float) -> float:
    """UPH_estimated / UPH_target, saturated at 1.1."""
    if target_uph <= 0:
        return 1.0
    ratio = estimated_uph / target_uph
    return _clamp(min(ratio, 1.1) / 1.1)


def _check_overlaps(proposal: LayoutProposal) -> list[Violation]:
    """All non-fence, non-operator bodies must have disjoint AABBs."""
    bodies = [
        c for c in proposal.components
        if c.type not in ("fence", "operator_zone")
    ]
    violations: list[Violation] = []
    for i, ci in enumerate(bodies):
        ri = _bbox_for(ci)
        for cj in bodies[i + 1 :]:
            rj = _bbox_for(cj)
            if _aabb_overlap(ri, rj):
                violations.append(
                    Violation(
                        kind="overlap", severity="hard", component_ids=[ci.id, cj.id],
                        message=f"{ci.id} and {cj.id} bounding boxes overlap.",
                    )
                )
    return violations


def _check_envelope(
    proposal: LayoutProposal, spec: WorkcellSpec
) -> list[Violation]:
    cw, ch = spec.cell_envelope_mm
    violations: list[Violation] = []
    for c in proposal.components:
        if c.type == "fence":
            continue
        r = _bbox_for(c)
        if r.x < -1e-3 or r.y < -1e-3 or r.x + r.w > cw + 1e-3 or r.y + r.h > ch + 1e-3:
            violations.append(
                Violation(
                    kind="outside_envelope", severity="hard", component_ids=[c.id],
                    message=f"{c.id} extends outside cell envelope ({cw:.0f}×{ch:.0f} mm).",
                )
            )
    return violations


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


def score_layout(
    proposal: LayoutProposal,
    spec: WorkcellSpec,
    robot_spec: RobotSpec | None,
    weights: dict[str, float] | None = None,
) -> ScoreBreakdown:
    """Aggregate the five components. Hard violations zero the aggregate."""
    w = {**DEFAULT_WEIGHTS, **(weights or {})}

    compactness = score_compactness(proposal, spec)
    reach = score_reach_margin(proposal, spec, robot_spec)
    cycle = score_cycle_efficiency(proposal, robot_spec, spec.throughput.cases_per_hour_target)
    safety = score_safety_clearance(proposal, spec)
    throughput = score_throughput_feasibility(
        cycle["estimated_uph"], spec.throughput.cases_per_hour_target
    )

    violations: list[Violation] = []
    violations.extend(reach["violations"])
    violations.extend(safety["violations"])
    violations.extend(_check_overlaps(proposal))
    violations.extend(_check_envelope(proposal, spec))

    has_hard = any(v.severity == "hard" for v in violations)
    sub_scores = {
        "compactness": compactness,
        "reach_margin": float(reach["score"]),
        "cycle_efficiency": float(cycle["score"]),
        "safety_clearance": float(safety["score"]),
        "throughput_feasibility": throughput,
    }
    if has_hard:
        aggregate = 0.0
    else:
        total_w = sum(max(0.0, v) for v in w.values()) or 1.0
        # Combine throughput into cycle if its weight is 0 (default).
        if w.get("throughput_feasibility", 0.0) <= 0:
            cycle_combined = (sub_scores["cycle_efficiency"] + throughput) / 2.0
            aggregate = (
                w["compactness"] * compactness
                + w["reach_margin"] * sub_scores["reach_margin"]
                + w["cycle_efficiency"] * cycle_combined
                + w["safety_clearance"] * sub_scores["safety_clearance"]
            ) / total_w
        else:
            aggregate = sum(w[k] * sub_scores[k] for k in sub_scores) / total_w

    return ScoreBreakdown(
        compactness=_clamp(compactness),
        reach_margin=_clamp(sub_scores["reach_margin"]),
        cycle_efficiency=_clamp(sub_scores["cycle_efficiency"]),
        safety_clearance=_clamp(sub_scores["safety_clearance"]),
        throughput_feasibility=_clamp(throughput),
        aggregate=_clamp(aggregate),
        violations=violations,
        weights=dict(w),
    )


def violations_summary(violations: Iterable[Violation]) -> dict[str, int]:
    hard = sum(1 for v in violations if v.severity == "hard")
    soft = sum(1 for v in violations if v.severity == "soft")
    return {"hard": hard, "soft": soft, "total": hard + soft}
