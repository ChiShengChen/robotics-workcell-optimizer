"""Robot catalog schemas. Single source of truth for robot specs in code."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class IdealUseCase(str, Enum):
    LIGHT_CASE = "light_case"
    MEDIUM_CASE = "medium_case"
    HEAVY_BAG = "heavy_bag"
    MIXED_SKU = "mixed_sku"
    LAYER_PICKER = "layer_picker"


class RobotSpec(BaseModel):
    """A single palletizing robot spec from the catalog."""

    model_config = ConfigDict(extra="forbid")

    model: str = Field(description="Manufacturer model identifier, e.g. 'IRB 460'.")
    manufacturer: str = Field(description="Manufacturer name, e.g. 'ABB'.")
    axes: int = Field(description="Number of robot axes (4, 5, or 6).", ge=4, le=6)
    payload_kg: float = Field(description="Rated payload in kilograms (incl. EOAT).", gt=0)
    reach_mm: float = Field(description="Maximum horizontal reach in millimeters.", gt=0)
    vertical_reach_mm_min: float = Field(
        description="Lowest reachable Z relative to robot base (mm; negative = below base).",
    )
    vertical_reach_mm_max: float = Field(
        description="Highest reachable Z relative to robot base (mm; positive = above base).",
    )
    repeatability_mm: float = Field(
        description="Position repeatability per ISO 9283 (mm; typically 0.05-0.5).", gt=0
    )
    footprint_l_mm: float = Field(description="Base footprint length in mm.", gt=0)
    footprint_w_mm: float = Field(description="Base footprint width in mm.", gt=0)
    weight_kg: float = Field(description="Robot mass in kilograms.", gt=0)
    cycles_per_hour_std: float = Field(
        description="Standard 400/2000/400 mm cycles per hour at rated payload.", gt=0
    )
    price_usd_low: float = Field(description="Bare-arm price low estimate in USD.", gt=0)
    price_usd_high: float = Field(description="Bare-arm price high estimate in USD.", gt=0)
    ideal_use_case: IdealUseCase = Field(description="Primary application class.")
    manufacturer_url: str | None = Field(
        default=None, description="Datasheet or product page URL."
    )
    notes: str = Field(default="", description="Caveats, source notes, approximations.")

    @property
    def effective_max_reach_mm(self) -> float:
        """Conservative reach with α=0.85 derate (palletizing best-practice).

        Plain @property (not @computed_field) so it stays out of JSON
        serialization — keeps round-trips clean under extra='forbid'.
        """
        return 0.85 * self.reach_mm
