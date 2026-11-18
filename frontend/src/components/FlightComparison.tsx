import React, { useEffect, useState } from "react";
import { GitCompare } from "lucide-react";
import type Plotly from "plotly.js";
import { apiClient } from "../api/client";
import type { FlightComparisonResult, FlightListItem } from "../types/telemetry";
import { PlotlyChart } from "./PlotlyChart";

interface FlightComparisonProps {
  flights: FlightListItem[];
}

const COMPARE_COLORS = ["#38bdf8", "#f59e0b", "#f43f5e", "#10b981", "#c084fc", "#fb7185"];

export const FlightComparison: React.FC<FlightComparisonProps> = ({ flights }) => {
  const [selectedIds, setSelectedIds] = useState<number[]>(() =>
    flights.slice(0, 2).map((f) => f.id)
  );
  const [result, setResult] = useState<FlightComparisonResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (selectedIds.length < 2 && flights.length >= 2) {
      setSelectedIds(flights.slice(0, 2).map((f) => f.id));
    }
  }, [flights, selectedIds.length]);

  const toggleFlight = (id: number) => {
    setSelectedIds((prev) => {
      if (prev.includes(id)) {
        return prev.length > 2 ? prev.filter((x) => x !== id) : prev;
      }
      if (prev.length >= 4) return prev;
      return [...prev, id];
    });
  };

  useEffect(() => {
    if (selectedIds.length < 2) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    apiClient
      .compareFlights(selectedIds, 80)
      .then((res) => {
        if (!cancelled) setResult(res);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Comparison failed");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedIds]);

  if (flights.length < 2) {
    return (
      <div className="panel empty-state">
        Upload or seed at least 2 flights to run side-by-side mission comparison.
      </div>
    );
  }

  const buildTraces = (
    extractor: (item: FlightComparisonResult["flights"][number]) => (number | null)[]
  ): Plotly.Data[] => {
    if (!result) return [];
    return result.flights.map((f, idx) => ({
      x: f.aligned_progress_pct,
      y: extractor(f),
      type: "scatter",
      mode: "lines",
      name: f.name,
      line: { color: COMPARE_COLORS[idx % COMPARE_COLORS.length], width: 2 },
    }));
  };

  return (
    <div className="comparison-view">
      <div className="panel compare-toolbar">
        <div className="compare-head">
          <GitCompare size={18} color="#38bdf8" />
          <div>
            <h3>Multi-Flight Telemetry Comparison</h3>
            <span className="muted">Select 2–4 flights (first selected acts as baseline)</span>
          </div>
        </div>
        <div className="compare-selector">
          {flights.map((f) => {
            const active = selectedIds.includes(f.id);
            return (
              <button
                key={f.id}
                className={`compare-pill ${active ? "active" : ""}`}
                onClick={() => toggleFlight(f.id)}
              >
                <span className="mono-val">{f.health_score.toFixed(0)}</span>
                <span>{f.name}</span>
              </button>
            );
          })}
        </div>
      </div>

      {error && <div className="alert-error">{error}</div>}
      {loading && <div className="panel muted">Computing aligned flight deltas...</div>}

      {result && (
        <>
          <div className="panel insights-card">
            <h4>Automated Comparative Engineering Insights</h4>
            <ul>
              {result.insights.map((ins, idx) => (
                <li key={idx}>{ins}</li>
              ))}
            </ul>
          </div>

          <div className="charts-grid">
            <div className="panel chart-card">
              <div className="chart-header">
                <h3>Aligned Pack Voltage vs Mission Progress</h3>
              </div>
              <div className="chart-box">
                <PlotlyChart
                  data={buildTraces((f) => f.aligned_voltage_v)}
                  layout={{
                    xaxis: { title: { text: "Mission Progress (%)" } },
                    yaxis: { title: { text: "Voltage (V)" } },
                  }}
                />
              </div>
            </div>

            <div className="panel chart-card">
              <div className="chart-header">
                <h3>Aligned Total Vibration Magnitude</h3>
              </div>
              <div className="chart-box">
                <PlotlyChart
                  data={buildTraces((f) => f.aligned_vibe_mag)}
                  layout={{
                    xaxis: { title: { text: "Mission Progress (%)" } },
                    yaxis: { title: { text: "Vibration (m/s²)" } },
                  }}
                />
              </div>
            </div>

            <div className="panel chart-card">
              <div className="chart-header">
                <h3>Aligned Attitude Tracking RMS Error</h3>
              </div>
              <div className="chart-box">
                <PlotlyChart
                  data={buildTraces((f) => f.aligned_attitude_err_deg)}
                  layout={{
                    xaxis: { title: { text: "Mission Progress (%)" } },
                    yaxis: { title: { text: "Tracking Error (deg)" } },
                  }}
                />
              </div>
            </div>

            <div className="panel chart-card">
              <div className="chart-header">
                <h3>Aligned Altitude Profile</h3>
              </div>
              <div className="chart-box">
                <PlotlyChart
                  data={buildTraces((f) => f.aligned_altitude_m)}
                  layout={{
                    xaxis: { title: { text: "Mission Progress (%)" } },
                    yaxis: { title: { text: "Altitude (m)" } },
                  }}
                />
              </div>
            </div>
          </div>

          <div className="panel table-panel">
            <h3>Quantitative KPI Delta Matrix (Baseline: {result.flights[0].name})</h3>
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Metric</th>
                    <th>Unit</th>
                    {result.flights.map((f, idx) => (
                      <th key={f.flight_id}>
                        {f.name} {idx === 0 ? "(Baseline)" : ""}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.metric_deltas.map((row) => (
                    <tr key={row.metric}>
                      <td>{row.label}</td>
                      <td className="muted">{row.unit}</td>
                      {result.flights.map((f, idx) => {
                        const val = row.compared_values[f.flight_id];
                        const delta = row.deltas_from_baseline[f.flight_id];
                        return (
                          <td key={f.flight_id} className="mono-val">
                            {val !== null && val !== undefined ? val.toFixed(2) : "—"}
                            {idx > 0 && delta !== null && delta !== undefined && (
                              <span className="delta-tag">
                                {" "}
                                ({delta >= 0 ? "+" : ""}
                                {delta.toFixed(2)})
                              </span>
                            )}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
};
