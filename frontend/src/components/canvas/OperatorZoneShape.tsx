// Operator zone: rect with diagonal hatching pattern.

import { Group, Line, Rect, Text } from 'react-konva'

interface Props {
  xPx: number
  yPx: number
  widthPx: number
  heightPx: number
  label: string
  selected?: boolean
  onClick?: () => void
}

export function OperatorZoneShape({
  xPx,
  yPx,
  widthPx,
  heightPx,
  label,
  selected = false,
  onClick,
}: Props) {
  // Hatching: parallel diagonal lines every 12 px.
  const hatch: number[][] = []
  const step = 14
  for (let off = -heightPx; off < widthPx; off += step) {
    hatch.push([off, 0, off + heightPx, heightPx])
  }
  return (
    <Group x={xPx} y={yPx} onClick={onClick} onTap={onClick}>
      <Rect
        width={widthPx}
        height={heightPx}
        fill="rgba(34, 197, 94, 0.06)"
        stroke={selected ? '#3b82f6' : '#16a34a'}
        strokeWidth={selected ? 3 : 1.2}
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
      <Text text={label} x={4} y={4} fontSize={10} fill="#166534" listening={false} />
    </Group>
  )
}
