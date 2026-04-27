"""Multi-LLM abstraction.

Three engineering bets here:
1. ONE canonical Pydantic schema → three provider-shaped JSON schemas via adapter funcs.
2. Validation-repair loop: every extract response runs through Pydantic; on failure we
   feed the validation error back as a follow-up turn before falling back providers.
3. Cost ledger accumulates per-provider $ usage so the demo can show telemetry.
"""

from __future__ import annotations

import json
import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

Provider = Literal["claude", "openai", "gemini"]
Tier = Literal["fast", "smart"]

T = TypeVar("T", bound=BaseModel)


# ---------------------------------------------------------------------------
# Pricing + model maps
# ---------------------------------------------------------------------------

PRICING_PER_MTOKEN: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "gpt-5-nano": (0.05, 0.40),
    "gpt-4.1": (2.50, 10.0),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.0),
}

DEFAULT_MODEL_BY_TIER: dict[tuple[Provider, Tier], str] = {
    ("claude", "fast"): "claude-haiku-4-5",
    ("claude", "smart"): "claude-sonnet-4-5",
    ("openai", "fast"): "gpt-5-nano",
    ("openai", "smart"): "gpt-4.1",
    ("gemini", "fast"): "gemini-2.5-flash",
    ("gemini", "smart"): "gemini-2.5-pro",
}


def estimate_cost_usd(model: str, in_tokens: int, out_tokens: int) -> float:
    in_rate, out_rate = PRICING_PER_MTOKEN.get(model, (0.0, 0.0))
    return (in_tokens * in_rate + out_tokens * out_rate) / 1_000_000


# ---------------------------------------------------------------------------
# Result + ledger types
# ---------------------------------------------------------------------------


@dataclass
class LLMResult(Generic[T]):
    """Provider-agnostic result envelope."""

    text: str
    parsed: T | None
    usage_in_tokens: int
    usage_out_tokens: int
    cost_usd: float
    model: str
    latency_ms: float
    provider: Provider
    raw: Any = None  # provider-native response (debug only)


@dataclass
class CostLedger:
    """Accumulates per-provider cost across a session."""

    by_provider: dict[Provider, float] = field(
        default_factory=lambda: {"claude": 0.0, "openai": 0.0, "gemini": 0.0}
    )
    by_model: dict[str, float] = field(default_factory=dict)
    total_usd: float = 0.0
    call_count: int = 0

    def record(self, result: LLMResult) -> None:
        self.by_provider[result.provider] = self.by_provider.get(result.provider, 0.0) + result.cost_usd
        self.by_model[result.model] = self.by_model.get(result.model, 0.0) + result.cost_usd
        self.total_usd += result.cost_usd
        self.call_count += 1


# ---------------------------------------------------------------------------
# Schema adapters: ONE Pydantic model → three provider shapes.
# These are the "narrow waist" of the multi-LLM design.
# ---------------------------------------------------------------------------


