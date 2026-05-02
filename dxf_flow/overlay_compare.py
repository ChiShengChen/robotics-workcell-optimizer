"""Side-by-side overlay of cv vs hough parser modes for each PNG.

Writes <name>_overlay.png next to each input — left half = cv, right half =
hough — so you can eyeball what each mode catches and what it misses.

Run from repo root:
    cd backend && source .venv/bin/activate && \
    python ../dxf_flow/overlay_compare.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

# Make `app.*` importable when run from anywhere.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services.image_floor_plan import ImageParseResult, parse_image  # noqa: E402

IMG_DIR = Path(__file__).resolve().parent
FLOOR_W_M = 10.0
FLOOR_H_M = 10.0

# BGR for cv2.
COLOUR = {
    "wall": (0, 0, 220),       # red
    "obstacle": (220, 120, 0),  # blue
}


def _draw_overlay(canvas: np.ndarray, result: ImageParseResult, margin_mm: float = 200.0) -> np.ndarray:
    h_px, w_px = canvas.shape[:2]
    mm_per_px_x = (FLOOR_W_M * 1000.0) / w_px
    mm_per_px_y = (FLOOR_H_M * 1000.0) / h_px
    canvas = cv2.addWeighted(canvas, 0.45, np.full_like(canvas, 255), 0.55, 0)
    for r in result.rects:
        pts_px: list[tuple[int, int]] = []
        for x_mm, y_mm in r.polygon[:-1]:
            px = int(round((x_mm - margin_mm) / mm_per_px_x))
            py = int(round(h_px - (y_mm - margin_mm) / mm_per_px_y))
            pts_px.append((px, py))
        pts = np.array(pts_px, dtype=np.int32).reshape((-1, 1, 2))
        colour = COLOUR[r.kind]
        cv2.polylines(canvas, [pts], isClosed=True, color=colour, thickness=4)
        lx = int(round((r.cx_mm - margin_mm) / mm_per_px_x))
        ly = int(round(h_px - (r.cy_mm - margin_mm) / mm_per_px_y))
        label = f"{r.kind} {r.width_mm:.0f}x{r.depth_mm:.0f}mm"
        font = cv2.FONT_HERSHEY_SIMPLEX
        scale, thickness = 0.55, 1
        (tw, th), _ = cv2.getTextSize(label, font, scale, thickness)
        cv2.rectangle(
            canvas,
            (lx - tw // 2 - 4, ly - th // 2 - 4),
            (lx + tw // 2 + 4, ly + th // 2 + 4),
            colour, -1,
        )
        cv2.putText(
            canvas, label, (lx - tw // 2, ly + th // 2),
            font, scale, (255, 255, 255), thickness, cv2.LINE_AA,
        )
    return canvas


def overlay(img_path: Path) -> list[Path]:
    """Render one overlay PNG per mode next to the input image.
    Filename pattern: <stem>_overlay_<mode>.png  (e.g. floor_overlay_cv.png).
    Returns the list of written paths."""
    raw = img_path.read_bytes()

    arr = np.frombuffer(raw, dtype=np.uint8)
    base = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if base is None:
        raise RuntimeError(f"could not decode {img_path}")

    # Run all four modes. hybrid silently falls back to auto if no LLM key.
    modes = ("cv", "hough", "auto", "hybrid")
    written: list[Path] = []

    for mode in modes:
        result = parse_image(raw, FLOOR_W_M, FLOOR_H_M, mode=mode)
        panel = _draw_overlay(base.copy(), result)

        suffix = (
            f"  ({result.mode} fallback)"
            if mode == "hybrid" and result.mode != "hybrid"
            else ""
        )
        title_h = 56
        w_px = panel.shape[1]
        title = np.full((title_h, w_px, 3), (235, 235, 235), dtype=np.uint8)
        cv2.putText(
            title,
            f"{img_path.name}  -  mode={mode}  -  {result.n_walls}W / {result.n_obstacles}O"
            f"  -  floor {FLOOR_W_M}x{FLOOR_H_M} m{suffix}",
            (16, 36),
            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (20, 20, 20), 2, cv2.LINE_AA,
        )
        out_img = np.vstack([title, panel])

        out_path = img_path.with_name(f"{img_path.stem}_overlay_{mode}.png")
        cv2.imwrite(str(out_path), out_img)
        written.append(out_path)
    return written


if __name__ == "__main__":
    pngs = sorted(p for p in IMG_DIR.glob("*.png") if "_overlay" not in p.stem)
    if not pngs:
        sys.exit(f"no PNGs under {IMG_DIR}")
    for p in pngs:
        for out in overlay(p):
            print(f"  wrote {out.relative_to(IMG_DIR.parent)}")
    print("\nopen them with:")
    print(f"  open {IMG_DIR}/*_overlay_*.png")
