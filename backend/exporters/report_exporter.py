"""PDF (ReportLab), interactive standalone HTML, and CSV flight health exporters."""

from __future__ import annotations

import csv
import html
import io
import json
from datetime import datetime, timezone
from typing import Any

from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _fmt(val: Any, suffix: str = "", decimals: int = 2) -> str:
    if val is None:
        return "N/A"
    if isinstance(val, int):
        return f"{val}{suffix}"
    if isinstance(val, float):
        return f"{val:.{decimals}f}{suffix}"
    return f"{val}{suffix}"


def _build_pdf_telemetry_drawing(
    records: list[dict[str, Any]],
    series_key: str,
    title: str,
    line_color: colors.Color,
    y_label: str,
) -> Drawing | None:
    pts: list[tuple[float, float]] = []
    step = max(1, len(records) // 250)
    for row in records[::step]:
        t = row.get("timestamp_s")
        v = row.get(series_key)
        if t is not None and v is not None:
            pts.append((float(t), float(v)))

    if len(pts) < 2:
        return None

    y_vals = [p[1] for p in pts]
    y_min = min(y_vals)
    y_max = max(y_vals)
    if abs(y_max - y_min) < 1e-4:
        y_min -= 1.0
        y_max += 1.0
    else:
        pad = 0.1 * (y_max - y_min)
        y_min -= pad
        y_max += pad

    x_min = pts[0][0]
    x_max = pts[-1][0]
    if abs(x_max - x_min) < 1e-4:
        x_max = x_min + 1.0

    drawing = Drawing(480, 145)
    drawing.add(String(45, 130, f"{title} ({y_label})", fontSize=9, fillColor=colors.HexColor("#0f172a")))

    lp = LinePlot()
    lp.x = 45
    lp.y = 22
    lp.width = 415
    lp.height = 98
    lp.data = [pts]
    lp.lines[0].strokeColor = line_color
    lp.lines[0].strokeWidth = 1.6
    lp.xValueAxis.valueMin = x_min
    lp.xValueAxis.valueMax = x_max
    lp.xValueAxis.labels.fontSize = 7
    lp.yValueAxis.valueMin = y_min
    lp.yValueAxis.valueMax = y_max
    lp.yValueAxis.labels.fontSize = 7
    lp.yValueAxis.gridStrokeColor = colors.HexColor("#e2e8f0")
    lp.yValueAxis.visibleGrid = True
    drawing.add(lp)
    return drawing


def export_pdf_report(
    flight_meta: dict[str, Any],
    summary: dict[str, Any],
    anomalies: list[dict[str, Any]],
    events: list[dict[str, Any]],
    telemetry_records: list[dict[str, Any]],
) -> bytes:
    """Generate a binary PDF engineering flight health report via ReportLab."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
        topMargin=0.55 * inch,
        bottomMargin=0.55 * inch,
        title=f"AeroLens Report - {flight_meta.get('name', 'Flight')}",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "AeroTitle",
        parent=styles["Heading1"],
        fontSize=18,
        textColor=colors.HexColor("#0f172a"),
        spaceAfter=4,
    )
    subtitle_style = ParagraphStyle(
        "AeroSub",
        parent=styles["Normal"],
        fontSize=9.5,
        textColor=colors.HexColor("#475569"),
        spaceAfter=8,
    )
    section_style = ParagraphStyle(
        "AeroSec",
        parent=styles["Heading2"],
        fontSize=12,
        textColor=colors.HexColor("#0369a1"),
        spaceBefore=10,
        spaceAfter=6,
    )
    body_style = ParagraphStyle(
        "AeroBody",
        parent=styles["Normal"],
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#1e293b"),
    )

    elements: list[Any] = []

    flight_name = html.escape(str(flight_meta.get("name", "Untitled Flight")))
    elements.append(Paragraph("AEROLENS FLIGHT ANALYTICS — DIAGNOSTIC REPORT", title_style))
    elements.append(
        Paragraph(
            f"Flight: <b>{flight_name}</b> | Autopilot: {html.escape(str(flight_meta.get('autopilot', 'Unknown')))} "
            f"| Vehicle: {html.escape(str(flight_meta.get('vehicle_type', 'Multirotor')))} "
            f"| Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            subtitle_style,
        )
    )

    if flight_meta.get("is_synthetic"):
        synth_tbl = Table(
            [[f"SYNTHETIC TEST DATASET — {flight_meta.get('synthetic_label') or 'Deterministic Simulation'}"]],
            colWidths=[7.3 * inch],
        )
        synth_tbl.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fef3c7")),
                    ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#92400e")),
                    ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                    ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#f59e0b")),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        elements.append(synth_tbl)
        elements.append(Spacer(1, 8))

    # Executive Scorecard
    elements.append(Paragraph("1. Executive Flight Health Scorecard", section_style))
    score_data = [
        ["Overall Score", "Grade", "Status", "Power", "Vibration", "Attitude", "Navigation"],
        [
            f"{_fmt(summary.get('health_score'), '/100', 1)}",
            str(summary.get("health_grade", "-")),
            str(summary.get("flight_status", "-")),
            f"{_fmt(summary.get('power_score'), '', 1)}",
            f"{_fmt(summary.get('vibration_score'), '', 1)}",
            f"{_fmt(summary.get('attitude_score'), '', 1)}",
            f"{_fmt(summary.get('navigation_score'), '', 1)}",
        ],
    ]
    score_tbl = Table(score_data, colWidths=[1.15 * inch, 0.7 * inch, 1.05 * inch, 1.1 * inch, 1.1 * inch, 1.1 * inch, 1.1 * inch])
    status_val = str(summary.get("flight_status", "NOMINAL"))
    status_bg = (
        colors.HexColor("#dcfce7")
        if status_val == "NOMINAL"
        else (colors.HexColor("#fef3c7") if status_val == "CAUTION" else colors.HexColor("#fee2e2"))
    )
    score_tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("BACKGROUND", (2, 1), (2, 1), status_bg),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    elements.append(score_tbl)

    # Quantitative Subsystem Metrics
    elements.append(Paragraph("2. Physics & Subsystem Telemetry Summary", section_style))
    metrics_rows = [
        ["Metric", "Value", "Metric", "Value"],
        [
            "Duration / Samples",
            f"{_fmt(summary.get('duration_s'), ' s', 1)} ({summary.get('sample_count', 0)} pts)",
            "Sample Rate",
            _fmt(summary.get("sample_rate_hz"), " Hz", 1),
        ],
        [
            "Max Altitude / Distance",
            f"{_fmt(summary.get('max_altitude_m'), ' m', 1)} / {_fmt(summary.get('total_distance_m'), ' m', 0)}",
            "Max Groundspeed",
            _fmt(summary.get("max_groundspeed_ms"), " m/s", 1),
        ],
        [
            "Estimated Pack Cells",
            f"{summary.get('estimated_cell_count', '?')}S LiPo",
            "Pack Voltage (Min / Max)",
            f"{_fmt(summary.get('voltage_min_v'), ' V')} / {_fmt(summary.get('voltage_max_v'), ' V')}",
        ],
        [
            "Estimated Battery R_int",
            f"{_fmt(summary.get('internal_resistance_mohm'), ' mΩ', 1)} ({_fmt(summary.get('r_int_per_cell_mohm'), ' mΩ/cell', 1)})",
            "Regression Fit R² / Max Sag",
            f"{_fmt(summary.get('r_int_r2'), '', 3)} / {_fmt(summary.get('max_voltage_sag_v'), ' V', 2)}",
        ],
        [
            "Vibration Total RMS / Peak",
            f"{_fmt(summary.get('vibe_rms_total'), ' m/s²', 2)} / {_fmt(summary.get('vibe_peak_total'), ' m/s²', 2)}",
            "FFT Peak Freq / Clips",
            f"{_fmt(summary.get('dominant_vibe_freq_hz'), ' Hz', 1)} / {summary.get('total_clip_count', 0)} clips",
        ],
        [
            "Roll / Pitch RMS Error",
            f"{_fmt(summary.get('roll_rms_error_deg'), '°', 2)} / {_fmt(summary.get('pitch_rms_error_deg'), '°', 2)}",
            "PID Lag (Roll / Pitch)",
            f"{_fmt(summary.get('roll_lag_ms'), ' ms', 0)} / {_fmt(summary.get('pitch_lag_ms'), ' ms', 0)}",
        ],
        [
            "GPS HDOP (Mean / Max)",
            f"{_fmt(summary.get('gps_hdop_mean'), '', 2)} / {_fmt(summary.get('gps_hdop_max'), '', 2)}",
            "Min Sats / GPS Jumps",
            f"{_fmt(summary.get('gps_nsats_min'), ' sats', 0)} / {summary.get('gps_jump_count', 0)} jumps",
        ],
    ]
    metrics_tbl = Table(metrics_rows, colWidths=[1.8 * inch, 1.85 * inch, 1.8 * inch, 1.85 * inch])
    metrics_tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e293b")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (2, 1), (2, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    elements.append(metrics_tbl)

    # Embedded Vector Telemetry Charts
    v_chart = _build_pdf_telemetry_drawing(
        telemetry_records,
        "voltage_v",
        "Battery Pack Voltage vs Time",
        colors.HexColor("#0284c7"),
        "V",
    )
    vib_chart = _build_pdf_telemetry_drawing(
        telemetry_records,
        "vibe_mag",
        "Combined IMU Vibration Magnitude vs Time",
        colors.HexColor("#d97706"),
        "m/s²",
    )
    if v_chart or vib_chart:
        elements.append(Paragraph("3. Telemetry Signatures", section_style))
        if v_chart:
            elements.append(v_chart)
        if vib_chart:
            elements.append(vib_chart)

    # Detected Anomalies Table
    elements.append(Paragraph(f"4. Detected Anomalies ({len(anomalies)})", section_style))
    if not anomalies:
        elements.append(
            Paragraph("No physics or statistical anomalies exceeded safety thresholds during this flight.", body_style)
        )
    else:
        anom_rows: list[list[Any]] = [
            ["Severity", "Category", "Window (s)", "Diagnostic Finding", "Actionable Recommendation"]
        ]
        for a in anomalies:
            anom_rows.append(
                [
                    str(a.get("severity", "")).upper(),
                    str(a.get("category", "")).upper(),
                    f"{a.get('start_time_s', 0):.1f}-{a.get('end_time_s', 0):.1f}",
                    Paragraph(f"<b>{html.escape(str(a.get('title', '')))}</b><br/>{html.escape(str(a.get('description', '')))}", body_style),
                    Paragraph(html.escape(str(a.get("recommendation", ""))), body_style),
                ]
            )
        anom_tbl = Table(anom_rows, colWidths=[0.75 * inch, 0.85 * inch, 0.8 * inch, 2.75 * inch, 2.15 * inch])
        anom_tbl.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        elements.append(anom_tbl)

    # Timeline Events
    if events:
        elements.append(Paragraph(f"5. Mission Event Timeline (Top {min(len(events), 30)})", section_style))
        ev_rows: list[list[Any]] = [["Time (s)", "Type", "Severity", "Event"]]
        for ev in events[:30]:
            ev_rows.append(
                [
                    f"{ev.get('timestamp_s', 0):.1f}",
                    str(ev.get("event_type", "")),
                    str(ev.get("severity", "")).upper(),
                    Paragraph(f"<b>{html.escape(str(ev.get('title', '')))}</b> — {html.escape(str(ev.get('description', '')))}", body_style),
                ]
            )
        ev_tbl = Table(ev_rows, colWidths=[0.7 * inch, 1.05 * inch, 0.8 * inch, 4.75 * inch])
        ev_tbl.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e293b")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
                ]
            )
        )
        elements.append(ev_tbl)

    doc.build(elements)
    return buffer.getvalue()


def export_html_report(
    flight_meta: dict[str, Any],
    summary: dict[str, Any],
    anomalies: list[dict[str, Any]],
    events: list[dict[str, Any]],
    fft_spectrum: dict[str, list[float]],
    telemetry_records: list[dict[str, Any]],
) -> str:
    """Generate a self-contained interactive standalone HTML flight report."""
    step = max(1, len(telemetry_records) // 400)
    sampled = telemetry_records[::step]

    payload_json = json.dumps(
        {
            "records": sampled,
            "fft": fft_spectrum,
            "anomalies": anomalies,
        }
    )

    synth_banner = ""
    if flight_meta.get("is_synthetic"):
        synth_banner = (
            '<div class="synth-banner">SYNTHETIC SIMULATION DATASET — '
            f"{html.escape(str(flight_meta.get('synthetic_label') or 'Physics-Generated Test Flight'))}</div>"
        )

    anom_html_rows = []
    for a in anomalies:
        sev = html.escape(str(a.get("severity", "info")))
        anom_html_rows.append(
            f"<tr>"
            f"<td><span class='badge badge-{sev}'>{sev.upper()}</span></td>"
            f"<td>{html.escape(str(a.get('category', '')))}</td>"
            f"<td>{a.get('start_time_s', 0):.1f}s – {a.get('end_time_s', 0):.1f}s</td>"
            f"<td><strong>{html.escape(str(a.get('title', '')))}</strong><br/><small>{html.escape(str(a.get('description', '')))}</small></td>"
            f"<td>{html.escape(str(a.get('recommendation', '')))}</td>"
            f"</tr>"
        )
    if not anom_html_rows:
        anom_html_rows.append(
            "<tr><td colspan='5' style='text-align:center;color:#94a3b8'>No anomalies detected. Flight operated within nominal thresholds.</td></tr>"
        )

    ev_html_rows = [
        f"<tr><td>{e.get('timestamp_s', 0):.1f}s</td><td>{html.escape(str(e.get('event_type', '')))}</td>"
        f"<td><span class='badge badge-{html.escape(str(e.get('severity', 'info')))}'>{html.escape(str(e.get('severity', 'info'))).upper()}</span></td>"
        f"<td><strong>{html.escape(str(e.get('title', '')))}</strong> — {html.escape(str(e.get('description', '')))}</td></tr>"
        for e in events[:50]
    ]

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>AeroLens Interactive Report — {html.escape(str(flight_meta.get('name', 'Flight')))}</title>
<style>
  :root {{ --bg:#070b14; --panel:#0f172a; --border:#1e293b; --text:#e2e8f0; --muted:#94a3b8; --cyan:#38bdf8; --emerald:#10b981; --amber:#f59e0b; --rose:#f43f5e; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; padding:24px; font-family:Inter,system-ui,-apple-system,sans-serif; background:var(--bg); color:var(--text); }}
  .container {{ max-width:1280px; margin:0 auto; }}
  .header {{ display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:16px; border-bottom:1px solid var(--border); padding-bottom:16px; margin-bottom:20px; }}
  h1 {{ margin:0; font-size:22px; color:#f8fafc; }}
  .sub {{ color:var(--muted); font-size:13px; margin-top:4px; }}
  .synth-banner {{ background:rgba(245,158,11,0.14); border:1px solid rgba(245,158,11,0.5); color:#fbbf24; padding:10px 14px; border-radius:8px; font-size:13px; font-weight:600; margin-bottom:16px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(175px,1fr)); gap:12px; margin-bottom:20px; }}
  .card {{ background:var(--panel); border:1px solid var(--border); border-radius:10px; padding:14px; }}
  .card-label {{ color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:0.06em; }}
  .card-val {{ font-size:22px; font-weight:700; margin-top:6px; color:#f8fafc; }}
  .charts-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(520px,1fr)); gap:16px; margin-bottom:22px; }}
  .chart-card {{ background:var(--panel); border:1px solid var(--border); border-radius:10px; padding:14px; }}
  .chart-title {{ font-size:13px; font-weight:600; margin-bottom:8px; color:var(--cyan); }}
  svg.plot {{ width:100%; height:220px; background:#090f1d; border-radius:6px; }}
  table {{ width:100%; border-collapse:collapse; background:var(--panel); border:1px solid var(--border); border-radius:10px; overflow:hidden; margin-bottom:22px; font-size:13px; }}
  th, td {{ padding:10px 12px; border-bottom:1px solid var(--border); text-align:left; vertical-align:top; }}
  th {{ background:#131f38; color:#cbd5e1; font-size:11px; text-transform:uppercase; }}
  .badge {{ display:inline-block; padding:2px 8px; border-radius:999px; font-size:11px; font-weight:700; }}
  .badge-critical {{ background:rgba(244,63,94,0.2); color:#fda4af; }}
  .badge-warning {{ background:rgba(245,158,11,0.2); color:#fcd34d; }}
  .badge-info {{ background:rgba(56,189,248,0.2); color:#7dd3fc; }}
  .tooltip {{ position:fixed; pointer-events:none; background:#020617; border:1px solid var(--cyan); padding:6px 10px; border-radius:6px; font-size:12px; display:none; z-index:99; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div>
      <h1>AEROLENS // Post-Flight Telemetry Intelligence Report</h1>
      <div class="sub">Flight: <strong>{html.escape(str(flight_meta.get('name', 'Untitled')))}</strong> &bull; Autopilot: {html.escape(str(flight_meta.get('autopilot', 'Unknown')))} &bull; Format: {html.escape(str(flight_meta.get('format_id', '')))}</div>
    </div>
    <div style="text-align:right">
      <div style="font-size:28px;font-weight:800;color:var(--cyan)">{_fmt(summary.get('health_score'), '/100', 1)} ({html.escape(str(summary.get('health_grade', '?')))})</div>
      <div class="sub">Status: {html.escape(str(summary.get('flight_status', 'NOMINAL')))}</div>
    </div>
  </div>
  {synth_banner}
  <div class="grid">
    <div class="card"><div class="card-label">Power Score</div><div class="card-val">{_fmt(summary.get('power_score'), '', 1)}</div></div>
    <div class="card"><div class="card-label">Vibration Score</div><div class="card-val">{_fmt(summary.get('vibration_score'), '', 1)}</div></div>
    <div class="card"><div class="card-label">Attitude Score</div><div class="card-val">{_fmt(summary.get('attitude_score'), '', 1)}</div></div>
    <div class="card"><div class="card-label">Navigation Score</div><div class="card-val">{_fmt(summary.get('navigation_score'), '', 1)}</div></div>
    <div class="card"><div class="card-label">Battery R_int</div><div class="card-val">{_fmt(summary.get('internal_resistance_mohm'), ' mΩ', 1)}</div></div>
    <div class="card"><div class="card-label">Vibe RMS / Peak</div><div class="card-val">{_fmt(summary.get('vibe_rms_total'), ' m/s²', 1)}</div></div>
    <div class="card"><div class="card-label">FFT Peak Freq</div><div class="card-val">{_fmt(summary.get('dominant_vibe_freq_hz'), ' Hz', 1)}</div></div>
    <div class="card"><div class="card-label">Roll/Pitch Lag</div><div class="card-val">{_fmt(summary.get('roll_lag_ms'), ' ms', 0)}</div></div>
  </div>

  <div class="charts-grid">
    <div class="chart-card"><div class="chart-title">Battery Voltage (V) &amp; Current (A)</div><svg id="chart-power" class="plot"></svg></div>
    <div class="chart-card"><div class="chart-title">Vibration Magnitude (m/s²)</div><svg id="chart-vibe" class="plot"></svg></div>
    <div class="chart-card"><div class="chart-title">Attitude Roll &amp; Pitch Tracking Error (deg)</div><svg id="chart-att" class="plot"></svg></div>
    <div class="chart-card"><div class="chart-title">Vibration FFT Amplitude Spectrum (Hz)</div><svg id="chart-fft" class="plot"></svg></div>
  </div>

  <h3>Detected Anomalies ({len(anomalies)})</h3>
  <table>
    <thead><tr><th>Severity</th><th>Subsystem</th><th>Window</th><th>Diagnostic Finding</th><th>Recommendation</th></tr></thead>
    <tbody>{''.join(anom_html_rows)}</tbody>
  </table>

  <h3>Timeline Events ({len(events)})</h3>
  <table>
    <thead><tr><th>Time</th><th>Type</th><th>Severity</th><th>Description</th></tr></thead>
    <tbody>{''.join(ev_html_rows)}</tbody>
  </table>
</div>
<div id="tip" class="tooltip"></div>
<script>
const DATA = {payload_json};
const tip = document.getElementById('tip');
function drawSeries(svgId, xVals, seriesList) {{
  const svg = document.getElementById(svgId);
  if (!svg || xVals.length < 2) return;
  const W = 560, H = 220, pad = 34;
  svg.setAttribute('viewBox', `0 0 ${{W}} ${{H}}`);
  let allY = [];
  seriesList.forEach(s => s.y.forEach(v => {{ if (v !== null && !Number.isNaN(v)) allY.push(v); }}));
  if (!allY.length) return;
  let yMin = Math.min(...allY), yMax = Math.max(...allY);
  if (Math.abs(yMax - yMin) < 1e-4) {{ yMin -= 1; yMax += 1; }}
  const xMin = xVals[0], xMax = xVals[xVals.length - 1] || 1;
  const sx = x => pad + ((x - xMin) / (xMax - xMin || 1)) * (W - 2 * pad);
  const sy = y => (H - pad) - ((y - yMin) / (yMax - yMin)) * (H - 2 * pad);
  let html = `<line x1="${{pad}}" y1="${{H-pad}}" x2="${{W-pad}}" y2="${{H-pad}}" stroke="#334155"/>`
           + `<line x1="${{pad}}" y1="${{pad}}" x2="${{pad}}" y2="${{H-pad}}" stroke="#334155"/>`
           + `<text x="6" y="${{pad+4}}" fill="#94a3b8" font-size="10">${{yMax.toFixed(1)}}</text>`
           + `<text x="6" y="${{H-pad}}" fill="#94a3b8" font-size="10">${{yMin.toFixed(1)}}</text>`
           + `<text x="${{W-pad-26}}" y="${{H-10}}" fill="#94a3b8" font-size="10">${{xMax.toFixed(1)}}</text>`;
  seriesList.forEach((s, idx) => {{
    let d = '';
    for (let i = 0; i < xVals.length; i++) {{
      const v = s.y[i];
      if (v === null || v === undefined) continue;
      d += (d ? 'L' : 'M') + sx(xVals[i]).toFixed(1) + ',' + sy(v).toFixed(1);
    }}
    html += `<path d="${{d}}" fill="none" stroke="${{s.color}}" stroke-width="1.8"/>`;
    html += `<text x="${{pad + 10 + idx*120}}" y="16" fill="${{s.color}}" font-size="11">${{s.name}}</text>`;
  }});
  svg.innerHTML = html;
  svg.onmousemove = (ev) => {{
    const rect = svg.getBoundingClientRect();
    const frac = Math.max(0, Math.min(1, ((ev.clientX - rect.left) / rect.width * W - pad) / (W - 2*pad)));
    const idx = Math.min(xVals.length - 1, Math.max(0, Math.round(frac * (xVals.length - 1))));
    const lines = seriesList.map(s => `${{s.name}}: ${{s.y[idx] !== null && s.y[idx] !== undefined ? Number(s.y[idx]).toFixed(2) : 'N/A'}}`);
    tip.style.display = 'block';
    tip.style.left = (ev.clientX + 12) + 'px';
    tip.style.top = (ev.clientY - 12) + 'px';
    tip.innerHTML = `t/f=${{xVals[idx].toFixed(1)}}<br/>` + lines.join('<br/>');
  }};
  svg.onmouseleave = () => {{ tip.style.display = 'none'; }};
}}
const t = DATA.records.map(r => r.timestamp_s || 0);
drawSeries('chart-power', t, [
  {{ name: 'Voltage (V)', color: '#38bdf8', y: DATA.records.map(r => r.voltage_v) }},
  {{ name: 'V_oc Est (V)', color: '#10b981', y: DATA.records.map(r => r.v_oc_est) }}
]);
drawSeries('chart-vibe', t, [
  {{ name: 'Vibe Mag (m/s2)', color: '#f59e0b', y: DATA.records.map(r => r.vibe_mag) }}
]);
drawSeries('chart-att', t, [
  {{ name: 'Roll Err (deg)', color: '#38bdf8', y: DATA.records.map(r => r.roll_err_deg) }},
  {{ name: 'Pitch Err (deg)', color: '#f43f5e', y: DATA.records.map(r => r.pitch_err_deg) }}
]);
if (DATA.fft && DATA.fft.frequencies_hz && DATA.fft.frequencies_hz.length) {{
  drawSeries('chart-fft', DATA.fft.frequencies_hz, [
    {{ name: 'FFT Amplitude', color: '#a855f7', y: DATA.fft.amplitudes }}
  ]);
}}
</script>
</body>
</html>"""


def export_csv_report(
    flight_meta: dict[str, Any],
    summary: dict[str, Any],
    anomalies: list[dict[str, Any]],
    telemetry_records: list[dict[str, Any]],
) -> str:
    """Export canonical + derived telemetry rows as RFC-4180 CSV with metadata comments."""
    buf = io.StringIO()
    buf.write(f"# AEROLENS FLIGHT EXPORT: {flight_meta.get('name', 'Flight')}\n")
    buf.write(f"# AUTOPILOT: {flight_meta.get('autopilot', 'Unknown')} | FORMAT: {flight_meta.get('format_id', '')}\n")
    buf.write(f"# SYNTHETIC: {bool(flight_meta.get('is_synthetic', False))}\n")
    buf.write(
        f"# HEALTH_SCORE: {summary.get('health_score', '')} ({summary.get('health_grade', '')}) "
        f"| R_INT_MOHM: {summary.get('internal_resistance_mohm', '')}\n"
    )

    if not telemetry_records:
        return buf.getvalue()

    fieldnames = list(telemetry_records[0].keys()) + ["anomaly_active"]
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for row in telemetry_records:
        t = float(row.get("timestamp_s") or 0.0)
        is_anom = any(
            float(a.get("start_time_s", 0.0)) <= t <= float(a.get("end_time_s", 0.0))
            for a in anomalies
        )
        out = dict(row)
        out["anomaly_active"] = int(is_anom)
        writer.writerow(out)
    return buf.getvalue()
