"""Obstacle schema — region the layout cannot intrude into.

Imported from a CAD floor plan (DXF) or hand-authored. Treated as a HARD
constraint by both the scoring service and the SA / CP-SAT optimisers.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Obstacle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Stable obstacle id (e.g. 'cad_lwpolyline_3').")
    polygon: list[list[float]] = Field(
        description=(
            "Closed-or-open polyline of [x_mm, y_mm] points. If `closed=True` "
            "the first and last points should match."
        ),
    )
    closed: bool = Field(
        default=True,
        description="Whether the polygon represents a filled region (True) or a wall polyline (False).",
    )
    source_layer: str | None = Field(
        default=None, description="Originating CAD layer name (for provenance)."
    )
    source_entity: str | None = Field(
        default=None, description="DXF entity type (LINE / LWPOLYLINE / CIRCLE / ARC / POLYLINE)."
    )
    label: str | None = Field(default=None, description="Optional human-readable label.")
