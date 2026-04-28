// 3D top-down + perspective view of the workcell with a live pick-and-place
// animation. Reads the active LayoutProposal + spec, runs a cycle state
// machine that walks an EOAT through pick -> transport -> place phases, and
// renders the robot with 4-axis IK so the arm follows the EOAT.

import { Suspense, useEffect, useMemo, useRef, useState } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import { Grid, OrbitControls, RoundedBox, Text } from '@react-three/drei'
import * as THREE from 'three'
import { Pause, Play, RotateCcw } from 'lucide-react'

import type { LayoutProposal, Obstacle, PlacedComponent, WorkcellSpec } from '@/api/types'
import { buildStack, type CaseDims, type PalletDims } from '@/lib/stacking'
import {
  type ArmGeometry,
  type CycleConfig,
  type CycleState,
  initialCycleState,
  solveArmIK,
  step as stepCycle,
} from '@/lib/cycleAnimation'
import { RobotStaticMesh } from './RobotStaticMesh'

interface Props {
  proposal: LayoutProposal | null
  spec: WorkcellSpec | null
}

const MM_TO_M = 0.001
const ROBOT_BASE_HEIGHT = 0.6
const CONVEYOR_HEIGHT = 0.9
const FENCE_HEIGHT = 2.0
const FENCE_THICKNESS = 0.04
const OPERATOR_PAD_HEIGHT = 0.02
const PALLET_TOP_Y = 0.022 + 0.078 + 0.022 // matches plank-built pallet structure

