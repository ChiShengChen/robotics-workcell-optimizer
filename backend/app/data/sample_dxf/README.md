# Sample DXF floor plans

Five hand-built ASCII DXF files for exercising `/api/cad/import-dxf` and
the **Import floor plan (.dxf)…** button in the InputPanel. Regenerate
any time with:

```bash
backend/.venv/bin/python backend/scripts/gen_sample_dxf.py
```

| File | Envelope | Obstacles | Use case |
|------|----------|-----------|----------|
| `simple_8x6.dxf` | 8.4 × 6.3 m | 1 column | Default sanity check; matches the in-memory test in `tests/test_cad.py`. |
| `medium_12x8.dxf` | 12.6 × 8.4 m | 1 column + 1 equipment box | **Roomy enough for `dual_arm_dual_pallet`** — try the multi-arm template here. |
| `complex_15x10.dxf` | 15.8 × 10.5 m | 4 structural columns + 2 equipment boxes | Realistic cluttered factory floor; SA / CP-SAT have to navigate around the columns. |
| `tight_6x4.dxf` | 6.3 × 4.2 m | 3 columns in a dense grid | Stress test — the small envelope + dense columns force aggressive placement; CP-SAT may hit INFEASIBLE. |
| `l_shape_10x10.dxf` | 10.5 × 10.5 m | 1 column + 1 equipment | **Non-convex outer wall** (L-shape with a 4×4 m bite from the NE corner); exercises the bbox-of-largest-polygon assumption. |

## Convention

- All units are mm (drop straight in — no `?scale_to_mm=` needed).
- The largest closed LWPOLYLINE is the **outer wall** and is auto-treated as
  the cell boundary (defines the envelope, NOT added as an obstacle).
- Other entities (CIRCLE = column, smaller LWPOLYLINE = equipment) become
  obstacles the layout must avoid.
- See `backend/app/services/cad_import.py` for parser details and the
  `treat_largest_as_boundary=False` opt-out.

## Quick smoke test

```bash
for f in backend/app/data/sample_dxf/*.dxf; do
  echo "--- $(basename $f) ---"
  curl -sS -F "file=@$f" "http://localhost:5173/api/cad/import-dxf?margin_mm=200" \
    | python3 -c "import json,sys; r=json.load(sys.stdin); \
      print(f'  envelope: {r[\"suggested_cell_envelope_mm\"][0]/1000:.1f} x {r[\"suggested_cell_envelope_mm\"][1]/1000:.1f} m, {r[\"n_entities_imported\"]} obstacles')"
done
```
