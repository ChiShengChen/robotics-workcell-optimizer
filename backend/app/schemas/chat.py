"""Chat refinement schemas — RFC 6902 JSON Patch as the LLM's edit channel."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class JsonPatchOp(BaseModel):
    """A single RFC 6902 JSON Patch operation."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["add", "remove", "replace", "move", "copy", "test"] = Field(
        description="JSON Patch operation kind."
    )
    path: str = Field(description="JSON Pointer to the target location.")
    value: Any | None = Field(
        default=None, description="New value (required for add/replace/test)."
    )
    from_: str | None = Field(
        default=None,
        alias="from",
        description="Source pointer for move/copy operations.",
    )


class ChatRefinementResponse(BaseModel):
    """LLM's response to a refinement turn — patches + rationale + assumptions."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    patch: list[JsonPatchOp] = Field(
        description="Patches to apply to the active LayoutProposal (or empty for no-op)."
    )
    rationale: str = Field(description="Short explanation visible to the user.")
    assumptions: list[str] = Field(
        default_factory=list,
        description="Inferred values or interpretations the LLM made.",
    )
