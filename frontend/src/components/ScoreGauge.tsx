import { useEffect, useRef } from "react";
import * as echarts from "echarts";

type Props = {
  score: number;
  size?: number;
  showLabel?: boolean;
};

export default function ScoreGauge({ score, size = 120, showLabel = true }: Props) {
  const chartRef = useRef<HTMLDivElement>(null);
  const instanceRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!chartRef.current) return;

    if (!instanceRef.current) {
      instanceRef.current = echarts.init(chartRef.current);
    }

    const colorStops = [
      [0.0, "#c0392b"],
      [0.3, "#e67e22"],
      [0.5, "#b8860b"],
      [0.72, "#2d8a56"],
      [0.84, "#1a6b4a"],
      [1.0, "#145a3c"],
    ];

    instanceRef.current.setOption(
      {
        series: [
          {
            type: "gauge",
            startAngle: 210,
            endAngle: -30,
            center: ["50%", "55%"],
            radius: "90%",
            min: 0,
            max: 100,
            axisLine: {
              show: true,
              lineStyle: {
                width: 8,
                color: [colorStops],
              },
            },
            pointer: {
              show: true,
              length: "60%",
              width: 4,
              itemStyle: { color: "auto" },
            },
            axisTick: { show: false },
            splitLine: { show: false },
            axisLabel: { show: false },
            detail: {
              valueAnimation: true,
              fontSize: size * 0.18,
              fontWeight: "bold",
              offsetCenter: [0, "45%"],
              formatter: showLabel ? "{value}" : "",
            },
            title: { show: false },
            data: [{ value: score }],
          },
        ],
      },
      true
    );

    const handleResize = () => instanceRef.current?.resize();
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, [score, size, showLabel]);

  return (
    <div
      ref={chartRef}
      style={{ width: size, height: size * 0.85 }}
    />
  );
}
