"""Layout proposal + scoring schemas.

PlacedComponent uses a flexible `dims` dict so we can encode per-type geometry
(pallet length/width, fence polyline, conveyor length etc.) without exploding
into a separate concrete class for every variant.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PlacedType = Literal[
    "robot", "conveyor", "pallet", "fence", "operator_zone"
]


class PlacedComponent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Stable identifier (matches WorkcellSpec component id).")
    type: PlacedType = Field(description="Component type discriminator.")
    x_mm: float = Field(description="X position of component anchor in mm (cell origin LL).")
    y_mm: float = Field(description="Y position of component anchor in mm.")
    yaw_deg: float = Field(default=0.0, description="Yaw rotation in degrees (counter-clockwise).")
    dims: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Per-type geometry: e.g. pallet {length_mm, width_mm}, fence {polyline: [[x,y],...]}, "
            "conveyor {length_mm, width_mm}, robot {base_radius_mm, reach_mm}."
        ),
    )


class LayoutProposal(BaseModel):
    """One candidate layout. Aggregates components + cycle/UPH estimates."""

    model_config = ConfigDict(extra="forbid")

    proposal_id: str = Field(description="Unique id for this proposal.")
    template: Literal["in_line", "L_shape", "U_shape", "dual_pallet"] = Field(
        description="Topology template used to seed this proposal."
    )
    robot_model_id: str | None = Field(
        description="RobotSpec.model selected; null if no feasible robot was found."
    )
    components: list[PlacedComponent] = Field(
        description="All placed components (robot, conveyor, pallets, fence, operator zone)."
    )
    cell_bounds_mm: tuple[float, float] = Field(
        description="(W, H) of the workcell envelope used."
    )
    estimated_cycle_time_s: float = Field(
        description="Estimated single-cycle time in seconds.", ge=0
    )
    estimated_uph: float = Field(
        description="Estimated units per hour at the target case mass.", ge=0
    )
    rationale: str = Field(description="Short explanation of why this template was chosen.")
    assumptions: list[str] = Field(
        default_factory=list,
        description="Layout-level assumptions (e.g. 'budget relaxed by $25k to find a feasible arm').",
    )


class Violation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "unreachable",
        "overlap",
        "fence_clearance",
        "operator_zone_intrusion",
        "iso13855",
        "outside_envelope",
        "obstacle_intrusion",
    ] = Field(description="Violation category.")
    severity: Literal["hard", "soft"] = Field(description="Hard violations zero the aggregate.")
    component_ids: list[str] = Field(description="Components implicated.")
    message: str = Field(description="Human-readable explanation.")
    margin_mm: float | None = Field(
        default=None,
        description="Signed slack in mm: negative = how far violated, positive = remaining margin.",
    )


class ScoreBreakdown(BaseModel):
    """Five sub-scores in [0,1] (higher is better) plus aggregate and violations."""

    model_config = ConfigDict(extra="forbid")

    compactness: float = Field(description="Bounding-box utilization score 0-1.", ge=0, le=1)
    reach_margin: float = Field(description="Reach-feasibility score 0-1.", ge=0, le=1)
    cycle_efficiency: float = Field(description="Cycle-time score 0-1.", ge=0, le=1)
    safety_clearance: float = Field(description="ISO 13855 safety score 0-1.", ge=0, le=1)
    throughput_feasibility: float = Field(
        description="UPH_estimated / UPH_target saturated at 1.1 → score 0-1.", ge=0, le=1
    )
    aggregate: float = Field(
        description="Weighted aggregate; 0 if any hard violation present.", ge=0, le=1
    )
    violations: list[Violation] = Field(default_factory=list, description="All violations found.")
    weights: dict[str, float] = Field(
        default_factory=dict, description="Weights used for aggregation (for transparency)."
    )
