"""xyz_modules — portable robotics workcell sub-systems.

Public surface (see README_HANDOFF.md for the full contract):

    Scoring + kinematics:
        from xyz_modules.scoring import score_layout
        from xyz_modules.kinematics import (
            trapezoidal_time_s, estimate_cycle_time_s, estimate_uph,
            iso13855_safety_distance_mm,
        )

    Floor-plan import (input):
        from xyz_modules.cad_import import parse_dxf, aabb_intersects_polygon
        from xyz_modules.image_floor_plan import parse_image

    CAD + BOM output:
        from xyz_modules.export_cad import (
            write_dxf, write_dwg, write_stl, write_step,
        )
        from xyz_modules.bom import build_bom, write_csv, write_markdown
        from xyz_modules.cad_export import render, build_cost_breakdown

    Schemas:
        from xyz_modules.schemas.workcell import WorkcellSpec
        from xyz_modules.schemas.robot import RobotSpec
        from xyz_modules.schemas.layout import (
            LayoutProposal, PlacedComponent, ScoreBreakdown, Violation,
        )
"""

__version__ = "0.1.0"
