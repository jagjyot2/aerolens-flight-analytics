import React, { useCallback, useEffect, useState } from "react";
import {
  Activity,
  Download,
  FileSpreadsheet,
  FileText,
  GitCompare,
  Plane,
  Radio,
  Trash2,
} from "lucide-react";
import { apiClient } from "./api/client";
import { AnomalyPanel } from "./components/AnomalyPanel";
import { DragDropUploader } from "./components/DragDropUploader";
import { FlightComparison } from "./components/FlightComparison";
import { FlightPlaybackTimeline } from "./components/FlightPlaybackTimeline";
import { TelemetryCharts } from "./components/TelemetryCharts";
import type {
  FlightDetail,
  FlightListItem,
  FlightTelemetryResponse,
  ParserInfo,
} from "./types/telemetry";

export const App: React.FC = () => {
  const [flights, setFlights] = useState<FlightListItem[]>([]);
  const [parsers, setParsers] = useState<ParserInfo[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<FlightDetail | null>(null);
  const [telemetry, setTelemetry] = useState<FlightTelemetryResponse | null>(null);
  const [activeView, setActiveView] = useState<"inspect" | "compare">("inspect");

  const [cursorTimeS, setCursorTimeS] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [playbackSpeed, setPlaybackSpeed] = useState(2);
  const [loadingFlight, setLoadingFlight] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadFlights = useCallback(async (preferId?: number) => {
    try {
      const list = await apiClient.listFlights();
      setFlights(list);
      setSelectedId((prev) => {
        if (preferId && list.some((f) => f.id === preferId)) return preferId;
        if (prev && list.some((f) => f.id === prev)) return prev;
        return list.length > 0 ? list[0].id : null;
      });
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load flights.");
    }
  }, []);

  useEffect(() => {
    void apiClient.listParsers().then(setParsers).catch(() => {});
    void loadFlights();
  }, [loadFlights]);

  useEffect(() => {
    if (selectedId === null) {
      setDetail(null);
      setTelemetry(null);
      return;
    }
    let cancelled = false;
    setLoadingFlight(true);
    setIsPlaying(false);
    setCursorTimeS(0);
    Promise.all([
      apiClient.getFlightDetail(selectedId),
      apiClient.getFlightTelemetry(selectedId, 1800),
    ])
      .then(([d, t]) => {
        if (!cancelled) {
          setDetail(d);
          setTelemetry(t);
          setError(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load flight telemetry.");
      })
      .finally(() => {
        if (!cancelled) setLoadingFlight(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  const handleSeek = useCallback(
    (nextTime: number) => {
      const dur = detail?.duration_s ?? 0;
      if (nextTime >= dur) {
        setCursorTimeS(dur);
        setIsPlaying(false);
      } else {
        setCursorTimeS(Math.max(0, nextTime));
      }
    },
    [detail?.duration_s]
  );

  const handleJumpToTime = useCallback(
    (timeS: number) => {
      setIsPlaying(false);
      handleSeek(timeS);
      setActiveView("inspect");
    },
    [handleSeek]
  );

  const handleDelete = async (id: number, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await apiClient.deleteFlight(id);
      await loadFlights();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Delete failed.");
    }
  };

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <div className="brand-icon">
            <Radio size={20} color="#38bdf8" />
          </div>
          <div>
            <h1>AEROLENS</h1>
            <span>UAV Flight Telemetry &amp; Physics Anomaly Intelligence</span>
          </div>
        </div>

        <nav className="top-nav">
          <button
            className={`nav-tab ${activeView === "inspect" ? "active" : ""}`}
            onClick={() => setActiveView("inspect")}
          >
            <Activity size={15} />
            <span>Mission Diagnostics</span>
          </button>
          <button
            className={`nav-tab ${activeView === "compare" ? "active" : ""}`}
            onClick={() => setActiveView("compare")}
          >
            <GitCompare size={15} />
            <span>Compare Flights</span>
          </button>
        </nav>
      </header>

      <div className="workspace">
        <aside className="sidebar">
          <div className="panel sidebar-list-panel">
            <div className="sidebar-head">
              <h2>Ingested Flights ({flights.length})</h2>
            </div>
            <div className="flight-list">
              {flights.map((f) => {
                const isSelected = f.id === selectedId;
                return (
                  <div
                    key={f.id}
                    className={`flight-item ${isSelected ? "selected" : ""}`}
                    onClick={() => {
                      setSelectedId(f.id);
                      setActiveView("inspect");
                    }}
                  >
                    <div className="flight-item-top">
                      <span className="flight-title">{f.name}</span>
                      <button
                        className="icon-btn"
                        title="Delete flight"
                        onClick={(e) => void handleDelete(f.id, e)}
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                    <div className="flight-item-meta">
                      <span className={`badge badge-${f.flight_status.toLowerCase()}`}>
                        {f.health_score.toFixed(0)} ({f.health_grade})
                      </span>
                      <span className="muted">{f.format_id}</span>
                      <span className="muted">{f.duration_s.toFixed(0)}s</span>
                    </div>
                    {f.is_synthetic && (
                      <div className="synth-pill">SYNTHETIC SAMPLE</div>
                    )}
                  </div>
                );
              })}
              {flights.length === 0 && (
                <div className="muted small-copy" style={{ padding: 12 }}>
                  No flights stored yet. Upload a log or restore samples.
                </div>
              )}
            </div>
          </div>

          {detail && (
            <div className="panel export-panel">
              <h3>Export Diagnostic Report</h3>
              <div className="export-buttons">
                <a className="btn btn-primary" href={apiClient.exportUrl(detail.id, "pdf")} download>
                  <FileText size={14} />
                  <span>PDF Report</span>
                </a>
                <a className="btn btn-ghost" href={apiClient.exportUrl(detail.id, "html")} download>
                  <Download size={14} />
                  <span>Interactive HTML</span>
                </a>
                <a className="btn btn-ghost" href={apiClient.exportUrl(detail.id, "csv")} download>
                  <FileSpreadsheet size={14} />
                  <span>Telemetry CSV</span>
                </a>
              </div>
            </div>
          )}
        </aside>

        <main className="main-stage">
          <DragDropUploader
            parsers={parsers}
            onUploaded={(created) => void loadFlights(created.id)}
            onSeeded={() => void loadFlights()}
          />

          {error && <div className="alert-error">{error}</div>}

          {activeView === "compare" ? (
            <FlightComparison flights={flights} />
          ) : loadingFlight ? (
            <div className="panel empty-state">Loading telemetry streams &amp; spectral FFT...</div>
          ) : detail && telemetry ? (
            <>
              <div className="panel mission-banner">
                <div className="mission-title">
                  <Plane size={18} color="#38bdf8" />
                  <div>
                    <h2>{detail.name}</h2>
                    <span className="muted">
                      {detail.autopilot} &bull; {detail.vehicle_type} &bull; {detail.firmware_version}{" "}
                      &bull; {detail.sample_count.toLocaleString()} samples @{" "}
                      {detail.summary.sample_rate_hz} Hz
                    </span>
                  </div>
                </div>
                {detail.is_synthetic && (
                  <div className="synth-banner-inline">
                    SYNTHETIC TEST DATA — {detail.synthetic_label || "Physics Simulation"}
                  </div>
                )}
              </div>

              <FlightPlaybackTimeline
                records={telemetry.records}
                events={detail.events}
                durationS={detail.duration_s}
                cursorTimeS={cursorTimeS}
                isPlaying={isPlaying}
                playbackSpeed={playbackSpeed}
                onSeek={handleSeek}
                onTogglePlay={() => setIsPlaying((p) => !p)}
                onChangeSpeed={setPlaybackSpeed}
              />

              <AnomalyPanel
                summary={detail.summary}
                anomalies={detail.anomalies}
                events={detail.events}
                onJumpToTime={handleJumpToTime}
              />

              <TelemetryCharts
                records={telemetry.records}
                fftSpectrum={detail.fft_spectrum}
                anomalies={detail.anomalies}
                cursorTimeS={cursorTimeS}
                dominantFreqHz={detail.summary.dominant_vibe_freq_hz}
                onSeekTime={handleJumpToTime}
              />
            </>
          ) : (
            <div className="panel empty-state">
              Select a flight from the sidebar or upload a flight log to begin analysis.
            </div>
          )}
        </main>
      </div>
    </div>
  );
};

export default App;
