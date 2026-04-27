// 3D top-down + perspective view of the workcell. Mirrors the 2D canvas
// 1:1 by reading the same LayoutProposal, but renders boxes / cylinders /
// stacked cases via react-three-fiber. World units are metres (mm * 0.001).

import { useMemo } from 'react'
import { Canvas } from '@react-three/fiber'
import { Grid, OrbitControls, RoundedBox } from '@react-three/drei'
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

  // 4-axis palletizer kinematics (stylised static pose):
  //   J1 base rotation (vertical axis) — implicit in the cylindrical base.
  //   J2 lower arm pitching up at θ2 from a hip joint.
  //   J3 upper arm pitching at θ3, kept horizontal by the parallelogram link.
  //   J4 wrist rotation keeps the EOAT vertical (palletizer convention).
  // We pose the arm extended at ~70% reach toward +x so reviewers see it
  // hovering over the pallet instead of stowed straight up.
  const HIP_HEIGHT = ROBOT_BASE_HEIGHT + baseR * 0.35
  const LOWER_LEN = reach * 0.55
  const UPPER_LEN = reach * 0.55
  const THETA2 = (60 * Math.PI) / 180   // lower arm pitch up from horizontal
  const THETA3 = (-25 * Math.PI) / 180  // upper arm pitch down from horizontal
  // Forward/up endpoint of lower arm.
  const elbowX = LOWER_LEN * Math.cos(THETA2)
  const elbowY = HIP_HEIGHT + LOWER_LEN * Math.sin(THETA2)
  // Endpoint of upper arm (= wrist position).
  const wristX = elbowX + UPPER_LEN * Math.cos(THETA3)
  const wristY = elbowY + UPPER_LEN * Math.sin(THETA3)
  const armR = baseR * 0.18

  return (
    <group position={[x, 0, z]}>
      {/* Base column */}
      <mesh position={[0, ROBOT_BASE_HEIGHT / 2, 0]} castShadow receiveShadow>
        <cylinderGeometry args={[baseR, baseR * 1.05, ROBOT_BASE_HEIGHT, 32]} />
        <meshStandardMaterial color="#1f2937" metalness={0.45} roughness={0.4} />
      </mesh>
      {/* Hip joint housing */}
      <mesh position={[0, HIP_HEIGHT, 0]} rotation={[Math.PI / 2, 0, 0]} castShadow>
        <cylinderGeometry args={[baseR * 0.45, baseR * 0.45, baseR * 0.7, 24]} />
        <meshStandardMaterial color="#fbbf24" metalness={0.5} roughness={0.35} />
      </mesh>
      {/* Lower arm (J2) */}
      <Cylinder
        from={[0, HIP_HEIGHT, 0]}
        to={[elbowX, elbowY, 0]}
        radius={armR}
        color="#f97316"
      />
      {/* Elbow housing */}
      <mesh position={[elbowX, elbowY, 0]} rotation={[Math.PI / 2, 0, 0]} castShadow>
        <cylinderGeometry args={[armR * 1.4, armR * 1.4, armR * 1.6, 20]} />
        <meshStandardMaterial color="#fbbf24" metalness={0.5} roughness={0.35} />
      </mesh>
      {/* Upper arm (J3) */}
      <Cylinder
        from={[elbowX, elbowY, 0]}
        to={[wristX, wristY, 0]}
        radius={armR}
        color="#f97316"
      />
      {/* Parallelogram link — slim secondary arm offset above for that
          characteristic palletizer silhouette. */}
      <Cylinder
        from={[0, HIP_HEIGHT + baseR * 0.45, 0]}
        to={[elbowX, elbowY + baseR * 0.45, 0]}
        radius={armR * 0.6}
        color="#9ca3af"
      />
      <Cylinder
        from={[elbowX, elbowY + baseR * 0.45, 0]}
        to={[wristX, wristY + baseR * 0.05, 0]}
        radius={armR * 0.6}
        color="#9ca3af"
      />
      {/* J4 wrist (vertical) + EOAT */}
      <mesh position={[wristX, wristY - 0.08, 0]} castShadow>
        <cylinderGeometry args={[armR * 1.2, armR * 1.2, 0.16, 16]} />
        <meshStandardMaterial color="#1f2937" metalness={0.5} roughness={0.4} />
      </mesh>
      {/* EOAT (vacuum-style end effector plate) */}
      <mesh position={[wristX, wristY - 0.22, 0]} castShadow>
        <boxGeometry args={[0.32, 0.04, 0.32]} />
        <meshStandardMaterial color="#0f172a" metalness={0.4} roughness={0.5} />
      </mesh>
      {[
        [-0.1, -0.245, -0.1],
        [0.1, -0.245, -0.1],
        [-0.1, -0.245, 0.1],
        [0.1, -0.245, 0.1],
      ].map(([dx, dy, dz], i) => (
        <mesh key={i} position={[wristX + dx, wristY + dy, dz]} castShadow>
          <cylinderGeometry args={[0.04, 0.05, 0.05, 12]} />
          <meshStandardMaterial color="#475569" metalness={0.3} roughness={0.6} />
        </mesh>
      ))}
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

/** Cylinder rendered between two world-space points. */
function Cylinder({
  from,
  to,
  radius,
  color,
}: {
  from: [number, number, number]
  to: [number, number, number]
  radius: number
  color: string
}) {
  const dx = to[0] - from[0]
  const dy = to[1] - from[1]
  const dz = to[2] - from[2]
  const length = Math.hypot(dx, dy, dz) || 1e-6
  const midpoint: [number, number, number] = [
    (from[0] + to[0]) / 2,
    (from[1] + to[1]) / 2,
    (from[2] + to[2]) / 2,
  ]
  // Default cylinderGeometry is aligned to +Y; rotate to match (dx, dy, dz).
  const axis = new THREE.Vector3(dx, dy, dz).normalize()
  const yAxis = new THREE.Vector3(0, 1, 0)
  const quat = new THREE.Quaternion().setFromUnitVectors(yAxis, axis)
  const euler = new THREE.Euler().setFromQuaternion(quat)
  return (
    <mesh position={midpoint} rotation={[euler.x, euler.y, euler.z]} castShadow receiveShadow>
      <cylinderGeometry args={[radius, radius, length, 16]} />
      <meshStandardMaterial color={color} metalness={0.45} roughness={0.4} />
    </mesh>
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

  // Roller layout: rollers run perpendicular to flow, evenly spaced along
  // the long axis (every ~120 mm). When yaw=90 the long axis is z, otherwise x.
  const longAxisLen = isVertical ? h : w
  const shortAxisLen = isVertical ? w : h
  const rollerSpacing = 0.12
  const rollerR = 0.025
  const nRollers = Math.max(2, Math.floor(longAxisLen / rollerSpacing))
  const rollerLen = shortAxisLen * 0.92
  const longStart = -longAxisLen / 2 + rollerSpacing / 2

  return (
    <group position={[x, 0, z]}>
      {/* Side rails (left + right relative to flow direction) */}
      {[-1, 1].map((side) => (
        <mesh
          key={side}
          position={
            isVertical
              ? [side * (shortAxisLen / 2 - 0.025), CONVEYOR_HEIGHT - 0.04, 0]
              : [0, CONVEYOR_HEIGHT - 0.04, side * (shortAxisLen / 2 - 0.025)]
          }
          castShadow
          receiveShadow
        >
          <boxGeometry args={
            isVertical ? [0.05, 0.18, longAxisLen] : [longAxisLen, 0.18, 0.05]
          } />
          <meshStandardMaterial color="#1e3a8a" metalness={0.5} roughness={0.4} />
        </mesh>
      ))}
      {/* Lower frame box */}
      <mesh position={[0, CONVEYOR_HEIGHT * 0.4, 0]} castShadow receiveShadow>
        <boxGeometry args={[w * 0.85, CONVEYOR_HEIGHT * 0.7, h * 0.85]} />
        <meshStandardMaterial color="#1e40af" metalness={0.35} roughness={0.55} />
      </mesh>
      {/* Support legs at each corner */}
      {[
        [-w / 2 + 0.06, -h / 2 + 0.06],
        [w / 2 - 0.06, -h / 2 + 0.06],
        [-w / 2 + 0.06, h / 2 - 0.06],
        [w / 2 - 0.06, h / 2 - 0.06],
      ].map(([lx, lz], i) => (
        <mesh
          key={i}
          position={[lx, (CONVEYOR_HEIGHT - 0.1) / 2, lz]}
          castShadow
          receiveShadow
        >
          <boxGeometry args={[0.06, CONVEYOR_HEIGHT - 0.1, 0.06]} />
          <meshStandardMaterial color="#475569" metalness={0.4} roughness={0.6} />
        </mesh>
      ))}
      {/* Rollers */}
      {Array.from({ length: nRollers }).map((_, i) => {
        const tAlong = longStart + i * rollerSpacing
        return (
          <mesh
            key={i}
            position={
              isVertical
                ? [0, CONVEYOR_HEIGHT - 0.005, tAlong]
                : [tAlong, CONVEYOR_HEIGHT - 0.005, 0]
            }
            rotation={
              isVertical
                ? [0, 0, Math.PI / 2]
                : [Math.PI / 2, 0, 0]
            }
            castShadow
          >
            <cylinderGeometry args={[rollerR, rollerR, rollerLen, 16]} />
            <meshStandardMaterial color="#cbd5e1" metalness={0.7} roughness={0.3} />
          </mesh>
        )
      })}
      {/* Flow arrow above rollers — pointing toward the robot side */}
      <mesh
        position={isVertical ? [0, CONVEYOR_HEIGHT + 0.04, h * 0.25] : [-w * 0.25, CONVEYOR_HEIGHT + 0.04, 0]}
        rotation={[-Math.PI / 2, 0, isVertical ? 0 : -Math.PI / 2]}
      >
        <coneGeometry args={[Math.min(w, h) * 0.18, Math.min(w, h) * 0.4, 3]} />
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

  const cases = useMemo(() => buildPalletCases(c, spec), [c, spec])

  // Real EUR pallet construction: 7 top planks + 3 bottom planks + 9 corner blocks.
  // 5 top planks running along `length` axis, equally spaced across `width`.
  const topPlankH = 0.022
  const blockH = 0.078       // corner blocks (between top + bottom decks)
  const bottomPlankH = 0.022
  const N_TOP = 5
  const plankW = width / (N_TOP + (N_TOP - 1) * 0.25) // small gap between planks
  const gap = plankW * 0.25
  const topY = blockH + bottomPlankH + topPlankH / 2
  const blockY = bottomPlankH + blockH / 2
  const bottomY = bottomPlankH / 2
  const blockSize = Math.min(length, width) * 0.13
  const blockOffsetsX = [-length / 2 + blockSize / 2, 0, length / 2 - blockSize / 2]
  const blockOffsetsZ = [-width / 2 + blockSize / 2, 0, width / 2 - blockSize / 2]

  return (
    <group position={[x, 0, z]}>
      {/* Top deck: 5 planks running along x */}
      {Array.from({ length: N_TOP }).map((_, i) => {
        const offsetZ = -width / 2 + plankW / 2 + i * (plankW + gap)
        return (
          <mesh
            key={`top-${i}`}
            position={[0, topY, offsetZ]}
            castShadow
            receiveShadow
          >
            <boxGeometry args={[length, topPlankH, plankW]} />
            <meshStandardMaterial color="#b45309" roughness={0.85} />
          </mesh>
        )
      })}
      {/* Bottom deck: 3 planks running along x */}
      {[-width / 2 + plankW / 2, 0, width / 2 - plankW / 2].map((zOff, i) => (
        <mesh
          key={`bot-${i}`}
          position={[0, bottomY, zOff]}
          castShadow
          receiveShadow
        >
          <boxGeometry args={[length, bottomPlankH, plankW]} />
          <meshStandardMaterial color="#92400e" roughness={0.9} />
        </mesh>
      ))}
      {/* 9 corner blocks (3x3 grid) — RoundedBox so the wood looks worn at edges */}
      {blockOffsetsX.map((bx, i) =>
        blockOffsetsZ.map((bz, j) => (
          <RoundedBox
            key={`block-${i}-${j}`}
            position={[bx, blockY, bz]}
            args={[blockSize, blockH, blockSize]}
            radius={Math.min(blockSize, blockH) * 0.08}
            smoothness={2}
            castShadow
            receiveShadow
          >
            <meshStandardMaterial color="#78350f" roughness={0.9} />
          </RoundedBox>
        )),
      )}
      {/* Stacked cases — RoundedBox + slightly varied colour per layer for depth */}
      {cases.map((b, i) => (
        <RoundedBox
          key={i}
          position={[
            b.cx - length / 2,
            blockH + bottomPlankH + topPlankH + b.cz + b.h / 2,
            b.cy - width / 2,
          ]}
          args={[b.w, b.h, b.d]}
          radius={Math.min(b.w, b.h, b.d) * 0.04}
          smoothness={2}
          castShadow
          receiveShadow
        >
          <meshStandardMaterial
            color={b.color}
            roughness={0.85}
            metalness={0.0}
          />
        </RoundedBox>
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
