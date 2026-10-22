import { useRef, useEffect, useState } from 'react'
import { sparklineScale, type ScaleMode } from './sparklineScale'

type SparklineProps = {
  values: number[]
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
  // A value outside the window (an idle bucket in a zoomed series) is pinned to
  // the edge instead of diving out of the box, so the line stays one stroke.
  const yOf = (v: number) => {
    const y = height - ((v - lo) / span) * (height - 4) - 2;
    return Math.max(2, Math.min(height - 2, y));
  };
  const coords = values.map((v, i) => ({ x: (i / (values.length - 1)) * w, y: yOf(v) }));
  const points = coords.map(c => `${c.x},${c.y}`).join(' ');

  const pathLength = coords.slice(1).reduce((acc, c, i) => {
    const prev = coords[i];
    return acc + Math.hypot(c.x - prev.x, c.y - prev.y);
  }, 0);

  // The data can shrink under a live cursor, so the index is always checked.
  const cursor = cursorIdx !== null && cursorIdx >= 0 && cursorIdx < values.length ? cursorIdx : null;
  const cursorCoord = cursor === null ? null : coords[cursor];

  return (
    <svg
      viewBox={`0 0 ${w} ${height}`}
      style={{ width: '100%', height, display: 'block', cursor: onHover ? 'pointer' : undefined }}
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
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeDasharray={pathLength}
        strokeDashoffset={animate ? 0 : pathLength}
        style={{ transition: 'stroke-dashoffset 0.8s ease-out' }}
      />
      {cursorCoord && (
        <>
          {/* A tinted column makes the hovered bucket obvious on a thin line. */}
          <rect
            x={cursorCoord.x - 4}
            y={0}
            width={8}
            height={height}
            fill={color}
            opacity="0.14"
          />
          <line
            x1={cursorCoord.x}
            y1={0}
            x2={cursorCoord.x}
            y2={height}
            stroke={color}
            strokeWidth="1"
            opacity="0.7"
          />
          <circle
            cx={cursorCoord.x}
            cy={cursorCoord.y}
            r="3"
            fill={color}
            stroke="var(--card-bg)"
            strokeWidth="1.4"
          />
        </>
      )}
    </svg>
  );
}
