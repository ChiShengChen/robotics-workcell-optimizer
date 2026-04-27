"""DXF import + obstacle-intrusion scoring tests."""

from __future__ import annotations

import io

import ezdxf
import pytest

from app.schemas.layout import LayoutProposal, PlacedComponent
from app.schemas.obstacle import Obstacle
from app.schemas.workcell import (
    Conveyor,
    Pallet,
    Robot,
    Throughput,
    WorkcellSpec,
)
from app.services.cad_import import aabb_intersects_polygon, parse_dxf
from app.services.catalog import RobotCatalogService
from app.services.scoring import score_layout


@pytest.fixture(scope="module")
def catalog() -> RobotCatalogService:
    svc = RobotCatalogService()
    svc.load()
    return svc


@pytest.fixture
def sample_dxf_bytes() -> bytes:
    """A 12m x 8m floor with one circular column + one rectangular equipment piece."""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    # Outer wall (12 m x 8 m).
    msp.add_lwpolyline(
        [(0, 0), (12000, 0), (12000, 8000), (0, 8000), (0, 0)], close=True
    )
    # Internal column at (3000, 3000) r=500 mm.
    msp.add_circle((3000, 3000), 500)
    # Existing equipment 9000-10500 x 5000-6000.
    msp.add_lwpolyline(
        [(9000, 5000), (10500, 5000), (10500, 6000), (9000, 6000), (9000, 5000)],
        close=True,
    )
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode("utf-8")


# ---------------------------------------------------------------------------


def test_parse_dxf_extracts_entities_and_skips_outer_wall(sample_dxf_bytes):
    """Outer wall = largest closed polygon → treated as boundary, NOT an obstacle.
    The remaining column + equipment are returned as obstacles. Bounding box
    matches the outer wall + 200 mm margin shift."""
    result = parse_dxf(sample_dxf_bytes, scale_to_mm=1.0, margin_mm=200.0)
    # 3 entities total, 1 (outer wall) treated as boundary -> 2 obstacles.
    assert result.n_entities_imported == 2
    assert result.n_entities_skipped == 0
    assert result.bounding_box_mm == (200.0, 200.0, 12200.0, 8200.0)
    # Suggested envelope = bbox * 1.05.
    assert result.suggested_cell_envelope_mm == pytest.approx((12600.0, 8400.0))
    # Obstacles: circle (33 pts incl. close) + lwpolyline (5 pts).
    kinds = sorted(o.source_entity for o in result.obstacles)
    assert kinds == ["CIRCLE", "LWPOLYLINE"]


def test_parse_dxf_treat_largest_as_boundary_disabled(sample_dxf_bytes):
    """If the user explicitly disables boundary detection, all 3 closed
    polygons come back as obstacles."""
    result = parse_dxf(sample_dxf_bytes, scale_to_mm=1.0, margin_mm=0.0,
                      treat_largest_as_boundary=False)
    assert result.n_entities_imported == 3


def test_aabb_intersects_polygon_basic_cases():
    # Simple square obstacle 10..20 x 10..20.
    polygon = [[10.0, 10.0], [20.0, 10.0], [20.0, 20.0], [10.0, 20.0], [10.0, 10.0]]
    # Rect fully outside.
    assert not aabb_intersects_polygon(0, 0, 5, 5, polygon)
    # Rect fully inside.
    assert aabb_intersects_polygon(12, 12, 5, 5, polygon)
    # Rect overlapping edge.
    assert aabb_intersects_polygon(15, 15, 10, 10, polygon)
    # Rect entirely containing polygon.
    assert aabb_intersects_polygon(0, 0, 100, 100, polygon)
    # Rect touching corner only — bbox EDGES coincide; treated as overlap
    # in our implementation since a polygon vertex sits on a rect corner.
    # (Touch-only is acceptable for safety.)


def test_score_flags_obstacle_intrusion(catalog):
    """A spec with a 1500x1000mm obstacle directly under the robot's
    bbox should produce a HARD obstacle_intrusion violation."""
    spec = WorkcellSpec(
        cell_envelope_mm=(8000.0, 6000.0),
        components=[
            Robot(id="robot_1"),
            Conveyor(id="infeed_1", length_mm=2500.0, width_mm=600.0, flow_direction_deg=0.0),
            Pallet(id="pallet_a", standard="EUR"),
        ],
        throughput=Throughput(cases_per_hour_target=500.0),
        case_dims_mm=(400.0, 300.0, 220.0),
        case_mass_kg=12.0,
        pallet_standard="EUR",
        budget_usd=160_000.0,
        # Obstacle right under the robot center (cell ~4000, ~3000).
        obstacles=[
            Obstacle(
                id="cad_box",
                polygon=[[3500, 2500], [4500, 2500], [4500, 3500], [3500, 3500], [3500, 2500]],
                closed=True,
                source_entity="LWPOLYLINE",
            )
        ],
    )
    proposal = LayoutProposal(
        proposal_id="x", template="in_line", robot_model_id="MPL80II",
        components=[
            PlacedComponent(id="robot_1", type="robot", x_mm=4000, y_mm=3000,
                            dims={"base_radius_mm": 290, "reach_mm": 2061, "effective_reach_mm": 1752}),
            PlacedComponent(id="infeed_1", type="conveyor", x_mm=500, y_mm=2700,
                            dims={"length_mm": 2500, "width_mm": 600, "role": "infeed"}),
            PlacedComponent(id="pallet_a", type="pallet", x_mm=4900, y_mm=2600,
                            dims={"length_mm": 1200, "width_mm": 800}),
        ],
        cell_bounds_mm=(8000, 6000), estimated_cycle_time_s=1.7, estimated_uph=2000, rationale="",
    )
    robot = catalog.get_by_id("MPL80II")
    sb = score_layout(proposal, spec, robot)
    obstacle_v = [v for v in sb.violations if v.kind == "obstacle_intrusion"]
    assert len(obstacle_v) >= 1
    assert any(v.severity == "hard" for v in obstacle_v)
    assert sb.aggregate == 0.0  # any hard violation zeroes aggregate


def test_score_no_obstacle_no_violation(catalog):
    """Same proposal but with no obstacles → no obstacle_intrusion violation."""
    spec = WorkcellSpec(
        cell_envelope_mm=(8000.0, 6000.0),
        components=[Robot(id="robot_1"), Conveyor(id="i", length_mm=2500, width_mm=600, flow_direction_deg=0)],
        throughput=Throughput(cases_per_hour_target=500.0),
        obstacles=[],  # empty
    )
    proposal = LayoutProposal(
        proposal_id="x", template="in_line", robot_model_id="MPL80II",
        components=[
            PlacedComponent(id="robot_1", type="robot", x_mm=4000, y_mm=3000,
                            dims={"base_radius_mm": 290, "reach_mm": 2061, "effective_reach_mm": 1752}),
        ],
        cell_bounds_mm=(8000, 6000), estimated_cycle_time_s=1.7, estimated_uph=2000, rationale="",
    )
    robot = catalog.get_by_id("MPL80II")
    sb = score_layout(proposal, spec, robot)
    assert all(v.kind != "obstacle_intrusion" for v in sb.violations)
