// Mesh slot lookup. Maps catalog robot model id -> expected static GLB path
// under /public/models/. Drop a `.glb` at the path and the 3D canvas will
// pick it up; if missing, the procedural arm + cabinet renders instead.

const MANIFEST_PATH = '/models/manifest.json'

interface MeshManifest {
  robots: Record<string, string>
  pallets: Record<string, string>
}

let manifestPromise: Promise<MeshManifest | null> | null = null

/** Lazy-fetch the model manifest. The manifest may be missing (no /models
 *  directory deployed) — return null in that case so callers can fall back. */
export function loadMeshManifest(): Promise<MeshManifest | null> {
  if (manifestPromise) return manifestPromise
  manifestPromise = fetch(MANIFEST_PATH)
    .then((r) => (r.ok ? r.json() : null))
    .catch(() => null)
  return manifestPromise
}

/** Slugify a robot model id ("M-410iC/110" -> "m-410ic_110") so it lines up
 *  with a filesystem-friendly GLB filename. */
export function modelSlug(modelId: string): string {
  return modelId.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')
}

/** Resolve a robot model id to a GLB URL (or null if no slot is configured). */
export async function robotMeshPath(modelId: string | null | undefined): Promise<string | null> {
  if (!modelId) return null
  const manifest = await loadMeshManifest()
  if (!manifest) return null
  const explicit = manifest.robots?.[modelId]
  if (explicit) return explicit
  // Fall back to the conventional filename.
  return `/models/${modelSlug(modelId)}.glb`
}
