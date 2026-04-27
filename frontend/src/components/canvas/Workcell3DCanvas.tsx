// 3D top-down + perspective view of the workcell. Mirrors the 2D canvas
// 1:1 by reading the same LayoutProposal, but renders boxes / cylinders /
// stacked cases via react-three-fiber. World units are metres (mm * 0.001).

import { useMemo } from 'react'
import { Canvas } from '@react-three/fiber'
import { Grid, OrbitControls } from '@react-three/drei'
import * as THREE from 'three'

import type { LayoutProposal, PlacedComponent, WorkcellSpec } from '@/api/types'
import {
  buildStack,
  type CaseDims,
  type PalletDims,
} from '@/lib/stacking'

interface Props {
  proposal: LayoutProposal | null
  spec: WorkcellSpec | null
}

const MM_TO_M = 0.001
const ROBOT_BASE_HEIGHT = 0.6 // m
const CONVEYOR_HEIGHT = 0.9
const PALLET_THICKNESS = 0.15
const FENCE_HEIGHT = 2.0
const FENCE_THICKNESS = 0.04
const OPERATOR_PAD_HEIGHT = 0.02

export function Workcell3DCanvas({ proposal, spec }: Props) {
  const cellW = (proposal?.cell_bounds_mm[0] ?? spec?.cell_envelope_mm[0] ?? 8000) * MM_TO_M
  const cellH = (proposal?.cell_bounds_mm[1] ?? spec?.cell_envelope_mm[1] ?? 6000) * MM_TO_M

  return (
    <div className="h-full w-full bg-gradient-to-b from-slate-100 to-slate-200">
      <Canvas
        shadows
        camera={{ position: [cellW * 1.1, cellH * 1.1, cellW * 1.1], fov: 40 }}
      >
        <ambientLight intensity={0.55} />
        <hemisphereLight args={['#bfdbfe', '#0f172a', 0.4]} />
        <directionalLight
          position={[cellW * 0.6, 8, cellH * 0.4]}
          intensity={1.1}
          castShadow
          shadow-mapSize={[1024, 1024]}
        />

        {/* Cell floor */}
        <mesh
          position={[cellW / 2, -0.001, cellH / 2]}
          rotation={[-Math.PI / 2, 0, 0]}
          receiveShadow
        >
          <planeGeometry args={[cellW, cellH]} />
          <meshStandardMaterial color="#f8fafc" />
        </mesh>

        {/* Cell border */}
        <CellBorder w={cellW} h={cellH} />

        {/* World origin grid (CLAUDE.md: x → right, y → forward, z → up) */}
        <Grid
          args={[cellW * 2, cellH * 2]}
          cellSize={1}
          cellThickness={0.5}
          cellColor="#cbd5e1"
          sectionSize={5}
          sectionThickness={1}
          sectionColor="#94a3b8"
          fadeDistance={cellW * 1.5}
          followCamera={false}
          infiniteGrid
          position={[cellW / 2, 0, cellH / 2]}
        />

        {(proposal?.components ?? []).map((c) =>
          renderComponent(c, spec ?? undefined),
        )}

        <OrbitControls
          target={[cellW / 2, 0.5, cellH / 2]}
          minDistance={2}
          maxDistance={Math.max(cellW, cellH) * 4}
          maxPolarAngle={Math.PI / 2 - 0.05}
        />
      </Canvas>
      <div className="pointer-events-none absolute bottom-2 right-3 rounded bg-white/80 px-2 py-1 text-[11px] text-slate-600 shadow-sm">
        {cellW.toFixed(1)} × {cellH.toFixed(1)} m · drag to orbit · scroll to zoom
      </div>
    </div>
  )
}

function CellBorder({ w, h }: { w: number; h: number }) {
  const geom = useMemo(() => {
    const points = [
      new THREE.Vector3(0, 0.001, 0),
      new THREE.Vector3(w, 0.001, 0),
      new THREE.Vector3(w, 0.001, h),
      new THREE.Vector3(0, 0.001, h),
      new THREE.Vector3(0, 0.001, 0),
    ]
    return new THREE.BufferGeometry().setFromPoints(points)
  }, [w, h])
  return (
    <primitive
      object={
        new THREE.Line(
          geom,
          new THREE.LineBasicMaterial({ color: '#94a3b8' }),
        )
      }
    />
  )
}

function renderComponent(c: PlacedComponent, spec?: WorkcellSpec) {
  if (c.type === 'robot') return <Robot3D key={c.id} c={c} />
  if (c.type === 'conveyor') return <Conveyor3D key={c.id} c={c} />
  if (c.type === 'pallet') return <Pallet3D key={c.id} c={c} spec={spec} />
  if (c.type === 'fence') return <Fence3D key={c.id} c={c} />
  if (c.type === 'operator_zone') return <OperatorZone3D key={c.id} c={c} />
  return null
}

