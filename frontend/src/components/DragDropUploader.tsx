import React, { useRef, useState } from "react";
import { FileUp, Sparkles, UploadCloud } from "lucide-react";
import { apiClient } from "../api/client";
import type { FlightDetail, ParserInfo } from "../types/telemetry";

interface DragDropUploaderProps {
  parsers: ParserInfo[];
  onUploaded: (flight: FlightDetail) => void;
  onSeeded: () => void;
}

export const DragDropUploader: React.FC<DragDropUploaderProps> = ({
  parsers,
  onUploaded,
  onSeeded,
}) => {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [dragging, setDragging] = useState(false);
  const [customName, setCustomName] = useState("");
  const [formatHint, setFormatHint] = useState("");
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleFile = async (file: File) => {
    setUploading(true);
    setError(null);
    try {
      const created = await apiClient.uploadFlight(file, customName, formatHint);
      setCustomName("");
      onUploaded(created);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to parse uploaded log.");
    } finally {
      setUploading(false);
    }
  };

  const handleSeed = async () => {
    setUploading(true);
    setError(null);
    try {
      await apiClient.seedSamples();
      onSeeded();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to seed sample flights.");
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="panel uploader-panel">
      <div
        className={`dropzone ${dragging ? "dragging" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          const file = e.dataTransfer.files?.[0];
          if (file) void handleFile(file);
        }}
        onClick={() => inputRef.current?.click()}
      >
        <input
          ref={inputRef}
          type="file"
          hidden
          accept=".csv,.log,.ulg,.ulog,.tlog,.json,.txt"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void handleFile(file);
            e.target.value = "";
          }}
        />
        <UploadCloud size={28} color="#38bdf8" />
        <div className="drop-text">
          <strong>{uploading ? "Ingesting & Analyzing Telemetry..." : "Drop Flight Log Here or Browse"}</strong>
          <span>
            Supports ArduPilot DataFlash (.csv, .log), PX4 ULog (.ulg, .ulog.json, .ulog.csv), and
            MAVLink (.tlog, .tlog.json)
          </span>
        </div>
        <button
          type="button"
          className="btn btn-primary"
          disabled={uploading}
          onClick={(e) => {
            e.stopPropagation();
            inputRef.current?.click();
          }}
        >
          <FileUp size={15} />
          <span>Select Log</span>
        </button>
      </div>

      <div className="upload-options">
        <input
          type="text"
          className="text-input"
          placeholder="Optional flight mission name..."
          value={customName}
          onChange={(e) => setCustomName(e.target.value)}
        />
        <select
          className="select-input"
          value={formatHint}
          onChange={(e) => setFormatHint(e.target.value)}
          aria-label="Parser format override"
        >
          <option value="">Auto-detect Format</option>
          {parsers.map((p) => (
            <option key={p.format_id} value={p.format_id}>
              {p.display_name}
            </option>
          ))}
        </select>
        <button type="button" className="btn btn-ghost" disabled={uploading} onClick={handleSeed}>
          <Sparkles size={14} />
          <span>Restore Synthetic Samples</span>
        </button>
      </div>

      {error && <div className="alert-error">{error}</div>}
    </div>
  );
};
