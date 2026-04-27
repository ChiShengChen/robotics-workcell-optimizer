// Robot footprint + max-reach annulus + label.
// Coordinates passed in are PIXEL coords (caller has already converted from mm),
// with Konva's y-down origin at the bottom-left of the cell rect.

import { Circle, Group, Text } from 'react-konva'

interface Props {
  xPx: number
  yPx: number
  baseRadiusPx: number
  reachPx: number
  effectiveReachPx: number
  label: string
  selected?: boolean
  onClick?: () => void
  onDragMove?: (xPx: number, yPx: number) => void
  onDragEnd?: (xPx: number, yPx: number) => void
}

export function RobotShape({
  xPx,
  yPx,
  baseRadiusPx,
  reachPx,
  effectiveReachPx,
  label,
  selected = false,
  onClick,
  onDragMove,
  onDragEnd,
}: Props) {
  return (
    <Group
      x={xPx}
      y={yPx}
      draggable
      onClick={onClick}
      onTap={onClick}
      onDragMove={(e) => onDragMove?.(e.target.x(), e.target.y())}
      onDragEnd={(e) => onDragEnd?.(e.target.x(), e.target.y())}
    >
      {/* Max-reach circle (light dashed) */}
      <Circle
        radius={reachPx}
        stroke="#94a3b8"
        strokeWidth={1}
        dash={[6, 6]}
        listening={false}
      />
      {/* Effective-reach circle (solid teal) */}
      <Circle
        radius={effectiveReachPx}
        stroke="#0d9488"
        strokeWidth={1.5}
        listening={false}
      />
      {/* Robot base */}
      <Circle
        radius={baseRadiusPx}
        fill="#1f2937"
        stroke={selected ? '#3b82f6' : '#0f172a'}
        strokeWidth={selected ? 3 : 1}
      />
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
  )
}
