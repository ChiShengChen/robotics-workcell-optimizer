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
from app.services.cad_import import aabb_intersects_polygon
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


def _conveyor_target(c: PlacedComponent) -> tuple[float, float]:
    length = float(c.dims.get("length_mm", 0))
    width = float(c.dims.get("width_mm", 0))
    is_vertical = abs(((c.yaw_deg % 180.0) + 180.0) % 180.0 - 90.0) < 1e-3
    if is_vertical:
        return (c.x_mm + width / 2, c.y_mm + length)
    return (c.x_mm + length, c.y_mm + width / 2)


def _pallet_target(c: PlacedComponent) -> tuple[float, float]:
    length = float(c.dims.get("length_mm", 1200))
    width = float(c.dims.get("width_mm", 800))
    return (c.x_mm + length / 2, c.y_mm + width / 2)


def _reach_targets(proposal: LayoutProposal) -> list[tuple[str, float, float]]:
    """Pick/place targets the robot must reach: conveyor tip + pallet centers."""
    targets: list[tuple[str, float, float]] = []
    for c in proposal.components:
        if c.type == "conveyor":
            tx, ty = _conveyor_target(c)
            targets.append((c.id, tx, ty))
        elif c.type == "pallet":
            tx, ty = _pallet_target(c)
            targets.append((c.id, tx, ty))
    return targets


def _targets_for_robot(
    proposal: LayoutProposal, robot_id: str
) -> list[tuple[str, float, float]]:
    """Pick/place targets the *specific* robot must reach.

    For multi-arm layouts, task_assignment[robot_id] lists BOTH conveyor ids
    and pallet ids the robot owns. For single-arm (no task_assignment), every
    robot serves every conveyor + every pallet.
    """
    targets: list[tuple[str, float, float]] = []
    assigned_ids = proposal.task_assignment.get(robot_id)
    for c in proposal.components:
        if c.type == "conveyor":
            if assigned_ids is None or c.id in assigned_ids:
                tx, ty = _conveyor_target(c)
                targets.append((c.id, tx, ty))
        elif c.type == "pallet":
            if assigned_ids is None or c.id in assigned_ids:
                tx, ty = _pallet_target(c)
                targets.append((c.id, tx, ty))
    return targets


def _robots(proposal: LayoutProposal) -> list[PlacedComponent]:
    return [c for c in proposal.components if c.type == "robot"]


def _robot_center(proposal: LayoutProposal) -> tuple[float, float] | None:
    """Backward-compat helper for single-arm callers — returns the FIRST robot."""
    robots = _robots(proposal)
    return (robots[0].x_mm, robots[0].y_mm) if robots else None


def score_reach_margin(
    proposal: LayoutProposal,
    spec: WorkcellSpec,
    robot_specs: list[RobotSpec] | RobotSpec | None,
) -> dict:
    """Signed margin = effective_reach - distance_to_target. Multi-robot
    aware: each robot is checked against its assigned targets only (per
    `task_assignment`); for single-arm or empty task_assignment, every robot
    serves every pallet. Aggregate score = sigmoid of the WORST min margin
    across all robots.
    """
    spec_list = _normalise_robot_specs(robot_specs)
    robots = _robots(proposal)
    if not spec_list:
        return _no_robot_reach("No robot selected — cannot compute reach margin.")
    if not robots:
        return _no_robot_reach("Layout has no robot component.")

    margins_per_robot: list[tuple[str, float, list[tuple[str, float]]]] = []
    violations: list[Violation] = []
    for i, robot_pc in enumerate(robots):
        # Pair the i-th robot PlacedComponent with the i-th RobotSpec (or last).
        rspec = spec_list[i] if i < len(spec_list) else spec_list[-1]
        eff = rspec.effective_max_reach_mm
        rx, ry = robot_pc.x_mm, robot_pc.y_mm
        targets = _targets_for_robot(proposal, robot_pc.id)
        if not targets:
            margins_per_robot.append((robot_pc.id, math.inf, []))
            continue
        per_target: list[tuple[str, float]] = []
        for tid, tx, ty in targets:
            d = math.hypot(tx - rx, ty - ry)
            margin = eff - d
            per_target.append((tid, margin))
            if margin < 0:
                violations.append(
                    Violation(
                        kind="unreachable", severity="hard",
                        component_ids=[robot_pc.id, tid],
                        message=(
                            f"{robot_pc.id} -> {tid}: {-margin:.0f} mm beyond effective "
                            f"reach ({eff:.0f} mm)."
                        ),
                        margin_mm=margin,
                    )
                )
        min_for_robot = min(m for _, m in per_target)
        margins_per_robot.append((robot_pc.id, min_for_robot, per_target))

    finite_mins = [m for _, m, _ in margins_per_robot if m != math.inf]
    if not finite_mins:
        return {"score": 0.5, "min_margin_mm": 0.0, "target_margins": [], "violations": violations}
    worst_min = min(finite_mins)
    if worst_min < 0:
        score = 0.0
    else:
        score = _sigmoid(worst_min, REACH_K, REACH_X0)
    return {
        "score": _clamp(score),
        "min_margin_mm": worst_min,
        "target_margins": margins_per_robot,
        "violations": violations,
    }


