// Optional GLB-backed robot static mesh. Used as a one-shot decoration when
// the user has dropped a real palletizer GLB into /public/models/. Does NOT
// drive the IK animation — the procedural arm always handles motion. This
// component just adds the static body (base / cabinet / cosmetic detail) on
// top of the existing IK rig if a mesh is available.
//
// Wrap usage in <Suspense fallback={null}> so a missing file silently no-ops.

import { useEffect, useState } from 'react'
import { useGLTF } from '@react-three/drei'
import * as THREE from 'three'

import { robotMeshPath } from '@/lib/meshes'

interface Props {
  modelId: string | null | undefined
  position: [number, number, number]
  scale?: number
  rotationY?: number
}

export function RobotStaticMesh({ modelId, position, scale = 1, rotationY = 0 }: Props) {
  const [meshUrl, setMeshUrl] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    robotMeshPath(modelId).then(async (url) => {
      if (!alive || !url) return
      // Probe HEAD before committing — useGLTF throws on 404 in Suspense.
      try {
        const resp = await fetch(url, { method: 'HEAD' })
        if (alive && resp.ok) setMeshUrl(url)
      } catch {
        // network / CORS — silently skip
      }
    })
    return () => { alive = false }
  }, [modelId])

  if (!meshUrl) return null
  return (
    <GLBNode url={meshUrl} position={position} scale={scale} rotationY={rotationY} />
  )
}

function GLBNode({
  url,
  position,
  scale,
  rotationY,
}: {
  url: string
  position: [number, number, number]
  scale: number
  rotationY: number
}) {
  const { scene } = useGLTF(url)
  // Clone so multiple instances of the same model don't share transforms.
  const cloned = scene.clone(true)
  cloned.traverse((obj) => {
    if (obj instanceof THREE.Mesh) {
      obj.castShadow = true
      obj.receiveShadow = true
    }
  })
  return (
    <primitive
      object={cloned}
      position={position}
      scale={scale}
      rotation={[0, rotationY, 0]}
    />
  )
}
