// Pallet rectangle + standard/pattern badge.

import { Group, Rect, Text } from 'react-konva'
import type Konva from 'konva'

import { ViolationBadge } from './violationBadge'

interface Props {
  xPx: number
  yPx: number
  widthPx: number
  heightPx: number
  label: string
  standard: string
  pattern: string
  selected?: boolean
  violated?: boolean
  violatedLabel?: string
  onClick?: () => void
  onDragMove?: (xPx: number, yPx: number) => void
  onDragEnd?: (xPx: number, yPx: number) => void
  dragBoundFunc?: (pos: { x: number; y: number }) => { x: number; y: number }
}

export function PalletShape({
  xPx,
  yPx,
  widthPx,
  heightPx,
  label,
  standard,
  pattern,
  selected = false,
  violated = false,
  violatedLabel = '',
  onClick,
  onDragMove,
  onDragEnd,
  dragBoundFunc,
}: Props) {
  const stroke = violated ? '#dc2626' : selected ? '#3b82f6' : '#a16207'
  const strokeWidth = violated ? 3 : selected ? 3 : 1.5
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
        fill="#fef3c7"
        stroke={stroke}
        strokeWidth={strokeWidth}
        cornerRadius={1}
      />
      <Group scaleY={-1}>
        <Text text={label} x={4} y={-heightPx + 4} fontSize={10} fill="#78350f" listening={false} />
        <Text
          text={`${standard} · ${pattern}`}
          x={4}
          y={-14}
          fontSize={9}
          fill="#92400e"
          listening={false}
        />
      </Group>
      {violated && <ViolationBadge text={violatedLabel} x={widthPx / 2} y={heightPx + 14} />}
    </Group>
  )
}
