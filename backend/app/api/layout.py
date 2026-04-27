"""POST /api/generate-layout — WorkcellSpec → list[LayoutProposal]."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.layout import LayoutProposal
from app.schemas.workcell import WorkcellSpec
from app.services.catalog import get_catalog
from app.services.layout import GreedyLayoutGenerator

router = APIRouter(prefix="/generate-layout", tags=["layout"])


class GenerateLayoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: WorkcellSpec = Field(description="Extracted workcell specification.")
    n_variants: int = Field(default=3, description="Number of template variants to return.", ge=1, le=4)


@router.post("", response_model=list[LayoutProposal])
async def generate_layout(req: GenerateLayoutRequest) -> list[LayoutProposal]:
    catalog = get_catalog()
    generator = GreedyLayoutGenerator(catalog)
    return generator.generate(req.spec, n_variants=req.n_variants)
