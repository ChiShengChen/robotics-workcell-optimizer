"""Export a layout_config_trial_*.json into 2D DXF + 3D STL.

Reads the trial config in the same folder, writes:
  cad_flow/{trial_stem}.dxf   — top-down 2D plan (LWPOLYLINE per component)
  cad_flow/{trial_stem}.stl   — 3D mesh (boxes + pallet stack + reach disc)

Unit convention (matches the trial JSON):
  - positions: meters
  - sizes (pallet_size_mm, box_size_mm, gripper.collision_size_m): mixed,
    explicit suffix in field name. We normalise everything to mm internally.

Usage:
  python cad_flow/export_cad.py cad_flow/layout_config_trial_596641.json
  python cad_flow/export_cad.py            # picks the only trial in cad_flow/
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import ezdxf
import trimesh

PKG_ROOT = Path(__file__).resolve().parent
ROBOT_CATALOG = PKG_ROOT / "data" / "robots.json"
# CAD_FLOW alias retained for the CLI's relative_to() output strings.
CAD_FLOW = PKG_ROOT

DEFAULT_ROBOT_FOOTPRINT_MM = (800.0, 800.0)
DEFAULT_ROBOT_REACH_MM = 2100.0
ROBOT_BODY_HEIGHT_MM = 900.0    # rough stand-in for the arm bounding box
GRIPPER_FALLBACK_MM = (300.0, 180.0, 105.0)

# Fence / safety zone defaults (ISO 13855 / 13857)
FENCE_HEIGHT_MM = 2100.0        # ISO 13857 typical perimeter guard height
FENCE_OFFSET_MM = 500.0         # gap from reach envelope to fence interior
FENCE_THICKNESS_MM = 50.0       # mesh-panel thickness for the 3D extrude
OPERATOR_ZONE_DEPTH_MM = 1200.0 # walking aisle behind the conveyor infeed


def load_robot(model_id: str) -> dict | None:
    if not ROBOT_CATALOG.exists():
        return None
    data = json.loads(ROBOT_CATALOG.read_text())
    target = model_id.replace("_", " ").lower()
    for r in data.get("robots", []):
        key = f"{r.get('manufacturer','')} {r.get('model','')}".lower()
        if target in key or key in target:
            return r
    return None


def m_to_mm(v: float) -> float:
    return v * 1000.0


def rect_corners(cx_mm: float, cy_mm: float, w_mm: float, h_mm: float, yaw_deg: float):
    """Return 4 corner (x, y) in mm for a rectangle centred at (cx, cy)."""
    a = math.radians(yaw_deg)
    cos_a, sin_a = math.cos(a), math.sin(a)
    hw, hh = w_mm / 2.0, h_mm / 2.0
    local = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
    return [(cx_mm + lx * cos_a - ly * sin_a, cy_mm + lx * sin_a + ly * cos_a) for lx, ly in local]


# ---------------------------------------------------------------------------
# 2D DXF
# ---------------------------------------------------------------------------


def write_dxf(cfg: dict, robot: dict | None, out_path: Path) -> None:
    doc = ezdxf.new(dxfversion="R2010", setup=True)
    doc.units = ezdxf.units.MM
    msp = doc.modelspace()

    layers = {
        "ROBOT": 1,        # red
        "REACH": 8,        # grey
        "CONVEYOR": 5,     # blue
        "PALLET": 3,       # green
        "STACK": 2,        # yellow
        "GRIPPER": 6,      # magenta
        "FENCE": 1,        # red — safety
        "OPERATOR_ZONE": 4,  # cyan
        "ANNOTATION": 7,   # white/black
    }
    for name, color in layers.items():
        if name not in doc.layers:
            doc.layers.add(name=name, color=color)

    # Robot footprint
    rx_mm = m_to_mm(cfg["robot_position_xy"][0])
    ry_mm = m_to_mm(cfg["robot_position_xy"][1])
    yaw = float(cfg.get("robot_yaw_deg", 0.0))
    if robot:
        rfw = float(robot.get("footprint_w_mm", DEFAULT_ROBOT_FOOTPRINT_MM[1]))
        rfl = float(robot.get("footprint_l_mm", DEFAULT_ROBOT_FOOTPRINT_MM[0]))
        reach_mm = float(robot.get("reach_mm", DEFAULT_ROBOT_REACH_MM))
    else:
        rfl, rfw = DEFAULT_ROBOT_FOOTPRINT_MM
        reach_mm = DEFAULT_ROBOT_REACH_MM
    msp.add_lwpolyline(
        [(x, y) for x, y in rect_corners(rx_mm, ry_mm, rfl, rfw, yaw)],
        close=True,
        dxfattribs={"layer": "ROBOT"},
    )
    msp.add_circle((rx_mm, ry_mm), reach_mm, dxfattribs={"layer": "REACH"})
    msp.add_text(
        cfg.get("robot_id", "ROBOT"),
        dxfattribs={"layer": "ANNOTATION", "height": 80},
    ).set_placement((rx_mm, ry_mm + rfw / 2 + 100))

    # Conveyor (use collision box centre + dims)
    conv = cfg["conveyor_collision"]
    cv_cx = m_to_mm(conv["center"][0])
    cv_cy = m_to_mm(conv["center"][1])
    cv_w = m_to_mm(conv["dimensions"][0])
    cv_h = m_to_mm(conv["dimensions"][1])
    msp.add_lwpolyline(
        [(x, y) for x, y in rect_corners(cv_cx, cv_cy, cv_w, cv_h, 0.0)],
        close=True,
        dxfattribs={"layer": "CONVEYOR"},
    )
    msp.add_text("CONVEYOR", dxfattribs={"layer": "ANNOTATION", "height": 60}).set_placement(
        (cv_cx, cv_cy)
    )

    # Pallet
    px_mm = m_to_mm(cfg["pallet_position"][0])
    py_mm = m_to_mm(cfg["pallet_position"][1])
    pl_l, pl_w, _ = cfg["pallet_size_mm"]
    pyaw = float(cfg.get("pallet_yaw_deg", 0.0))
    msp.add_lwpolyline(
        [(x, y) for x, y in rect_corners(px_mm, py_mm, pl_l, pl_w, pyaw)],
        close=True,
        dxfattribs={"layer": "PALLET"},
    )
    msp.add_text("PALLET", dxfattribs={"layer": "ANNOTATION", "height": 60}).set_placement(
        (px_mm, py_mm)
    )

    # Box stack footprint (top-down: stack_rows × stack_columns of box footprints)
    bx_l, bx_w, _ = cfg["box_size_mm"]
    rows = int(cfg.get("stack_rows", 1))
    cols = int(cfg.get("stack_columns", 1))
    total_l = cols * bx_l
    total_w = rows * bx_w
    # Centre the stack on the pallet
    start_x = px_mm - total_l / 2.0
    start_y = py_mm - total_w / 2.0
    for i in range(rows):
        for j in range(cols):
            cx = start_x + (j + 0.5) * bx_l
            cy = start_y + (i + 0.5) * bx_w
            msp.add_lwpolyline(
                [(x, y) for x, y in rect_corners(cx, cy, bx_l, bx_w, pyaw)],
                close=True,
                dxfattribs={"layer": "STACK"},
            )

    # Fence + operator zone
    fence_poly, op_poly = _safety_polygons(
        rx_mm, ry_mm, reach_mm, cv_cx, cv_cy, cv_w, cv_h, px_mm, py_mm, pl_l, pl_w
    )
    msp.add_lwpolyline(fence_poly, close=True, dxfattribs={"layer": "FENCE"})
    msp.add_text(
        f"SAFETY FENCE (h={int(FENCE_HEIGHT_MM)}mm)",
        dxfattribs={"layer": "ANNOTATION", "height": 70},
    ).set_placement((fence_poly[0][0], fence_poly[0][1] - 120))
    msp.add_lwpolyline(op_poly, close=True, dxfattribs={"layer": "OPERATOR_ZONE"})
    msp.add_text(
        "OPERATOR ZONE",
        dxfattribs={"layer": "ANNOTATION", "height": 70},
    ).set_placement((sum(p[0] for p in op_poly) / 4, sum(p[1] for p in op_poly) / 4))

    doc.saveas(out_path)


def _safety_polygons(
    rx, ry, reach, cv_cx, cv_cy, cv_w, cv_h, px, py, pl_l, pl_w
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Compute fence + operator-zone polygons (mm).

    Fence: union AABB of (reach annulus + conveyor + pallet), inflated by
    FENCE_OFFSET_MM. Operator zone: a strip behind the conveyor infeed
    (opposite end from the robot).
    """
    xs = [rx - reach, rx + reach, cv_cx - cv_w / 2, cv_cx + cv_w / 2, px - pl_l / 2, px + pl_l / 2]
    ys = [ry - reach, ry + reach, cv_cy - cv_h / 2, cv_cy + cv_h / 2, py - pl_w / 2, py + pl_w / 2]
    min_x, max_x = min(xs) - FENCE_OFFSET_MM, max(xs) + FENCE_OFFSET_MM
    min_y, max_y = min(ys) - FENCE_OFFSET_MM, max(ys) + FENCE_OFFSET_MM
    fence = [(min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)]

    # Operator zone: aisle on the conveyor infeed side (further from the robot
    # along x). If the conveyor sits to the robot's -x, infeed is at min_x.
    infeed_left = cv_cx < rx
    if infeed_left:
        op_x_max = min_x
        op_x_min = min_x - OPERATOR_ZONE_DEPTH_MM
    else:
        op_x_min = max_x
        op_x_max = max_x + OPERATOR_ZONE_DEPTH_MM
    op = [(op_x_min, min_y), (op_x_max, min_y), (op_x_max, max_y), (op_x_min, max_y)]
    return fence, op


