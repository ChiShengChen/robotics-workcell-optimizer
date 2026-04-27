// Robot footprint + max-reach annulus + label.
// Coordinates passed in are PIXEL coords (caller has already converted from mm).
// Parent Layer applies scaleY={-1} so y-up mm aligns with screen y-down.

import { Circle, Group, Text } from 'react-konva'
import type Konva from 'konva'

import { ViolationBadge } from './violationBadge'

interface Props {
  xPx: number
  yPx: number
  baseRadiusPx: number
  reachPx: number
  effectiveReachPx: number
  label: string
  selected?: boolean
  violated?: boolean
  violatedLabel?: string
  onClick?: () => void
  onDragMove?: (xPx: number, yPx: number) => void
  onDragEnd?: (xPx: number, yPx: number) => void
  dragBoundFunc?: (pos: { x: number; y: number }) => { x: number; y: number }
}

export function RobotShape({
  xPx,
  yPx,
  baseRadiusPx,
  reachPx,
  effectiveReachPx,
  label,
  selected = false,
  violated = false,
  violatedLabel = '',
  onClick,
  onDragMove,
  onDragEnd,
  dragBoundFunc,
}: Props) {
  const stroke = violated ? '#dc2626' : selected ? '#3b82f6' : '#0f172a'
  const strokeWidth = violated ? 3 : selected ? 3 : 1
  return (
    <Group
      x={xPx}
      y={yPx}
      draggable
      onClick={onClick}
      onTap={onClick}
      dragBoundFunc={dragBoundFunc}
      onDragMove={(e: Konva.KonvaEventObject<DragEvent>) =>
        onDragMove?.(e.target.x(), e.target.y())
      }
      onDragEnd={(e: Konva.KonvaEventObject<DragEvent>) =>
        onDragEnd?.(e.target.x(), e.target.y())
      }
    >
      <Circle radius={reachPx} stroke="#94a3b8" strokeWidth={1} dash={[6, 6]} listening={false} />
      <Circle radius={effectiveReachPx} stroke="#0d9488" strokeWidth={1.5} listening={false} />
      <Circle
        radius={baseRadiusPx}
        fill="#1f2937"
        stroke={stroke}
        strokeWidth={strokeWidth}
      />
      {/* Inner scaleY=-1 so label reads upright (parent layer is flipped). */}
      <Group scaleY={-1}>
        <Text
          text={label}
          x={-baseRadiusPx}
          y={-6}
          width={baseRadiusPx * 2}
          align="center"
          fontSize={11}
          fill="#f8fafc"
          listening={false}
        />
      </Group>
      {violated && <ViolationBadge text={violatedLabel} y={baseRadiusPx + 14} />}
    </Group>
  )
}
