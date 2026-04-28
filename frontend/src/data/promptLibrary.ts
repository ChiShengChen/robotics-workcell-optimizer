// Curated prompt library for the InputPanel dropdown. Each entry fills
// the textarea so the reviewer can hit Extract Spec themselves and watch
// the LLM extract → assumptions flow. The cadSampleId pairings tell the
// user which bundled floor plan to load first for the best demo.

export type PromptCategory =
  | 'Single-arm'
  | 'Dual-arm (high throughput)'
  | '6-axis mixed orientation'
  | 'Continuous operation'
  | 'Edge cases (LLM discipline)'
  | 'Paired with CAD floor plan'

export interface PromptLibraryEntry {
  id: string
  category: PromptCategory
  title: string
  description: string
  cph: number
  prompt: string
  /** If set, suggest the user pre-load this CAD sample (id from /api/cad/samples). */
  cadSampleId?: string
  /** Optional badge ("recommended") shown next to title. */
  badge?: string
}

export const PROMPT_LIBRARY: PromptLibraryEntry[] = [
  // ===== Single-arm =====
  {
    id: 'cereal_800',
    category: 'Single-arm',
    title: 'Mid-speed cereal line (800 cph)',
    description: 'Default sanity check — single arm, EUR pallet, dual station for continuous run.',
    cph: 800,
    prompt:
      "Single-line palletizing for breakfast cereal boxes. Each carton 350 x 250 x 200 mm at 6 kg. 800 cases per hour, 16 hour day shift. EUR pallets, interlock pattern, max stack 1.6 m. Cell footprint 9 m by 6 m. Budget 180k USD.",
  },
  {
    id: 'cement_600',
    category: 'Single-arm',
    title: 'Heavy cement bags (600 bph)',
    description: 'High payload (25 kg), column stacking, ISO1 pallets — picks a 300+ kg robot.',
    cph: 600,
    prompt:
      "Cement bag palletizing line. Each bag 600 x 400 x 150 mm at 25 kg. 600 bags per hour. Cell envelope 10 m by 8 m. ISO1 pallets (1200 x 1000 mm) with column stacking pattern (no interlock — bags don't grip). High-payload palletizer required. Budget 250k USD.",
  },
  {
    id: 'pharma_imperial',
    category: 'Single-arm',
    title: 'Pharma cartons in imperial units (700 cph)',
    description: 'Tests LLM unit conversion (lb / inches / feet → mm / kg) — assumptions list explodes.',
    cph: 700,
    prompt:
      "Pharmaceutical cardboard cartons palletizing. Boxes are 18 x 14 x 10 inches at 22 lb. Throughput 700 boxes per hour. Cell footprint 28 x 22 feet. GMA pallets (48 x 40 inches), interlock pattern. Budget around 200k USD.",
  },
  {
    id: 'bulk_totes_250',
    category: 'Single-arm',
    title: 'Bulk totes — large + low speed (250 cph)',
    description: '35 kg totes on ISO1 pallets — picks a mid-sized 4-axis like IRB 460.',
    cph: 250,
    prompt:
      "Bulk palletizing — large totes 800 x 600 x 400 mm at 35 kg. 250 cases per hour. Cell envelope 10 m by 7 m. ISO1 pallets, column pattern. Budget 220k USD.",
  },

  // ===== Dual-arm =====
  {
    id: 'beverage_2500',
    category: 'Dual-arm (high throughput)',
    title: 'High-speed canned beverage (2500 cph)',
    description: 'Triggers dual_arm_dual_pallet first. 2× MPL80II, system UPH ≈ 2148.',
    cph: 2500,
    badge: 'recommended',
    prompt:
      "High-speed canned beverage palletizing line. Each tray 400 x 300 x 220 mm at 12 kg. Target 2500 cases per hour. Cell envelope 12 m by 7 m. EUR pallets, interlock pattern. Budget 300k USD.",
  },
  {
    id: 'ecommerce_2000',
    category: 'Dual-arm (high throughput)',
    title: 'Mixed-SKU e-commerce (2000 cph)',
    description: '3 SKUs random sequence + 6-axis arms — dual-arm with M-710iC/50 or IRB 6700.',
    cph: 2000,
    prompt:
      "Mixed case palletizing for an e-commerce DC. Three SKUs random sequence: 350 x 250 x 180 mm 8 kg, 500 x 400 x 300 mm 18 kg, 600 x 400 x 250 mm 22 kg. Combined target 2000 cases per hour. Cell envelope 14 m by 9 m. GMA pallets (48 x 40 inches), random orientation infeed needs 6-axis arms. Budget 400k USD bare arms.",
  },
  {
    id: 'brewery_1800_24x7',
    category: 'Dual-arm (high throughput)',
    title: 'Brewery 24/7 beer cartons (1800 cph)',
    description: '24-hour continuous + 1800 cph → both dual_arm and dual_pallet are candidates.',
    cph: 1800,
    prompt:
      "Brewery palletizing — 24-pack beer cartons at 16 kg each, 350 x 250 x 240 mm. We need 1800 cases per hour, 24/7 continuous operation. Cell footprint 13 m by 8 m. EUR pallets, interlock. Budget 320k USD.",
  },
  {
    id: 'canned_3500_surge',
    category: 'Dual-arm (high throughput)',
    title: 'Canned food export surge (3500 cph)',
    description: 'Aggressive cph target — even dual-arm may underrun, triggers budget relax assumption.',
    cph: 3500,
    prompt:
      "Canned food line. Trays of 24 cans, 400 x 300 x 130 mm at 14 kg. Target 3500 cases per hour for export demand surge. Cell envelope 12 m by 8 m. EUR pallets. Budget 280k USD.",
  },

  // ===== 6-axis =====
  {
    id: 'mixed_random_orient_1000',
    category: '6-axis mixed orientation',
    title: '3 SKUs random orientation infeed (1000 cph)',
    description: 'Forces 6-axis selection (catalog filtered by ideal_use_case=mixed_sku).',
    cph: 1000,
    prompt:
      "Mixed case palletizing — 3 SKUs arrive in random orientation on the infeed conveyor: 400 x 300 x 220 mm 12 kg, 500 x 350 x 280 mm 15 kg, 600 x 400 x 250 mm 18 kg. 1000 cases per hour combined. Cell 11 m by 8 m. EUR pallets. Budget 280k USD bare arm.",
  },

  // ===== Continuous operation =====
  {
    id: 'continuous_800',
    category: 'Continuous operation',
    title: '24/7 cereal line, no line stops (800 cph)',
    description: 'Triggers dual_pallet template via continuous-op heuristic.',
    cph: 800,
    prompt:
      "24/7 continuous operation cereal palletizing. Cases 350 x 250 x 200 mm at 6 kg. 800 cases per hour, no line stops allowed for pallet changeover. 9 m by 6 m cell. EUR pallets. Budget 200k USD.",
  },

  // ===== Edge cases =====
  {
    id: 'infeasible_engine',
    category: 'Edge cases (LLM discipline)',
    title: '❌ Infeasible: 800 kg engine blocks',
    description: 'No catalog robot fits — relaxation chain runs, assumptions list everything tried.',
    cph: 1500,
    prompt:
      "Palletize 800 kg engine blocks at 1500 cycles per hour on a 5 m by 4 m floor with 50k USD budget. EUR pallets.",
  },
  {
    id: 'tight_budget',
    category: 'Edge cases (LLM discipline)',
    title: '💸 Tight budget (60k USD)',
    description: 'Forces budget relaxation — assumption explains how much extra cost was needed.',
    cph: 400,
    prompt:
      "Light cardboard box palletizing on a budget. Cases 300 x 200 x 150 mm at 4 kg. 400 cases per hour. Cell envelope 7 m by 5 m. Half pallets (800 x 600 mm). Budget 60k USD bare arm only.",
  },
  {
    id: 'ambiguous_undecided',
    category: 'Edge cases (LLM discipline)',
    title: '❓ Ambiguous spec — many fields undecided',
    description: 'LLM should set 7+ fields to null and explain in assumptions[]. The hallucination-control demo.',
    cph: 600,
    prompt:
      "We need a palletizing cell on the new floor. Space about 8 by 6 meters maybe. Customer wants ~10 boxes a minute. Pallet type undecided yet — depends on export region. Box weight not finalized. Just block out something feasible for budget purposes.",
  },

  // ===== Paired with CAD =====
  {
    id: 'cad_medium_dual_arm',
    category: 'Paired with CAD floor plan',
    title: '🗂 Medium DXF + dual-arm (recommended combo)',
    description: 'Load "Medium 12×8 m" CAD sample first; system places 2 robots avoiding obstacles.',
    cph: 2500,
    cadSampleId: 'medium_12x8',
    badge: 'recommended',
    prompt:
      "Beverage trays 12 kg each (400 x 300 x 220 mm). 2500 cph target. EUR pallets. Budget 300k USD.",
  },
  {
    id: 'cad_complex_mid_speed',
    category: 'Paired with CAD floor plan',
    title: '🗂 Complex DXF + mid-speed mixed SKU (1200 cph)',
    description: 'Load "Complex 15×10 m + 4 columns" first; SA / CP-SAT navigate around 4 columns + 2 equipment.',
    cph: 1200,
    cadSampleId: 'complex_15x10',
    prompt:
      "Mixed SKU palletizing on a busy factory floor. Cases 500 x 400 x 300 mm at 18 kg. 1200 cases per hour. EUR pallets. Budget 250k USD.",
  },
  {
    id: 'cad_tight_pressure_test',
    category: 'Paired with CAD floor plan',
    title: '🗂 Tight DXF + small line (300 cph) — INFEASIBLE likely',
    description: 'Load "Tight 6×4 m + 3 columns" first; shows CP-SAT INFEASIBLE behaviour.',
    cph: 300,
    cadSampleId: 'tight_6x4',
    prompt:
      "Light boxes (350 x 250 x 180 mm at 5 kg). 300 cases per hour. Half pallets (800 x 600 mm). Budget 80k USD.",
  },
]

export function promptLibraryByCategory(): Map<PromptCategory, PromptLibraryEntry[]> {
  const m = new Map<PromptCategory, PromptLibraryEntry[]>()
  for (const e of PROMPT_LIBRARY) {
    const arr = m.get(e.category) ?? []
    arr.push(e)
    m.set(e.category, arr)
  }
  return m
}
