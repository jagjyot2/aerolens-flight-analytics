import React, { useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, Crosshair, ShieldAlert } from "lucide-react";
import type { DetectedAnomaly, FlightSummary, TimelineEvent } from "../types/telemetry";

interface AnomalyPanelProps {
  summary: FlightSummary;
  anomalies: DetectedAnomaly[];
  events: TimelineEvent[];
  onJumpToTime: (timeS: number) => void;
}

const scoreColor = (score: number): string => {
  if (score >= 85) return "#10b981";
  if (score >= 65) return "#f59e0b";
  return "#f43f5e";
};

export const AnomalyPanel: React.FC<AnomalyPanelProps> = ({
  summary,
  anomalies,
  events,
  onJumpToTime,
}) => {
  const [catFilter, setCatFilter] = useState<string>("all");
  const [tab, setTab] = useState<"anomalies" | "timeline">("anomalies");

  const filteredAnomalies = useMemo(() => {
    if (catFilter === "all") return anomalies;
    return anomalies.filter((a) => a.category === catFilter);
  }, [anomalies, catFilter]);

  const subsystems = [
    { label: "Power & R_int", score: summary.power_score },
    { label: "Vibration & FFT", score: summary.vibration_score },
    { label: "Attitude & PID", score: summary.attitude_score },
    { label: "Navigation & GNSS", score: summary.navigation_score },
  ];

  return (
    <div className="anomaly-layout">
      <div className="panel scorecard-panel">
        <div className="health-hero">
          <div
            className="health-ring"
            style={{
              borderColor: scoreColor(summary.health_score),
              color: scoreColor(summary.health_score),
            }}
          >
            <div className="health-num">{summary.health_score.toFixed(1)}</div>
            <div className="health-grade">GRADE {summary.health_grade}</div>
          </div>
          <div className="health-meta">
            <span className={`status-pill status-${summary.flight_status.toLowerCase()}`}>
              {summary.flight_status}
            </span>
            <p className="muted small-copy">
              Composite score weighted equally across electro-chemical battery health, IMU spectral
              vibration, attitude PID control fidelity, and GNSS integrity.
            </p>
          </div>
        </div>

        <div className="subsystem-bars">
          {subsystems.map((sub) => (
            <div key={sub.label} className="sub-bar-row">
              <div className="sub-bar-head">
                <span>{sub.label}</span>
                <span className="mono-val">{sub.score.toFixed(1)}/100</span>
              </div>
              <div className="bar-track">
                <div
                  className="bar-fill"
                  style={{ width: `${Math.max(4, sub.score)}%`, backgroundColor: scoreColor(sub.score) }}
                />
              </div>
            </div>
          ))}
        </div>

        <div className="physics-kpi-grid">
          <div className="kpi-mini">
            <span>Battery R_int ({summary.estimated_cell_count}S)</span>
            <strong>
              {summary.internal_resistance_mohm !== null
                ? `${summary.internal_resistance_mohm.toFixed(1)} mΩ`
                : "N/A"}
            </strong>
            <small>
              {summary.r_int_per_cell_mohm !== null
                ? `${summary.r_int_per_cell_mohm.toFixed(1)} mΩ/cell • R²=${summary.r_int_r2?.toFixed(3)}`
                : "—"}
            </small>
          </div>
          <div className="kpi-mini">
            <span>Vibe RMS / FFT Peak</span>
            <strong>
              {summary.vibe_rms_total !== null ? `${summary.vibe_rms_total.toFixed(1)} m/s²` : "N/A"}
            </strong>
            <small>
              {summary.dominant_vibe_freq_hz !== null
                ? `Peak @ ${summary.dominant_vibe_freq_hz.toFixed(1)} Hz • ${summary.total_clip_count} clips`
                : `${summary.total_clip_count} clips`}
            </small>
          </div>
          <div className="kpi-mini">
            <span>PID Error &amp; Lag</span>
            <strong>
              {summary.roll_rms_error_deg !== null ? `${summary.roll_rms_error_deg.toFixed(2)}°` : "N/A"}
            </strong>
            <small>
              Lag: {summary.roll_lag_ms !== null ? `${summary.roll_lag_ms.toFixed(0)} ms` : "—"}
              {summary.oscillation_freq_hz ? ` • Osc ${summary.oscillation_freq_hz.toFixed(1)} Hz` : ""}
            </small>
          </div>
          <div className="kpi-mini">
            <span>GNSS Integrity</span>
            <strong>
              HDOP {summary.gps_hdop_max !== null ? summary.gps_hdop_max.toFixed(2) : "—"}
            </strong>
            <small>
              Min {summary.gps_nsats_min ?? "—"} sats • {summary.gps_jump_count} jumps
            </small>
          </div>
        </div>
      </div>

      <div className="panel findings-panel">
        <div className="findings-header">
          <div className="btn-group">
            <button
              className={`chip-btn ${tab === "anomalies" ? "active" : ""}`}
              onClick={() => setTab("anomalies")}
            >
              Anomalies ({anomalies.length})
            </button>
            <button
              className={`chip-btn ${tab === "timeline" ? "active" : ""}`}
              onClick={() => setTab("timeline")}
            >
              Event Timeline ({events.length})
            </button>
          </div>
          {tab === "anomalies" && (
            <div className="btn-group">
              {["all", "power", "vibration", "attitude", "navigation"].map((c) => (
                <button
                  key={c}
                  className={`chip-btn ${catFilter === c ? "active" : ""}`}
                  onClick={() => setCatFilter(c)}
                >
                  {c.toUpperCase()}
                </button>
              ))}
            </div>
          )}
        </div>

        {tab === "anomalies" ? (
          <div className="anomaly-list">
            {filteredAnomalies.length === 0 ? (
              <div className="nominal-empty">
                <CheckCircle2 size={28} color="#10b981" />
                <div>
                  <strong>No Anomalies Detected</strong>
                  <p className="muted">All physical and statistical diagnostics remain within nominal limits.</p>
                </div>
              </div>
            ) : (
              filteredAnomalies.map((a) => (
                <div key={a.anomaly_id} className={`anomaly-card sev-border-${a.severity}`}>
                  <div className="anomaly-card-top">
                    <div className="anomaly-title-row">
                      {a.severity === "critical" ? (
                        <ShieldAlert size={16} color="#f43f5e" />
                      ) : (
                        <AlertTriangle size={16} color="#f59e0b" />
                      )}
                      <span className={`badge badge-${a.severity}`}>{a.severity.toUpperCase()}</span>
                      <span className="cat-tag">{a.category.toUpperCase()}</span>
                      <strong>{a.title}</strong>
                    </div>
                    <button
                      className="btn-jump"
                      onClick={() => onJumpToTime(a.peak_time_s)}
                      title={`Jump playback to ${a.peak_time_s.toFixed(1)}s`}
                    >
                      <Crosshair size={13} />
                      <span>t={a.peak_time_s.toFixed(1)}s</span>
                    </button>
                  </div>
                  <p className="anomaly-desc">{a.description}</p>
                  <div className="anomaly-foot">
                    <span>
                      Window: <strong>{a.start_time_s.toFixed(1)}s – {a.end_time_s.toFixed(1)}s</strong>
                    </span>
                    <span>
                      Peak: <strong>{a.peak_value} {a.unit}</strong> (Thresh: {a.threshold_value} {a.unit})
                    </span>
                  </div>
                  <div className="anomaly-rec">
                    <strong>Action:</strong> {a.recommendation}
                  </div>
                </div>
              ))
            )}
          </div>
        ) : (
          <div className="timeline-list">
            {events.map((ev, idx) => (
              <div
                key={`${ev.timestamp_s}-${idx}`}
                className="timeline-item"
                onClick={() => onJumpToTime(ev.timestamp_s)}
              >
                <span className="mono-val cyan">{ev.timestamp_s.toFixed(1)}s</span>
                <span className={`badge badge-${ev.severity}`}>{ev.severity.toUpperCase()}</span>
                <div className="timeline-item-body">
                  <strong>{ev.title}</strong>
                  <p className="muted">{ev.description}</p>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
