import type {
  FlightComparisonResult,
  FlightDetail,
  FlightListItem,
  FlightTelemetryResponse,
  ParserInfo,
} from "../types/telemetry";

const API_BASE = "/api";

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, init);
  if (!resp.ok) {
    let msg = `Request failed (${resp.status})`;
    try {
      const body = (await resp.json()) as { detail?: string };
      if (body.detail) msg = body.detail;
    } catch {
      // ignore non-json error bodies
    }
    throw new Error(msg);
  }
  return (await resp.json()) as T;
}

export const apiClient = {
  listParsers: (): Promise<ParserInfo[]> => requestJson(`${API_BASE}/parsers`),

  listFlights: (): Promise<FlightListItem[]> => requestJson(`${API_BASE}/flights`),

  getFlightDetail: (flightId: number): Promise<FlightDetail> =>
    requestJson(`${API_BASE}/flights/${flightId}`),

  getFlightTelemetry: (flightId: number, maxPoints = 2000): Promise<FlightTelemetryResponse> =>
    requestJson(`${API_BASE}/flights/${flightId}/telemetry?max_points=${maxPoints}`),

  uploadFlight: async (file: File, name?: string, formatHint?: string): Promise<FlightDetail> => {
    const formData = new FormData();
    formData.append("file", file);
    if (name && name.trim()) formData.append("name", name.trim());
    if (formatHint && formatHint.trim()) formData.append("format_hint", formatHint.trim());
    return requestJson(`${API_BASE}/flights/upload`, {
      method: "POST",
      body: formData,
    });
  },

  deleteFlight: (flightId: number): Promise<{ deleted: boolean; flight_id: number }> =>
    requestJson(`${API_BASE}/flights/${flightId}`, { method: "DELETE" }),

  seedSamples: (): Promise<{ seeded: number }> =>
    requestJson(`${API_BASE}/flights/seed-samples`, { method: "POST" }),

  compareFlights: (flightIds: number[], nPoints = 80): Promise<FlightComparisonResult> =>
    requestJson(`${API_BASE}/flights/compare`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ flight_ids: flightIds, n_points: nPoints }),
    }),

  exportUrl: (flightId: number, kind: "pdf" | "html" | "csv"): string =>
    `${API_BASE}/flights/${flightId}/export/${kind}`,
};
