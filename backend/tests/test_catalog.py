"""Catalog load + filter tests."""

from __future__ import annotations

from app.schemas.robot import IdealUseCase
from app.services.catalog import RobotCatalogService


def test_catalog_loads_22_robots():
    svc = RobotCatalogService()
    robots = svc.load()
    assert len(robots) == 22
    # Spot-check a known model
    m410 = svc.get_by_id("M-410iC/110")
    assert m410.manufacturer == "FANUC"
    assert m410.payload_kg == 110.0
    assert m410.cycles_per_hour_std == 2200.0


def test_find_payload_and_axes_filter():
    svc = RobotCatalogService()
    svc.load()
    big_4axis = svc.find(min_payload_kg=200.0, axes_filter=[4])
    # Every result must satisfy both filters
    assert len(big_4axis) > 0
    for r in big_4axis:
        assert r.payload_kg >= 200.0
        assert r.axes == 4
    # All 4-axis 200 kg+ palletizers in the catalog (counted from JSON):
    # IRB 660-250, IRB 760, M-410iC/315, M-410iB/700, KR 700 PA, MPL300II, MPL500II,
    # MPL800II, CP300L, CP500L, CP700L → 11 robots.
    assert len(big_4axis) == 11
    # Sorted cheapest-first.
    prices = [r.price_usd_low for r in big_4axis]
    assert prices == sorted(prices)


def test_find_use_case_filter_for_mixed_sku():
    svc = RobotCatalogService()
    svc.load()
    mixed = svc.find(use_case_filter=[IdealUseCase.MIXED_SKU])
    # Per CLAUDE.md rule: mixed_sku should be assigned to ALL 6-axis robots.
    assert len(mixed) > 0
    for r in mixed:
        assert r.axes == 6


def test_find_no_match_returns_empty():
    svc = RobotCatalogService()
    svc.load()
    impossible = svc.find(min_payload_kg=2000.0, min_reach_mm=10000.0)
    assert impossible == []
