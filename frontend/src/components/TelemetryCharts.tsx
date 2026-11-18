import React, { useMemo, useState } from "react";
import type Plotly from "plotly.js";
import type { DetectedAnomaly, FFTSpectrum, TelemetryRecord } from "../types/telemetry";
import { PlotlyChart } from "./PlotlyChart";

interface TelemetryChartsProps {
  records: TelemetryRecord[];
  fftSpectrum: FFTSpectrum;
  anomalies: DetectedAnomaly[];
  cursorTimeS: number;
  dominantFreqHz: number | null;
  onSeekTime: (timeS: number) => void;
}

export const TelemetryCharts: React.FC<TelemetryChartsProps> = ({
  records,
  fftSpectrum,
  anomalies,
  cursorTimeS,
  dominantFreqHz,
  onSeekTime,
}) => {
  const [showDesired, setShowDesired] = useState(true);
  const [attMode, setAttMode] = useState<"angles" | "error">("angles");

  const times = useMemo(() => records.map((r) => r.timestamp_s), [records]);

  const anomalyShapes = useMemo(() => {
    const shapes: Partial<Plotly.Shape>[] = anomalies
      .filter((a) => a.end_time_s > a.start_time_s)
      .slice(0, 12)
      .map((a) => ({
        type: "rect",
        xref: "x",
        yref: "paper",
        x0: a.start_time_s,
        x1: a.end_time_s,
        y0: 0,
        y1: 1,
        fillcolor: a.severity === "critical" ? "rgba(244,63,94,0.12)" : "rgba(245,158,11,0.10)",
        line: { width: 0 },
      }));

    shapes.push({
      type: "line",
      xref: "x",
      yref: "paper",
      x0: cursorTimeS,
      x1: cursorTimeS,
      y0: 0,
      y1: 1,
      line: { color: "#38bdf8", width: 1.5, dash: "dot" },
    });
    return shapes;
  }, [anomalies, cursorTimeS]);

  const powerData: Plotly.Data[] = useMemo(
    () => [
      {
        x: times,
        y: records.map((r) => r.voltage_v),
        type: "scatter",
        mode: "lines",
        name: "Pack Voltage (V)",
        line: { color: "#38bdf8", width: 2 },
      },
      {
        x: times,
        y: records.map((r) => r.v_oc_est),
        type: "scatter",
        mode: "lines",
        name: "IR-Compensated V_oc (V)",
        line: { color: "#10b981", width: 1.5, dash: "dash" },
      },
      {
        x: times,
        y: records.map((r) => r.current_a),
        type: "scatter",
        mode: "lines",
        name: "Current Draw (A)",
        yaxis: "y2",
        line: { color: "#f59e0b", width: 1.4 },
        opacity: 0.85,
      },
    ],
    [times, records]
  );

  const vibeTimeData: Plotly.Data[] = useMemo(
    () => [
      {
        x: times,
        y: records.map((r) => r.vibe_mag),
        type: "scatter",
        mode: "lines",
        name: "Total Vibe Mag",
        line: { color: "#f59e0b", width: 1.9 },
      },
      {
        x: times,
        y: records.map((r) => r.vibe_x),
        type: "scatter",
        mode: "lines",
        name: "Vibe X",
        line: { color: "#38bdf8", width: 1 },
        opacity: 0.65,
      },
      {
        x: times,
        y: records.map((r) => r.vibe_y),
        type: "scatter",
        mode: "lines",
        name: "Vibe Y",
        line: { color: "#a855f7", width: 1 },
        opacity: 0.65,
      },
      {
        x: times,
        y: records.map((r) => r.vibe_z),
        type: "scatter",
        mode: "lines",
        name: "Vibe Z",
        line: { color: "#ec4899", width: 1 },
        opacity: 0.65,
      },
    ],
    [times, records]
  );

  const fftData: Plotly.Data[] = useMemo(
    () => [
      {
        x: fftSpectrum.frequencies_hz || [],
        y: fftSpectrum.amplitudes || [],
        type: "scatter",
        mode: "lines",
        fill: "tozeroy",
        fillcolor: "rgba(168,85,247,0.18)",
        name: "FFT Amplitude (m/s²)",
        line: { color: "#c084fc", width: 1.8 },
      },
    ],
    [fftSpectrum]
  );

  const attitudeData: Plotly.Data[] = useMemo(() => {
    if (attMode === "error") {
      return [
        {
          x: times,
          y: records.map((r) => r.roll_err_deg),
          type: "scatter",
          mode: "lines",
          name: "Roll Error (deg)",
          line: { color: "#38bdf8", width: 1.8 },
        },
        {
          x: times,
          y: records.map((r) => r.pitch_err_deg),
          type: "scatter",
          mode: "lines",
          name: "Pitch Error (deg)",
          line: { color: "#f43f5e", width: 1.8 },
        },
      ];
    }
    const traces: Plotly.Data[] = [
      {
        x: times,
        y: records.map((r) => r.roll_deg),
        type: "scatter",
        mode: "lines",
        name: "Roll Actual (°)",
        line: { color: "#38bdf8", width: 1.9 },
      },
      {
        x: times,
        y: records.map((r) => r.pitch_deg),
        type: "scatter",
        mode: "lines",
        name: "Pitch Actual (°)",
        line: { color: "#10b981", width: 1.9 },
      },
    ];
    if (showDesired) {
      traces.push(
        {
          x: times,
          y: records.map((r) => r.roll_des_deg),
          type: "scatter",
          mode: "lines",
          name: "Roll Setpoint (°)",
          line: { color: "#7dd3fc", width: 1.2, dash: "dot" },
        },
        {
          x: times,
          y: records.map((r) => r.pitch_des_deg),
          type: "scatter",
          mode: "lines",
          name: "Pitch Setpoint (°)",
          line: { color: "#6ee7b7", width: 1.2, dash: "dot" },
        }
      );
    }
    return traces;
  }, [times, records, showDesired, attMode]);

  const gpsIntegrityData: Plotly.Data[] = useMemo(
    () => [
      {
        x: times,
        y: records.map((r) => r.gps_hdop),
        type: "scatter",
        mode: "lines",
        name: "GPS HDOP",
        line: { color: "#f43f5e", width: 1.9 },
      },
      {
        x: times,
        y: records.map((r) => r.gps_nsats),
        type: "scatter",
        mode: "lines",
        name: "Satellites",
        yaxis: "y2",
        line: { color: "#22d3ee", width: 1.7 },
      },
    ],
    [times, records]
  );

  const groundTrackData: Plotly.Data[] = useMemo(() => {
    const valid = records.filter(
      (r) =>
        r.gps_lat !== null &&
        r.gps_lon !== null &&
        Math.abs(r.gps_lat) > 0.001 &&
        Math.abs(r.gps_lon) > 0.001
    );
    const cur = records.reduce<TelemetryRecord | null>((best, r) => {
      if (!best) return r;
      return Math.abs(r.timestamp_s - cursorTimeS) < Math.abs(best.timestamp_s - cursorTimeS) ? r : best;
    }, null);

    const traces: Plotly.Data[] = [
      {
        x: valid.map((r) => r.gps_lon),
        y: valid.map((r) => r.gps_lat),
        type: "scatter",
        mode: "lines",
        name: "Flight Ground Track",
        line: { color: "#38bdf8", width: 2 },
      },
    ];
    if (cur && cur.gps_lat !== null && cur.gps_lon !== null) {
      traces.push({
        x: [cur.gps_lon],
        y: [cur.gps_lat],
        type: "scatter",
        mode: "markers",
        name: "Playback Position",
        marker: { color: "#f43f5e", size: 10, symbol: "diamond" },
      });
    }
    return traces;
  }, [records, cursorTimeS]);

  if (records.length === 0) {
    return <div className="panel empty-state">No telemetry samples loaded.</div>;
  }

  return (
    <div className="charts-grid">
      <div className="panel chart-card">
        <div className="chart-header">
          <div>
            <h3>Powertrain &amp; Battery Sag Electro-Chemistry</h3>
            <span className="chart-sub">Terminal voltage vs IR-compensated V_oc and load current</span>
          </div>
        </div>
        <div className="chart-box">
          <PlotlyChart
            data={powerData}
            onPointClick={onSeekTime}
            layout={{
              shapes: anomalyShapes,
              xaxis: { title: { text: "Mission Time (s)" } },
              yaxis: { title: { text: "Voltage (V)" } },
              yaxis2: {
                title: { text: "Current (A)" },
                overlaying: "y",
                side: "right",
                gridcolor: "rgba(0,0,0,0)",
              },
              margin: { l: 48, r: 48, t: 28, b: 36 },
            }}
          />
        </div>
      </div>

      <div className="panel chart-card">
        <div className="chart-header">
          <div>
            <h3>Attitude PID Loop Tracking</h3>
            <span className="chart-sub">Click any point on chart to seek playback cursor</span>
          </div>
          <div className="btn-group">
            <button
              className={`chip-btn ${attMode === "angles" ? "active" : ""}`}
              onClick={() => setAttMode("angles")}
            >
              Angles
            </button>
            <button
              className={`chip-btn ${attMode === "error" ? "active" : ""}`}
              onClick={() => setAttMode("error")}
            >
              Error
            </button>
            {attMode === "angles" && (
              <button
                className={`chip-btn ${showDesired ? "active" : ""}`}
                onClick={() => setShowDesired((v) => !v)}
              >
                Setpoint
              </button>
            )}
          </div>
        </div>
        <div className="chart-box">
          <PlotlyChart
            data={attitudeData}
            onPointClick={onSeekTime}
            layout={{
              shapes: anomalyShapes,
              xaxis: { title: { text: "Mission Time (s)" } },
              yaxis: { title: { text: attMode === "angles" ? "Angle (deg)" : "Tracking Error (deg)" } },
            }}
          />
        </div>
      </div>

      <div className="panel chart-card">
        <div className="chart-header">
          <div>
            <h3>IMU Vibration Time-Series</h3>
            <span className="chart-sub">3-axis acceleration variance &amp; total RMS envelope (m/s²)</span>
          </div>
        </div>
        <div className="chart-box">
          <PlotlyChart
            data={vibeTimeData}
            onPointClick={onSeekTime}
            layout={{
              shapes: [
                ...anomalyShapes,
                {
                  type: "line",
                  xref: "paper",
                  yref: "y",
                  x0: 0,
                  x1: 1,
                  y0: 32,
                  y1: 32,
                  line: { color: "#f59e0b", width: 1, dash: "dash" },
                },
              ],
              xaxis: { title: { text: "Mission Time (s)" } },
              yaxis: { title: { text: "Vibration (m/s²)" } },
            }}
          />
        </div>
      </div>

      <div className="panel chart-card">
        <div className="chart-header">
          <div>
            <h3>SciPy Spectral Vibration FFT</h3>
            <span className="chart-sub">
              Hann-windowed rFFT amplitude spectrum{" "}
              {dominantFreqHz ? `• Dominant peak: ${dominantFreqHz.toFixed(1)} Hz` : ""}
            </span>
          </div>
        </div>
        <div className="chart-box">
          <PlotlyChart
            data={fftData}
            layout={{
              xaxis: { title: { text: "Frequency (Hz)" } },
              yaxis: { title: { text: "Amplitude (m/s²)" } },
              shapes: dominantFreqHz
                ? [
                    {
                      type: "line",
                      xref: "x",
                      yref: "paper",
                      x0: dominantFreqHz,
                      x1: dominantFreqHz,
                      y0: 0,
                      y1: 1,
                      line: { color: "#f43f5e", width: 1.5, dash: "dash" },
                    },
                  ]
                : [],
            }}
          />
        </div>
      </div>

      <div className="panel chart-card">
        <div className="chart-header">
          <div>
            <h3>GNSS HDOP &amp; Satellite Lock Integrity</h3>
            <span className="chart-sub">Detects multipath spikes (HDOP &gt; 2.0) and constellation drops</span>
          </div>
        </div>
        <div className="chart-box">
          <PlotlyChart
            data={gpsIntegrityData}
            onPointClick={onSeekTime}
            layout={{
              shapes: anomalyShapes,
              xaxis: { title: { text: "Mission Time (s)" } },
              yaxis: { title: { text: "HDOP" } },
              yaxis2: {
                title: { text: "Satellites" },
                overlaying: "y",
                side: "right",
                gridcolor: "rgba(0,0,0,0)",
              },
              margin: { l: 48, r: 48, t: 28, b: 36 },
            }}
          />
        </div>
      </div>

      <div className="panel chart-card">
        <div className="chart-header">
          <div>
            <h3>2D GNSS Ground Track &amp; Glitch Projection</h3>
            <span className="chart-sub">Geographic flight path with live playback vehicle marker</span>
          </div>
        </div>
        <div className="chart-box">
          <PlotlyChart
            data={groundTrackData}
            layout={{
              hovermode: "closest",
              xaxis: { title: { text: "Longitude (°)" } },
              yaxis: { title: { text: "Latitude (°)" }, scaleanchor: "x", scaleratio: 1 },
            }}
          />
        </div>
      </div>
    </div>
  );
};
