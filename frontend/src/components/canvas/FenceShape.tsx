// Safety fence as closed polyline. Listens=false (static).

import { Line } from 'react-konva'

interface Props {
  pointsPx: number[] // flattened [x0,y0,x1,y1,...]
}

export function FenceShape({ pointsPx }: Props) {
  return (
    <Line
      points={pointsPx}
      stroke="#dc2626"
      strokeWidth={2}
      dash={[10, 6]}
      closed
      fill="rgba(220, 38, 38, 0.04)"
      listening={false}
    />
  )
}