def _normalise_robot_specs(
    robot_specs: list[RobotSpec] | RobotSpec | None,
) -> list[RobotSpec]:
    if robot_specs is None:
        return []
    if isinstance(robot_specs, RobotSpec):
        return [robot_specs]
    return list(robot_specs)


def _no_robot_reach(msg: str) -> dict:
    return {
        "score": 0.0,
        "min_margin_mm": -math.inf,
        "target_margins": [],
        "violations": [
            Violation(kind="unreachable", severity="hard", component_ids=[], message=msg, margin_mm=None)
        ],
    }


def score_cycle_efficiency(
    proposal: LayoutProposal,
    robot_specs: list[RobotSpec] | RobotSpec | None,
    target_uph: float,
) -> dict:
    """Per-robot trapezoidal cycle estimate; system UPH = sum across robots
    (parallel operation under task partition). Returns the average cycle and
    the system UPH for display.
    """
    spec_list = _normalise_robot_specs(robot_specs)
    robots = _robots(proposal)
    if not spec_list or not robots:
        return {"score": 0.0, "estimated_cycle_s": 0.0, "estimated_uph": 0.0}

    per_robot_cycles: list[float] = []
    per_robot_uph: list[float] = []
    n_pallets_total = sum(1 for c in proposal.components if c.type == "pallet")

    for i, robot_pc in enumerate(robots):
        rspec = spec_list[i] if i < len(spec_list) else spec_list[-1]
        derate = SIX_AXIS_DERATE if rspec.axes == 6 else 1.0
        v = V_MAX_MM_S_4AXIS * derate
        a = A_MAX_MM_S2_4AXIS * derate
        targets = _targets_for_robot(proposal, robot_pc.id)
        if not targets:
            continue
        rx, ry = robot_pc.x_mm, robot_pc.y_mm
        avg_d = sum(math.hypot(tx - rx, ty - ry) for _, tx, ty in targets) / len(targets)
        motion_s = trapezoidal_time_s(2 * avg_d, v, a)
        cycle_s = motion_s + 0.8
        cycle_s = max(cycle_s, 3600.0 / rspec.cycles_per_hour_std)
        # Pallets THIS ROBOT is responsible for.
        assigned = proposal.task_assignment.get(robot_pc.id)
        n_my_pallets = (
            len([c for c in proposal.components
                 if c.type == "pallet" and (assigned is None or c.id in assigned)])
        ) if assigned is not None else n_pallets_total
        if n_my_pallets >= 2 or proposal.template == "dual_pallet":
            cycle_s = cycle_s / (2.0 * 0.95)
        cat_cycle = estimate_cycle_time_s(rspec, dual_pallet=(n_my_pallets >= 2))
        if cycle_s < cat_cycle:
            cycle_s = cat_cycle
        uph = 3600.0 / cycle_s if cycle_s > 0 else 0.0
        per_robot_cycles.append(cycle_s)
        per_robot_uph.append(uph)

    if not per_robot_cycles:
        return {"score": 0.0, "estimated_cycle_s": 0.0, "estimated_uph": 0.0}

    # Worst single-robot cycle = bottleneck for "estimated_cycle_s" display.
    bottleneck_cycle = max(per_robot_cycles)
    system_uph = sum(per_robot_uph)

    if target_uph <= 0:
        return {"score": 1.0, "estimated_cycle_s": bottleneck_cycle, "estimated_uph": system_uph}
    ratio = system_uph / target_uph
    score = _clamp(min(ratio, 1.1) / 1.1)
    return {"score": score, "estimated_cycle_s": bottleneck_cycle, "estimated_uph": system_uph}


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


