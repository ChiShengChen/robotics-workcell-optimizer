"""Export endpoints — DXF / STL / STEP / BOM (CSV + Markdown).

Single POST endpoint. The frontend posts the active LayoutProposal and a
format string; the backend runs the cad_flow exporters and streams the
file back as an attachment so the browser fires its native download.

  POST /api/export
    body: { proposal: LayoutProposal, format: 'dxf'|'stl'|'step'|'bom_csv'|'bom_md' }
    -> 200 with file bytes (Content-Disposition: attachment; filename=...)
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.layout import LayoutProposal
from app.services.cad_export import render

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/export", tags=["export"])


ExportFormat = Literal["dxf", "stl", "step", "bom_csv", "bom_md"]


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal: LayoutProposal = Field(description="Active LayoutProposal to export.")
    format: ExportFormat = Field(description="Output format.")


@router.post("")
async def export_proposal(req: ExportRequest) -> Response:
    """Render and stream a CAD/BOM artefact for the supplied proposal."""
    try:
        data, content_type, filename = render(req.proposal.model_dump(), req.format)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except RuntimeError as e:
        # cadquery missing for STEP, etc.
        raise HTTPException(status_code=501, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("Export failed")
        raise HTTPException(status_code=500, detail=f"Export failed: {e}") from e

    return Response(
        content=data,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