# ---------------------------------------------------------------------------
# 3D STL
# ---------------------------------------------------------------------------


def box_mesh(center_mm, size_mm, yaw_deg=0.0) -> trimesh.Trimesh:
    """Centre at (cx, cy, cz). Size in mm. Optional yaw about Z."""
    box = trimesh.creation.box(extents=size_mm)
    if yaw_deg:
        box.apply_transform(trimesh.transformations.rotation_matrix(math.radians(yaw_deg), [0, 0, 1]))
    box.apply_translation(center_mm)
    return box


def cylinder_mesh(center_mm, radius_mm, height_mm) -> trimesh.Trimesh:
    cyl = trimesh.creation.cylinder(radius=radius_mm, height=height_mm, sections=32)
    cyl.apply_translation(center_mm)
    return cyl


def write_stl(cfg: dict, robot: dict | None, out_path: Path) -> None:
    meshes: list[trimesh.Trimesh] = []

    # --- Conveyor (full 3D box from collision spec) ---
    conv = cfg["conveyor_collision"]
    cv_center = (m_to_mm(conv["center"][0]), m_to_mm(conv["center"][1]), m_to_mm(conv["center"][2]))
    cv_size = (m_to_mm(conv["dimensions"][0]), m_to_mm(conv["dimensions"][1]), m_to_mm(conv["dimensions"][2]))
    meshes.append(box_mesh(cv_center, cv_size))

    # --- Robot pedestal (cylinder) + arm body (box on top) ---
    rx_mm = m_to_mm(cfg["robot_position_xy"][0])
    ry_mm = m_to_mm(cfg["robot_position_xy"][1])
    ped_h = m_to_mm(float(cfg.get("pedestal_height_m", 0.0)))
    if robot:
        rfw = float(robot.get("footprint_w_mm", DEFAULT_ROBOT_FOOTPRINT_MM[1]))
        rfl = float(robot.get("footprint_l_mm", DEFAULT_ROBOT_FOOTPRINT_MM[0]))
    else:
        rfl, rfw = DEFAULT_ROBOT_FOOTPRINT_MM
    ped_radius = min(rfl, rfw) / 2.0
    if ped_h > 1.0:
        meshes.append(cylinder_mesh((rx_mm, ry_mm, ped_h / 2.0), ped_radius, ped_h))

    arm_center = (rx_mm, ry_mm, ped_h + ROBOT_BODY_HEIGHT_MM / 2.0)
    meshes.append(box_mesh(arm_center, (rfl, rfw, ROBOT_BODY_HEIGHT_MM), yaw_deg=cfg.get("robot_yaw_deg", 0.0)))

    # --- Pallet ---
    px_mm = m_to_mm(cfg["pallet_position"][0])
    py_mm = m_to_mm(cfg["pallet_position"][1])
    pz_mm = m_to_mm(cfg["pallet_position"][2])
    pl_l, pl_w, pl_h = cfg["pallet_size_mm"]
    pyaw = float(cfg.get("pallet_yaw_deg", 0.0))
    # pallet_position[2] given as half-thickness ground reference -> centre the pallet there
    meshes.append(box_mesh((px_mm, py_mm, pz_mm), (pl_l, pl_w, pl_h), yaw_deg=pyaw))

    # --- Box stack ---
    bx_l, bx_w, bx_h = cfg["box_size_mm"]
    rows = int(cfg.get("stack_rows", 1))
    cols = int(cfg.get("stack_columns", 1))
    layers = int(cfg.get("stack_layers", 1))
    total_l = cols * bx_l
    total_w = rows * bx_w
    start_x = px_mm - total_l / 2.0
    start_y = py_mm - total_w / 2.0
    pallet_top_z = pz_mm + pl_h / 2.0
    for k in range(layers):
        z = pallet_top_z + (k + 0.5) * bx_h
        for i in range(rows):
            for j in range(cols):
                cx = start_x + (j + 0.5) * bx_l
                cy = start_y + (i + 0.5) * bx_w
                meshes.append(box_mesh((cx, cy, z), (bx_l, bx_w, bx_h), yaw_deg=pyaw))

    # --- Gripper (placed above pallet centre as a parked pose) ---
    grip = cfg.get("gripper", {}).get("collision_size_m")
    if grip:
        gsize = (m_to_mm(grip[0]), m_to_mm(grip[1]), m_to_mm(grip[2]))
    else:
        gsize = GRIPPER_FALLBACK_MM
    stack_top_z = pallet_top_z + layers * bx_h
    meshes.append(box_mesh((px_mm, py_mm, stack_top_z + 200.0), gsize))

    # --- Fence (4 thin walls extruded to FENCE_HEIGHT_MM) + operator zone pad ---
    if robot:
        reach_mm = float(robot.get("reach_mm", DEFAULT_ROBOT_REACH_MM))
    else:
        reach_mm = DEFAULT_ROBOT_REACH_MM
    fence_poly, op_poly = _safety_polygons(
        rx_mm, ry_mm, reach_mm,
        m_to_mm(conv["center"][0]), m_to_mm(conv["center"][1]),
        m_to_mm(conv["dimensions"][0]), m_to_mm(conv["dimensions"][1]),
        px_mm, py_mm, pl_l, pl_w,
    )
    meshes.extend(_fence_walls_3d(fence_poly))
    op_min = (min(p[0] for p in op_poly), min(p[1] for p in op_poly))
    op_max = (max(p[0] for p in op_poly), max(p[1] for p in op_poly))
    op_w = op_max[0] - op_min[0]
    op_h = op_max[1] - op_min[1]
    op_cx = (op_min[0] + op_max[0]) / 2.0
    op_cy = (op_min[1] + op_max[1]) / 2.0
    meshes.append(box_mesh((op_cx, op_cy, 5.0), (op_w, op_h, 10.0)))

    scene = trimesh.util.concatenate(meshes)
    scene.export(out_path)


