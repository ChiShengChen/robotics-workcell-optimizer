"""Robot catalog service: load + filter the robots.json source-of-truth."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import HTTPException

from app.schemas.robot import IdealUseCase, RobotSpec

DEFAULT_CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "robots.json"


class RobotCatalogService:
    """Loads robots.json and offers filter / get-by-id queries."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or DEFAULT_CATALOG_PATH
        self._robots: list[RobotSpec] = []
        self._meta: dict = {}

    def load(self) -> list[RobotSpec]:
        with self._path.open() as fh:
            payload = json.load(fh)
        self._meta = payload.get("_meta", {})
        self._robots = [RobotSpec.model_validate(r) for r in payload["robots"]]
        return self._robots

    @property
    def robots(self) -> list[RobotSpec]:
        if not self._robots:
            self.load()
        return self._robots

    @property
    def meta(self) -> dict:
        if not self._robots:
            self.load()
        return self._meta

    def find(
        self,
        min_payload_kg: float | None = None,
        min_reach_mm: float | None = None,
        max_price_usd: float | None = None,
        axes_filter: list[int] | None = None,
        use_case_filter: list[IdealUseCase] | None = None,
        min_cycles_per_hour: float | None = None,
    ) -> list[RobotSpec]:
        """Filter by capability requirements; rank cheapest-first within feasible set."""
        results = []
        for r in self.robots:
            if min_payload_kg is not None and r.payload_kg < min_payload_kg:
                continue
            if min_reach_mm is not None and r.reach_mm < min_reach_mm:
                continue
            if max_price_usd is not None and r.price_usd_low > max_price_usd:
                continue
            if axes_filter and r.axes not in axes_filter:
                continue
            if use_case_filter and r.ideal_use_case not in use_case_filter:
                continue
            if min_cycles_per_hour is not None and r.cycles_per_hour_std < min_cycles_per_hour:
                continue
            results.append(r)
        results.sort(key=lambda r: r.price_usd_low)
        return results

    def get_by_id(self, model: str) -> RobotSpec:
        for r in self.robots:
            if r.model == model:
                return r
        raise HTTPException(status_code=404, detail=f"Robot model {model!r} not in catalog.")


_default_service: RobotCatalogService | None = None


def get_catalog() -> RobotCatalogService:
    """Process-wide singleton (lazy)."""
    global _default_service
    if _default_service is None:
        _default_service = RobotCatalogService()
        _default_service.load()
    return _default_service