export function Workcell3DCanvas({ proposal, spec }: Props) {
  const cellW = (proposal?.cell_bounds_mm[0] ?? spec?.cell_envelope_mm[0] ?? 8000) * MM_TO_M
  const cellH = (proposal?.cell_bounds_mm[1] ?? spec?.cell_envelope_mm[1] ?? 6000) * MM_TO_M

  const [playing, setPlaying] = useState(true)
  const [speed, setSpeed] = useState(0.6)
  const [resetTick, setResetTick] = useState(0)

  return (
    <div className="relative h-full w-full bg-gradient-to-b from-slate-100 to-slate-200">
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

        {/* Floor + grid */}
        <mesh
          position={[cellW / 2, -0.001, cellH / 2]}
          rotation={[-Math.PI / 2, 0, 0]}
          receiveShadow
        >
          <planeGeometry args={[cellW, cellH]} />
          <meshStandardMaterial color="#f8fafc" />
        </mesh>
        <CellBorder w={cellW} h={cellH} />
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

        {/* CAD obstacles render in both animated + static modes */}
        {(spec?.obstacles ?? []).map((ob) => (
          <Obstacle3D key={ob.id} obstacle={ob} />
        ))}

        {proposal && spec ? (
          <AnimatedScene
            proposal={proposal}
            spec={spec}
            playing={playing}
            speed={speed}
            resetTick={resetTick}
          />
        ) : (
          (proposal?.components ?? []).map((c) =>
            renderStaticComponent(c, spec ?? undefined),
          )
        )}

        <OrbitControls
          target={[cellW / 2, 0.5, cellH / 2]}
          minDistance={2}
          maxDistance={Math.max(cellW, cellH) * 4}
          maxPolarAngle={Math.PI / 2 - 0.05}
        />
      </Canvas>

      <div className="pointer-events-auto absolute left-3 top-3 flex items-center gap-2 rounded bg-white/90 px-2 py-1 text-[11px] text-slate-700 shadow">
        <button
          type="button"
          onClick={() => setPlaying((v) => !v)}
          className="flex h-5 w-5 items-center justify-center rounded hover:bg-slate-100"
          title={playing ? 'Pause' : 'Play'}
        >
          {playing ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
        </button>
        <button
          type="button"
          onClick={() => setResetTick((t) => t + 1)}
          className="flex h-5 w-5 items-center justify-center rounded hover:bg-slate-100"
          title="Reset cycle"
        >
          <RotateCcw className="h-3.5 w-3.5" />
        </button>
        <span className="text-slate-400">|</span>
        <label className="flex items-center gap-1 text-[10px] text-slate-500">
          speed
          <input
            type="range"
            min={0.2}
            max={3}
            step={0.1}
            value={speed}
            onChange={(e) => setSpeed(parseFloat(e.target.value))}
            className="w-20 accent-blue-600"
          />
          <span className="w-8 tabular-nums text-slate-700">{speed.toFixed(1)}x</span>
        </label>
      </div>

      <div className="pointer-events-none absolute bottom-2 right-3 rounded bg-white/80 px-2 py-1 text-[11px] text-slate-600 shadow-sm">
        {cellW.toFixed(1)} × {cellH.toFixed(1)} m · drag to orbit · scroll to zoom
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Animated scene: holds the cycle state, runs useFrame, places components.
// ---------------------------------------------------------------------------

function AnimatedScene({
  proposal,
  spec,
  playing,
  speed,
  resetTick,
}: {
  proposal: LayoutProposal
  spec: WorkcellSpec
  playing: boolean
  speed: number
  resetTick: number
}) {
  const robotComps = useMemo(
    () => proposal.components.filter((c) => c.type === 'robot'),
    [proposal.components],
  )
  const conveyorComps = useMemo(
    () => proposal.components.filter((c) => c.type === 'conveyor'),
    [proposal.components],
  )

  // Build one cycle config + state per robot, partitioned by task_assignment.
  const robotInstances = useMemo(() => {
    return robotComps.map((rc, idx) =>
      buildRobotInstance(rc, idx, proposal, spec, robotComps.length),
    )
  }, [robotComps, conveyorComps, proposal, spec])

  const [version, setVersion] = useState(0) // bumps when any pallet count changes
  const placedRef = useRef<Record<string, number>>({})

  // Reset all instances when proposal/spec changes or user hits reset.
  useEffect(() => {
    placedRef.current = {}
    for (const inst of robotInstances) {
      if (inst.cycleCfg) {
        inst.stateRef.current = initialCycleState(inst.cycleCfg)
        inst.eoatRef.current.copy(inst.cycleCfg.homePoint)
        // Apply phase offset by stepping the state forward.
        if (inst.phaseOffsetT > 0) {
          const stepped = stepCycle(inst.stateRef.current, inst.cycleCfg, inst.phaseOffsetT)
          inst.stateRef.current = stepped.state
          inst.eoatRef.current.copy(stepped.eoatPosition)
          inst.carryingRef.current = stepped.carrying
        }
      }
    }
    setVersion((v) => v + 1)
  }, [robotInstances, resetTick])

  // Belt cases per conveyor.
  const beltCasesByConveyor = useRef<Record<string, { u: number }[]>>({})
  useEffect(() => {
    const next: Record<string, { u: number }[]> = {}
    for (const c of conveyorComps) {
      next[c.id] = Array.from({ length: 4 }).map((_, i) => ({ u: i * 0.22 }))
    }
    beltCasesByConveyor.current = next
  }, [conveyorComps.map((c) => c.id).join('|')])

  // Determine which robot owns each conveyor (for "hide front belt case while
  // robot is carrying" behaviour).
  const conveyorCarrying = useMemo(() => {
    const owners: Record<string, string | null> = {}
    for (const c of conveyorComps) {
      let owner: string | null = null
      for (const rc of robotComps) {
        const assigned = proposal.task_assignment[rc.id]
        if (assigned === undefined || assigned.includes(c.id)) {
          owner = rc.id
          break
        }
      }
      owners[c.id] = owner
    }
    return owners
  }, [conveyorComps, robotComps, proposal.task_assignment])

  useFrame((_, dt) => {
    const dtSim = playing ? dt * speed : 0
    let placedChanged = false
    for (const inst of robotInstances) {
      if (!inst.stateRef.current || !inst.cycleCfg) continue
      const out = stepCycle(inst.stateRef.current, inst.cycleCfg, dtSim)
      const prev = inst.stateRef.current
      inst.stateRef.current = out.state
      inst.eoatRef.current.copy(out.eoatPosition)
      inst.carryingRef.current = out.carrying
      if (inst.armGeom) {
        const pose = solveArmIK(inst.armGeom, inst.eoatRef.current)
        inst.armPoseRef.current = {
          j1: pose.j1,
          elbow: pose.elbowWorld,
          wrist: pose.wristWorld,
          hip: pose.hipWorld,
        }
      }
      if (prev && out.state.placedPerPallet !== prev.placedPerPallet) {
        placedChanged = true
      }
    }
    if (dtSim > 0) {
      const beltSpeed = 0.18
      for (const arr of Object.values(beltCasesByConveyor.current)) {
        for (const bc of arr) {
          bc.u += beltSpeed * dtSim
          if (bc.u > 1.05) bc.u -= 1.2
        }
      }
    }
    if (placedChanged) {
      // Aggregate placedPerPallet across all robots for the pallet renderer.
      const merged: Record<string, number> = {}
      for (const inst of robotInstances) {
        const pp = inst.stateRef.current?.placedPerPallet ?? {}
        for (const [pid, n] of Object.entries(pp)) {
          merged[pid] = (merged[pid] ?? 0) + n
        }
      }
      placedRef.current = merged
      setVersion((v) => v + 1)
    }
  })

  // Build a per-conveyor "carrying" ref so ConveyorAnimated hides the front
  // belt case while ITS owner robot is carrying.
  const conveyorCarryingRef = useRef<Record<string, boolean>>({})
  useFrame(() => {
    const next: Record<string, boolean> = {}
    for (const c of conveyorComps) {
      const ownerId = conveyorCarrying[c.id]
      const ownerInst = robotInstances.find((i) => i.robotId === ownerId)
      next[c.id] = ownerInst?.carryingRef.current ?? false
    }
    conveyorCarryingRef.current = next
  })

  return (
    <group>
      {proposal.components
        .filter((c) => c.type === 'fence' || c.type === 'operator_zone')
        .map((c) => renderStaticComponent(c))}

      {robotInstances.map(
        (inst) =>
          inst.armGeom && (
            <RobotArm
              key={inst.robotId}
              armGeom={inst.armGeom}
              poseRef={inst.armPoseRef}
              carryingRef={inst.carryingRef}
              eoatRef={inst.eoatRef}
              baseR={inst.baseR}
              reach={inst.reach}
              modelId={inst.modelId}
            />
          ),
      )}

      {conveyorComps.map((c) => (
        <ConveyorAnimated
          key={c.id}
          c={c}
          spec={spec}
          beltCasesRef={{
            current: beltCasesByConveyor.current[c.id] ?? [],
          } as React.MutableRefObject<{ u: number }[]>}
          carryingRef={{
            get current() {
              return conveyorCarryingRef.current[c.id] ?? false
            },
          } as unknown as React.MutableRefObject<boolean>}
        />
      ))}

      <PalletsWithPlaced
        key={version}
        proposal={proposal}
        spec={spec}
        placedPerPallet={placedRef.current}
      />
    </group>
  )
}

interface RobotInstance {
  robotId: string
  modelId: string | null
  cycleCfg: CycleConfig | null
  armGeom: ArmGeometry | null
  baseR: number
  reach: number
  phaseOffsetT: number
  stateRef: React.MutableRefObject<CycleState | null>
  eoatRef: React.MutableRefObject<THREE.Vector3>
  carryingRef: React.MutableRefObject<boolean>
  armPoseRef: React.MutableRefObject<{
    j1: number
    elbow: THREE.Vector3
    wrist: THREE.Vector3
    hip: THREE.Vector3
  } | null>
}

function buildRobotInstance(
  robotComp: PlacedComponent,
  idx: number,
  proposal: LayoutProposal,
  spec: WorkcellSpec,
  totalRobots: number,
): RobotInstance {
  const baseR = ((robotComp.dims.base_radius_mm as number | undefined) ?? 350) * MM_TO_M
  const reach = ((robotComp.dims.reach_mm as number | undefined) ?? 2400) * MM_TO_M
  const armGeom: ArmGeometry = {
    baseWorld: new THREE.Vector3(robotComp.x_mm * MM_TO_M, 0, robotComp.y_mm * MM_TO_M),
    hipHeight: ROBOT_BASE_HEIGHT + baseR * 0.35,
    l1: reach * 0.55,
    l2: reach * 0.55,
  }
  const cycleCfg = buildCycleConfigForRobot(robotComp, proposal, spec)
  // Phase-offset each robot's cycle so they don't move in lockstep.
  const phaseOffsetT = cycleCfg && totalRobots > 1
    ? (idx / totalRobots) * cycleCfg.cycleSeconds
    : 0
  return {
    robotId: robotComp.id,
    modelId: (robotComp.dims.model_id as string | undefined) ?? proposal.robot_model_ids[idx] ?? proposal.robot_model_id ?? null,
    cycleCfg,
    armGeom,
    baseR,
    reach,
    phaseOffsetT,
    stateRef: { current: null } as React.MutableRefObject<CycleState | null>,
    eoatRef: { current: new THREE.Vector3() } as React.MutableRefObject<THREE.Vector3>,
    carryingRef: { current: false } as React.MutableRefObject<boolean>,
    armPoseRef: { current: null } as React.MutableRefObject<{
      j1: number
      elbow: THREE.Vector3
      wrist: THREE.Vector3
      hip: THREE.Vector3
    } | null>,
  }
}

function buildCycleConfigForRobot(
  robot: PlacedComponent,
  proposal: LayoutProposal,
  spec: WorkcellSpec,
): CycleConfig | null {
  // Conveyors + pallets assigned to THIS robot.
  const assigned = proposal.task_assignment[robot.id]
  const includes = (id: string) => assigned === undefined || assigned.includes(id)
  const conveyors = proposal.components.filter(
    (c) => c.type === 'conveyor' && includes(c.id),
  )
  const pallets = proposal.components.filter(
    (c) => c.type === 'pallet' && includes(c.id),
  )
  if (conveyors.length === 0 || pallets.length === 0) return null

  // Pick from the first assigned conveyor.
  const conveyor = conveyors[0]
  const length = (conveyor.dims.length_mm as number | undefined) ?? 2000
  const width = (conveyor.dims.width_mm as number | undefined) ?? 600
  const isVertical = Math.abs(((conveyor.yaw_deg % 180) + 180) % 180 - 90) < 1e-3
  const pickWorld = isVertical
    ? new THREE.Vector3(
        (conveyor.x_mm + width / 2) * MM_TO_M,
        CONVEYOR_HEIGHT + 0.05,
        (conveyor.y_mm + length) * MM_TO_M,
      )
    : new THREE.Vector3(
        (conveyor.x_mm + length) * MM_TO_M,
        CONVEYOR_HEIGHT + 0.05,
        (conveyor.y_mm + width / 2) * MM_TO_M,
      )

  const caseH = (spec.case_dims_mm?.[2] ?? 220) * MM_TO_M

  const placePoints = pallets.map((p) => {
    const pl = (p.dims.length_mm as number | undefined) ?? 1200
    const pw = (p.dims.width_mm as number | undefined) ?? 800
    const cx = (p.x_mm + pl / 2) * MM_TO_M
    const cz = (p.y_mm + pw / 2) * MM_TO_M
    return { palletId: p.id, basePoint: new THREE.Vector3(cx, PALLET_TOP_Y + caseH / 2, cz) }
  })

  const homePoint = new THREE.Vector3(
    robot.x_mm * MM_TO_M,
    ROBOT_BASE_HEIGHT + 1.6,
    robot.y_mm * MM_TO_M,
  )

  const cycleSeconds = Math.max(1.0, proposal.estimated_cycle_time_s)
  return {
    cycleSeconds,
    pickPoint: pickWorld,
    placePointsByPallet: placePoints,
    homePoint,
    hoverHeight: 0.6,
    caseHeight: caseH,
  }
}


// ---------------------------------------------------------------------------
// Robot arm — drawn from live pose ref each frame.
// ---------------------------------------------------------------------------

function RobotArm({
  armGeom,
  poseRef,
  carryingRef,
  eoatRef,
  baseR,
  reach,
  modelId,
}: {
  armGeom: ArmGeometry
  poseRef: React.MutableRefObject<{ j1: number; elbow: THREE.Vector3; wrist: THREE.Vector3; hip: THREE.Vector3 } | null>
  carryingRef: React.MutableRefObject<boolean>
  eoatRef: React.MutableRefObject<THREE.Vector3>
  baseR: number
  reach: number
  modelId?: string | null
}) {
  const lowerRef = useRef<THREE.Mesh>(null)
  const upperRef = useRef<THREE.Mesh>(null)
  const link1Ref = useRef<THREE.Mesh>(null)
  const link2Ref = useRef<THREE.Mesh>(null)
  const elbowRef = useRef<THREE.Mesh>(null)
  const wristRef = useRef<THREE.Mesh>(null)
  const eoatPlateRef = useRef<THREE.Mesh>(null)
  const eoatGroupRef = useRef<THREE.Group>(null)
  const carriedCaseRef = useRef<THREE.Mesh>(null)
  const baseYawRef = useRef<THREE.Group>(null)

  const armR = baseR * 0.18
  const eff = reach * 0.85
  const linkOffsetY = baseR * 0.45

  useFrame(() => {
    const p = poseRef.current
    if (!p) return
    // Position elbow / wrist housings.
    elbowRef.current?.position.copy(p.elbow)
    wristRef.current?.position.copy(p.wrist).setY(p.wrist.y - 0.08)
    eoatGroupRef.current?.position.copy(p.wrist).setY(p.wrist.y - 0.22)

    // Lower arm: from hip to elbow.
    placeBetween(lowerRef.current, p.hip, p.elbow)
    // Upper arm: from elbow to wrist.
    placeBetween(upperRef.current, p.elbow, p.wrist)
    // Parallelogram links: offset above by linkOffsetY.
    const hipUp = p.hip.clone().setY(p.hip.y + linkOffsetY)
    const elbowUp = p.elbow.clone().setY(p.elbow.y + linkOffsetY)
    const wristUp = p.wrist.clone().setY(p.wrist.y + 0.05)
    placeBetween(link1Ref.current, hipUp, elbowUp)
    placeBetween(link2Ref.current, elbowUp, wristUp)

    // Base yaw rotation so the housing faces J1.
    if (baseYawRef.current) baseYawRef.current.rotation.y = -p.j1

    // Carried case visibility.
    if (carriedCaseRef.current) {
      carriedCaseRef.current.visible = carryingRef.current
    }

    // EOAT plate orientation (always vertical thanks to parallelogram).
    if (eoatPlateRef.current) {
      eoatPlateRef.current.rotation.set(0, -p.j1, 0)
    }
    void eoatRef
  })

  // Controller cabinet sits to the side of the base — rotated so it's
  // always behind the robot relative to the active pallet (looks intentional).
  const cabinetW = baseR * 1.1
  const cabinetH = ROBOT_BASE_HEIGHT * 1.4
  const cabinetD = baseR * 0.8
  const cabinetOffset = baseR + cabinetD / 2 + 0.05
  const labelText = modelId ?? ''

  return (
    <group>
      {/* Optional GLB overlay (no-op if /public/models/<slug>.glb is missing) */}
      <Suspense fallback={null}>
        <RobotStaticMesh
          modelId={modelId ?? null}
          position={[armGeom.baseWorld.x, 0, armGeom.baseWorld.z]}
        />
      </Suspense>
      {/* Base column + yaw housing */}
      <mesh position={[armGeom.baseWorld.x, ROBOT_BASE_HEIGHT / 2, armGeom.baseWorld.z]} castShadow receiveShadow>
        <cylinderGeometry args={[baseR, baseR * 1.05, ROBOT_BASE_HEIGHT, 32]} />
        <meshStandardMaterial color="#1f2937" metalness={0.45} roughness={0.4} />
      </mesh>
      {/* Yellow safety stripe around the bottom of the base */}
      <mesh position={[armGeom.baseWorld.x, 0.06, armGeom.baseWorld.z]}>
        <torusGeometry args={[baseR * 1.06, 0.025, 8, 32]} />
        <meshStandardMaterial color="#fbbf24" />
      </mesh>
      {/* Manufacturer / model nameplate on base front */}
      {labelText && (
        <ModelLabel
          position={[
            armGeom.baseWorld.x,
            ROBOT_BASE_HEIGHT * 0.55,
            armGeom.baseWorld.z + baseR * 1.02,
          ]}
          text={labelText}
        />
      )}
      {/* Controller cabinet behind the base */}
      <group position={[armGeom.baseWorld.x, 0, armGeom.baseWorld.z - cabinetOffset]}>
        <mesh position={[0, cabinetH / 2, 0]} castShadow receiveShadow>
          <boxGeometry args={[cabinetW, cabinetH, cabinetD]} />
          <meshStandardMaterial color="#334155" metalness={0.4} roughness={0.55} />
        </mesh>
        {/* HMI pad */}
        <mesh position={[0, cabinetH * 0.7, cabinetD / 2 + 0.001]} castShadow>
          <boxGeometry args={[cabinetW * 0.55, cabinetH * 0.18, 0.012]} />
          <meshStandardMaterial color="#0f172a" emissive="#1e293b" />
        </mesh>
        {/* Status LED row */}
        {[0, 1, 2].map((i) => (
          <mesh
            key={i}
            position={[
              -cabinetW * 0.25 + i * cabinetW * 0.25,
              cabinetH * 0.35,
              cabinetD / 2 + 0.005,
            ]}
          >
            <sphereGeometry args={[0.018, 12, 12]} />
            <meshStandardMaterial
              color={i === 0 ? '#22c55e' : i === 1 ? '#fbbf24' : '#ef4444'}
              emissive={i === 0 ? '#22c55e' : i === 1 ? '#fbbf24' : '#ef4444'}
              emissiveIntensity={0.7}
            />
          </mesh>
        ))}
        {/* Cooling fan grille */}
        <mesh position={[0, cabinetH * 0.15, cabinetD / 2 + 0.001]}>
          <ringGeometry args={[0.05, 0.08, 16]} />
          <meshStandardMaterial color="#0f172a" />
        </mesh>
      </group>

      <group ref={baseYawRef} position={[armGeom.baseWorld.x, ROBOT_BASE_HEIGHT, armGeom.baseWorld.z]}>
        <mesh position={[0, baseR * 0.35, 0]} rotation={[Math.PI / 2, 0, 0]} castShadow>
          <cylinderGeometry args={[baseR * 0.45, baseR * 0.45, baseR * 0.7, 24]} />
          <meshStandardMaterial color="#fbbf24" metalness={0.5} roughness={0.35} />
        </mesh>
      </group>

      {/* Lower arm */}
      <mesh ref={lowerRef} castShadow>
        <cylinderGeometry args={[armR, armR, 1, 16]} />
        <meshStandardMaterial color="#f97316" metalness={0.45} roughness={0.4} />
      </mesh>
      {/* Elbow */}
      <mesh ref={elbowRef} rotation={[Math.PI / 2, 0, 0]} castShadow>
        <cylinderGeometry args={[armR * 1.4, armR * 1.4, armR * 1.6, 20]} />
        <meshStandardMaterial color="#fbbf24" metalness={0.5} roughness={0.35} />
      </mesh>
      {/* Upper arm */}
      <mesh ref={upperRef} castShadow>
        <cylinderGeometry args={[armR, armR, 1, 16]} />
        <meshStandardMaterial color="#f97316" metalness={0.45} roughness={0.4} />
      </mesh>
      {/* Parallelogram links */}
      <mesh ref={link1Ref} castShadow>
        <cylinderGeometry args={[armR * 0.6, armR * 0.6, 1, 12]} />
        <meshStandardMaterial color="#9ca3af" metalness={0.45} roughness={0.4} />
      </mesh>
      <mesh ref={link2Ref} castShadow>
        <cylinderGeometry args={[armR * 0.6, armR * 0.6, 1, 12]} />
        <meshStandardMaterial color="#9ca3af" metalness={0.45} roughness={0.4} />
      </mesh>
      {/* Wrist */}
      <mesh ref={wristRef} castShadow>
        <cylinderGeometry args={[armR * 1.2, armR * 1.2, 0.16, 16]} />
        <meshStandardMaterial color="#1f2937" metalness={0.5} roughness={0.4} />
      </mesh>
      {/* EOAT */}
      <group ref={eoatGroupRef}>
        <mesh ref={eoatPlateRef} castShadow>
          <boxGeometry args={[0.32, 0.04, 0.32]} />
          <meshStandardMaterial color="#0f172a" metalness={0.4} roughness={0.5} />
        </mesh>
        {[
          [-0.1, -0.025, -0.1],
          [0.1, -0.025, -0.1],
          [-0.1, -0.025, 0.1],
          [0.1, -0.025, 0.1],
        ].map(([dx, dy, dz], i) => (
          <mesh key={i} position={[dx, dy, dz]} castShadow>
            <cylinderGeometry args={[0.04, 0.05, 0.05, 12]} />
            <meshStandardMaterial color="#475569" metalness={0.3} roughness={0.6} />
          </mesh>
        ))}
        {/* Carried case (toggled by carryingRef) */}
        <mesh ref={carriedCaseRef} position={[0, -0.13, 0]} castShadow>
          <boxGeometry args={[0.4, 0.22, 0.3]} />
          <meshStandardMaterial color="#fbbf24" roughness={0.7} />
        </mesh>
      </group>

      {/* Floor reach rings */}
      <mesh position={[armGeom.baseWorld.x, 0.005, armGeom.baseWorld.z]} rotation={[-Math.PI / 2, 0, 0]}>
        <ringGeometry args={[eff - 0.02, eff + 0.02, 64]} />
        <meshBasicMaterial color="#0d9488" transparent opacity={0.65} />
      </mesh>
      <mesh position={[armGeom.baseWorld.x, 0.004, armGeom.baseWorld.z]} rotation={[-Math.PI / 2, 0, 0]}>
        <ringGeometry args={[reach - 0.01, reach + 0.01, 64]} />
        <meshBasicMaterial color="#94a3b8" transparent opacity={0.45} />
      </mesh>
    </group>
  )
}

/** Manufacturer / model nameplate decal on the robot base front. */
function ModelLabel({
  position,
  text,
}: {
  position: [number, number, number]
  text: string
}) {
  return (
    <group position={position}>
      <mesh>
        <planeGeometry args={[0.42, 0.12]} />
        <meshStandardMaterial color="#0f172a" />
      </mesh>
      <Text
        position={[0, 0, 0.001]}
        fontSize={0.045}
        color="#f8fafc"
        anchorX="center"
        anchorY="middle"
        maxWidth={0.4}
      >
        {text}
      </Text>
    </group>
  )
}

/** Position + scale a unit-tall cylinder mesh between two world points. */
function placeBetween(mesh: THREE.Mesh | null, from: THREE.Vector3, to: THREE.Vector3) {
  if (!mesh) return
  const dx = to.x - from.x
  const dy = to.y - from.y
  const dz = to.z - from.z
  const len = Math.hypot(dx, dy, dz) || 1e-6
  mesh.position.set((from.x + to.x) / 2, (from.y + to.y) / 2, (from.z + to.z) / 2)
  mesh.scale.set(1, len, 1)
  const yAxis = new THREE.Vector3(0, 1, 0)
  const axis = new THREE.Vector3(dx, dy, dz).normalize()
  mesh.quaternion.setFromUnitVectors(yAxis, axis)
}

// ---------------------------------------------------------------------------
// Conveyor with moving belt cases
// ---------------------------------------------------------------------------

function ConveyorAnimated({
  c,
  spec,
  beltCasesRef,
  carryingRef,
}: {
  c: PlacedComponent
  spec: WorkcellSpec
  beltCasesRef: React.MutableRefObject<{ u: number }[]>
  carryingRef: React.MutableRefObject<boolean>
}) {
  const length = ((c.dims.length_mm as number | undefined) ?? 2000) * MM_TO_M
  const width = ((c.dims.width_mm as number | undefined) ?? 600) * MM_TO_M
  const isVertical = Math.abs(((c.yaw_deg % 180) + 180) % 180 - 90) < 1e-3
  const w = isVertical ? width : length
  const h = isVertical ? length : width
  const x = c.x_mm * MM_TO_M + w / 2
  const z = c.y_mm * MM_TO_M + h / 2

  const longAxisLen = isVertical ? h : w
  const shortAxisLen = isVertical ? w : h
  const rollerSpacing = 0.12
  const rollerR = 0.025
  const nRollers = Math.max(2, Math.floor(longAxisLen / rollerSpacing))
  const rollerLen = shortAxisLen * 0.92
  const longStart = -longAxisLen / 2 + rollerSpacing / 2

  const rollerRefs = useRef<(THREE.Mesh | null)[]>([])

  // Belt case mesh refs.
  const beltCaseRefs = useRef<(THREE.Mesh | null)[]>([])
  const caseDims: CaseDims = spec.case_dims_mm
    ? {
        length_mm: spec.case_dims_mm[0],
        width_mm: spec.case_dims_mm[1],
        height_mm: spec.case_dims_mm[2],
      }
    : { length_mm: 400, width_mm: 300, height_mm: 220 }
  const cw = caseDims.length_mm * MM_TO_M
  const cd = caseDims.width_mm * MM_TO_M
  const ch = caseDims.height_mm * MM_TO_M

  useFrame((_, dt) => {
    // Roller spin.
    for (const r of rollerRefs.current) {
      if (!r) continue
      r.rotation.y += dt * 4
    }
    // Belt case positions: u = 0 at far end, u = 1 at robot end.
    const cases = beltCasesRef.current
    for (let i = 0; i < cases.length; i += 1) {
      const m = beltCaseRefs.current[i]
      if (!m) continue
      const u = cases[i].u
      // Visible window: 0 .. 1
      m.visible = u >= 0 && u <= 1
      // Hide the FRONT-most case while robot is carrying so the EOAT case
      // doesn't visually duplicate.
      if (carryingRef.current && u > 0.9) m.visible = false

      // Position along long axis (from far end -> pick end).
      const along = -longAxisLen / 2 + u * longAxisLen
      if (isVertical) {
        m.position.set(0, CONVEYOR_HEIGHT + ch / 2, along)
      } else {
        // For horizontal yaw=0 conveyor, flow is along +x toward robot which sits to the right.
        m.position.set(along, CONVEYOR_HEIGHT + ch / 2, 0)
      }
    }
  })

  return (
    <group position={[x, 0, z]}>
      {/* Side rails */}
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
      {/* Lower frame */}
      <mesh position={[0, CONVEYOR_HEIGHT * 0.4, 0]} castShadow receiveShadow>
        <boxGeometry args={[w * 0.85, CONVEYOR_HEIGHT * 0.7, h * 0.85]} />
        <meshStandardMaterial color="#1e40af" metalness={0.35} roughness={0.55} />
      </mesh>
      {/* Legs */}
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
            ref={(el) => { rollerRefs.current[i] = el }}
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
      {/* Belt cases (animated forward) */}
      {beltCasesRef.current.map((_, i) => (
        <mesh
          key={i}
          ref={(el) => { beltCaseRefs.current[i] = el }}
          castShadow
        >
          <boxGeometry args={[isVertical ? cw : cd, ch, isVertical ? cd : cw]} />
          <meshStandardMaterial color="#fbbf24" roughness={0.75} />
        </mesh>
      ))}
      {/* Flow arrow */}
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

// ---------------------------------------------------------------------------
// Pallets — show plank structure + dynamically-placed cases
// ---------------------------------------------------------------------------

function PalletsWithPlaced({
  proposal,
  spec,
  placedPerPallet,
}: {
  proposal: LayoutProposal
  spec: WorkcellSpec
  placedPerPallet: Record<string, number>
}) {
  const pallets = proposal.components.filter((c) => c.type === 'pallet')
  return (
    <>
      {pallets.map((p) => (
        <Pallet3D
          key={p.id}
          c={p}
          spec={spec}
          placedCount={placedPerPallet[p.id] ?? 0}
        />
      ))}
    </>
  )
}

function Pallet3D({
  c,
  spec,
  placedCount,
}: {
  c: PlacedComponent
  spec?: WorkcellSpec
  placedCount: number
}) {
  const length = ((c.dims.length_mm as number | undefined) ?? 1200) * MM_TO_M
  const width = ((c.dims.width_mm as number | undefined) ?? 800) * MM_TO_M
  const x = c.x_mm * MM_TO_M + length / 2
  const z = c.y_mm * MM_TO_M + width / 2

  const cases = useMemo(() => buildPalletCases(c, spec), [c, spec])
  const visibleCases = cases.slice(0, placedCount)

  const topPlankH = 0.022
  const blockH = 0.078
  const bottomPlankH = 0.022
  const N_TOP = 5
  const plankW = width / (N_TOP + (N_TOP - 1) * 0.25)
  const gap = plankW * 0.25
  const topY = blockH + bottomPlankH + topPlankH / 2
  const blockY = bottomPlankH + blockH / 2
  const bottomY = bottomPlankH / 2
  const blockSize = Math.min(length, width) * 0.13
  const blockOffsetsX = [-length / 2 + blockSize / 2, 0, length / 2 - blockSize / 2]
  const blockOffsetsZ = [-width / 2 + blockSize / 2, 0, width / 2 - blockSize / 2]

  return (
    <group position={[x, 0, z]}>
      {Array.from({ length: N_TOP }).map((_, i) => {
        const offsetZ = -width / 2 + plankW / 2 + i * (plankW + gap)
        return (
          <mesh key={`top-${i}`} position={[0, topY, offsetZ]} castShadow receiveShadow>
            <boxGeometry args={[length, topPlankH, plankW]} />
            <meshStandardMaterial color="#b45309" roughness={0.85} />
          </mesh>
        )
      })}
      {[-width / 2 + plankW / 2, 0, width / 2 - plankW / 2].map((zOff, i) => (
        <mesh key={`bot-${i}`} position={[0, bottomY, zOff]} castShadow receiveShadow>
          <boxGeometry args={[length, bottomPlankH, plankW]} />
          <meshStandardMaterial color="#92400e" roughness={0.9} />
        </mesh>
      ))}
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
      {visibleCases.map((b, i) => (
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
          <meshStandardMaterial color={b.color} roughness={0.85} />
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

// ---------------------------------------------------------------------------
// Static fallbacks (for non-animated bits or when AnimatedScene is absent)
// ---------------------------------------------------------------------------

function renderStaticComponent(c: PlacedComponent, spec?: WorkcellSpec) {
  if (c.type === 'fence') return <Fence3D key={c.id} c={c} />
  if (c.type === 'operator_zone') return <OperatorZone3D key={c.id} c={c} />
  if (c.type === 'pallet') return <Pallet3D key={c.id} c={c} spec={spec} placedCount={0} />
  return null
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
      object={new THREE.Line(geom, new THREE.LineBasicMaterial({ color: '#94a3b8' }))}
    />
  )
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
        <mesh key={i} position={[s.x, FENCE_HEIGHT / 2, s.z]} rotation={[0, s.angle, 0]} castShadow>
          <boxGeometry args={[s.len, FENCE_HEIGHT, FENCE_THICKNESS]} />
          <meshStandardMaterial color="#dc2626" transparent opacity={0.35} side={THREE.DoubleSide} />
        </mesh>
      ))}
    </group>
  )
}

function Obstacle3D({ obstacle }: { obstacle: Obstacle }) {
  // Walls extruded 1.5 m tall along each polyline segment.
  const segments: { x: number; z: number; len: number; angle: number }[] = []
  const poly = obstacle.polygon
  if (poly.length < 2) return null
  for (let i = 0; i < poly.length - 1; i += 1) {
    const [x0, y0] = poly[i]
    const [x1, y1] = poly[i + 1]
    const dx = (x1 - x0) * MM_TO_M
    const dz = (y1 - y0) * MM_TO_M
    const len = Math.hypot(dx, dz)
    if (len < 0.001) continue
    segments.push({
      x: ((x0 + x1) / 2) * MM_TO_M,
      z: ((y0 + y1) / 2) * MM_TO_M,
      len,
      angle: -Math.atan2(dz, dx),
    })
  }
  const wallH = 1.5
  const wallT = 0.06
  return (
    <group>
      {segments.map((s, i) => (
        <mesh key={i} position={[s.x, wallH / 2, s.z]} rotation={[0, s.angle, 0]} castShadow receiveShadow>
          <boxGeometry args={[s.len, wallH, wallT]} />
          <meshStandardMaterial color="#475569" roughness={0.8} />
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

