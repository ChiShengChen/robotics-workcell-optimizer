"""POST /api/score — { proposal, spec } → ScoreBreakdown.

Multi-arm aware: looks up RobotSpec for each robot listed in
`proposal.robot_model_ids` (falling back to `robot_model_id` for single-arm).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.layout import LayoutProposal, ScoreBreakdown
from app.schemas.robot import RobotSpec
from app.schemas.workcell import WorkcellSpec
from app.services.catalog import get_catalog
from app.services.scoring import score_layout

router = APIRouter(prefix="/score", tags=["score"])


class ScoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal: LayoutProposal = Field(description="Layout to score (post-edit if applicable).")
    spec: WorkcellSpec = Field(description="Original workcell spec (for envelope, throughput).")
    robot_model_id: str | None = Field(
        default=None, description="Override the primary robot model (single-arm only)."
    )
    robot_model_ids: list[str] | None = Field(
        default=None,
        description=(
            "Override the per-robot model list (multi-arm). Order matches the "
            "robot PlacedComponents in the proposal. Falls back to "
            "proposal.robot_model_ids, then [proposal.robot_model_id]."
        ),
    )
    weights: dict[str, float] | None = Field(
        default=None, description="Optional weight overrides for the aggregate."
    )


def _resolve_robot_specs(
    proposal: LayoutProposal,
    override_ids: list[str] | None,
    override_id: str | None,
) -> list[RobotSpec]:
    catalog = get_catalog()
    ids: list[str] = []
    if override_ids:
        ids = list(override_ids)
    elif override_id:
        ids = [override_id]
    elif proposal.robot_model_ids:
        ids = list(proposal.robot_model_ids)
    elif proposal.robot_model_id:
        ids = [proposal.robot_model_id]
    out: list[RobotSpec] = []
    for rid in ids:
        try:
            out.append(catalog.get_by_id(rid))
        except HTTPException:
            # Stale id — skip (scoring will treat as missing).
            pass
    return out


@router.post("", response_model=ScoreBreakdown)
async def score(req: ScoreRequest) -> ScoreBreakdown:
    robot_specs = _resolve_robot_specs(req.proposal, req.robot_model_ids, req.robot_model_id)
    return score_layout(req.proposal, req.spec, robot_specs, weights=req.weights)
