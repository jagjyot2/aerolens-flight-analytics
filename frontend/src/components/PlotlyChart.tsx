import React, { useEffect, useRef } from "react";
import Plotly from "plotly.js-dist-min";

interface PlotlyChartProps {
  data: Plotly.Data[];
  layout: Partial<Plotly.Layout>;
  config?: Partial<Plotly.Config>;
  className?: string;
  onPointClick?: (timeValue: number) => void;
}

export const PlotlyChart: React.FC<PlotlyChartProps> = ({
  data,
  layout,
  config,
  className = "",
  onPointClick,
}) => {
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const mergedLayout: Partial<Plotly.Layout> = {
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "#090f1d",
      font: { color: "#cbd5e1", family: "Inter, system-ui, sans-serif", size: 11 },
      margin: { l: 48, r: 22, t: 28, b: 36 },
      legend: {
        orientation: "h",
        y: 1.14,
        x: 0,
        bgcolor: "rgba(0,0,0,0)",
        font: { size: 10 },
      },
      xaxis: {
        gridcolor: "#1a253b",
        zerolinecolor: "#27344d",
        ...(layout.xaxis || {}),
      },
      yaxis: {
        gridcolor: "#1a253b",
        zerolinecolor: "#27344d",
        ...(layout.yaxis || {}),
      },
      hovermode: "x unified",
      ...layout,
    };

    const mergedConfig: Partial<Plotly.Config> = {
      responsive: true,
      displaylogo: false,
      modeBarButtonsToRemove: ["lasso2d", "select2d", "autoScale2d"],
      ...config,
    };

    void Plotly.react(el, data, mergedLayout, mergedConfig);

    const handleClick = (evt: Plotly.PlotMouseEvent) => {
      if (onPointClick && evt.points && evt.points.length > 0) {
        const xVal = Number(evt.points[0].x);
        if (Number.isFinite(xVal)) {
          onPointClick(xVal);
        }
      }
    };

    const plotlyEl = el as unknown as {
      on?: (event: string, cb: (e: Plotly.PlotMouseEvent) => void) => void;
      removeAllListeners?: (event: string) => void;
    };
    plotlyEl.removeAllListeners?.("plotly_click");
    if (onPointClick) {
      plotlyEl.on?.("plotly_click", handleClick);
    }

    const observer = new ResizeObserver(() => {
      Plotly.Plots.resize(el);
    });
    observer.observe(el);

    return () => {
      observer.disconnect();
    };
  }, [data, layout, config, onPointClick]);

  useEffect(() => {
    const el = containerRef.current;
    return () => {
      if (el) Plotly.purge(el);
    };
  }, []);

  return <div ref={containerRef} className={`plotly-wrap ${className}`} style={{ width: "100%", height: "100%" }} />;
};
