"""Robot kinematics + ISO 13855 helpers — pure functions, no Pydantic.

Extracted from `backend/app/services/layout.py` in the original project so
the scoring module can import them without dragging in the whole greedy
generator. The receiving project can either import these as-is or inline
the constants directly — they're stable numbers from manufacturer
datasheets + ISO 13855:2010.
"""

from __future__ import annotations

import math
from typing import Protocol


# ---------------------------------------------------------------------------
# ISO 13855:2010 — robot safety separation distances
# ---------------------------------------------------------------------------

# S = K · T + C  (single-handed body case).
# K = approach speed of a walking body
# T = total system stopping time (PLC scan + brake + safety relay)
# C = intrusion distance allowed past the protective device
ISO_K_MM_PER_S = 2000.0
ISO_T_S = 0.30
ISO_C_BODY_MM = 850.0          # light curtain (body crossing detection)
ISO_C_HARD_GUARD_MM = 600.0    # interlocked hard guard (no light curtain)


# ---------------------------------------------------------------------------
# Trapezoidal motion profile (mm / mm/s / mm/s²)
# Tuned for typical palletizing arms; 6-axis arms get a 0.85 derate because
# they trade speed for dexterity vs purpose-built 4-axis palletizers.
# ---------------------------------------------------------------------------

V_MAX_MM_S_4AXIS = 2500.0
A_MAX_MM_S2_4AXIS = 8000.0
SIX_AXIS_DERATE = 0.85

# 400 mm down, 2000 mm across, 400 mm up — and back. ISO/RIA "standard cycle"
# for cph_std comparison.
STD_CYCLE_PATH_MM = 2 * (400.0 + 2000.0 + 400.0)


class RobotProto(Protocol):
    """Minimal robot protocol for kinematics. Either pass a RobotSpec from
    `xyz_modules.schemas.robot` or a duck-typed object with these attributes.
    """

    axes: int                       # 4, 5, or 6
    cycles_per_hour_std: float


def trapezoidal_time_s(distance_mm: float, v_max_mm_s: float, a_mm_s2: float) -> float:
    """Trapezoidal motion profile time.

    t = d/v + v/a if d ≥ v²/a (the trapezoid has a cruise phase)
    t = 2·√(d/a) otherwise (triangle — never reaches v_max)
    """
    if distance_mm <= 0:
        return 0.0
    threshold = (v_max_mm_s * v_max_mm_s) / a_mm_s2
    if distance_mm >= threshold:
        return distance_mm / v_max_mm_s + v_max_mm_s / a_mm_s2
    return 2.0 * math.sqrt(distance_mm / a_mm_s2)


def estimate_cycle_time_s(robot: RobotProto, dual_pallet: bool = False) -> float:
    """Single 400/2000/400 cycle in seconds.

    Adds 0.8 s for pick + place (gripper open/close + settle). Floors at
    `3600 / cycles_per_hour_std` since manufacturers tune for that — if the
    trapezoidal estimate is faster than the published cph, trust cph.
    """
    v = V_MAX_MM_S_4AXIS * (SIX_AXIS_DERATE if robot.axes == 6 else 1.0)
    a = A_MAX_MM_S2_4AXIS * (SIX_AXIS_DERATE if robot.axes == 6 else 1.0)
    motion_s = trapezoidal_time_s(STD_CYCLE_PATH_MM, v, a)
    cycle_s = motion_s + 0.8
    cycle_floor_s = 3600.0 / robot.cycles_per_hour_std
    cycle_s = max(cycle_s, cycle_floor_s)
    if dual_pallet:
        # η_overlap = 0.95: two pallets share one robot, time per case ≈
        # 1 / (2 · 0.95) of the single-pallet cycle.
        cycle_s = cycle_s / (2.0 * 0.95)
    return cycle_s


def estimate_uph(cycle_time_s: float) -> float:
    """Cases per hour from cycle time."""
    return 3600.0 / cycle_time_s if cycle_time_s > 0 else 0.0


def iso13855_safety_distance_mm(has_hard_guard: bool) -> float:
    """ISO 13855 separation distance for the single-handed body case.

    `has_hard_guard` = True → interlocked guard (C = 600 mm).
    `has_hard_guard` = False → light curtain (C = 850 mm).
    """
    c = ISO_C_HARD_GUARD_MM if has_hard_guard else ISO_C_BODY_MM
    return ISO_K_MM_PER_S * ISO_T_S + c