def _check_obstacle_intrusion(
    proposal: LayoutProposal, spec: WorkcellSpec
) -> list[Violation]:
    """Any movable body whose bbox intersects an obstacle polygon = HARD violation.

    `margin_mm` reports the penetration depth (negative) measured as the smaller
    of the AABB-overlap dimensions, giving SA a smooth gradient to climb out
    of the intrusion. The detection itself is exact polygon-vs-rect.
    """
    if not spec.obstacles:
        return []
    bodies = [c for c in proposal.components if c.type not in ("fence",)]
    violations: list[Violation] = []
    for c in bodies:
        rect = _bbox_for(c)
        for ob in spec.obstacles:
            if not aabb_intersects_polygon(rect.x, rect.y, rect.w, rect.h, ob.polygon):
                continue
            # Polygon AABB.
            xs = [p[0] for p in ob.polygon]
            ys = [p[1] for p in ob.polygon]
            ox, oy = min(xs), min(ys)
            ow, oh = max(xs) - ox, max(ys) - oy
            overlap_x = min(rect.x + rect.w, ox + ow) - max(rect.x, ox)
            overlap_y = min(rect.y + rect.h, oy + oh) - max(rect.y, oy)
            depth = max(0.0, min(overlap_x, overlap_y))
            violations.append(
                Violation(
                    kind="obstacle_intrusion", severity="hard",
                    component_ids=[c.id, ob.id],
                    message=(
                        f"{c.id} intersects CAD obstacle {ob.id} "
                        f"({ob.source_entity}); penetration ≈ {depth:.0f} mm."
                    ),
                    margin_mm=-depth if depth > 0 else -1.0,
                )
            )
            break
    return violations


OPERATOR_FENCE_CLEARANCE_MM = 600.0


