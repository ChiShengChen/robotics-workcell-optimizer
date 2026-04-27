"""Extraction service: NL prompt → WorkcellSpec via LLMRouter."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path

from app.schemas.workcell import WorkcellSpec
from app.services.llm import LLMResult, LLMRouter

logger = logging.getLogger(__name__)

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"
LOGS_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

SYSTEM_PROMPT_PATH = PROMPT_DIR / "extract_spec.md"


# ---------------------------------------------------------------------------
# Few-shot examples (one beverage line, one ambiguous pallet — forces nulls)
# Both demonstrate populated `assumptions` arrays per the contract.
# ---------------------------------------------------------------------------

FEW_SHOT_BEVERAGE_USER = (
    "Beverage line palletizing canned soda trays. Each tray is 400 x 300 x 220 mm and "
    "weighs about 12 kg. Target rate is 500 cases per hour. Cell footprint about 8 m x 6 m. "
    "Use EUR pallets, dual pallet stations for continuous operation. Budget around 160k USD."
)

FEW_SHOT_BEVERAGE_ASSISTANT = WorkcellSpec.model_validate(
    {
        "schema_version": "1.0",
        "cell_envelope_mm": [8000.0, 6000.0],
        "components": [
            {"id": "robot_1", "type": "robot", "label": "Palletizing robot",
             "payload_kg": None, "reach_mm": None, "preferred_model": None},
            {"id": "infeed_1", "type": "conveyor", "label": "Infeed conveyor",
             "length_mm": 2500.0, "width_mm": 600.0, "flow_direction_deg": 0.0,
             "role": "infeed", "speed_mps": None},
            {"id": "pallet_a", "type": "pallet", "label": "Pallet station A",
             "standard": "EUR", "length_mm": 1200.0, "width_mm": 800.0, "pattern": "interlock"},
            {"id": "pallet_b", "type": "pallet", "label": "Pallet station B",
             "standard": "EUR", "length_mm": 1200.0, "width_mm": 800.0, "pattern": "interlock"},
            {"id": "fence_main", "type": "fence", "label": "Safety fence",
             "height_mm": 2000.0, "has_light_curtain": False},
            {"id": "operator_1", "type": "operator_zone", "label": "Operator access",
             "width_mm": 1500.0, "depth_mm": 1500.0},
        ],
        "constraints": [
            {"kind": "max_cycle_time", "hard": True, "target_id": None,
             "value": 7.2, "description": "500 cph -> 7.2 s/cycle ceiling."},
        ],
        "throughput": {"cases_per_hour_target": 500.0, "operating_hours_per_day": 20.0,
                       "sku_count": 1, "mixed_sequence": False},
        "case_dims_mm": [400.0, 300.0, 220.0],
        "case_mass_kg": 12.0,
        "pallet_standard": "EUR",
        "max_stack_height_mm": None,
        "budget_usd": 160000.0,
        "assumptions": [
            "Dual pallet stations inferred from 'continuous operation'.",
            "Operator access zone width set to 1500 mm (typical access lane).",
            "Robot payload left null; required >= case mass + EOAT (typ. 30 kg).",
            "Max stack height not stated.",
            "Interlock stacking pattern assumed for tray stability."
        ],
        "notes": "Throughput target 500 cph at one SKU.",
    }
).model_dump_json()

FEW_SHOT_AMBIG_USER = (
    "We need to palletize boxes of dry goods on a 6 by 4 meter floor. About 12 boxes a minute. "
    "Pallet type and box weight aren't finalized yet."
)

FEW_SHOT_AMBIG_ASSISTANT = WorkcellSpec.model_validate(
    {
        "schema_version": "1.0",
        "cell_envelope_mm": [6000.0, 4000.0],
        "components": [
            {"id": "robot_1", "type": "robot", "label": "Palletizing robot",
             "payload_kg": None, "reach_mm": None, "preferred_model": None},
            {"id": "infeed_1", "type": "conveyor", "label": "Infeed conveyor",
             "length_mm": 2000.0, "width_mm": 500.0, "flow_direction_deg": 0.0,
             "role": "infeed", "speed_mps": None},
            {"id": "pallet_a", "type": "pallet", "label": "Pallet station A",
             "standard": None, "length_mm": None, "width_mm": None, "pattern": None},
            {"id": "fence_main", "type": "fence", "label": "Safety fence",
             "height_mm": 2000.0, "has_light_curtain": False},
        ],
        "constraints": [],
        "throughput": {"cases_per_hour_target": 720.0, "operating_hours_per_day": 20.0,
                       "sku_count": 1, "mixed_sequence": False},
        "case_dims_mm": None,
        "case_mass_kg": None,
        "pallet_standard": None,
        "max_stack_height_mm": None,
        "budget_usd": None,
        "assumptions": [
            "12 boxes/minute converted to 720 cases_per_hour_target.",
            "Pallet standard not finalized; left null pending customer confirmation.",
            "Box dimensions and mass not stated; left null.",
            "Conveyor length set to 2000 mm (typical infeed) since not specified.",
            "Operator zone not requested in input; not added."
        ],
        "notes": "Multiple under-specified fields -- await confirmation before robot selection.",
    }
).model_dump_json()


def _load_system_prompt() -> str:
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def _hash_prompt(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _log_call(record: dict) -> None:
    """Append one JSON line to backend/logs/llm.jsonl."""
    log_path = LOGS_DIR / "llm.jsonl"
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


async def extract_workcell_spec(
    prompt: str, router: LLMRouter
) -> tuple[WorkcellSpec | None, LLMResult]:
    """Run the few-shot extraction. Returns (parsed_or_none, raw_result_for_debug)."""
    system_prompt = _load_system_prompt()
    messages = [
        {"role": "user", "content": FEW_SHOT_BEVERAGE_USER},
        {"role": "assistant", "content": FEW_SHOT_BEVERAGE_ASSISTANT},
        {"role": "user", "content": FEW_SHOT_AMBIG_USER},
        {"role": "assistant", "content": FEW_SHOT_AMBIG_ASSISTANT},
        {"role": "user", "content": prompt},
    ]
    t0 = time.perf_counter()
    result = await router.extract(
        messages, WorkcellSpec, tier="fast", system=system_prompt, temperature=0.0,
    )
    elapsed = (time.perf_counter() - t0) * 1000
    _log_call(
        {
            "kind": "extract",
            "prompt_hash": _hash_prompt(prompt),
            "model": result.model,
            "provider": result.provider,
            "in_tokens": result.usage_in_tokens,
            "out_tokens": result.usage_out_tokens,
            "cost_usd": round(result.cost_usd, 6),
            "latency_ms": round(elapsed, 1),
            "parsed_ok": result.parsed is not None,
        }
    )
    return result.parsed, result
