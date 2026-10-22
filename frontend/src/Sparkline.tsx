import { useRef, useEffect, useState } from 'react'
import { sparklineScale, type ScaleMode } from './sparklineScale'

type SparklineProps = {
  /** null marks a period with no data: it is left out of the line and of the scale. */
  values: (number | null)[]
  color: string
  height?: number
  /** Index to mark; usually shared by every sparkline on the page. */
  cursorIdx?: number | null
  onHover?: (index: number | null) => void
  mode?: ScaleMode
}

export function Sparkline({ values, color, height = 32, cursorIdx = null, onHover, mode = 'zero' }: SparklineProps) {
  const [animate, setAnimate] = useState(false)
  const prevDataLen = useRef(0)

  useEffect(() => {
    if (values.length >= 2 && prevDataLen.current !== values.length) {
      setAnimate(false)
      requestAnimationFrame(() => setAnimate(true))
      prevDataLen.current = values.length
    }
  }, [values])

  if (values.length < 2) return null;

  const { lo, span } = sparklineScale(values, mode);
  const w = 100;
  const yOf = (v: number) => height - ((v - lo) / span) * (height - 4) - 2;
  const coords: ({ x: number, y: number } | null)[] = values.map((v, i) =>
    v === null ? null : { x: (i / (values.length - 1)) * w, y: yOf(v) });

  // A gap in the data splits the line instead of drawing a straight bridge.
  const segments: { x: number, y: number }[][] = [];
  let run: { x: number, y: number }[] = [];
  for (const coord of coords) {
    if (!coord) {
      if (run.length > 1) segments.push(run);
      run = [];
      continue;
    }
    run.push(coord);
  }
  if (run.length > 1) segments.push(run);

  const pathLength = segments.reduce(
    (acc, seg) => acc + seg.slice(1).reduce((a, c, i) => {
      const prev = seg[i];
      return a + Math.hypot(c.x - prev.x, c.y - prev.y);
    }, 0),
    0,
  );

  // The data can shrink under a live cursor, so the index is always checked.
  const cursor = cursorIdx !== null && cursorIdx >= 0 && cursorIdx < values.length ? cursorIdx : null;
  const cursorCoord = cursor === null ? null : coords[cursor];

  return (
    <svg
      viewBox={`0 0 ${w} ${height}`}
      style={{ width: '100%', height, display: 'block', cursor: onHover ? 'crosshair' : undefined }}
      preserveAspectRatio="none"
      onPointerMove={(e) => {
        const rect = e.currentTarget.getBoundingClientRect()
        if (rect.width <= 0) return
        const ratio = (e.clientX - rect.left) / rect.width
        const idx = Math.min(values.length - 1, Math.max(0, Math.round(ratio * (values.length - 1))))
        onHover?.(idx)
      }}
      onPointerLeave={() => onHover?.(null)}
    >
      {segments.map((seg, i) => (
        <polyline
          key={i}
          points={seg.map(c => `${c.x},${c.y}`).join(' ')}
          fill="none"
          stroke={color}
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeDasharray={pathLength}
          strokeDashoffset={animate ? 0 : pathLength}
          style={{ transition: 'stroke-dashoffset 0.8s ease-out' }}
        />
      ))}
      {cursorCoord && (
        <>
          <line
            x1={cursorCoord.x}
            y1={0}
            x2={cursorCoord.x}
            y2={height}
            stroke={color}
            strokeWidth="0.5"
            opacity="0.45"
          />
          <circle cx={cursorCoord.x} cy={cursorCoord.y} r="2.5" fill={color} />
        </>
      )}
    </svg>
  );
}