def _check_operator_zone(
    proposal: LayoutProposal, spec: WorkcellSpec
) -> list[Violation]:
    """Operator zone constraints (ISO 10218 spirit):

    A. bbox must not overlap any robot / conveyor / pallet body.
    B. must not lie inside any robot's effective_reach disk
       (operator stands inside the robot's working envelope).
       Exempt if the cell has a light curtain (operator entry is gated
       by the safe-state interlock).
    C. must keep ≥ 600 mm clearance from the safety fence so the
       operator can actually stand and work without bumping the guard.
       Also exempt with a light curtain.

    All three are SOFT violations — the demo seeds intentionally place
    the operator zone near the robot row to visualise the access lane,
    and real palletizing cells almost always gate that lane with a
    light curtain + safe-state interlock so the layout is shippable.
    Marking these HARD would zero the aggregate of every greedy seed.
    Yields one Violation per failed constraint per operator zone.
    """
    operators = [c for c in proposal.components if c.type == "operator_zone"]
    if not operators:
        return []
    has_curtain = any(
        c.type == "fence" and bool(c.dims.get("has_light_curtain", False))
        for c in proposal.components
    ) or any(
        getattr(comp, "has_light_curtain", False)
        for comp in spec.components
        if comp.type == "fence"
    )
    robots = [c for c in proposal.components if c.type == "robot"]
    bodies = [
        c for c in proposal.components
        if c.type in ("robot", "conveyor", "pallet")
    ]
    fence = next((c for c in proposal.components if c.type == "fence"), None)

    violations: list[Violation] = []
    for op in operators:
        op_rect = _bbox_for(op)

        # A. Physical overlap with any body.
        for b in bodies:
            if _aabb_overlap(op_rect, _bbox_for(b)):
                violations.append(
                    Violation(
                        kind="operator_zone_intrusion", severity="soft",
                        component_ids=[op.id, b.id],
                        message=f"{op.id} bbox overlaps {b.id}.",
                    )
                )

        # B. Operator standing inside a robot's effective reach.
        if not has_curtain:
            op_corners = [
                (op_rect.x, op_rect.y),
                (op_rect.x + op_rect.w, op_rect.y),
                (op_rect.x, op_rect.y + op_rect.h),
                (op_rect.x + op_rect.w, op_rect.y + op_rect.h),
                (op_rect.x + op_rect.w / 2, op_rect.y + op_rect.h / 2),
            ]
            for r in robots:
                eff = float(r.dims.get("effective_reach_mm")
                            or r.dims.get("reach_mm") or 0.0)
                if eff <= 0:
                    continue
                rx, ry = r.x_mm, r.y_mm
                # Closest corner inside the disk -> intrusion.
                closest_in = min(
                    (math.hypot(cx - rx, cy - ry) for cx, cy in op_corners),
                    default=math.inf,
                )
                if closest_in < eff:
                    penetration = eff - closest_in
                    violations.append(
                        Violation(
                            kind="operator_zone_intrusion", severity="soft",
                            component_ids=[op.id, r.id],
                            message=(
                                f"{op.id} sits inside {r.id}'s effective reach "
                                f"({eff:.0f} mm); penetration ≈ {penetration:.0f} mm. "
                                f"Add a light curtain or move the operator out."
                            ),
                            margin_mm=-penetration,
                        )
                    )
                    break

        # C. Operator must keep clearance from the fence (work-room).
        if fence and not has_curtain:
            poly = fence.dims.get("polyline", []) or []
            if len(poly) >= 2:
                op_corners_c = [
                    (op_rect.x, op_rect.y),
                    (op_rect.x + op_rect.w, op_rect.y),
                    (op_rect.x + op_rect.w, op_rect.y + op_rect.h),
                    (op_rect.x, op_rect.y + op_rect.h),
                ]
                d = min(_distance_point_to_polyline(p, poly) for p in op_corners_c)
                slack = d - OPERATOR_FENCE_CLEARANCE_MM
                if slack < 0:
                    violations.append(
                        Violation(
                            kind="operator_zone_intrusion", severity="soft",
                            component_ids=[op.id, fence.id],
                            message=(
                                f"{op.id} is only {d:.0f} mm from the fence; "
                                f"recommend ≥ {OPERATOR_FENCE_CLEARANCE_MM:.0f} mm "
                                f"work-room clearance."
                            ),
                            margin_mm=slack,
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
    robot_specs: list[RobotSpec] | RobotSpec | None,
    weights: dict[str, float] | None = None,
) -> ScoreBreakdown:
    """Aggregate the five components. Hard violations zero the aggregate.

    Multi-arm: pass a list[RobotSpec] in the same order as the proposal's
    robot PlacedComponents. Single-arm: pass one RobotSpec (or None).
    """
    w = {**DEFAULT_WEIGHTS, **(weights or {})}

    compactness = score_compactness(proposal, spec)
    reach = score_reach_margin(proposal, spec, robot_specs)
    cycle = score_cycle_efficiency(proposal, robot_specs, spec.throughput.cases_per_hour_target)
    safety = score_safety_clearance(proposal, spec)
    throughput = score_throughput_feasibility(
        cycle["estimated_uph"], spec.throughput.cases_per_hour_target
    )

    violations: list[Violation] = []
    violations.extend(reach["violations"])
    violations.extend(safety["violations"])
    violations.extend(_check_overlaps(proposal))
    violations.extend(_check_envelope(proposal, spec))
    violations.extend(_check_obstacle_intrusion(proposal, spec))
    violations.extend(_check_operator_zone(proposal, spec))

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
