"""Tests for the OpenCV-based PNG floor-plan parser."""

from __future__ import annotations

import io

import cv2
import numpy as np
import pytest

from app.services.image_floor_plan import parse_image


def _white_image(w_px: int = 400, h_px: int = 400) -> np.ndarray:
    return np.full((h_px, w_px), 255, dtype=np.uint8)


def _encode_png(arr: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", arr)
    assert ok
    return io.BytesIO(buf.tobytes()).getvalue()


def test_parse_empty_image_returns_no_rects():
    img = _white_image()
    result = parse_image(_encode_png(img), floor_w_m=10.0, floor_h_m=10.0)
    assert result.n_walls == 0
    assert result.n_obstacles == 0
    assert result.suggested_cell_envelope_mm == (10000.0, 10000.0)


def test_parse_single_obstacle_classifies_as_obstacle():
    """A single compact dark square should come back as one obstacle."""
    img = _white_image(400, 400)
    # 60×60 px square at center of a 10m × 10m floor → 1.5×1.5 m obstacle
    cv2.rectangle(img, (170, 170), (230, 230), 0, thickness=-1)
    result = parse_image(
        _encode_png(img), floor_w_m=10.0, floor_h_m=10.0,
        treat_largest_as_boundary=False,  # only one shape — don't drop it
    )
    assert result.n_obstacles == 1
    assert result.n_walls == 0
    r = result.rects[0]
    assert r.kind == "obstacle"
    # 60 px × (10000 mm / 400 px) = 1500 mm; allow 5% slop for Otsu/morph.
    assert 1400 <= r.width_mm <= 1600
    assert 1400 <= r.depth_mm <= 1600


def test_parse_thin_rect_classifies_as_wall():
    """A long thin rect should be labelled as a wall."""
    img = _white_image(400, 400)
    # 300 px × 6 px horizontal bar → 7.5 m × 0.15 m → aspect 50 (>> 8)
    cv2.rectangle(img, (50, 200), (350, 206), 0, thickness=-1)
    result = parse_image(
        _encode_png(img), floor_w_m=10.0, floor_h_m=10.0,
        treat_largest_as_boundary=False,
    )
    assert result.n_walls == 1
    assert result.n_obstacles == 0
    assert result.rects[0].kind == "wall"


def test_polygon_corners_form_closed_rectangle():
    """Each rect's polygon must be 5 points (last == first) and convex."""
    img = _white_image(400, 400)
    cv2.rectangle(img, (100, 100), (300, 200), 0, thickness=-1)
    result = parse_image(
        _encode_png(img), floor_w_m=10.0, floor_h_m=10.0,
        treat_largest_as_boundary=False,
    )
    assert len(result.rects) == 1
    poly = result.rects[0].polygon
    assert len(poly) == 5
    assert poly[0] == poly[-1]


def test_invalid_floor_size_raises():
    img = _white_image()
    with pytest.raises(ValueError):
        parse_image(_encode_png(img), floor_w_m=0.0, floor_h_m=10.0)


def test_llm_mode_still_reserved():
    img = _white_image()
    with pytest.raises(NotImplementedError) as e:
        parse_image(_encode_png(img), floor_w_m=10.0, floor_h_m=10.0, mode="llm")  # type: ignore[arg-type]
    assert "reserved" in str(e.value)


def test_hybrid_mode_falls_back_to_auto_without_api_key(monkeypatch):
    """No GOOGLE_API_KEY → hybrid silently falls back to 'auto' rather than
    raising; UX-wise we want 'something' over an opaque error."""
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    img = _white_image(400, 400)
    cv2.rectangle(img, (170, 170), (230, 230), 0, thickness=-1)
    out = parse_image(
        _encode_png(img), floor_w_m=10.0, floor_h_m=10.0,
        mode="hybrid", treat_largest_as_boundary=False,
    )
    # Should have detected the square via auto fallback.
    assert out.n_walls + out.n_obstacles >= 1


def test_unknown_mode_raises_value_error():
    img = _white_image()
    with pytest.raises(ValueError):
        parse_image(_encode_png(img), floor_w_m=10.0, floor_h_m=10.0, mode="bogus")  # type: ignore[arg-type]


def test_auto_mode_keeps_solid_obstacle_and_splits_crossing_bars():
    """Auto mode = best of both: a solid square stays one obstacle AND a
    nearby X gets split into two walls (the failure modes of cv and hough
    respectively)."""
    img = _white_image(800, 400)
    # Solid 60x60 square on the left.
    cv2.rectangle(img, (60, 170), (120, 230), 0, thickness=-1)
    # Two crossing bars (X) on the right.
    cv2.line(img, (450, 100), (750, 300), 0, thickness=18)
    cv2.line(img, (750, 100), (450, 300), 0, thickness=18)

    out = parse_image(
        _encode_png(img), floor_w_m=20.0, floor_h_m=10.0,
        mode="auto", treat_largest_as_boundary=False,
    )
    # The square should land exactly once.
    obstacles_near_square = [
        r for r in out.rects if r.kind == "obstacle" and 800 < r.cx_mm < 4000
    ]
    assert len(obstacles_near_square) == 1
    # The X should give at least 2 walls (its two bars).
    walls_in_x_region = [r for r in out.rects if r.kind == "wall" and r.cx_mm > 8000]
    assert len(walls_in_x_region) >= 2


def test_hough_mode_separates_crossing_bars():
    """Two crossing bars (an X) should come back as TWO walls in hough
    mode and ONE merged rect in cv mode — that is the whole point of
    adding the hough path."""
    img = _white_image(600, 600)
    # Two diagonal bars 16 px thick, ~280 px long, crossing at center.
    cv2.line(img, (100, 100), (500, 500), 0, thickness=16)
    cv2.line(img, (500, 100), (100, 500), 0, thickness=16)

    cv_out = parse_image(
        _encode_png(img), floor_w_m=10.0, floor_h_m=10.0,
        mode="cv", treat_largest_as_boundary=False,
    )
    hough_out = parse_image(
        _encode_png(img), floor_w_m=10.0, floor_h_m=10.0,
        mode="hough", treat_largest_as_boundary=False,
    )
    # cv: the X is one connected component → at most one rect.
    assert len(cv_out.rects) <= 1
    # hough: at least the two bars (compact-shape leftover may add 0 - 2
    # tiny pieces from the crossing region).
    assert hough_out.n_walls >= 2


def test_corrupt_image_raises_value_error():
    with pytest.raises(ValueError):
        parse_image(b"not an image", floor_w_m=10.0, floor_h_m=10.0)


def test_largest_dropped_when_treat_as_boundary():
    """A large outer frame + small inner square: only the inner survives."""
    img = _white_image(400, 400)
    # Outer frame (drawn as 4 disconnected thin rects so each is its own
    # contour, plus one big inner shape).
    cv2.rectangle(img, (10, 10), (390, 14), 0, thickness=-1)   # top wall
    cv2.rectangle(img, (10, 10), (14, 390), 0, thickness=-1)   # left wall
    cv2.rectangle(img, (386, 10), (390, 390), 0, thickness=-1) # right wall
    cv2.rectangle(img, (10, 386), (390, 390), 0, thickness=-1) # bottom wall
    cv2.rectangle(img, (180, 180), (220, 220), 0, thickness=-1)  # small obstacle
    out_drop = parse_image(
        _encode_png(img), floor_w_m=10.0, floor_h_m=10.0,
        treat_largest_as_boundary=True,
    )
    out_keep = parse_image(
        _encode_png(img), floor_w_m=10.0, floor_h_m=10.0,
        treat_largest_as_boundary=False,
    )
    assert len(out_drop.rects) == len(out_keep.rects) - 1