def _fence_walls_3d(poly: list[tuple[float, float]]) -> list[trimesh.Trimesh]:
    """Extrude 4 axis-aligned wall panels of FENCE_THICKNESS_MM × FENCE_HEIGHT_MM."""
    walls: list[trimesh.Trimesh] = []
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        if abs(x2 - x1) > abs(y2 - y1):
            size = (abs(x2 - x1), FENCE_THICKNESS_MM, FENCE_HEIGHT_MM)
        else:
            size = (FENCE_THICKNESS_MM, abs(y2 - y1), FENCE_HEIGHT_MM)
        walls.append(box_mesh((cx, cy, FENCE_HEIGHT_MM / 2.0), size))
    return walls


# ---------------------------------------------------------------------------
# 3D STEP (BREP) — optional, requires `cadquery`
# ---------------------------------------------------------------------------


def write_step(cfg: dict, robot: dict | None, out_path: Path) -> None:
    """Build the same scene as the STL but as proper BREP solids and export STEP.

    Lazy-imports cadquery so the script still runs (DXF + STL) without it.
    """
    try:
        import cadquery as cq
    except ImportError as e:
        raise RuntimeError(
            "cadquery is required for STEP export. Install: pip install cadquery"
        ) from e

    assembly = cq.Assembly()

    # Conveyor
    conv = cfg["conveyor_collision"]
    cv_cx, cv_cy, cv_cz = (m_to_mm(conv["center"][i]) for i in range(3))
    cv_w_, cv_h_, cv_d_ = (m_to_mm(conv["dimensions"][i]) for i in range(3))
    assembly.add(
        cq.Workplane("XY").box(cv_w_, cv_h_, cv_d_),
        loc=cq.Location(cq.Vector(cv_cx, cv_cy, cv_cz)),
        name="conveyor",
    )

    # Robot pedestal + arm
    rx_mm = m_to_mm(cfg["robot_position_xy"][0])
    ry_mm = m_to_mm(cfg["robot_position_xy"][1])
    ped_h = m_to_mm(float(cfg.get("pedestal_height_m", 0.0)))
    if robot:
        rfl = float(robot.get("footprint_l_mm", DEFAULT_ROBOT_FOOTPRINT_MM[0]))
        rfw = float(robot.get("footprint_w_mm", DEFAULT_ROBOT_FOOTPRINT_MM[1]))
        reach_mm = float(robot.get("reach_mm", DEFAULT_ROBOT_REACH_MM))
    else:
        rfl, rfw = DEFAULT_ROBOT_FOOTPRINT_MM
        reach_mm = DEFAULT_ROBOT_REACH_MM
    if ped_h > 1.0:
        assembly.add(
            cq.Workplane("XY").circle(min(rfl, rfw) / 2.0).extrude(ped_h),
            loc=cq.Location(cq.Vector(rx_mm, ry_mm, 0.0)),
            name="pedestal",
        )
    yaw = float(cfg.get("robot_yaw_deg", 0.0))
    arm = cq.Workplane("XY").box(rfl, rfw, ROBOT_BODY_HEIGHT_MM)
    assembly.add(
        arm,
        loc=cq.Location(cq.Vector(rx_mm, ry_mm, ped_h + ROBOT_BODY_HEIGHT_MM / 2.0), cq.Vector(0, 0, 1), yaw),
        name="robot_arm",
    )

    # Pallet
    px_mm = m_to_mm(cfg["pallet_position"][0])
    py_mm = m_to_mm(cfg["pallet_position"][1])
    pz_mm = m_to_mm(cfg["pallet_position"][2])
    pl_l, pl_w, pl_h = cfg["pallet_size_mm"]
    pyaw = float(cfg.get("pallet_yaw_deg", 0.0))
    assembly.add(
        cq.Workplane("XY").box(pl_l, pl_w, pl_h),
        loc=cq.Location(cq.Vector(px_mm, py_mm, pz_mm), cq.Vector(0, 0, 1), pyaw),
        name="pallet",
    )

    # Box stack
    bx_l, bx_w, bx_h = cfg["box_size_mm"]
    rows = int(cfg.get("stack_rows", 1))
    cols = int(cfg.get("stack_columns", 1))
    n_layers = int(cfg.get("stack_layers", 1))
    start_x = px_mm - cols * bx_l / 2.0
    start_y = py_mm - rows * bx_w / 2.0
    pallet_top_z = pz_mm + pl_h / 2.0
    for k in range(n_layers):
        z = pallet_top_z + (k + 0.5) * bx_h
        for i in range(rows):
            for j in range(cols):
                cx = start_x + (j + 0.5) * bx_l
                cy = start_y + (i + 0.5) * bx_w
                assembly.add(
                    cq.Workplane("XY").box(bx_l, bx_w, bx_h),
                    loc=cq.Location(cq.Vector(cx, cy, z), cq.Vector(0, 0, 1), pyaw),
                    name=f"box_L{k}_R{i}_C{j}",
                )

    # Fence (4 wall solids)
    fence_poly, op_poly = _safety_polygons(
        rx_mm, ry_mm, reach_mm, cv_cx, cv_cy, cv_w_, cv_h_, px_mm, py_mm, pl_l, pl_w
    )
    for idx in range(len(fence_poly)):
        x1, y1 = fence_poly[idx]
        x2, y2 = fence_poly[(idx + 1) % len(fence_poly)]
        cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        if abs(x2 - x1) > abs(y2 - y1):
            sx, sy = abs(x2 - x1), FENCE_THICKNESS_MM
        else:
            sx, sy = FENCE_THICKNESS_MM, abs(y2 - y1)
        assembly.add(
            cq.Workplane("XY").box(sx, sy, FENCE_HEIGHT_MM),
            loc=cq.Location(cq.Vector(cx, cy, FENCE_HEIGHT_MM / 2.0)),
            name=f"fence_{idx}",
        )

    # Operator zone pad
    op_min = (min(p[0] for p in op_poly), min(p[1] for p in op_poly))
    op_max = (max(p[0] for p in op_poly), max(p[1] for p in op_poly))
    op_w = op_max[0] - op_min[0]
    op_h = op_max[1] - op_min[1]
    op_cx = (op_min[0] + op_max[0]) / 2.0
    op_cy = (op_min[1] + op_max[1]) / 2.0
    assembly.add(
        cq.Workplane("XY").box(op_w, op_h, 10.0),
        loc=cq.Location(cq.Vector(op_cx, op_cy, 5.0)),
        name="operator_zone",
    )

    assembly.save(str(out_path), exportType="STEP")


