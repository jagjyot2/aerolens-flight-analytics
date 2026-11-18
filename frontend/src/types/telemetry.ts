export interface FlightSummary {
  health_score: number;
  health_grade: string;
  flight_status: "NOMINAL" | "CAUTION" | "CRITICAL" | string;
  power_score: number;
  vibration_score: number;
  attitude_score: number;
  navigation_score: number;
  duration_s: number;
  sample_count: number;
  sample_rate_hz: number;
  max_altitude_m: number;
  max_groundspeed_ms: number;
  total_distance_m: number;
  estimated_cell_count: number;
  voltage_min_v: number | null;
  voltage_max_v: number | null;
  voltage_mean_v: number | null;
  current_max_a: number | null;
  current_mean_a: number | null;
  internal_resistance_ohm: number | null;
  internal_resistance_mohm: number | null;
  r_int_per_cell_mohm: number | null;
  r_int_r2: number | null;
  max_voltage_sag_v: number | null;
  vibe_rms_x: number | null;
  vibe_rms_y: number | null;
  vibe_rms_z: number | null;
  vibe_rms_total: number | null;
  vibe_peak_total: number | null;
  total_clip_count: number;
  dominant_vibe_freq_hz: number | null;
  dominant_vibe_amp: number | null;
  roll_rms_error_deg: number | null;
  pitch_rms_error_deg: number | null;
  roll_max_error_deg: number | null;
  pitch_max_error_deg: number | null;
  roll_lag_ms: number | null;
  pitch_lag_ms: number | null;
  oscillation_freq_hz: number | null;
  gps_hdop_mean: number | null;
  gps_hdop_max: number | null;
  gps_nsats_min: number | null;
  gps_nsats_mean: number | null;
  gps_jump_count: number;
}

export interface DetectedAnomaly {
  anomaly_id: string;
  category: "power" | "vibration" | "attitude" | "navigation" | string;
  code: string;
  severity: "info" | "warning" | "critical" | string;
  title: string;
  description: string;
  start_time_s: number;
  end_time_s: number;
  peak_time_s: number;
  peak_value: number;
  threshold_value: number;
  unit: string;
  recommendation: string;
  metrics: Record<string, unknown>;
}

export interface TimelineEvent {
  timestamp_s: number;
  end_time_s: number | null;
  event_type: string;
  category: string;
  severity: string;
  title: string;
  description: string;
}

export interface FFTSpectrum {
  frequencies_hz: number[];
  amplitudes: number[];
}

export interface FlightListItem {
  id: number;
  name: string;
  source_filename: string;
  format_id: string;
  autopilot: string;
  vehicle_type: string;
  firmware_version: string;
  is_synthetic: boolean;
  synthetic_label: string;
  health_score: number;
  health_grade: string;
  flight_status: string;
  duration_s: number;
  sample_count: number;
  anomaly_count: number;
  critical_count: number;
  warning_count: number;
  created_at: string;
  summary: FlightSummary;
}

export interface FlightDetail extends FlightListItem {
  metadata: Record<string, unknown>;
  fft_spectrum: FFTSpectrum;
  anomalies: DetectedAnomaly[];
  events: TimelineEvent[];
}

export interface TelemetryRecord {
  timestamp_s: number;
  voltage_v: number | null;
  current_a: number | null;
  battery_remaining_pct: number | null;
  roll_deg: number | null;
  pitch_deg: number | null;
  yaw_deg: number | null;
  roll_des_deg: number | null;
  pitch_des_deg: number | null;
  yaw_des_deg: number | null;
  vibe_x: number | null;
  vibe_y: number | null;
  vibe_z: number | null;
  clip_count: number | null;
  gps_lat: number | null;
  gps_lon: number | null;
  gps_alt_m: number | null;
  gps_hdop: number | null;
  gps_nsats: number | null;
  gps_speed_ms: number | null;
  baro_alt_m: number | null;
  throttle_pct: number | null;
  flight_mode: string;
  v_oc_est: number | null;
  vibe_mag: number | null;
  roll_err_deg: number | null;
  pitch_err_deg: number | null;
}

export interface FlightTelemetryResponse {
  flight_id: number;
  name: string;
  sample_count: number;
  returned_count: number;
  records: TelemetryRecord[];
  fft_spectrum: FFTSpectrum;
}

export interface FlightComparisonItem {
  flight_id: number;
  name: string;
  autopilot: string;
  is_synthetic: boolean;
  summary: FlightSummary;
  anomaly_count: number;
  critical_count: number;
  warning_count: number;
  aligned_progress_pct: number[];
  aligned_voltage_v: (number | null)[];
  aligned_vibe_mag: (number | null)[];
  aligned_attitude_err_deg: (number | null)[];
  aligned_altitude_m: (number | null)[];
}

export interface MetricDelta {
  metric: string;
  label: string;
  unit: string;
  baseline_value: number | null;
  compared_values: Record<number, number | null>;
  deltas_from_baseline: Record<number, number | null>;
}

export interface FlightComparisonResult {
  baseline_flight_id: number;
  flights: FlightComparisonItem[];
  metric_deltas: MetricDelta[];
  insights: string[];
}

export interface ParserInfo {
  format_id: string;
  display_name: string;
  supported_extensions: string[];
}
