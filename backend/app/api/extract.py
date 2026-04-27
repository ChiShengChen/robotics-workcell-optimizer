"""POST /api/extract — natural language → WorkcellSpec via the multi-LLM router."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.workcell import WorkcellSpec
from app.services.extraction import extract_workcell_spec
from app.services.llm import LLMRouter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/extract", tags=["extract"])


class ExtractRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(description="Natural-language description of the packaging line.")


@router.post("", response_model=WorkcellSpec)
async def extract(req: ExtractRequest) -> WorkcellSpec:
    llm_router = LLMRouter.from_env()
    if not llm_router.clients:
        raise HTTPException(
            status_code=503,
            detail=(
                "No LLM provider configured. Set ANTHROPIC_API_KEY, OPENAI_API_KEY, "
                "or GOOGLE_API_KEY in backend/.env."
            ),
        )

    try:
        spec, result = await extract_workcell_spec(req.prompt, llm_router)
    except Exception as e:
        logger.exception("Extraction call raised")
        raise HTTPException(status_code=500, detail=f"Extraction failed: {e}") from e

    if spec is None:
        # Surface raw output for debugging — caller can show it in the UI.
        raise HTTPException(
            status_code=422,
            detail={
                "message": "LLM returned no schema-valid WorkcellSpec after repair attempts.",
                "model": result.model,
                "provider": result.provider,
                "raw_output_preview": result.text[:2000],
            },
        )
    return spec