# ---------------------------------------------------------------------------
# 2D DWG (AutoCAD binary) — via LibreDWG's dxf2dwg CLI
# ---------------------------------------------------------------------------
#
# DWG is Autodesk-proprietary. There is no pip-installable native writer in
# Python. We use LibreDWG's `dxf2dwg` as a subprocess: write our DXF first,
# then convert. ezdxf is the source of truth — anything you see in DWG was
# written via write_dxf() one second earlier.
#
# Install dxf2dwg (CLI binary from LibreDWG, GPLv3):
#   - macOS (source): https://github.com/LibreDWG/libredwg → ./configure && make
#   - Linux:          apt install libredwg-tools  (Debian/Ubuntu)
#   - Alt path:       ODA File Converter (https://www.opendesign.com),
#                     drop ODAFileConverter binary onto PATH and we'll find it.


def write_dwg(cfg: dict, robot: dict | None, out_path: Path) -> None:
    """Write DWG by routing DXF through LibreDWG's dxf2dwg CLI.

    Raises RuntimeError with install hint if no converter is on PATH.
    """
    converter = _find_dwg_converter()
    if converter is None:
        raise RuntimeError(
            "DWG export requires a DXF→DWG converter on PATH. Install one of:\n"
            "  - LibreDWG (open source): https://github.com/LibreDWG/libredwg\n"
            "  - ODA File Converter (free, registration required): "
            "https://www.opendesign.com/guestfiles/oda_file_converter\n"
            "Then add the binary to PATH and re-export."
        )

    # Write DXF to a tempfile, then convert in-place to DWG.
    with tempfile.TemporaryDirectory() as td:
        dxf_path = Path(td) / "scene.dxf"
        write_dxf(cfg, robot, dxf_path)
        name, kind = converter
        if kind == "libredwg":
            # dxf2dwg [-o outfile] [--as rNNNN] DXFFILE
            # -y not a flag here — dxf2dwg refuses to overwrite. Tempfile
            # path is fresh so it's a non-issue; we control out_path too.
            if out_path.exists():
                out_path.unlink()
            res = subprocess.run(
                [name, "-o", str(out_path), "--as", "r2000", str(dxf_path)],
                capture_output=True, text=True, check=False,
            )
        else:
            # ODA File Converter expects directory-level conversion:
            # ODAFileConverter <inDir> <outDir> <ver> <type> <recurse> <audit> [filter]
            res = subprocess.run(
                [
                    name, str(dxf_path.parent), str(out_path.parent),
                    "ACAD2018", "DWG", "0", "1", "*.dxf",
                ],
                capture_output=True, text=True, check=False,
            )
            produced = out_path.parent / "scene.dwg"
            if produced.exists() and produced != out_path:
                produced.rename(out_path)

        if res.returncode != 0 or not out_path.exists():
            raise RuntimeError(
                f"DWG conversion failed ({name}). stderr:\n{res.stderr or res.stdout}"
            )


