"""Greedy layout generation: 4 templates, picks cheapest feasible robot per spec.

The neuro-symbolic split lives here: the LLM was responsible for picking the spec
shape, but coordinates are produced by deterministic geometry.

Coordinate convention (CLAUDE.md): origin = lower-left, x → right, y → up, mm.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from typing import Literal

from app.schemas.layout import LayoutProposal, PlacedComponent
from app.schemas.robot import IdealUseCase, RobotSpec
from app.schemas.workcell import Pallet, Robot, WorkcellSpec
from app.services.catalog import RobotCatalogService

Template = Literal["in_line", "L_shape", "U_shape", "dual_pallet"]

# Pallet footprints (mm) for known standards.
PALLET_FOOTPRINTS_MM: dict[str, tuple[float, float]] = {
    "EUR": (1200.0, 800.0),
    "GMA": (1219.0, 1016.0),
    "ISO1": (1200.0, 1000.0),
    "half": (800.0, 600.0),
}

# Defaults when spec leaves things null.
DEFAULT_CASE_MASS_KG = 15.0
DEFAULT_EOAT_MASS_KG = 30.0
DEFAULT_PICK_COUNT = 1
DEFAULT_INFEED_LENGTH_MM = 2500.0
DEFAULT_INFEED_WIDTH_MM = 600.0
DEFAULT_OPERATOR_W_MM = 1500.0
DEFAULT_OPERATOR_D_MM = 1500.0

# ISO 13855 single-handed scenario.
ISO_K_MM_PER_S = 2000.0
ISO_T_S = 0.30
ISO_C_BODY_MM = 850.0
ISO_C_HARD_GUARD_MM = 600.0  # if an interlocked hard guard replaces light curtain

# Trapezoidal motion profile defaults (mm/s, mm/s²).
V_MAX_MM_S_4AXIS = 2500.0
A_MAX_MM_S2_4AXIS = 8000.0
SIX_AXIS_DERATE = 0.85

# Standard 400/2000/400 cycle path length (mm) for single-cycle estimation.
STD_CYCLE_PATH_MM = 2 * (400.0 + 2000.0 + 400.0)  # out-and-back


@dataclass
class _Bounds:
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


def _pallet_dims(pallet: Pallet, fallback_standard: str | None) -> tuple[float, float]:
    if pallet.length_mm and pallet.width_mm:
        return pallet.length_mm, pallet.width_mm
    std = pallet.standard or fallback_standard or "EUR"
    return PALLET_FOOTPRINTS_MM.get(std, PALLET_FOOTPRINTS_MM["EUR"])


def trapezoidal_time_s(distance_mm: float, v_max_mm_s: float, a_mm_s2: float) -> float:
    """t = d/v + v/a if d ≥ v²/a (full trapezoid), else 2·sqrt(d/a) (triangle)."""
    if distance_mm <= 0:
        return 0.0
    threshold = (v_max_mm_s * v_max_mm_s) / a_mm_s2
    if distance_mm >= threshold:
        return distance_mm / v_max_mm_s + v_max_mm_s / a_mm_s2
    return 2.0 * math.sqrt(distance_mm / a_mm_s2)


def estimate_cycle_time_s(robot: RobotSpec, dual_pallet: bool = False) -> float:
    """Standard cycle (400/2000/400) trapezoidal; UPH from cph_std for sanity."""
    v = V_MAX_MM_S_4AXIS * (SIX_AXIS_DERATE if robot.axes == 6 else 1.0)
    a = A_MAX_MM_S2_4AXIS * (SIX_AXIS_DERATE if robot.axes == 6 else 1.0)
    motion_s = trapezoidal_time_s(STD_CYCLE_PATH_MM, v, a)
    # Add 0.4s for pick + 0.4s for place (gripper open/close + settle).
    cycle_s = motion_s + 0.8
    # Sanity-floor against the published cph_std (manufacturers tune for this).
    cycle_floor_s = 3600.0 / robot.cycles_per_hour_std
    cycle_s = max(cycle_s, cycle_floor_s)
    if dual_pallet:
        # η_overlap = 0.95 → effective cycle is 1 / (2·0.95) of single cycle.
        cycle_s = cycle_s / (2.0 * 0.95)
    return cycle_s


def estimate_uph(cycle_time_s: float) -> float:
    return 3600.0 / cycle_time_s if cycle_time_s > 0 else 0.0


def iso13855_safety_distance_mm(has_hard_guard: bool) -> float:
    """S = K·T + C, single-handed body case from ISO 13855."""
    c = ISO_C_HARD_GUARD_MM if has_hard_guard else ISO_C_BODY_MM
    return ISO_K_MM_PER_S * ISO_T_S + c


# ---------------------------------------------------------------------------


class GreedyLayoutGenerator:
    """Produces up to 3 layout proposals using different topology templates."""

    def __init__(self, catalog: RobotCatalogService) -> None:
        self.catalog = catalog

    # -- public API ---------------------------------------------------------

    def generate(self, spec: WorkcellSpec, n_variants: int = 3) -> list[LayoutProposal]:
        templates: list[Template] = ["in_line", "L_shape", "U_shape", "dual_pallet"]
        is_continuous = self._is_continuous_op(spec)
        # Bias dual_pallet to the front when continuous operation is required.
        if is_continuous:
            templates = ["dual_pallet", "L_shape", "in_line", "U_shape"]
        proposals: list[LayoutProposal] = []
        seen_keys: set[str] = set()
        for tpl in templates:
            if len(proposals) >= n_variants:
                break
            robot, robot_assumption = self._pick_robot(spec, dual_pallet=(tpl == "dual_pallet"))
            proposal = self._build_template(spec, tpl, robot, robot_assumption)
            key = f"{tpl}-{proposal.robot_model_id}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            proposals.append(proposal)
        return proposals

    # -- robot selection ----------------------------------------------------

    def _pick_robot(
        self, spec: WorkcellSpec, dual_pallet: bool
    ) -> tuple[RobotSpec | None, str | None]:
        case_mass = spec.case_mass_kg or DEFAULT_CASE_MASS_KG
        required_payload = case_mass * DEFAULT_PICK_COUNT + DEFAULT_EOAT_MASS_KG

        # Preferred model from spec wins if catalog has it.
        preferred = next(
            (
                c.preferred_model
                for c in spec.components
                if isinstance(c, Robot) and c.preferred_model
            ),
            None,
        )
        if preferred:
            try:
                return self.catalog.get_by_id(preferred), None
            except Exception:
                pass

        # Throughput requirement: 10% headroom; halve for dual-pallet (η_overlap≈0.95→~1.9x).
        target_uph = spec.throughput.cases_per_hour_target * 1.10
        if dual_pallet:
            target_uph = target_uph / 1.9

        use_case_filter = None
        if spec.throughput.mixed_sequence:
            use_case_filter = [IdealUseCase.MIXED_SKU]

        # Reach: estimate required reach from envelope diagonal / 3 as a coarse seed.
        cell_w, cell_h = spec.cell_envelope_mm
        rough_reach_required = max(1500.0, math.hypot(cell_w, cell_h) / 3.0)

        candidates = self.catalog.find(
            min_payload_kg=required_payload,
            min_reach_mm=rough_reach_required,
            max_price_usd=spec.budget_usd,
            use_case_filter=use_case_filter,
            min_cycles_per_hour=target_uph,
        )
        if candidates:
            return candidates[0], None

        # Relax budget first, then reach, then payload — note each relaxation.
        relaxations: list[str] = []
        if spec.budget_usd is not None:
            candidates = self.catalog.find(
                min_payload_kg=required_payload,
                min_reach_mm=rough_reach_required,
                use_case_filter=use_case_filter,
                min_cycles_per_hour=target_uph,
            )
            if candidates:
                relaxations.append(
                    f"No robot under ${spec.budget_usd:.0f} satisfies reach/payload/throughput; "
                    f"recommending ${candidates[0].price_usd_low:.0f}-${candidates[0].price_usd_high:.0f}."
                )
                return candidates[0], "; ".join(relaxations)
        # Drop reach requirement.
        candidates = self.catalog.find(
            min_payload_kg=required_payload,
            use_case_filter=use_case_filter,
            min_cycles_per_hour=target_uph,
        )
        if candidates:
            relaxations.append(
                f"Reach target {rough_reach_required:.0f} mm relaxed; cell envelope is small "
                f"relative to typical palletizing reach."
            )
            return candidates[0], "; ".join(relaxations)
        # Drop throughput.
        candidates = self.catalog.find(
            min_payload_kg=required_payload, use_case_filter=use_case_filter,
        )
        if candidates:
            relaxations.append(
                f"Throughput target {target_uph:.0f} UPH not met by any robot at this payload; "
                f"recommending fastest available; expect cycle time below target."
            )
            return candidates[0], "; ".join(relaxations)
        # Nothing works — return None and explain.
        return None, (
            f"No catalog robot satisfies payload >= {required_payload:.0f} kg "
            f"+ throughput {target_uph:.0f} UPH within the cell envelope."
        )

    # -- templates ----------------------------------------------------------

    def _build_template(
        self,
        spec: WorkcellSpec,
        template: Template,
        robot: RobotSpec | None,
        robot_assumption: str | None,
    ) -> LayoutProposal:
        if template == "in_line":
            placed, rationale = self._template_in_line(spec, robot)
        elif template == "L_shape":
            placed, rationale = self._template_l_shape(spec, robot)
        elif template == "U_shape":
            placed, rationale = self._template_u_shape(spec, robot)
        else:
            placed, rationale = self._template_dual_pallet(spec, robot)

        cycle_s = (
            estimate_cycle_time_s(robot, dual_pallet=(template == "dual_pallet"))
            if robot is not None
            else 0.0
        )
        uph = estimate_uph(cycle_s)
        assumptions: list[str] = []
        if robot_assumption:
            assumptions.append(robot_assumption)
        if robot is None:
            assumptions.append("No feasible robot — placeholder layout for visualization only.")

        return LayoutProposal(
            proposal_id=str(uuid.uuid4())[:8],
            template=template,
            robot_model_id=(robot.model if robot else None),
            components=placed,
            cell_bounds_mm=spec.cell_envelope_mm,
            estimated_cycle_time_s=cycle_s,
            estimated_uph=uph,
            rationale=rationale,
            assumptions=assumptions,
        )

    # -- shared placement helpers ------------------------------------------

    def _has_hard_guard(self, spec: WorkcellSpec) -> bool:
        return not any(
            c.type == "fence" and getattr(c, "has_light_curtain", False)
            for c in spec.components
        )

    def _is_continuous_op(self, spec: WorkcellSpec) -> bool:
        # Heuristic: dual-pallet implied by >1 pallet component or high throughput.
        n_pallets = sum(1 for c in spec.components if c.type == "pallet")
        if n_pallets >= 2:
            return True
        if spec.throughput.cases_per_hour_target >= 800:
            return True
        return False

    def _pallet_components(self, spec: WorkcellSpec) -> list[Pallet]:
        return [c for c in spec.components if isinstance(c, Pallet)]

    def _make_robot_placed(
        self, spec: WorkcellSpec, robot: RobotSpec | None, x: float, y: float
    ) -> PlacedComponent:
        base_radius = 350.0 if robot is None else max(robot.footprint_l_mm, robot.footprint_w_mm) / 2
        reach = 2400.0 if robot is None else robot.reach_mm
        return PlacedComponent(
            id="robot_1", type="robot", x_mm=x, y_mm=y, yaw_deg=0.0,
            dims={
                "base_radius_mm": base_radius,
                "reach_mm": reach,
                "effective_reach_mm": reach * 0.85,
                "footprint_l_mm": robot.footprint_l_mm if robot else 700.0,
                "footprint_w_mm": robot.footprint_w_mm if robot else 700.0,
            },
        )

    def _make_conveyor(
        self, anchor_x: float, anchor_y: float, length: float, width: float, yaw_deg: float = 0.0
    ) -> PlacedComponent:
        return PlacedComponent(
            id="conveyor_1", type="conveyor", x_mm=anchor_x, y_mm=anchor_y, yaw_deg=yaw_deg,
            dims={"length_mm": length, "width_mm": width, "role": "infeed"},
        )

    def _make_pallet(
        self, idx: int, x: float, y: float, length: float, width: float, standard: str | None
    ) -> PlacedComponent:
        return PlacedComponent(
            id=f"pallet_{idx}", type="pallet", x_mm=x, y_mm=y, yaw_deg=0.0,
            dims={"length_mm": length, "width_mm": width, "standard": standard or "EUR",
                  "pattern": "interlock"},
        )

    def _make_operator(self, x: float, y: float) -> PlacedComponent:
        return PlacedComponent(
            id="operator_zone_1", type="operator_zone", x_mm=x, y_mm=y, yaw_deg=0.0,
            dims={"width_mm": DEFAULT_OPERATOR_W_MM, "depth_mm": DEFAULT_OPERATOR_D_MM},
        )

    def _make_fence(self, polyline: list[list[float]]) -> PlacedComponent:
        return PlacedComponent(
            id="fence_main", type="fence", x_mm=0.0, y_mm=0.0, yaw_deg=0.0,
            dims={"polyline": polyline, "height_mm": 2000.0,
                  "safety_margin_mm": iso13855_safety_distance_mm(self._has_hard_guard_for_polyline())},
        )

    def _has_hard_guard_for_polyline(self) -> bool:
        # Helper to silence the per-polyline call; default to hard-guard sized.
        return False

    def _fence_polyline(
        self, robot_xy: tuple[float, float], reach_mm: float, cell_bounds: tuple[float, float],
        has_hard_guard: bool,
    ) -> list[list[float]]:
        """Square fence offset = reach + ISO 13855 separation. Clamped to cell bounds."""
        s_safe = iso13855_safety_distance_mm(has_hard_guard)
        margin = reach_mm + s_safe
        rx, ry = robot_xy
        cw, ch = cell_bounds
        x0 = max(0.0, rx - margin)
        y0 = max(0.0, ry - margin)
        x1 = min(cw, rx + margin)
        y1 = min(ch, ry + margin)
        return [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]

    # -- in_line ------------------------------------------------------------

    def _template_in_line(
        self, spec: WorkcellSpec, robot: RobotSpec | None
    ) -> tuple[list[PlacedComponent], str]:
        cw, ch = spec.cell_envelope_mm
        reach = robot.reach_mm if robot else 2400.0
        rx, ry = cw / 2, ch / 2
        placed: list[PlacedComponent] = [self._make_robot_placed(spec, robot, rx, ry)]

        # Infeed conveyor: 0.7·R upstream (left of robot, flowing →+x).
        conv_distance = 0.7 * reach
        conv_x = max(0.0, rx - conv_distance - DEFAULT_INFEED_LENGTH_MM)
        conv_y = ry - DEFAULT_INFEED_WIDTH_MM / 2
        placed.append(self._make_conveyor(conv_x, conv_y, DEFAULT_INFEED_LENGTH_MM, DEFAULT_INFEED_WIDTH_MM))

        pallets = self._pallet_components(spec)
        std = spec.pallet_standard
        if not pallets:
            pl, pw = PALLET_FOOTPRINTS_MM.get(std or "EUR", PALLET_FOOTPRINTS_MM["EUR"])
            pallets_dims = [(pl, pw)]
        else:
            pallets_dims = [_pallet_dims(p, std) for p in pallets[:1]]

        # Single pallet station to the right of robot, in line with infeed.
        pl, pw = pallets_dims[0]
        pal_x = min(cw - pl, rx + 0.7 * reach)
        pal_y = ry - pw / 2
        placed.append(self._make_pallet(1, pal_x, pal_y, pl, pw, std))

        # Operator zone behind the pallet.
        placed.append(self._make_operator(min(cw - DEFAULT_OPERATOR_W_MM, pal_x),
                                          min(ch - DEFAULT_OPERATOR_D_MM, pal_y + pw + 100)))

        polyline = self._fence_polyline((rx, ry), reach, (cw, ch), self._has_hard_guard(spec))
        placed.append(self._make_fence(polyline))

        return placed, "Linear conveyor → robot → single pallet; minimal floor area, simplest cabling."

    # -- L_shape ------------------------------------------------------------

    def _template_l_shape(
        self, spec: WorkcellSpec, robot: RobotSpec | None
    ) -> tuple[list[PlacedComponent], str]:
        cw, ch = spec.cell_envelope_mm
        reach = robot.reach_mm if robot else 2400.0
        rx, ry = cw * 0.4, ch * 0.5
        placed: list[PlacedComponent] = [self._make_robot_placed(spec, robot, rx, ry)]

        # Infeed from below (flow +y).
        conv_x = rx - DEFAULT_INFEED_WIDTH_MM / 2
        conv_y = max(0.0, ry - 0.7 * reach - DEFAULT_INFEED_LENGTH_MM)
        placed.append(self._make_conveyor(conv_x, conv_y, DEFAULT_INFEED_LENGTH_MM,
                                          DEFAULT_INFEED_WIDTH_MM, yaw_deg=90.0))

        std = spec.pallet_standard
        pallets = self._pallet_components(spec)
        if not pallets:
            pl, pw = PALLET_FOOTPRINTS_MM.get(std or "EUR", PALLET_FOOTPRINTS_MM["EUR"])
        else:
            pl, pw = _pallet_dims(pallets[0], std)
        pal_x = min(cw - pl, rx + 0.7 * reach)
        pal_y = ry - pw / 2
        placed.append(self._make_pallet(1, pal_x, pal_y, pl, pw, std))

        placed.append(self._make_operator(min(cw - DEFAULT_OPERATOR_W_MM, pal_x),
                                          min(ch - DEFAULT_OPERATOR_D_MM, pal_y + pw + 100)))

        polyline = self._fence_polyline((rx, ry), reach, (cw, ch), self._has_hard_guard(spec))
        placed.append(self._make_fence(polyline))

        return placed, ("L-shaped: infeed perpendicular to outfeed pallet, "
                        "compact corner footprint with operator access on the open side.")

    # -- U_shape ------------------------------------------------------------

    def _template_u_shape(
        self, spec: WorkcellSpec, robot: RobotSpec | None
    ) -> tuple[list[PlacedComponent], str]:
        cw, ch = spec.cell_envelope_mm
        reach = robot.reach_mm if robot else 2400.0
        rx, ry = cw / 2, ch * 0.45
        placed: list[PlacedComponent] = [self._make_robot_placed(spec, robot, rx, ry)]

        # Infeed from below (flow +y).
        conv_x = rx - DEFAULT_INFEED_WIDTH_MM / 2
        conv_y = max(0.0, ry - 0.7 * reach - DEFAULT_INFEED_LENGTH_MM)
        placed.append(self._make_conveyor(conv_x, conv_y, DEFAULT_INFEED_LENGTH_MM,
                                          DEFAULT_INFEED_WIDTH_MM, yaw_deg=90.0))

        std = spec.pallet_standard
        pallets = self._pallet_components(spec)
        if len(pallets) < 2:
            pl, pw = PALLET_FOOTPRINTS_MM.get(std or "EUR", PALLET_FOOTPRINTS_MM["EUR"])
            dims_list = [(pl, pw), (pl, pw)]
        else:
            dims_list = [_pallet_dims(p, std) for p in pallets[:2]]

        # Two pallets flanking the robot east + west, infeed from south.
        (pl_l, pw_l), (pl_r, pw_r) = dims_list
        left_x = max(0.0, rx - 0.7 * reach - pl_l)
        right_x = min(cw - pl_r, rx + 0.7 * reach)
        placed.append(self._make_pallet(1, left_x, ry - pw_l / 2, pl_l, pw_l, std))
        placed.append(self._make_pallet(2, right_x, ry - pw_r / 2, pl_r, pw_r, std))

        # Operator zone to the north (open side).
        placed.append(self._make_operator(rx - DEFAULT_OPERATOR_W_MM / 2,
                                          min(ch - DEFAULT_OPERATOR_D_MM, ry + 0.7 * reach + 100)))

        polyline = self._fence_polyline((rx, ry), reach, (cw, ch), self._has_hard_guard(spec))
        placed.append(self._make_fence(polyline))

        return placed, ("U-shaped: pallets flank the robot east+west, infeed from south, "
                        "operator on the open north side; balanced reach utilization.")

    # -- dual_pallet --------------------------------------------------------

    def _template_dual_pallet(
        self, spec: WorkcellSpec, robot: RobotSpec | None
    ) -> tuple[list[PlacedComponent], str]:
        cw, ch = spec.cell_envelope_mm
        reach = robot.reach_mm if robot else 2400.0
        rx, ry = cw / 2, ch / 2
        placed: list[PlacedComponent] = [self._make_robot_placed(spec, robot, rx, ry)]

        # Infeed from south (flow +y).
        conv_x = rx - DEFAULT_INFEED_WIDTH_MM / 2
        conv_y = max(0.0, ry - 0.7 * reach - DEFAULT_INFEED_LENGTH_MM)
        placed.append(self._make_conveyor(conv_x, conv_y, DEFAULT_INFEED_LENGTH_MM,
                                          DEFAULT_INFEED_WIDTH_MM, yaw_deg=90.0))

        std = spec.pallet_standard
        pallets = self._pallet_components(spec)
        if len(pallets) < 2:
            pl, pw = PALLET_FOOTPRINTS_MM.get(std or "EUR", PALLET_FOOTPRINTS_MM["EUR"])
            dims_list = [(pl, pw), (pl, pw)]
        else:
            dims_list = [_pallet_dims(p, std) for p in pallets[:2]]

        # Two pallets on opposite sides (east + west) for swap-and-continue operation.
        (pl_l, pw_l), (pl_r, pw_r) = dims_list
        left_x = max(0.0, rx - 0.7 * reach - pl_l)
        right_x = min(cw - pl_r, rx + 0.7 * reach)
        placed.append(self._make_pallet(1, left_x, ry - pw_l / 2, pl_l, pw_l, std))
        placed.append(self._make_pallet(2, right_x, ry - pw_r / 2, pl_r, pw_r, std))

        # Operator zone north — same as U_shape but rationale differs.
        placed.append(self._make_operator(rx - DEFAULT_OPERATOR_W_MM / 2,
                                          min(ch - DEFAULT_OPERATOR_D_MM, ry + 0.7 * reach + 100)))

        polyline = self._fence_polyline((rx, ry), reach, (cw, ch), self._has_hard_guard(spec))
        placed.append(self._make_fence(polyline))

        return placed, ("Dual-pallet swap stations: continuous operation while operator "
                        "exchanges full pallets on the opposite side; ~1.9× single-pallet UPH.")
