// Track parent element size for the Konva Stage. Cell envelope determines
// the px-per-mm factor so any envelope fits.

import { useEffect, useRef, useState, type RefObject } from 'react'

export interface StageSize {
  width: number
  height: number
}

export function useStageSize<T extends HTMLElement>(): {
  ref: RefObject<T | null>
  size: StageSize
} {
  const ref = useRef<T>(null)
  const [size, setSize] = useState<StageSize>({ width: 600, height: 480 })

  useEffect(() => {
    if (!ref.current) return
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect
        if (width > 0 && height > 0) {
          setSize({ width: Math.floor(width), height: Math.floor(height) })
        }
      }
    })
    ro.observe(ref.current)
    return () => ro.disconnect()
  }, [])

  return { ref, size }
}

/** Compute mm-per-px so the cell envelope fills the stage with `padding` px gutters. */
export function computeMmPerPx(
  envelopeMm: [number, number],
  stage: StageSize,
  padding = 16,
): number {
  const [cellW, cellH] = envelopeMm
  const usableW = Math.max(1, stage.width - 2 * padding)
  const usableH = Math.max(1, stage.height - 2 * padding)
  // pick the larger ratio so both dimensions fit
  return Math.max(cellW / usableW, cellH / usableH)
}
