"""Workcell specification schemas — the canonical contract for extracted specs.

Discriminated unions for components let the LLM emit type-tagged JSON the schema
adapters can faithfully translate to each provider's structured-output API.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class ComponentBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Stable identifier within the workcell, e.g. 'robot_1'.")
    label: str | None = Field(
        default=None, description="Human-readable label for UI display."
    )


class Robot(ComponentBase):
    type: Literal["robot"] = "robot"
    payload_kg: float | None = Field(
        default=None, description="Required payload at the EOAT in kg; null if not stated."
    )
    reach_mm: float | None = Field(
        default=None, description="Required horizontal reach in mm; null if not stated."
    )
    preferred_model: str | None = Field(
        default=None,
        description="Customer-specified robot model id, e.g. 'M-410iC/110'; null if open.",
    )


class Conveyor(ComponentBase):
    type: Literal["conveyor"] = "conveyor"
    length_mm: float = Field(description="Conveyor length in mm.", gt=0)
    width_mm: float = Field(description="Conveyor belt width in mm.", gt=0)
    flow_direction_deg: float = Field(
        description="Belt flow direction in degrees (0 = +x, 90 = +y)."
    )
    role: Literal["infeed", "outfeed"] = Field(
        default="infeed", description="Whether this conveyor feeds in or carries away."
    )
    speed_mps: float | None = Field(
        default=None, description="Belt speed in m/s; null if unknown."
    )


class Pallet(ComponentBase):
    type: Literal["pallet"] = "pallet"
    standard: Literal["EUR", "GMA", "ISO1", "half"] | None = Field(
        default=None, description="Standard pallet footprint; null if unspecified."
    )
    length_mm: float | None = Field(
        default=None, description="Pallet length in mm (auto from standard if null)."
    )
    width_mm: float | None = Field(
        default=None, description="Pallet width in mm (auto from standard if null)."
    )
    pattern: Literal["column", "interlock", "pinwheel"] | None = Field(
        default=None, description="Stacking pattern; null if unspecified."
    )


class Fence(ComponentBase):
    type: Literal["fence"] = "fence"
    height_mm: float = Field(
        default=2000.0, description="Guard fence height in mm (ISO 13857 typ. 2000-2200)."
    )
    has_light_curtain: bool = Field(
        default=False, description="True if a light curtain replaces hard guard at access points."
    )


class OperatorZone(ComponentBase):
    type: Literal["operator_zone"] = "operator_zone"
    width_mm: float = Field(description="Zone width in mm.", gt=0)
    depth_mm: float = Field(description="Zone depth (away from cell) in mm.", gt=0)


Component = Annotated[
    Robot | Conveyor | Pallet | Fence | OperatorZone,
    Field(discriminator="type"),
]


class ConstraintKind(str, Enum):
    MIN_CLEARANCE = "min_clearance"
    MAX_CYCLE_TIME = "max_cycle_time"
    MUST_REACH = "must_reach"
    MAX_FOOTPRINT = "max_footprint"


class Constraint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ConstraintKind = Field(description="Constraint category.")
    hard: bool = Field(description="True = inviolable; False = soft objective.")
    target_id: str | None = Field(
        default=None, description="Component id this constraint applies to; null = global."
    )
    value: float | None = Field(
        default=None, description="Numeric value (mm, s, m², etc. depending on kind)."
    )
    description: str = Field(default="", description="Human-readable explanation.")


class Throughput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cases_per_hour_target: float = Field(
        description="Target throughput in cases per hour (UPH).", gt=0
    )
    operating_hours_per_day: float = Field(
        default=20.0, description="Productive hours per day (default 20 with maintenance window)."
    )
    sku_count: int = Field(default=1, description="Number of distinct SKUs handled.", ge=1)
    mixed_sequence: bool = Field(
        default=False, description="True if SKUs arrive in random sequence (drives 6-axis pick)."
    )


class WorkcellSpec(BaseModel):
    """Customer requirements + cell envelope, extracted from natural language."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    cell_envelope_mm: tuple[float, float] = Field(
        description="(W, H) of available floor area in mm — origin is lower-left."
    )
    components: list[Component] = Field(
        default_factory=list,
        description="Components requested by the spec (robots, conveyors, pallets, fence, zones).",
    )
    constraints: list[Constraint] = Field(
        default_factory=list, description="Hard and soft constraints."
    )
    throughput: Throughput = Field(description="Throughput targets and operating profile.")
    case_dims_mm: tuple[float, float, float] | None = Field(
        default=None, description="(L, W, H) of a single case in mm; null if unstated."
    )
    case_mass_kg: float | None = Field(
        default=None, description="Mass of one case in kg; null if unstated."
    )
    pallet_standard: Literal["EUR", "GMA", "ISO1", "half"] | None = Field(
        default=None, description="Pallet standard if unambiguous; else null."
    )
    max_stack_height_mm: float | None = Field(
        default=None, description="Maximum stack height including pallet; null if unstated."
    )
    budget_usd: float | None = Field(
        default=None, description="Total cell budget in USD; null if unstated."
    )
    assumptions: list[str] = Field(
        default_factory=list,
        description=(
            "Every inferred or defaulted value MUST be recorded here as a short note. "
            "This is the primary hallucination-control mechanism."
        ),
    )
    notes: str = Field(
        default="", description="Free-form notes the LLM wishes to surface to the user."
    )
