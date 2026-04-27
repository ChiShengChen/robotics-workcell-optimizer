// Pallet rectangle + standard/pattern badge.

import { Group, Rect, Text } from 'react-konva'

interface Props {
  xPx: number
  yPx: number
  widthPx: number
  heightPx: number
  label: string
  standard: string
  pattern: string
  selected?: boolean
  onClick?: () => void
  onDragMove?: (xPx: number, yPx: number) => void
  onDragEnd?: (xPx: number, yPx: number) => void
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
      <Rect
        width={widthPx}
        height={heightPx}
        fill="#fef3c7"
        stroke={selected ? '#3b82f6' : '#a16207'}
        strokeWidth={selected ? 3 : 1.5}
        cornerRadius={1}
      />
      <Text text={label} x={4} y={4} fontSize={10} fill="#78350f" listening={false} />
      <Text
        text={`${standard} · ${pattern}`}
        x={4}
        y={heightPx - 14}
        fontSize={9}
        fill="#92400e"
        listening={false}
      />
    </Group>
  )
}
