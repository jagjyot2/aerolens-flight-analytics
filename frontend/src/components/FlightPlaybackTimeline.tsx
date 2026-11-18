import React, { useEffect, useMemo } from "react";
import { Pause, Play, RotateCcw } from "lucide-react";
import type { TelemetryRecord, TimelineEvent } from "../types/telemetry";

interface FlightPlaybackTimelineProps {
  records: TelemetryRecord[];
  events: TimelineEvent[];
  durationS: number;
  cursorTimeS: number;
  isPlaying: boolean;
  playbackSpeed: number;
  onSeek: (timeS: number) => void;
  onTogglePlay: () => void;
  onChangeSpeed: (speed: number) => void;
}

const fmt = (v: number | null | undefined, suffix = "", dec = 2): string => {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${v.toFixed(dec)}${suffix}`;
};

export const FlightPlaybackTimeline: React.FC<FlightPlaybackTimelineProps> = ({
  records,
  events,
  durationS,
  cursorTimeS,
  isPlaying,
  playbackSpeed,
  onSeek,
  onTogglePlay,
  onChangeSpeed,
}) => {
  useEffect(() => {
    if (!isPlaying || durationS <= 0) return;
    const stepMs = 100;
    const timer = window.setInterval(() => {
      onSeek(cursorTimeS + (stepMs / 1000) * playbackSpeed);
    }, stepMs);
    return () => window.clearInterval(timer);
  }, [isPlaying, cursorTimeS, durationS, playbackSpeed, onSeek]);

  const currentSample = useMemo(() => {
    if (!records.length) return null;
    let lo = 0;
    let hi = records.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (records[mid].timestamp_s < cursorTimeS) lo = mid + 1;
      else hi = mid;
    }
    return records[lo];
  }, [records, cursorTimeS]);

  const safeDur = Math.max(durationS, 0.1);

  return (
    <div className="panel playback-bar">
      <div className="playback-controls">
        <div className="playback-buttons">
          <button className="btn btn-primary" onClick={onTogglePlay} title={isPlaying ? "Pause" : "Play"}>
            {isPlaying ? <Pause size={15} /> : <Play size={15} />}
            <span>{isPlaying ? "Pause" : "Play"}</span>
          </button>
          <button className="btn btn-ghost" onClick={() => onSeek(0)} title="Reset to t=0">
            <RotateCcw size={14} />
          </button>
          <div className="btn-group">
            {[1, 2, 5, 10].map((spd) => (
              <button
                key={spd}
                className={`chip-btn ${playbackSpeed === spd ? "active" : ""}`}
                onClick={() => onChangeSpeed(spd)}
              >
                {spd}x
              </button>
            ))}
          </div>
        </div>

        <div className="playback-time-readout">
          <span className="mono-val">{cursorTimeS.toFixed(1)}s</span>
          <span className="muted"> / {durationS.toFixed(1)}s</span>
        </div>
      </div>

      <div className="timeline-track-wrap">
        <input
          type="range"
          min={0}
          max={safeDur}
          step={0.1}
          value={Math.min(cursorTimeS, safeDur)}
          onChange={(e) => onSeek(parseFloat(e.target.value))}
          className="timeline-slider"
          aria-label="Flight playback timestamp"
        />
        <div className="timeline-markers">
          {events.slice(0, 35).map((ev, idx) => {
            const leftPct = Math.max(0, Math.min(100, (ev.timestamp_s / safeDur) * 100));
            return (
              <button
                key={`${ev.timestamp_s}-${idx}`}
                className={`marker-dot sev-${ev.severity}`}
                style={{ left: `${leftPct}%` }}
                title={`${ev.timestamp_s.toFixed(1)}s — ${ev.title}`}
                onClick={() => onSeek(ev.timestamp_s)}
              />
            );
          })}
        </div>
      </div>

      {currentSample && (
        <div className="hud-strip">
          <div className="hud-cell">
            <span className="hud-lbl">Mode</span>
            <span className="hud-val cyan">{currentSample.flight_mode || "UNKNOWN"}</span>
          </div>
          <div className="hud-cell">
            <span className="hud-lbl">Voltage</span>
            <span className="hud-val">{fmt(currentSample.voltage_v, " V", 2)}</span>
          </div>
          <div className="hud-cell">
            <span className="hud-lbl">Current</span>
            <span className="hud-val">{fmt(currentSample.current_a, " A", 1)}</span>
          </div>
          <div className="hud-cell">
            <span className="hud-lbl">Altitude</span>
            <span className="hud-val">{fmt(currentSample.baro_alt_m ?? currentSample.gps_alt_m, " m", 1)}</span>
          </div>
          <div className="hud-cell">
            <span className="hud-lbl">Roll / Pitch</span>
            <span className="hud-val">
              {fmt(currentSample.roll_deg, "°", 1)} / {fmt(currentSample.pitch_deg, "°", 1)}
            </span>
          </div>
          <div className="hud-cell">
            <span className="hud-lbl">Vibe Mag</span>
            <span className="hud-val">{fmt(currentSample.vibe_mag, " m/s²", 1)}</span>
          </div>
          <div className="hud-cell">
            <span className="hud-lbl">GNSS</span>
            <span className="hud-val">
              {currentSample.gps_nsats ?? "—"} sats • HDOP {fmt(currentSample.gps_hdop, "", 2)}
            </span>
          </div>
        </div>
      )}
    </div>
  );
};
