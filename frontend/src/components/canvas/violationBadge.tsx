// Tiny "VIOLATION" pill rendered above a shape. Uses an inner scaleY=-1
// so text reads upright when parent layer is y-flipped.

import { Group, Rect, Text } from 'react-konva'

interface Props {
  text: string
  x?: number
  y?: number
}

const PADDING_X = 4
const PADDING_Y = 2
const FONT_SIZE = 10
const CHAR_W = 6.2 // rough px per character at FONT_SIZE 10 bold

export function ViolationBadge({ text, x = 0, y = 0 }: Props) {
  if (!text) return null
  const w = Math.max(28, text.length * CHAR_W + 2 * PADDING_X)
  const h = FONT_SIZE + 2 * PADDING_Y
  return (
    <Group x={x} y={y} scaleY={-1} listening={false}>
      <Rect
        x={-w / 2}
        y={-h / 2}
        width={w}
        height={h}
        fill="#dc2626"
        cornerRadius={3}
      />
      <Text
        text={text}
        x={-w / 2}
        y={-h / 2 + PADDING_Y}
        width={w}
        align="center"
        fontSize={FONT_SIZE}
        fontStyle="bold"
        fill="#ffffff"
      />
    </Group>
  )
}
