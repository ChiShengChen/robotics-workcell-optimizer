#!/usr/bin/env python3
"""Generate a small library of sample DXF floor plans for the demo.

Run:
    .venv/bin/python backend/scripts/gen_sample_dxf.py

Drops .dxf files under backend/app/data/sample_dxf/. Each scenario is
designed to exercise a different code path:

  1. simple_8x6     — 8x6m cell, 1 small column (default test case)
  2. medium_12x8    — 12x8m cell, 1 column + 1 equipment box (dual-arm friendly)
  3. complex_15x10  — 15x10m cell, 4 columns + 2 equipment boxes (cluttered)
  4. tight_6x4      — 6x4m cell, dense column grid (forces small layouts)
  5. l_shape_10x10  — L-shaped outer wall + 1 column (non-convex envelope)
"""

from __future__ import annotations

import math
from pathlib import Path

import ezdxf

OUT_DIR = Path(__file__).resolve().parent.parent / "app" / "data" / "sample_dxf"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def write_dxf(name: str, build_fn) -> Path:
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    build_fn(msp)
    out = OUT_DIR / f"{name}.dxf"
    doc.saveas(out)
    print(f"  wrote {out}")
    return out


# ---------------------------------------------------------------------------


def simple_8x6(msp):
    """Default test: 8m x 6m cell with one 500mm-radius column at (3, 3)."""
    msp.add_lwpolyline([(0, 0), (8000, 0), (8000, 6000), (0, 6000), (0, 0)], close=True)
    msp.add_circle((3000, 3000), 500)


def medium_12x8(msp):
    """12m x 8m roomy cell — fits dual-arm dual-pallet comfortably."""
    msp.add_lwpolyline(
        [(0, 0), (12000, 0), (12000, 8000), (0, 8000), (0, 0)], close=True
    )
    # One column near the edge so it doesn't dominate the layout.
    msp.add_circle((1500, 1500), 400)
    # One pre-existing equipment box in a corner.
    msp.add_lwpolyline(
        [(10500, 6500), (11500, 6500), (11500, 7500), (10500, 7500), (10500, 6500)],
        close=True,
    )


def complex_15x10(msp):
    """15m x 10m cell with 4 columns + 2 equipment boxes — realistic cluttered floor."""
    msp.add_lwpolyline(
        [(0, 0), (15000, 0), (15000, 10000), (0, 10000), (0, 0)], close=True
    )
    # Four structural columns in a 2x2 grid (avoiding cell centre).
    for cx, cy in [(2500, 2500), (12500, 2500), (2500, 7500), (12500, 7500)]:
        msp.add_circle((cx, cy), 350)
    # Existing conveyor / equipment along the south wall.
    msp.add_lwpolyline(
        [(5500, 0), (9500, 0), (9500, 1200), (5500, 1200), (5500, 0)],
        close=True,
    )
    # Charging station / control cabinet in the north.
    msp.add_lwpolyline(
        [(13500, 4500), (15000, 4500), (15000, 5500), (13500, 5500), (13500, 4500)],
        close=True,
    )


def tight_6x4(msp):
    """6m x 4m tight cell with a dense column grid — stress test for placement."""
    msp.add_lwpolyline([(0, 0), (6000, 0), (6000, 4000), (0, 4000), (0, 0)], close=True)
    # 3 columns spaced across the cell.
    msp.add_circle((1500, 1500), 250)
    msp.add_circle((4500, 1500), 250)
    msp.add_circle((3000, 3500), 250)


def l_shape_10x10(msp):
    """L-shaped floor plan (10m x 10m bounding, with a 4m x 4m bite from the
    NE corner). Non-convex outer wall — exercises the bounding-box assumption.
    """
    # L-shape: outer wall as a single closed polyline.
    msp.add_lwpolyline(
        [
            (0, 0),
            (10000, 0),
            (10000, 6000),
            (6000, 6000),
            (6000, 10000),
            (0, 10000),
            (0, 0),
        ],
        close=True,
    )
    # One column in the long arm.
    msp.add_circle((3000, 8000), 400)
    # Equipment in the wide arm.
    msp.add_lwpolyline(
        [(7500, 1500), (9500, 1500), (9500, 3500), (7500, 3500), (7500, 1500)],
        close=True,
    )


SCENARIOS = [
    ("simple_8x6", simple_8x6),
    ("medium_12x8", medium_12x8),
    ("complex_15x10", complex_15x10),
    ("tight_6x4", tight_6x4),
    ("l_shape_10x10", l_shape_10x10),
]


def main():
    print(f"Writing {len(SCENARIOS)} sample DXF floor plans -> {OUT_DIR}")
    for name, fn in SCENARIOS:
        write_dxf(name, fn)
    print("Done.")


if __name__ == "__main__":
    main()
