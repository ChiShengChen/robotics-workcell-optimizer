// Conveyor: rectangle with arrow indicating flow.

import { Arrow, Group, Rect, Text } from 'react-konva'
import type Konva from 'konva'

import { ViolationBadge } from './violationBadge'

interface Props {
  xPx: number
  yPx: number
  widthPx: number
  heightPx: number
  yawDeg: number
  label: string
  role: 'infeed' | 'outfeed'
  selected?: boolean
  violated?: boolean
  violatedLabel?: string
  onClick?: () => void
  onDragMove?: (xPx: number, yPx: number) => void
  onDragEnd?: (xPx: number, yPx: number) => void
  dragBoundFunc?: (pos: { x: number; y: number }) => { x: number; y: number }
}

export function ConveyorShape({
  xPx,
  yPx,
  widthPx,
  heightPx,
  yawDeg,
  label,
  role,
  selected = false,
  violated = false,
  violatedLabel = '',
  onClick,
  onDragMove,
  onDragEnd,
  dragBoundFunc,
}: Props) {
  const isVertical = Math.abs(((yawDeg % 180) + 180) % 180 - 90) < 1e-3
  const arrow = isVertical
    ? [widthPx / 2, heightPx * 0.85, widthPx / 2, heightPx * 0.15]
    : [widthPx * 0.15, heightPx / 2, widthPx * 0.85, heightPx / 2]
  const stroke = violated ? '#dc2626' : selected ? '#3b82f6' : '#1e40af'
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
      <Rect
        width={widthPx}
        height={heightPx}
        fill="#dbeafe"
        stroke={stroke}
        strokeWidth={strokeWidth}
        cornerRadius={2}
      />
      <Arrow
        points={arrow}
        pointerLength={6}
        pointerWidth={6}
        stroke="#1e40af"
        fill="#1e40af"
        strokeWidth={1.5}
        listening={false}
      />
      <Group scaleY={-1}>
        <Text
          text={`${label} (${role})`}
          x={4}
          y={-heightPx + 4}
          fontSize={10}
          fill="#1e3a8a"
          listening={false}
        />
      </Group>
      {violated && <ViolationBadge text={violatedLabel} x={widthPx / 2} y={heightPx + 14} />}
    </Group>
  )
}