def _strip_keys(node: Any, keys: set[str]) -> Any:
    """Recursively remove keys from a JSON schema dict."""
    if isinstance(node, dict):
        return {k: _strip_keys(v, keys) for k, v in node.items() if k not in keys}
    if isinstance(node, list):
        return [_strip_keys(v, keys) for v in node]
    return node


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline $ref / $defs into a single tree (OpenAI strict and Gemini both prefer flat)."""
    defs = schema.pop("$defs", None) or schema.pop("definitions", None) or {}

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node and len(node) == 1:
                ref_path = node["$ref"]
                # only handle local refs like "#/$defs/Foo"
                key = ref_path.split("/")[-1]
                if key in defs:
                    return resolve(defs[key])
                return node
            return {k: resolve(v) for k, v in node.items()}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(schema)


def for_openai_strict(model: type[BaseModel]) -> dict[str, Any]:
    """OpenAI structured output (strict mode):
    - additionalProperties:false on every object
    - every property listed in `required` (use Optional[T] | None for nullable)
    - drop unsupported keywords (minLength, pattern, format, minimum, maximum, default,
      title, description on root) — strict schema vocabulary is restricted.
    """
    schema = _inline_refs(model.model_json_schema())
    schema = _strip_keys(
        schema,
        {"format", "minLength", "maxLength", "pattern", "minimum", "maximum",
         "exclusiveMinimum", "exclusiveMaximum", "default", "examples", "title"},
    )

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"].keys())
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


def for_claude(model: type[BaseModel]) -> dict[str, Any]:
    """Claude tool-use input_schema: standard JSON schema; Anthropic accepts $defs/refs.
    We keep the schema intact (descriptions matter — they help the model).
    """
    schema = model.model_json_schema()
    # Anthropic does not accept "title" at root in input_schema docs.
    schema.pop("title", None)
    return schema


def for_gemini(model: type[BaseModel]) -> dict[str, Any]:
    """Gemini response_schema: needs `additionalProperties` stripped (older SDK quirk),
    and `$defs` inlined.
    """
    schema = _inline_refs(model.model_json_schema())
    schema = _strip_keys(schema, {"additionalProperties", "title", "$schema"})
    return schema


# ---------------------------------------------------------------------------
# Client ABC + provider implementations.
# ---------------------------------------------------------------------------


class LLMClient(ABC):
    provider: Provider

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> LLMResult[None]:
        ...

    @abstractmethod
    async def extract(
        self,
        messages: list[dict[str, str]],
        schema: type[T],
        *,
        model: str | None = None,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResult[T]:
        ...

    async def extract_with_repair(
        self,
        messages: list[dict[str, str]],
        schema: type[T],
        *,
        model: str | None = None,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        max_retries: int = 2,
    ) -> LLMResult[T]:
        """Validate + retry with the validation error appended as a user turn."""
        attempt_messages = list(messages)
        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            result = await self.extract(
                attempt_messages,
                schema,
                model=model,
                system=system,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            if result.parsed is not None:
                return result
            # Build a repair turn from whatever text we got back.
            err_msg = (
                "Your previous output failed validation against the schema. "
                "Return ONLY valid JSON matching the schema; no prose, no markdown.\n"
                f"Error detail: {last_error or 'response had no parseable JSON'}\n"
                f"Your previous output was:\n{result.text[:1500]}"
            )
            attempt_messages = list(messages) + [
                {"role": "assistant", "content": result.text},
                {"role": "user", "content": err_msg},
            ]
            last_error = ValueError(f"attempt {attempt + 1} returned no parsed object")
        # exhausted retries
        return result  # type: ignore[return-value]


# --- Claude ---


class ClaudeClient(LLMClient):
    provider: Provider = "claude"

    def __init__(self, api_key: str | None = None) -> None:
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(api_key=api_key or os.getenv("ANTHROPIC_API_KEY"))

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> LLMResult[None]:
        m = model or DEFAULT_MODEL_BY_TIER[("claude", "smart")]
        t0 = time.perf_counter()
        kwargs: dict[str, Any] = {
            "model": m,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if system:
            kwargs["system"] = system
        resp = await self._client.messages.create(**kwargs)
        latency = (time.perf_counter() - t0) * 1000
        text = "".join(block.text for block in resp.content if hasattr(block, "text"))
        in_tok = getattr(resp.usage, "input_tokens", 0)
        out_tok = getattr(resp.usage, "output_tokens", 0)
        return LLMResult(
            text=text, parsed=None, usage_in_tokens=in_tok, usage_out_tokens=out_tok,
            cost_usd=estimate_cost_usd(m, in_tok, out_tok), model=m, latency_ms=latency,
            provider=self.provider, raw=resp,
        )

    async def extract(
        self,
        messages: list[dict[str, str]],
        schema: type[T],
        *,
        model: str | None = None,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResult[T]:
        m = model or DEFAULT_MODEL_BY_TIER[("claude", "smart")]
        tool_def = {
            "name": "emit_structured_output",
            "description": f"Emit a {schema.__name__} that matches the input_schema exactly.",
            "input_schema": for_claude(schema),
        }
        kwargs: dict[str, Any] = {
            "model": m,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "tools": [tool_def],
            "tool_choice": {"type": "tool", "name": "emit_structured_output"},
        }
        if system:
            kwargs["system"] = system
        t0 = time.perf_counter()
        resp = await self._client.messages.create(**kwargs)
        latency = (time.perf_counter() - t0) * 1000

        parsed: T | None = None
        text = ""
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "emit_structured_output":
                text = json.dumps(block.input)
                try:
                    parsed = schema.model_validate(block.input)
                except ValidationError as e:
                    logger.warning("Claude tool output failed Pydantic validation: %s", e)
            elif hasattr(block, "text"):
                text += block.text

        in_tok = getattr(resp.usage, "input_tokens", 0)
        out_tok = getattr(resp.usage, "output_tokens", 0)
        return LLMResult(
            text=text, parsed=parsed, usage_in_tokens=in_tok, usage_out_tokens=out_tok,
            cost_usd=estimate_cost_usd(m, in_tok, out_tok), model=m, latency_ms=latency,
            provider=self.provider, raw=resp,
        )


# --- OpenAI ---


class OpenAIClient(LLMClient):
    provider: Provider = "openai"

    def __init__(self, api_key: str | None = None) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> LLMResult[None]:
        m = model or DEFAULT_MODEL_BY_TIER[("openai", "smart")]
        msgs = ([{"role": "system", "content": system}] if system else []) + messages
        t0 = time.perf_counter()
        resp = await self._client.chat.completions.create(
            model=m, messages=msgs, temperature=temperature, max_tokens=max_tokens,
        )
        latency = (time.perf_counter() - t0) * 1000
        text = resp.choices[0].message.content or ""
        in_tok = resp.usage.prompt_tokens if resp.usage else 0
        out_tok = resp.usage.completion_tokens if resp.usage else 0
        return LLMResult(
            text=text, parsed=None, usage_in_tokens=in_tok, usage_out_tokens=out_tok,
            cost_usd=estimate_cost_usd(m, in_tok, out_tok), model=m, latency_ms=latency,
            provider=self.provider, raw=resp,
        )

    async def extract(
        self,
        messages: list[dict[str, str]],
        schema: type[T],
        *,
        model: str | None = None,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResult[T]:
        m = model or DEFAULT_MODEL_BY_TIER[("openai", "smart")]
        msgs = ([{"role": "system", "content": system}] if system else []) + messages
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": schema.__name__,
                "strict": True,
                "schema": for_openai_strict(schema),
            },
        }
        t0 = time.perf_counter()
        resp = await self._client.chat.completions.create(
            model=m, messages=msgs, temperature=temperature, max_tokens=max_tokens,
            response_format=response_format,
        )
        latency = (time.perf_counter() - t0) * 1000
        text = resp.choices[0].message.content or ""
        parsed: T | None = None
        try:
            parsed = schema.model_validate_json(text)
        except (ValidationError, json.JSONDecodeError) as e:
            logger.warning("OpenAI structured output failed validation: %s", e)
        in_tok = resp.usage.prompt_tokens if resp.usage else 0
        out_tok = resp.usage.completion_tokens if resp.usage else 0
        return LLMResult(
            text=text, parsed=parsed, usage_in_tokens=in_tok, usage_out_tokens=out_tok,
            cost_usd=estimate_cost_usd(m, in_tok, out_tok), model=m, latency_ms=latency,
            provider=self.provider, raw=resp,
        )


# --- Gemini ---


class GeminiClient(LLMClient):
    provider: Provider = "gemini"

    def __init__(self, api_key: str | None = None) -> None:
        from google import genai

        self._client = genai.Client(api_key=api_key or os.getenv("GOOGLE_API_KEY"))

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> LLMResult[None]:
        m = model or DEFAULT_MODEL_BY_TIER[("gemini", "smart")]
        # Flatten messages — Gemini chat history uses role 'user'/'model'
        contents = []
        for msg in messages:
            role = "model" if msg["role"] == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": msg["content"]}]})
        config: dict[str, Any] = {"temperature": temperature, "max_output_tokens": max_tokens}
        if system:
            config["system_instruction"] = system
        t0 = time.perf_counter()
        resp = await self._client.aio.models.generate_content(
            model=m, contents=contents, config=config,
        )
        latency = (time.perf_counter() - t0) * 1000
        text = resp.text or ""
        usage = getattr(resp, "usage_metadata", None)
        in_tok = getattr(usage, "prompt_token_count", 0) if usage else 0
        out_tok = getattr(usage, "candidates_token_count", 0) if usage else 0
        return LLMResult(
            text=text, parsed=None, usage_in_tokens=in_tok, usage_out_tokens=out_tok,
            cost_usd=estimate_cost_usd(m, in_tok, out_tok), model=m, latency_ms=latency,
            provider=self.provider, raw=resp,
        )

    async def extract(
        self,
        messages: list[dict[str, str]],
        schema: type[T],
        *,
        model: str | None = None,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResult[T]:
        m = model or DEFAULT_MODEL_BY_TIER[("gemini", "smart")]
        contents = []
        for msg in messages:
            role = "model" if msg["role"] == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": msg["content"]}]})
        config: dict[str, Any] = {
            "temperature": temperature,
            "max_output_tokens": max_tokens,
            "response_mime_type": "application/json",
            "response_schema": for_gemini(schema),
        }
        if system:
            config["system_instruction"] = system
        t0 = time.perf_counter()
        resp = await self._client.aio.models.generate_content(
            model=m, contents=contents, config=config,
        )
        latency = (time.perf_counter() - t0) * 1000
        text = resp.text or ""
        parsed: T | None = None
        try:
            parsed = schema.model_validate_json(text)
        except (ValidationError, json.JSONDecodeError) as e:
            logger.warning("Gemini structured output failed validation: %s", e)
        usage = getattr(resp, "usage_metadata", None)
        in_tok = getattr(usage, "prompt_token_count", 0) if usage else 0
        out_tok = getattr(usage, "candidates_token_count", 0) if usage else 0
        return LLMResult(
            text=text, parsed=parsed, usage_in_tokens=in_tok, usage_out_tokens=out_tok,
            cost_usd=estimate_cost_usd(m, in_tok, out_tok), model=m, latency_ms=latency,
            provider=self.provider, raw=resp,
        )


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


PROVIDER_ORDER: list[Provider] = ["claude", "openai", "gemini"]


class LLMRouter:
    """Picks a provider/model by tier and falls back across providers on hard failure."""

    def __init__(
        self,
        clients: dict[Provider, LLMClient] | None = None,
        default_provider: Provider | None = None,
        ledger: CostLedger | None = None,
    ) -> None:
        self.clients: dict[Provider, LLMClient] = clients or {}
        self.default_provider: Provider = (
            default_provider or os.getenv("LLM_DEFAULT_PROVIDER", "claude")  # type: ignore[assignment]
        )
        self.ledger = ledger or CostLedger()

    @classmethod
    def from_env(cls) -> "LLMRouter":
        clients: dict[Provider, LLMClient] = {}
        if os.getenv("ANTHROPIC_API_KEY"):
            clients["claude"] = ClaudeClient()
        if os.getenv("OPENAI_API_KEY"):
            clients["openai"] = OpenAIClient()
        if os.getenv("GOOGLE_API_KEY"):
            clients["gemini"] = GeminiClient()
        return cls(clients=clients)

    def _model_for(self, provider: Provider, tier: Tier) -> str:
        env_key = f"LLM_{tier.upper()}_MODEL"
        env_val = os.getenv(env_key)
        if env_val:
            return env_val
        return DEFAULT_MODEL_BY_TIER[(provider, tier)]

    def _fallback_chain(self, primary: Provider) -> list[Provider]:
        chain = [primary] + [p for p in PROVIDER_ORDER if p != primary]
        return [p for p in chain if p in self.clients]

    async def extract(
        self,
        messages: list[dict[str, str]],
        schema: type[T],
        *,
        tier: Tier = "smart",
        provider: Provider | None = None,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        max_retries: int = 2,
    ) -> LLMResult[T]:
        primary = provider or self.default_provider
        chain = self._fallback_chain(primary)
        if not chain:
            raise RuntimeError("No LLM clients configured. Set ANTHROPIC_API_KEY / OPENAI_API_KEY / GOOGLE_API_KEY.")
        last_result: LLMResult[T] | None = None
        for prov in chain:
            client = self.clients[prov]
            model = self._model_for(prov, tier)
            try:
                result = await client.extract_with_repair(
                    messages, schema, model=model, system=system,
                    temperature=temperature, max_tokens=max_tokens, max_retries=max_retries,
                )
                self.ledger.record(result)
                if result.parsed is not None:
                    return result
                last_result = result
                logger.warning("Provider %s exhausted retries; trying next.", prov)
            except Exception as e:
                logger.exception("Provider %s raised; falling back: %s", prov, e)
        # All providers failed — return the last attempt so caller can surface raw text.
        if last_result is not None:
            return last_result
        raise RuntimeError("All providers failed and no result captured.")

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        tier: Tier = "smart",
        provider: Provider | None = None,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> LLMResult[None]:
        primary = provider or self.default_provider
        chain = self._fallback_chain(primary)
        if not chain:
            raise RuntimeError("No LLM clients configured.")
        for prov in chain:
            client = self.clients[prov]
            model = self._model_for(prov, tier)
            try:
                result = await client.chat(
                    messages, model=model, system=system,
                    temperature=temperature, max_tokens=max_tokens,
                )
                self.ledger.record(result)
                return result
            except Exception as e:
                logger.exception("Provider %s chat failed; falling back: %s", prov, e)
        raise RuntimeError("All providers failed for chat().")
