"""POST /api/optimize         — synchronous SA optimization, returns full result.
POST /api/optimize/stream    — SSE: progress events + final done event.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.layout import LayoutProposal, ScoreBreakdown
from app.schemas.workcell import WorkcellSpec
from app.services.catalog import get_catalog
from app.services.optimizer import SAOptimizer, delta_summary
from app.services.scoring import score_layout

router = APIRouter(prefix="/optimize", tags=["optimize"])


class OptimizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal: LayoutProposal = Field(description="Seed layout to optimize.")
    spec: WorkcellSpec = Field(description="Source workcell spec.")
    robot_model_id: str | None = Field(default=None, description="Override robot id.")
    max_iterations: int = Field(default=400, ge=10, le=4000)
    seed: int | None = Field(default=None, description="RNG seed for reproducibility.")


class OptimizeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    optimized_proposal: LayoutProposal
    seed_score: ScoreBreakdown
    optimized_score: ScoreBreakdown
    score_history: list[float]
    best_history: list[float]
    delta_summary: dict[str, float]
    walltime_s: float
    iterations: int
    accepted: int
    rejected: int


def _resolve_robot_spec(req: OptimizeRequest):
    catalog = get_catalog()
    robot_id = req.robot_model_id or req.proposal.robot_model_id
    if robot_id is None:
        return None
    try:
        return catalog.get_by_id(robot_id)
    except Exception:
        return None


@router.post("", response_model=OptimizeResponse)
async def optimize(req: OptimizeRequest) -> OptimizeResponse:
    robot_spec = _resolve_robot_spec(req)
    seed_score = score_layout(req.proposal, req.spec, robot_spec)
    sa = SAOptimizer(max_iterations=req.max_iterations, seed=req.seed)
    optimized, stats = sa.optimize(req.proposal, req.spec, robot_spec)
    optimized_score = score_layout(optimized, req.spec, robot_spec)
    return OptimizeResponse(
        optimized_proposal=optimized,
        seed_score=seed_score,
        optimized_score=optimized_score,
        score_history=stats.score_history,
        best_history=stats.best_history,
        delta_summary=delta_summary(req.proposal, seed_score, optimized, optimized_score),
        walltime_s=stats.walltime_s,
        iterations=stats.iterations,
        accepted=stats.accepted,
        rejected=stats.rejected,
    )


# ---------------------------------------------------------------------------
# SSE streaming endpoint
# ---------------------------------------------------------------------------


def _sse(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n".encode("utf-8")


@router.post("/stream")
async def optimize_stream(req: OptimizeRequest) -> StreamingResponse:
    robot_spec = _resolve_robot_spec(req)
    seed_score = score_layout(req.proposal, req.spec, robot_spec)
    queue: asyncio.Queue[bytes | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    # Throttle progress: emit every Nth iteration so the stream isn't flooded.
    progress_every = max(1, req.max_iterations // 80)

    def on_step(i: int, _proposal: LayoutProposal, current: float, best: float) -> None:
        if i % progress_every == 0 or i == req.max_iterations:
            payload = {"iteration": i, "current_score": current, "best_score": best}
            try:
                loop.call_soon_threadsafe(queue.put_nowait, _sse("progress", payload))
            except RuntimeError:
                # event loop closed (client disconnected); drop silently.
                pass

    async def runner() -> None:
        try:
            sa = SAOptimizer(max_iterations=req.max_iterations, seed=req.seed)
            # SAOptimizer.optimize is sync + CPU-bound; run in a thread.
            optimized, stats = await asyncio.to_thread(
                sa.optimize, req.proposal, req.spec, robot_spec, on_step,
            )
            optimized_score = score_layout(optimized, req.spec, robot_spec)
            done_payload = {
                "optimized_proposal": optimized.model_dump(mode="json"),
                "seed_score": seed_score.model_dump(mode="json"),
                "optimized_score": optimized_score.model_dump(mode="json"),
                "score_history": stats.score_history,
                "best_history": stats.best_history,
                "delta_summary": delta_summary(
                    req.proposal, seed_score, optimized, optimized_score
                ),
                "walltime_s": stats.walltime_s,
                "iterations": stats.iterations,
                "accepted": stats.accepted,
                "rejected": stats.rejected,
            }
            await queue.put(_sse("done", done_payload))
        except Exception as e:  # noqa: BLE001
            await queue.put(_sse("error", {"message": str(e)}))
        finally:
            await queue.put(None)

    task = asyncio.create_task(runner())

    async def gen() -> AsyncIterator[bytes]:
        try:
            while True:
                chunk = await queue.get()
                if chunk is None:
                    break
                yield chunk
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
