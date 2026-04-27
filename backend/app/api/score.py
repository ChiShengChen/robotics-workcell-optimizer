"""POST /api/score — { proposal, spec, robot_model_id } → ScoreBreakdown."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.layout import LayoutProposal, ScoreBreakdown
from app.schemas.workcell import WorkcellSpec
from app.services.catalog import get_catalog
from app.services.scoring import score_layout

router = APIRouter(prefix="/score", tags=["score"])


class ScoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal: LayoutProposal = Field(description="Layout to score (post-edit if applicable).")
    spec: WorkcellSpec = Field(description="Original workcell spec (for envelope, throughput).")
    robot_model_id: str | None = Field(
        default=None, description="Override the robot model; defaults to proposal.robot_model_id."
    )
    weights: dict[str, float] | None = Field(
        default=None, description="Optional weight overrides for the aggregate."
    )


@router.post("", response_model=ScoreBreakdown)
async def score(req: ScoreRequest) -> ScoreBreakdown:
    catalog = get_catalog()
    robot_id = req.robot_model_id or req.proposal.robot_model_id
    robot_spec = None
    if robot_id is not None:
        try:
            robot_spec = catalog.get_by_id(robot_id)
        except HTTPException:
            # Robot id from a stale proposal — score with no robot (zeros reach).
            robot_spec = None
    return score_layout(req.proposal, req.spec, robot_spec, weights=req.weights)
