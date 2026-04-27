"""GET /api/examples — list bundled WorkcellSpec examples for the demo UI."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.workcell import WorkcellSpec

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "data" / "examples"

router = APIRouter(prefix="/examples", tags=["examples"])


class ExampleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Stable example id used as a query parameter.")
    label: str = Field(description="Short human-readable label for the dropdown.")
    description: str = Field(description="One-sentence summary shown beneath the label.")
    prompt: str = Field(description="The natural-language prompt for /api/extract.")
    spec: WorkcellSpec = Field(description="Pre-extracted WorkcellSpec for instant demo.")


@router.get("", response_model=list[ExampleSpec])
async def list_examples() -> list[ExampleSpec]:
    """Returns every example JSON file in backend/app/data/examples/."""
    examples: list[ExampleSpec] = []
    for path in sorted(EXAMPLES_DIR.glob("*.json")):
        with path.open() as fh:
            examples.append(ExampleSpec.model_validate(json.load(fh)))
    return examples