function Robot3D({ c }: { c: PlacedComponent }) {
  const baseR = ((c.dims.base_radius_mm as number | undefined) ?? 350) * MM_TO_M
  const reach = ((c.dims.reach_mm as number | undefined) ?? 2400) * MM_TO_M
  const eff = ((c.dims.effective_reach_mm as number | undefined) ?? reach * 0.85) * 1
  const x = c.x_mm * MM_TO_M
  const z = c.y_mm * MM_TO_M

  return (
    <group position={[x, 0, z]}>
      {/* Base */}
      <mesh position={[0, ROBOT_BASE_HEIGHT / 2, 0]} castShadow>
        <cylinderGeometry args={[baseR, baseR, ROBOT_BASE_HEIGHT, 32]} />
        <meshStandardMaterial color="#1f2937" metalness={0.3} roughness={0.5} />
      </mesh>
      {/* Stylised arm — primary segment */}
      <mesh position={[0, ROBOT_BASE_HEIGHT + 0.3, 0]} castShadow>
        <cylinderGeometry args={[baseR * 0.4, baseR * 0.5, 0.6, 16]} />
        <meshStandardMaterial color="#fbbf24" metalness={0.4} roughness={0.4} />
      </mesh>
      {/* Stylised arm — extended segment */}
      <mesh position={[reach * 0.5, ROBOT_BASE_HEIGHT + 0.6, 0]} rotation={[0, 0, Math.PI / 2]} castShadow>
        <cylinderGeometry args={[0.08, 0.1, reach * 0.95, 12]} />
        <meshStandardMaterial color="#f59e0b" metalness={0.4} roughness={0.4} />
      </mesh>
      {/* Effective-reach floor ring */}
      <mesh position={[0, 0.005, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <ringGeometry args={[eff - 0.02, eff + 0.02, 64]} />
        <meshBasicMaterial color="#0d9488" transparent opacity={0.65} />
      </mesh>
      {/* Max-reach floor ring (lighter) */}
      <mesh position={[0, 0.004, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <ringGeometry args={[reach - 0.01, reach + 0.01, 64]} />
        <meshBasicMaterial color="#94a3b8" transparent opacity={0.45} />
      </mesh>
    </group>
  )
}

function Conveyor3D({ c }: { c: PlacedComponent }) {
  const length = ((c.dims.length_mm as number | undefined) ?? 2000) * MM_TO_M
  const width = ((c.dims.width_mm as number | undefined) ?? 600) * MM_TO_M
  const isVertical = Math.abs(((c.yaw_deg % 180) + 180) % 180 - 90) < 1e-3
  const w = isVertical ? width : length
  const h = isVertical ? length : width
  const x = c.x_mm * MM_TO_M + w / 2
  const z = c.y_mm * MM_TO_M + h / 2
  return (
    <group position={[x, 0, z]}>
      {/* Belt body */}
      <mesh position={[0, CONVEYOR_HEIGHT / 2, 0]} castShadow receiveShadow>
        <boxGeometry args={[w, CONVEYOR_HEIGHT, h]} />
        <meshStandardMaterial color="#1e40af" metalness={0.2} roughness={0.6} />
      </mesh>
      {/* Belt surface */}
      <mesh position={[0, CONVEYOR_HEIGHT + 0.005, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[w * 0.95, h * 0.95]} />
        <meshStandardMaterial color="#1e293b" roughness={0.9} />
      </mesh>
      {/* Flow arrow on belt — towards the robot side */}
      <mesh
        position={isVertical ? [0, CONVEYOR_HEIGHT + 0.01, h * 0.25] : [-w * 0.25, CONVEYOR_HEIGHT + 0.01, 0]}
        rotation={[-Math.PI / 2, 0, isVertical ? 0 : -Math.PI / 2]}
      >
        <coneGeometry args={[Math.min(w, h) * 0.2, Math.min(w, h) * 0.4, 3]} />
        <meshBasicMaterial color="#fbbf24" />
      </mesh>
    </group>
  )
}

function Pallet3D({ c, spec }: { c: PlacedComponent; spec?: WorkcellSpec }) {
  const length = ((c.dims.length_mm as number | undefined) ?? 1200) * MM_TO_M
  const width = ((c.dims.width_mm as number | undefined) ?? 800) * MM_TO_M
  const x = c.x_mm * MM_TO_M + length / 2
  const z = c.y_mm * MM_TO_M + width / 2

  // Build the case stack from the spec.
  const cases = useMemo(() => buildPalletCases(c, spec), [c, spec])

  return (
    <group position={[x, 0, z]}>
      {/* Pallet body */}
      <mesh position={[0, PALLET_THICKNESS / 2, 0]} castShadow receiveShadow>
        <boxGeometry args={[length, PALLET_THICKNESS, width]} />
        <meshStandardMaterial color="#a16207" roughness={0.85} />
      </mesh>
      {/* Stacked cases */}
      {cases.map((b, i) => (
        <mesh
          key={i}
          position={[
            b.cx - length / 2,
            PALLET_THICKNESS + b.cz + b.h / 2,
            b.cy - width / 2,
          ]}
          castShadow
          receiveShadow
        >
          <boxGeometry args={[b.w, b.h, b.d]} />
          <meshStandardMaterial color={b.color} roughness={0.7} />
        </mesh>
      ))}
    </group>
  )
}

interface Case3D {
  cx: number
  cy: number
  cz: number
  w: number
  h: number
  d: number
  color: string
}

function buildPalletCases(c: PlacedComponent, spec?: WorkcellSpec): Case3D[] {
  const palletStandard = (c.dims.standard as string | undefined) ?? spec?.pallet_standard ?? 'EUR'
  const palletDims: PalletDims =
    palletStandard === 'GMA'
      ? { length_mm: 1219, width_mm: 1016 }
      : palletStandard === 'half'
        ? { length_mm: 800, width_mm: 600 }
        : palletStandard === 'ISO1'
          ? { length_mm: 1200, width_mm: 1000 }
          : { length_mm: 1200, width_mm: 800 }

  const cas: CaseDims = spec?.case_dims_mm
    ? {
        length_mm: spec.case_dims_mm[0],
        width_mm: spec.case_dims_mm[1],
        height_mm: spec.case_dims_mm[2],
      }
    : { length_mm: 400, width_mm: 300, height_mm: 220 }

  const maxStack = spec?.max_stack_height_mm ?? 1500
  const nLayers = Math.max(1, Math.min(8, Math.floor(maxStack / cas.height_mm)))
  const pattern = (c.dims.pattern as 'column' | 'interlock' | 'pinwheel' | undefined) ?? 'interlock'
  const stack = buildStack(palletDims, cas, pattern, nLayers)

  // Rotate alternating layers' colour so we can see interlock pattern from afar.
  const palette = ['#fde68a', '#fbbf24', '#f59e0b', '#ea580c']
  const cases: Case3D[] = []
  stack.perLayer.forEach((layer, layerIdx) => {
    layer.forEach((r) => {
      cases.push({
        cx: r.x_mm * MM_TO_M + (r.w_mm * MM_TO_M) / 2,
        cy: r.y_mm * MM_TO_M + (r.h_mm * MM_TO_M) / 2,
        cz: layerIdx * cas.height_mm * MM_TO_M,
        w: r.w_mm * MM_TO_M,
        h: cas.height_mm * MM_TO_M,
        d: r.h_mm * MM_TO_M,
        color: palette[layerIdx % palette.length],
      })
    })
  })
  return cases
}

function Fence3D({ c }: { c: PlacedComponent }) {
  const poly = (c.dims.polyline as number[][] | undefined) ?? []
  if (poly.length < 2) return null
  const segments: { x: number; z: number; len: number; angle: number }[] = []
  for (let i = 0; i < poly.length - 1; i += 1) {
    const [x0, y0] = poly[i]
    const [x1, y1] = poly[i + 1]
    const dx = (x1 - x0) * MM_TO_M
    const dz = (y1 - y0) * MM_TO_M
    const len = Math.hypot(dx, dz)
    if (len < 0.01) continue
    segments.push({
      x: ((x0 + x1) / 2) * MM_TO_M,
      z: ((y0 + y1) / 2) * MM_TO_M,
      len,
      angle: -Math.atan2(dz, dx),
    })
  }
  return (
    <group>
      {segments.map((s, i) => (
        <mesh
          key={i}
          position={[s.x, FENCE_HEIGHT / 2, s.z]}
          rotation={[0, s.angle, 0]}
          castShadow
        >
          <boxGeometry args={[s.len, FENCE_HEIGHT, FENCE_THICKNESS]} />
          <meshStandardMaterial
            color="#dc2626"
            transparent
            opacity={0.35}
            side={THREE.DoubleSide}
          />
        </mesh>
      ))}
    </group>
  )
}

function OperatorZone3D({ c }: { c: PlacedComponent }) {
  const w = ((c.dims.width_mm as number | undefined) ?? 1500) * MM_TO_M
  const d = ((c.dims.depth_mm as number | undefined) ?? 1500) * MM_TO_M
  const x = c.x_mm * MM_TO_M + w / 2
  const z = c.y_mm * MM_TO_M + d / 2
  return (
    <mesh position={[x, OPERATOR_PAD_HEIGHT / 2 + 0.001, z]} receiveShadow>
      <boxGeometry args={[w, OPERATOR_PAD_HEIGHT, d]} />
      <meshStandardMaterial color="#16a34a" transparent opacity={0.35} />
    </mesh>
  )
}
