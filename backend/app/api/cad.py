"""POST /api/cad/import-dxf — multipart upload of a DXF floor plan.

Returns a list of `Obstacle`s (in mm) plus a suggested cell envelope so the
frontend can apply them straight onto the active WorkcellSpec.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.obstacle import Obstacle
from app.services.cad_import import parse_dxf

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/cad", tags=["cad"])


class CadImportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    obstacles: list[Obstacle] = Field(description="Parsed obstacle polygons in mm.")
    bounding_box_mm: tuple[float, float, float, float] | None = Field(
        description="Min/max bounding box of all entities (min_x, min_y, max_x, max_y) in mm."
    )
    suggested_cell_envelope_mm: tuple[float, float] | None = Field(
        description="(W, H) suggested cell envelope = bbox * 1.05."
    )
    units_assumed: str = Field(description="Units the parser used (e.g. 'mm', 'scaled x1000').")
    n_entities_imported: int = Field(description="Number of DXF entities successfully imported.")
    n_entities_skipped: int = Field(
        description="DXF entities that weren't drawable obstacles (TEXT, BLOCK, etc.)."
    )


@router.post("/import-dxf", response_model=CadImportResponse)
async def import_dxf(
    file: UploadFile = File(..., description="DXF file (ASCII; binary DXF not supported)."),
    scale_to_mm: float = 1.0,
    margin_mm: float = 200.0,
) -> CadImportResponse:
    """Parse a DXF floor plan into obstacle polygons.

    Query params:
    - `scale_to_mm`: multiplier (use 1000 if the drawing is in metres,
      25.4 if inches). Default 1.0 = drawing already in mm.
    - `margin_mm`: shift the bounding box origin so the smallest (x, y)
      lands at (margin_mm, margin_mm). Default 200 mm.
    """
    if not file.filename or not file.filename.lower().endswith(".dxf"):
        raise HTTPException(status_code=400, detail="Upload a .dxf file (ASCII format).")
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file.")
    try:
        result = parse_dxf(raw, scale_to_mm=scale_to_mm, margin_mm=margin_mm)
    except Exception as e:  # noqa: BLE001
        logger.exception("DXF parse failed")
        raise HTTPException(
            status_code=422, detail=f"DXF parse failed: {e}. Is this an ASCII DXF?"
        ) from e

    return CadImportResponse(
        obstacles=[
            Obstacle(
                id=o.id,
                polygon=o.polygon,
                closed=o.closed,
                source_layer=o.source_layer,
                source_entity=o.source_entity,
            )
            for o in result.obstacles
        ],
        bounding_box_mm=result.bounding_box_mm,
        suggested_cell_envelope_mm=result.suggested_cell_envelope_mm,
        units_assumed=result.units_assumed,
        n_entities_imported=result.n_entities_imported,
        n_entities_skipped=result.n_entities_skipped,
    )
