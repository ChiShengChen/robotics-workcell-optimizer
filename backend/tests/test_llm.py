"""LLM schema-adapter + repair-loop unit tests (no real API calls)."""

from __future__ import annotations

from typing import Literal
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel, ConfigDict, Field

from app.services.llm import (
    DEFAULT_MODEL_BY_TIER,
    LLMResult,
    LLMRouter,
    estimate_cost_usd,
    for_claude,
    for_gemini,
    for_openai_strict,
)


class _Tiny(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="A name.")
    age: int | None = Field(default=None, description="Optional age.")
    role: Literal["admin", "user"] = Field(description="Role enum.")


# ---- Schema adapter shape tests ----------------------------------------------------------------


def test_openai_strict_marks_additional_properties_false_everywhere():
    schema = for_openai_strict(_Tiny)
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    # Every property must appear in `required` (strict mode requirement).
    assert set(schema["required"]) == set(schema["properties"].keys())
    # Format/min/max should be stripped.
    for prop in schema["properties"].values():
        assert "minimum" not in prop
        assert "format" not in prop


def test_claude_schema_keeps_descriptions():
    schema = for_claude(_Tiny)
    assert schema["type"] == "object"
    # Descriptions are critical for tool-use guidance — must survive.
    assert schema["properties"]["name"]["description"] == "A name."
    # Claude tolerates $defs but we use a flat model so none here.


def test_gemini_schema_strips_additional_properties():
    schema = for_gemini(_Tiny)

    def walk(node):
        if isinstance(node, dict):
            assert "additionalProperties" not in node, "Gemini SDK rejects additionalProperties"
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)


def test_pricing_lookup():
    # Claude haiku-4-5 = $1 in / $5 out per 1M tokens
    cost = estimate_cost_usd("claude-haiku-4-5", in_tokens=1_000_000, out_tokens=0)
    assert cost == pytest.approx(1.0)
    cost = estimate_cost_usd("claude-haiku-4-5", in_tokens=0, out_tokens=1_000_000)
    assert cost == pytest.approx(5.0)
    # Unknown model → cost 0 (don't crash)
    assert estimate_cost_usd("mystery-model", 1000, 1000) == 0.0


# ---- Repair loop test (with a fake client) ---------------------------------------------------


class _FailingClient:
    """First call returns invalid output; second call returns valid."""

    def __init__(self) -> None:
        self.calls = 0

    async def extract(self, messages, schema, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return LLMResult(
                text="not valid json", parsed=None, usage_in_tokens=10, usage_out_tokens=5,
                cost_usd=0.0, model="fake", latency_ms=1.0, provider="claude",
            )
        valid = schema(name="x", role="admin")
        return LLMResult(
            text=valid.model_dump_json(), parsed=valid, usage_in_tokens=12, usage_out_tokens=8,
            cost_usd=0.0, model="fake", latency_ms=1.0, provider="claude",
        )


@pytest.mark.asyncio
async def test_extract_with_repair_succeeds_on_second_attempt():
    from app.services.llm import LLMClient

    client = _FailingClient()
    # Borrow the ABC's repair loop directly (it doesn't need .extract to be ABC-bound).
    result = await LLMClient.extract_with_repair(
        client,  # type: ignore[arg-type]
        messages=[{"role": "user", "content": "go"}],
        schema=_Tiny,
        max_retries=2,
    )
    assert client.calls == 2
    assert result.parsed is not None
    assert result.parsed.name == "x"


# ---- Router fallback test ---------------------------------------------------------------------


class _AlwaysFailClient:
    async def extract(self, messages, schema, **kwargs):
        return LLMResult(
            text="bad", parsed=None, usage_in_tokens=1, usage_out_tokens=1,
            cost_usd=0.0, model="fake-fail", latency_ms=1.0, provider="claude",
        )

    async def extract_with_repair(self, messages, schema, **kwargs):
        return await self.extract(messages, schema, **kwargs)


class _AlwaysOkClient:
    async def extract(self, messages, schema, **kwargs):
        valid = schema(name="ok", role="user")
        return LLMResult(
            text=valid.model_dump_json(), parsed=valid, usage_in_tokens=1, usage_out_tokens=1,
            cost_usd=0.0, model="fake-ok", latency_ms=1.0, provider="openai",
        )

    async def extract_with_repair(self, messages, schema, **kwargs):
        return await self.extract(messages, schema, **kwargs)


@pytest.mark.asyncio
async def test_router_falls_back_across_providers():
    router = LLMRouter(
        clients={"claude": _AlwaysFailClient(), "openai": _AlwaysOkClient()},  # type: ignore[dict-item]
        default_provider="claude",
    )
    result = await router.extract(
        messages=[{"role": "user", "content": "go"}], schema=_Tiny,
    )
    assert result.parsed is not None
    assert result.parsed.name == "ok"
    # Cost ledger should have recorded both attempts (the failing one too).
    assert router.ledger.call_count == 2


def test_default_model_by_tier_covers_all_providers():
    for prov in ("claude", "openai", "gemini"):
        for tier in ("fast", "smart"):
            assert (prov, tier) in DEFAULT_MODEL_BY_TIER
