// 纯 SVG 仪表盘，替代 echarts（-400KB bundle）
// 原 echarts 版消耗约 400KB 打包体积，等效功能用 SVG arc 实现
import { scoreColor } from "../utils/display";

type Props = {
  score: number;
  size?: number;
  showLabel?: boolean;
};

// 仪表盘弧：startAngle=210°, endAngle=-30°（顺时针 240°量程）
// SVG arc 公式：startAngle=210°, sweep=240°
function polarToXY(cx: number, cy: number, r: number, angleDeg: number) {
  const rad = (angleDeg - 90) * (Math.PI / 180);
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
}

export default function ScoreGauge({ score, size = 120, showLabel = true }: Props) {
  const cx = size / 2;
  const cy = size * 0.5;
  const r = size * 0.38;
  const strokeW = size * 0.07;

  // 弧从 210° 到 -30°（顺时针 240°），对应 score 0→100
  const startDeg = 210;
  const totalDeg = 240;
  const filledDeg = (Math.min(Math.max(score, 0), 100) / 100) * totalDeg;
  const endDeg = startDeg + filledDeg;

  const bg0 = polarToXY(cx, cy, r, startDeg);
  const bg1 = polarToXY(cx, cy, r, startDeg + totalDeg);
  const fg1 = polarToXY(cx, cy, r, endDeg);

  const bgLargeArc = totalDeg > 180 ? 1 : 0;
  const fgLargeArc = filledDeg > 180 ? 1 : 0;

  const bgPath = `M ${bg0.x} ${bg0.y} A ${r} ${r} 0 ${bgLargeArc} 1 ${bg1.x} ${bg1.y}`;
  const fgPath = filledDeg > 0
    ? `M ${bg0.x} ${bg0.y} A ${r} ${r} 0 ${fgLargeArc} 1 ${fg1.x} ${fg1.y}`
    : "";

  const color = scoreColor(score);

  return (
    <svg
      width={size}
      height={size * 0.85}
      viewBox={`0 0 ${size} ${size * 0.85}`}
      aria-label={`评分 ${score}`}
    >
      {/* 背景弧 */}
      <path d={bgPath} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth={strokeW} strokeLinecap="round" />
      {/* 填充弧 */}
      {fgPath && (
        <path d={fgPath} fill="none" stroke={color} strokeWidth={strokeW} strokeLinecap="round" />
      )}
      {/* 分值 */}
      {showLabel && (
        <text
          x={cx}
          y={cy + r * 0.25}
          textAnchor="middle"
          dominantBaseline="middle"
          fill={color}
          fontSize={size * 0.2}
          fontWeight="bold"
          fontFamily="'JetBrains Mono', 'SF Mono', monospace"
        >
          {Math.round(score)}
        </text>
      )}
    </svg>
  );
}
