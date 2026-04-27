// Operator zone: rect with diagonal hatching pattern.

import { Group, Line, Rect, Text } from 'react-konva'

import { ViolationBadge } from './violationBadge'

interface Props {
  xPx: number
  yPx: number
  widthPx: number
  heightPx: number
  label: string
  selected?: boolean
  violated?: boolean
  violatedLabel?: string
  onClick?: () => void
}

export function OperatorZoneShape({
  xPx,
  yPx,
  widthPx,
  heightPx,
  label,
  selected = false,
  violated = false,
  violatedLabel = '',
  onClick,
}: Props) {
  const hatch: number[][] = []
  const step = 14
  for (let off = -heightPx; off < widthPx; off += step) {
    hatch.push([off, 0, off + heightPx, heightPx])
  }
  const stroke = violated ? '#dc2626' : selected ? '#3b82f6' : '#16a34a'
  const strokeWidth = violated ? 3 : selected ? 3 : 1.2
  return (
    <Group x={xPx} y={yPx} onClick={onClick} onTap={onClick}>
      <Rect
        width={widthPx}
        height={heightPx}
        fill="rgba(34, 197, 94, 0.06)"
        stroke={stroke}
        strokeWidth={strokeWidth}
        dash={[4, 4]}
      />
      {hatch.map((h, i) => (
        <Line
          key={i}
          points={h}
          stroke="#86efac"
          strokeWidth={1}
          opacity={0.5}
          listening={false}
        />
      ))}
      <Group scaleY={-1}>
        <Text text={label} x={4} y={-heightPx + 4} fontSize={10} fill="#166534" listening={false} />
      </Group>
      {violated && <ViolationBadge text={violatedLabel} x={widthPx / 2} y={heightPx + 14} />}
    </Group>
  )
}