def _find_dwg_converter() -> tuple[str, str] | None:
    """Locate a DXF→DWG converter binary. Returns (path, kind) or None.

    kind is 'libredwg' for dxf2dwg-style CLI or 'oda' for ODA File Converter.
    Prefer LibreDWG since its CLI is per-file (cleaner) than ODA's per-dir.

    Searches PATH plus a few common user-local install dirs (`~/.local/bin`
    in particular — `make install prefix=$HOME/.local` for source builds).
    """
    # Extra dirs that often aren't on the uvicorn process's PATH.
    extra_dirs = [
        Path.home() / ".local" / "bin",
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
    ]

    def look(binary: str) -> str | None:
        p = shutil.which(binary)
        if p:
            return p
        for d in extra_dirs:
            cand = d / binary
            if cand.exists() and cand.is_file():
                return str(cand)
        return None

    for name in ("dxf2dwg",):
        p = look(name)
        if p:
            return p, "libredwg"
    for name in ("ODAFileConverter", "OdaFileConverter"):
        p = look(name)
        if p:
            return p, "oda"
    # macOS app bundle fallback for ODA
    for app in (
        "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter",
    ):
        if Path(app).exists():
            return app, "oda"
    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    flags = {a for a in argv[1:] if a.startswith("--")}
    want_step = "--step" in flags
    want_dwg = "--dwg" in flags
    skip_stl = "--no-stl" in flags
    skip_dxf = "--no-dxf" in flags

    if args:
        cfg_path = Path(args[0]).resolve()
    else:
        candidates = sorted(CAD_FLOW.glob("layout_config_trial_*.json"))
        if not candidates:
            print(f"no layout_config_trial_*.json found in {CAD_FLOW}", file=sys.stderr)
            return 1
        cfg_path = candidates[0]

    cfg = json.loads(cfg_path.read_text())
    robot = load_robot(cfg.get("robot_id", ""))
    if robot is None:
        print(f"warn: robot {cfg.get('robot_id')!r} not found in catalog — using defaults", file=sys.stderr)

    stem = cfg_path.stem
    if not skip_dxf:
        dxf_out = CAD_FLOW / f"{stem}.dxf"
        write_dxf(cfg, robot, dxf_out)
        print(f"wrote {dxf_out.relative_to(CAD_FLOW.parent)}")
    if not skip_stl:
        stl_out = CAD_FLOW / f"{stem}.stl"
        write_stl(cfg, robot, stl_out)
        print(f"wrote {stl_out.relative_to(CAD_FLOW.parent)}")
    if want_step:
        step_out = CAD_FLOW / f"{stem}.step"
        write_step(cfg, robot, step_out)
        print(f"wrote {step_out.relative_to(CAD_FLOW.parent)}")
    if want_dwg:
        dwg_out = CAD_FLOW / f"{stem}.dwg"
        try:
            write_dwg(cfg, robot, dwg_out)
            print(f"wrote {dwg_out.relative_to(CAD_FLOW.parent)}")
        except RuntimeError as e:
            print(f"warn: DWG export skipped — {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
