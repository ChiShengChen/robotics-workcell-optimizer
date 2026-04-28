"""CAD floor-plan endpoints.

  POST /api/cad/import-dxf   — multipart upload of an ASCII DXF
  GET  /api/cad/samples      — list bundled sample floor plans
  POST /api/cad/load-sample  — parse one bundled sample by id

All return a CadImportResponse the frontend can apply straight onto
the active WorkcellSpec.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.obstacle import Obstacle
from app.services.cad_import import parse_dxf

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/cad", tags=["cad"])

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "data" / "sample_dxf"

# Friendly metadata for each bundled sample. Keys MUST match the .dxf
# filename stem in backend/app/data/sample_dxf/.
SAMPLE_META: dict[str, dict[str, str]] = {
    "simple_8x6": {
        "label": "Simple 8 × 6 m + 1 column",
        "description": "Default sanity check — small cell, 1 structural column.",
    },
    "medium_12x8": {
        "label": "Medium 12 × 8 m + column + equipment (dual-arm friendly)",
        "description": "Roomy cell. Pair with cph_target ≥ 1500 to trigger dual-arm.",
    },
    "complex_15x10": {
        "label": "Complex 15 × 10 m + 4 columns + 2 equipment",
        "description": "Realistic cluttered floor; SA / CP-SAT navigate around obstacles.",
    },
    "tight_6x4": {
        "label": "Tight 6 × 4 m + 3 columns dense grid",
        "description": "Stress test — cramped cell. CP-SAT may hit INFEASIBLE.",
    },
    "l_shape_10x10": {
        "label": "L-shape 10 × 10 m + 1 column + 1 equipment",
        "description": "Non-convex outer wall; tests bbox-of-largest-polygon assumption.",
    },
}


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


class CadSample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Sample id; pass to /api/cad/load-sample.")
    label: str = Field(description="Short human-readable label for a dropdown.")
    description: str = Field(description="One-sentence summary of the scenario.")


class LoadSampleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Sample id from /api/cad/samples.")


def _parse_dxf_file(path: Path) -> CadImportResponse:
    raw = path.read_bytes()
    result = parse_dxf(raw, scale_to_mm=1.0, margin_mm=200.0)
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


@router.post("/import-dxf", response_model=CadImportResponse)
async def import_dxf(
    file: UploadFile = File(..., description="DXF file (ASCII; binary DXF not supported)."),
    scale_to_mm: float = 1.0,
    margin_mm: float = 200.0,
) -> CadImportResponse:
    """Parse an uploaded DXF floor plan into obstacle polygons.

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


@router.get("/samples", response_model=list[CadSample])
async def list_samples() -> list[CadSample]:
    """Bundled sample floor plans (no upload needed). Order matches a sensible
    demo progression: simple → medium → complex → tight → L-shape."""
    out: list[CadSample] = []
    for sid in ("simple_8x6", "medium_12x8", "complex_15x10", "tight_6x4", "l_shape_10x10"):
        if not (SAMPLES_DIR / f"{sid}.dxf").exists():
            continue
        meta = SAMPLE_META.get(sid, {"label": sid, "description": ""})
        out.append(CadSample(id=sid, label=meta["label"], description=meta["description"]))
    return out


@router.post("/load-sample", response_model=CadImportResponse)
async def load_sample(req: LoadSampleRequest) -> CadImportResponse:
    """Parse one bundled sample DXF by id.

    Equivalent to uploading the file via /api/cad/import-dxf, but no
    multipart, no upload needed. Use /api/cad/samples first to list available
    ids.
    """
    if "/" in req.id or ".." in req.id:  # cheap path-traversal guard
        raise HTTPException(status_code=400, detail="Invalid sample id.")
    path = SAMPLES_DIR / f"{req.id}.dxf"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Sample {req.id!r} not found.")
    try:
        return _parse_dxf_file(path)
    except Exception as e:  # noqa: BLE001
        logger.exception("DXF parse failed")
        raise HTTPException(status_code=422, detail=f"DXF parse failed: {e}") from e
