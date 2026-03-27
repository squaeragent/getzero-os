"""Card renderer — turns HTML templates into PNG via Playwright."""

import asyncio
import math
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from playwright.sync_api import sync_playwright

_thread_pool = ThreadPoolExecutor(max_workers=2)

TEMPLATES_DIR = Path(__file__).parent / "templates"


def _format_layer_value(layer: dict) -> str:
    """Human-readable value for a layer row."""
    v = layer.get("value")
    name = layer.get("layer", "")
    if v is None:
        return "---"
    if isinstance(v, dict):
        if "agree" in v and "total" in v:
            return f"{v['agree']}/{v['total']} agree"
        if "bid_ratio" in v:
            return f"bid {v['bid_ratio']:.0%}"
        return str(v)
    if isinstance(v, float):
        if name == "funding":
            return f"{v:+.6f}"
        return f"{v:.2f}"
    if isinstance(v, (int, bool)):
        return str(v)
    return str(v)


def _build_layers_html(layers: list) -> str:
    """Build HTML rows for the 7 evaluation layers."""
    rows = []
    for l in layers:
        icon = "\u2713" if l.get("passed") else "\u2717"
        color = "#c8ff00" if l.get("passed") else "#ff3333"
        val = _format_layer_value(l)
        rows.append(
            f'<div class="layer-row">'
            f'<span style="color:{color}">{icon}</span> '
            f'<span class="layer-name">{l.get("layer", "?")}</span>'
            f'<span class="layer-val">{val}</span>'
            f'</div>'
        )
    return "\n".join(rows)


def _build_consensus_bar(consensus: int, total: int = 7) -> str:
    """Build 7-segment consensus bar."""
    segs = []
    for i in range(total):
        c = "#c8ff00" if i < consensus else "#333"
        segs.append(f'<span class="seg" style="background:{c}"></span>')
    return "".join(segs)


def _build_heat_grid(coins: list) -> str:
    """Build grid cells for heat map (top 10)."""
    top = sorted(coins, key=lambda c: c.get("conviction", 0), reverse=True)[:10]
    cells = []
    for c in top:
        conv = c.get("conviction", 0)
        opacity = max(0.3, min(1.0, conv))
        dir_color = "#ff3333" if c.get("direction") == "SHORT" else "#c8ff00"
        bar_w = int(conv * 100)
        cells.append(
            f'<div class="heat-cell" style="opacity:{opacity}">'
            f'<div class="hc-top"><span class="hc-coin">{c.get("coin", "?")}</span>'
            f'<span class="hc-dir" style="color:{dir_color}">{c.get("direction", "---")}</span></div>'
            f'<div class="hc-mid">{c.get("consensus", 0)}/7</div>'
            f'<div class="hc-bar"><div class="hc-fill" style="width:{bar_w}%"></div></div>'
            f'</div>'
        )
    return "\n".join(cells)


def _velocity_arrow(label: str) -> tuple[str, str]:
    """Return (arrow_html, color) for a velocity label."""
    mapping = {
        "ACCELERATING": ("\u2191\u2191", "#c8ff00"),
        "BUILDING": ("\u2191", "#c8ff00"),
        "STEADY": ("\u2192", "#888"),
        "DECELERATING": ("\u2193", "#ff3333"),
        "RETREATING": ("\u2193\u2193", "#ff3333"),
    }
    return mapping.get(label, ("\u2192", "#888"))


def _build_approaching_rows(approaching: list) -> str:
    """Build rows for approaching coins with optional velocity data."""
    rows = []
    for a in approaching[:5]:
        cons = a.get("consensus", 0)
        thresh = a.get("threshold", 5)
        pct = int((cons / thresh) * 100) if thresh else 0
        dir_color = "#ff3333" if a.get("direction") == "SHORT" else "#c8ff00"
        bn = a.get("bottleneck", "---")

        # Velocity arrow (present when conviction tracker data is available)
        vel_html = ""
        vel_label = a.get("velocity_label")
        if vel_label:
            arrow, arrow_color = _velocity_arrow(vel_label)
            vel_html = f'<span class="ap-vel" style="color:{arrow_color}">{arrow}</span>'

        # Time to threshold
        ttt = a.get("time_to_threshold")
        ttt_html = ""
        if ttt:
            ttt_html = f'<span class="ap-ttt">{ttt} to {thresh}/7</span>'

        rows.append(
            f'<div class="ap-row">'
            f'<div class="ap-top">'
            f'<span class="ap-coin">{a.get("coin", "?")}</span>'
            f'{vel_html}'
            f'<span class="ap-dir" style="color:{dir_color}">{a.get("direction", "---")}</span>'
            f'<span class="ap-frac">{cons}/{thresh}</span>'
            f'</div>'
            f'<div class="ap-bar"><div class="ap-fill" style="width:{pct}%"></div></div>'
            f'<div class="ap-bn">bottleneck: <span style="color:#ffb000">{bn}</span>{ttt_html}</div>'
            f'</div>'
        )
    return "\n".join(rows)


def _build_positions_rows(positions: list) -> str:
    """Build top-3 position rows for brief card."""
    top = sorted(positions, key=lambda p: p.get("size_usd", 0), reverse=True)[:3]
    rows = []
    for p in top:
        dir_color = "#ff3333" if p.get("direction") == "SHORT" else "#c8ff00"
        rows.append(
            f'<div class="pos-row">'
            f'<span class="pos-coin">{p.get("coin", "?")}</span>'
            f'<span class="pos-dir" style="color:{dir_color}">{p.get("direction", "---")}</span>'
            f'<span class="pos-price">${p.get("entry_price", 0):.4f}</span>'
            f'</div>'
        )
    return "\n".join(rows)


def _fg_color(val: int) -> str:
    """Fear & Greed zone color."""
    if val <= 20 or val >= 80:
        return "#ff3333"
    if val <= 40 or val >= 60:
        return "#ffb000"
    return "#e8e4df"


def _fg_label(val: int) -> str:
    if val <= 20:
        return "EXTREME FEAR"
    if val <= 40:
        return "FEAR"
    if val <= 60:
        return "NEUTRAL"
    if val <= 80:
        return "GREED"
    return "EXTREME GREED"


def _build_equity_svg(points: list) -> dict:
    """Build SVG elements for equity curve chart.

    Returns dict with equity_segments, marker_elements, y_max, y_min,
    y_zero_pct, final_pnl, pnl_color.
    """
    if not points:
        return {
            "equity_segments": "", "marker_elements": "",
            "y_max": "0", "y_min": "0", "y_zero_pct": "50",
            "final_pnl": "$0.00", "pnl_color": "#666",
        }

    pnls = [p.get("pnl", 0) for p in points]
    y_max = max(max(pnls), 0)
    y_min = min(min(pnls), 0)
    y_range = y_max - y_min if y_max != y_min else 1

    n = len(points)
    svg_w, svg_h = 740, 280

    def to_xy(i, pnl):
        x = (i / max(n - 1, 1)) * svg_w
        y = svg_h - ((pnl - y_min) / y_range) * svg_h
        return x, y

    # Zero line position (percentage from top)
    y_zero_pct = ((y_max - 0) / y_range) * 100 if y_range else 50

    # Build line segments colored green/red
    segments = []
    for i in range(len(points) - 1):
        x1, y1 = to_xy(i, pnls[i])
        x2, y2 = to_xy(i + 1, pnls[i + 1])
        color = "#c8ff00" if pnls[i + 1] >= 0 else "#ff3333"
        segments.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{color}" stroke-width="2.5" />'
        )

    # Entry/exit markers
    markers = []
    for i, p in enumerate(points):
        ev = p.get("event")
        if ev in ("entry", "exit"):
            x, y = to_xy(i, pnls[i])
            if ev == "entry":
                markers.append(
                    f'<text x="{x:.1f}" y="{y - 8:.1f}" fill="#c8ff00" '
                    f'font-size="14" text-anchor="middle">&#9650;</text>'
                )
            else:
                markers.append(
                    f'<text x="{x:.1f}" y="{y + 16:.1f}" fill="#ff3333" '
                    f'font-size="14" text-anchor="middle">&#9660;</text>'
                )

    final_pnl = pnls[-1]
    return {
        "equity_segments": "\n".join(segments),
        "marker_elements": "\n".join(markers),
        "y_max": f"${y_max:.2f}",
        "y_min": f"${y_min:.2f}",
        "y_zero_pct": f"{y_zero_pct:.0f}",
        "final_pnl": f"${final_pnl:+.2f}",
        "pnl_color": "#c8ff00" if final_pnl >= 0 else "#ff3333",
    }


def _build_radar_svg(layers: list) -> dict:
    """Build SVG elements for 7-axis radar chart.

    Returns dict with web_rings, axis_lines, data_points, axis_labels.
    """
    n = len(layers) if layers else 7
    cx, cy = 250, 170
    max_r = 120

    def polar(angle_idx, radius):
        angle = (2 * math.pi * angle_idx / n) - (math.pi / 2)
        return cx + radius * math.cos(angle), cy + radius * math.sin(angle)

    # Concentric web rings (3 levels)
    rings = []
    for level in (0.33, 0.66, 1.0):
        r = max_r * level
        pts = " ".join(f"{polar(i, r)[0]:.1f},{polar(i, r)[1]:.1f}" for i in range(n))
        rings.append(f'<polygon class="web-ring" points="{pts}" />')

    # Axis lines
    lines = []
    for i in range(n):
        ex, ey = polar(i, max_r)
        lines.append(f'<line class="axis-line" x1="{cx}" y1="{cy}" x2="{ex:.1f}" y2="{ey:.1f}" />')

    # Data polygon (passed = full radius, failed = 30% radius)
    data_pts = []
    for i, layer in enumerate(layers or []):
        r = max_r if layer.get("passed") else max_r * 0.3
        px, py = polar(i, r)
        data_pts.append(f"{px:.1f},{py:.1f}")

    # Labels
    labels = []
    label_r = max_r + 24
    for i, layer in enumerate(layers or []):
        lx, ly = polar(i, label_r)
        name = layer.get("layer", f"L{i+1}")
        css = "label-pass" if layer.get("passed") else "label-fail"
        anchor = "middle"
        if lx < cx - 10:
            anchor = "end"
        elif lx > cx + 10:
            anchor = "start"
        labels.append(
            f'<text class="label-text {css}" x="{lx:.1f}" y="{ly:.1f}" '
            f'text-anchor="{anchor}">{name}</text>'
        )

    return {
        "web_rings": "\n".join(rings),
        "axis_lines": "\n".join(lines),
        "data_points": " ".join(data_pts) if data_pts else "",
        "axis_labels": "\n".join(labels),
    }


def _build_backtest_summary_rows(strategies: list) -> str:
    """Build HTML rows for backtest summary grid."""
    if not strategies:
        return ""
    best_pnl = max((s.get("total_pnl_pct", -999) for s in strategies), default=0)
    rows = []
    for s in strategies[:9]:
        pnl = s.get("total_pnl_pct", 0)
        is_best = abs(pnl - best_pnl) < 1e-9 and pnl > -999
        pnl_class = "pnl-pos" if pnl >= 0 else "pnl-neg"
        best_cls = ' best' if is_best else ''
        wr = s.get("win_rate", 0)
        dd = s.get("max_drawdown_pct", 0)
        sharpe = s.get("sharpe_ratio", 0)
        trades = s.get("total_trades", 0)
        rows.append(
            f'<div class="row{best_cls}">'
            f'<span class="name">{s.get("name", s.get("strategy", "?"))}</span>'
            f'<span class="{pnl_class}">{pnl:+.1f}%</span>'
            f'<span>{trades}</span>'
            f'<span>{wr:.1f}%</span>'
            f'<span>{dd:.1f}%</span>'
            f'<span>{sharpe:.2f}</span>'
            f'</div>'
        )
    return "\n".join(rows)


def _build_backtest_equity_svg(equity_curve: list) -> dict:
    """Build SVG elements for backtest equity curve with drawdown shading.

    Returns dict with equity_segments, drawdown_fill, y_max, y_min,
    y_zero_pct, pnl_color.
    """
    if not equity_curve:
        return {
            "equity_segments": "", "drawdown_fill": "",
            "y_max": "0", "y_min": "0", "y_zero_pct": "50",
            "pnl_color": "#666",
        }

    equities = [p.get("equity", 100) for p in equity_curve]
    start_eq = equities[0] if equities else 100
    pnls = [e - start_eq for e in equities]

    y_max = max(max(pnls), 0)
    y_min = min(min(pnls), 0)
    y_range = y_max - y_min if y_max != y_min else 1

    n = len(equity_curve)
    svg_w, svg_h = 740, 280

    def to_xy(i, pnl):
        x = (i / max(n - 1, 1)) * svg_w
        y = svg_h - ((pnl - y_min) / y_range) * svg_h
        return x, y

    y_zero_pct = ((y_max - 0) / y_range) * 100 if y_range else 50

    # Build line segments colored green/red
    segments = []
    for i in range(n - 1):
        x1, y1 = to_xy(i, pnls[i])
        x2, y2 = to_xy(i + 1, pnls[i + 1])
        color = "#c8ff00" if pnls[i + 1] >= 0 else "#ff3333"
        segments.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{color}" stroke-width="2.5" />'
        )

    # Drawdown shading: fill area between peak-so-far line and equity line
    peak = pnls[0]
    dd_points = []
    peak_points = []
    for i, pnl in enumerate(pnls):
        peak = max(peak, pnl)
        if peak > pnl:  # in drawdown
            x_eq, y_eq = to_xy(i, pnl)
            x_pk, y_pk = to_xy(i, peak)
            dd_points.append((x_eq, y_eq))
            peak_points.append((x_pk, y_pk))

    dd_fill = ""
    if dd_points:
        # Build a filled polygon for each contiguous drawdown region
        poly_pts = []
        for x, y in dd_points:
            poly_pts.append(f"{x:.1f},{y:.1f}")
        for x, y in reversed(peak_points):
            poly_pts.append(f"{x:.1f},{y:.1f}")
        dd_fill = f'<polygon points="{" ".join(poly_pts)}" fill="#ff3333" fill-opacity="0.08" />'

    final_pnl = pnls[-1]
    return {
        "equity_segments": "\n".join(segments),
        "drawdown_fill": dd_fill,
        "y_max": f"${y_max:.2f}",
        "y_min": f"${y_min:.2f}",
        "y_zero_pct": f"{y_zero_pct:.0f}",
        "pnl_color": "#c8ff00" if final_pnl >= 0 else "#ff3333",
    }


def _build_compare_svg(curve_a: list, curve_b: list) -> dict:
    """Build overlaid SVG equity lines for two strategies.

    Returns dict with equity_lines, y_max, y_min.
    """
    if not curve_a and not curve_b:
        return {"equity_lines": "", "y_max": "0", "y_min": "0"}

    def to_pnls(curve):
        eqs = [p.get("equity", 100) for p in curve]
        start = eqs[0] if eqs else 100
        return [e - start for e in eqs]

    pnls_a = to_pnls(curve_a) if curve_a else []
    pnls_b = to_pnls(curve_b) if curve_b else []

    all_pnls = pnls_a + pnls_b
    y_max = max(max(all_pnls), 0) if all_pnls else 0
    y_min = min(min(all_pnls), 0) if all_pnls else 0
    y_range = y_max - y_min if y_max != y_min else 1

    svg_w, svg_h = 740, 200

    def build_polyline(pnls, color):
        n = len(pnls)
        if n < 2:
            return ""
        pts = []
        for i, pnl in enumerate(pnls):
            x = (i / max(n - 1, 1)) * svg_w
            y = svg_h - ((pnl - y_min) / y_range) * svg_h
            pts.append(f"{x:.1f},{y:.1f}")
        return (
            f'<polyline points="{" ".join(pts)}" fill="none" '
            f'stroke="{color}" stroke-width="2" />'
        )

    lines = []
    if pnls_a:
        lines.append(build_polyline(pnls_a, "#c8ff00"))
    if pnls_b:
        lines.append(build_polyline(pnls_b, "#ffb000"))

    return {
        "equity_lines": "\n".join(lines),
        "y_max": f"${y_max:.2f}",
        "y_min": f"${y_min:.2f}",
    }


def _build_gauge_arcs() -> str:
    """Build SVG arc paths for the 5 color zones of the gauge."""
    cx, cy = 250, 220
    r = 160
    zones = [
        (0, 20, "#ff3333"),
        (20, 40, "#ffb000"),
        (40, 60, "#e8e4df"),
        (60, 80, "#ffb000"),
        (80, 100, "#ff3333"),
    ]
    arcs = []
    for start_pct, end_pct, color in zones:
        a1 = math.pi + (start_pct / 100) * math.pi
        a2 = math.pi + (end_pct / 100) * math.pi
        x1 = cx + r * math.cos(a1)
        y1 = cy + r * math.sin(a1)
        x2 = cx + r * math.cos(a2)
        y2 = cy + r * math.sin(a2)
        arcs.append(
            f'<path class="zone-arc" stroke="{color}" '
            f'd="M {x1:.1f} {y1:.1f} A {r} {r} 0 0 1 {x2:.1f} {y2:.1f}" />'
        )
    return "\n".join(arcs)


def _gauge_needle_xy(value: int) -> tuple:
    """Calculate needle endpoint for gauge value 0-100."""
    cx, cy = 250, 220
    needle_len = 130
    angle = math.pi + (value / 100) * math.pi
    nx = cx + needle_len * math.cos(angle)
    ny = cy + needle_len * math.sin(angle)
    return f"{nx:.1f}", f"{ny:.1f}"


def _build_milestones_grid(milestones: list) -> str:
    """Build grid cells for milestone card."""
    cells = []
    # Find the last earned milestone for highlighting
    earned_ids = [m["id"] for m in milestones if m.get("achieved")]
    latest_id = earned_ids[-1] if earned_ids else None

    for m in milestones:
        achieved = m.get("achieved", False)
        is_latest = m.get("id") == latest_id
        icon = "\u2713" if achieved else "\u25CB"
        icon_color = "#c8ff00" if achieved else "#444"
        cls = "milestone"
        if is_latest:
            cls += " latest earned"
        elif achieved:
            cls += " earned"
        name_cls = "m-name earned" if achieved else "m-name"
        cells.append(
            f'<div class="{cls}">'
            f'<span class="m-icon" style="color:{icon_color}">{icon}</span>'
            f'<span class="{name_cls}">{m.get("name", "?")}</span>'
            f'</div>'
        )
    return "\n".join(cells)


def _preprocess(template_name: str, data: dict) -> dict:
    """Expand data dict with pre-built HTML snippets for template injection."""
    out = {}
    # Flatten all top-level scalars
    for k, v in data.items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = str(v) if v is not None else "---"

    if template_name == "eval_card":
        out["layers_html"] = _build_layers_html(data.get("layers", []))
        consensus = data.get("consensus", 0)
        out["consensus_bar"] = _build_consensus_bar(consensus)
        dir_val = data.get("direction", "NONE")
        dir_color = "#ff3333" if dir_val == "SHORT" else "#c8ff00" if dir_val == "LONG" else "#e8e4df"
        out["direction_color"] = dir_color
        conv = data.get("conviction", 0)
        out["conviction_pct"] = f"{conv * 100:.0f}%" if isinstance(conv, (int, float)) else str(conv)

    elif template_name == "heat_card":
        out["heat_grid"] = _build_heat_grid(data.get("coins", []))
        out["count"] = str(data.get("count", len(data.get("coins", []))))

    elif template_name == "brief_card":
        fg = data.get("fear_greed", 50)
        out["fear_greed"] = str(fg)
        out["fg_color"] = _fg_color(fg)
        out["fg_label"] = _fg_label(fg)
        out["fg_rotation"] = str(int(-90 + (fg / 100) * 180))
        positions = data.get("positions", [])
        out["open_positions"] = str(data.get("open_positions", len(positions)))
        n_short = sum(1 for p in positions if p.get("direction") == "SHORT")
        n_long = sum(1 for p in positions if p.get("direction") == "LONG")
        out["position_summary"] = f"{len(positions)} positions  {n_short} SHORT  {n_long} LONG"
        out["positions_html"] = _build_positions_rows(positions)
        session = data.get("session", {})
        out["session_status"] = "ACTIVE" if session.get("active") else "INACTIVE"
        out["session_color"] = "#c8ff00" if session.get("active") else "#666"
        out["session_strategy"] = session.get("strategy", "---")

    elif template_name == "approaching_card":
        out["approaching_html"] = _build_approaching_rows(data.get("approaching", []))

    elif template_name == "result_card":
        out["paper_badge"] = "PAPER" if data.get("paper") else "LIVE"
        out["paper_color"] = "#ffb000" if data.get("paper") else "#c8ff00"
        pnl = data.get("total_pnl", 0)
        out["pnl_color"] = "#c8ff00" if pnl >= 0 else "#ff3333"
        out["total_pnl"] = f"{pnl:+.2f}"
        out["win_rate"] = f"{data.get('win_rate', 0):.1f}%"
        out["max_drawdown"] = f"{data.get('max_drawdown', 0):.2f}"
        ec = data.get("eval_count", 0)
        rc = data.get("reject_count", 0)
        tc = data.get("trades", 0)
        out["funnel"] = f"{ec} evaluated  {rc} rejected  {tc} trades"

    elif template_name == "equity_card":
        points = data.get("points", [])
        eq = _build_equity_svg(points)
        out.update(eq)

    elif template_name == "radar_card":
        layers = data.get("layers", [])
        radar = _build_radar_svg(layers)
        out.update(radar)

    elif template_name == "gauge_card":
        val = int(data.get("value", 50))
        out["value"] = str(val)
        out["fg_color"] = _fg_color(val)
        out["fg_label"] = _fg_label(val)
        out["zone_arcs"] = _build_gauge_arcs()
        nx, ny = _gauge_needle_xy(val)
        out["needle_x"] = nx
        out["needle_y"] = ny

    elif template_name == "funnel_card":
        ec = data.get("eval_count", 0)
        rc = data.get("reject_count", 0)
        tc = data.get("trades", 0)
        # Bar widths proportional to eval_count (max = 100%)
        out["eval_bar_pct"] = "100"
        out["reject_bar_pct"] = str(int((rc / ec) * 100)) if ec else "0"
        out["trades_bar_pct"] = str(max(3, int((tc / ec) * 100))) if ec else "0"
        rate = (rc / ec * 100) if ec else 0
        out["reject_rate"] = f"{rate:.1f}%"

    elif template_name == "regime_card":
        # Direction color
        dom = data.get("dominant_direction", "MIXED")
        dir_colors = {"SHORT": "#ff3333", "LONG": "#c8ff00", "MIXED": "#ffb000", "QUIET": "#666"}
        out["direction_color"] = dir_colors.get(dom, "#e8e4df")
        # Distribution bar percentages
        total = data.get("total", 0) or 1
        out["short_pct"] = str(int((data.get("trending_short", 0) / total) * 100))
        out["long_pct"] = str(int((data.get("trending_long", 0) / total) * 100))
        out["neutral_pct"] = str(int((data.get("neutral", 0) / total) * 100))
        # Fear & greed color
        fg = data.get("fear_greed", 50)
        out["fg_color"] = _fg_color(int(fg) if isinstance(fg, (int, float)) else 50)
        # Volatility color
        vol = data.get("volatility", "NORMAL")
        vol_colors = {"LOW": "#666", "NORMAL": "#e8e4df", "HIGH": "#ffb000", "EXTREME": "#ff3333"}
        out["vol_color"] = vol_colors.get(vol, "#e8e4df")

    elif template_name == "autopilot_card":
        # Strategy name (uppercase)
        out["strategy"] = data.get("strategy", "---").upper()
        out["reason"] = data.get("reason", "---")
        # Confidence bar
        conf = data.get("confidence", 0)
        conf_pct = int(conf * 100) if isinstance(conf, (int, float)) else 0
        out["confidence_pct"] = str(conf_pct)
        if conf_pct >= 70:
            out["confidence_color"] = "#c8ff00"
        elif conf_pct >= 40:
            out["confidence_color"] = "#ffb000"
        else:
            out["confidence_color"] = "#ff3333"
        # Regime color
        regime = data.get("regime", "MIXED")
        regime_colors = {"SHORT": "#ff3333", "LONG": "#c8ff00", "MIXED": "#ffb000", "QUIET": "#666"}
        out["regime_color"] = regime_colors.get(regime, "#e8e4df")
        out["regime"] = regime
        # Operator WR
        wr = data.get("operator_wr")
        if wr is not None:
            out["operator_wr_display"] = f"{wr}%"
            out["wr_color"] = "#c8ff00" if wr >= 55 else "#ffb000" if wr >= 45 else "#ff3333"
        else:
            out["operator_wr_display"] = "---"
            out["wr_color"] = "#666"
        # Alternatives HTML
        alts = data.get("alternatives", [])
        alt_rows = []
        for alt in alts[:3]:
            name = alt.get("strategy", "?").upper()
            score = alt.get("score", 0)
            reason = alt.get("reason", "")
            alt_rows.append(
                f'<div class="alt-row">'
                f'<span class="alt-name">{name}</span>'
                f'<span class="alt-score">{score}</span>'
                f'</div>'
            )
        out["alternatives_html"] = "\n".join(alt_rows) if alt_rows else '<div class="alt-row"><span class="alt-name">---</span></div>'

    elif template_name == "mode_card":
        active = data.get("active_mode", "comfort")
        out["active_mode"] = active.upper()
        modes_data = data.get("modes", {})
        for mode_name in ("comfort", "sport", "track"):
            prefix = mode_name
            mc = modes_data.get(mode_name, {})
            is_active = mode_name == active
            out[f"{prefix}_border"] = "#c8ff00" if is_active else "#333"
            out[f"{prefix}_bg"] = "#1a1a0a" if is_active else "#111"
            out[f"{prefix}_label_color"] = "#c8ff00" if is_active else "#888"
            push_on = mc.get("push_on", [])
            all_types = ["entry", "exit", "brief", "approaching", "heat_shift",
                         "regime_shift", "eval_candidate", "circuit_breaker"]
            marks = []
            for pt in all_types:
                icon = "\u2713" if pt in push_on else "\u2717"
                color = "#c8ff00" if pt in push_on else "#444"
                label = pt.replace("_", " ")
                marks.append(
                    f'<div class="push-row">'
                    f'<span style="color:{color}">{icon}</span> '
                    f'<span class="push-label">{label}</span></div>'
                )
            out[f"{prefix}_pushes"] = "\n".join(marks)
            out[f"{prefix}_approval"] = "YES" if mc.get("approval_required") else "NO"
            out[f"{prefix}_approval_color"] = "#ffb000" if mc.get("approval_required") else "#666"
            heat_h = mc.get("heat_push_interval_hours")
            out[f"{prefix}_heat_interval"] = f"{heat_h}h" if heat_h else "---"

    elif template_name == "insight_card":
        conf = data.get("confidence", 0)
        out["confidence_pct"] = str(int(conf * 100))
        out["confidence_display"] = f"{conf * 100:.0f}%"

    elif template_name == "backtest_summary_card":
        strategies = data.get("strategies", [])
        out["strategy_rows"] = _build_backtest_summary_rows(strategies)
        out["strategy_count"] = str(len(strategies))

    elif template_name == "backtest_equity_card":
        eq = _build_backtest_equity_svg(data.get("equity_curve", []))
        out.update(eq)
        # Format stats for corner display
        out["total_pnl_pct"] = f"{data.get('total_pnl_pct', 0):+.1f}"
        out["max_drawdown_pct"] = f"{data.get('max_drawdown_pct', 0):.1f}"
        out["win_rate"] = f"{data.get('win_rate', 0):.1f}"
        out["total_trades"] = str(data.get("total_trades", 0))

    elif template_name == "backtest_compare_card":
        a = data.get("a", {})
        b = data.get("b", {})
        cmp = _build_compare_svg(a.get("equity_curve", []), b.get("equity_curve", []))
        out.update(cmp)
        out["a_strategy"] = a.get("strategy", "A")
        out["b_strategy"] = b.get("strategy", "B")
        out["a_pnl"] = f"{a.get('total_pnl_pct', 0):+.1f}%"
        out["b_pnl"] = f"{b.get('total_pnl_pct', 0):+.1f}%"
        out["a_wr"] = f"{a.get('win_rate', 0):.1f}%"
        out["b_wr"] = f"{b.get('win_rate', 0):.1f}%"
        out["a_dd"] = f"{a.get('max_drawdown_pct', 0):.1f}%"
        out["b_dd"] = f"{b.get('max_drawdown_pct', 0):.1f}%"
        out["a_sharpe"] = f"{a.get('sharpe_ratio', 0):.2f}"
        out["b_sharpe"] = f"{b.get('sharpe_ratio', 0):.2f}"

    elif template_name == "score_card":
        perf = data.get("performance", 0)
        disc = data.get("discipline", 0)
        prot = data.get("protection", 0)
        cons = data.get("consistency", 0)
        adapt = data.get("adaptation", 0)
        total = data.get("total", 0)
        cls = data.get("class_name", "novice")
        out["performance"] = f"{perf:.1f}"
        out["discipline"] = f"{disc:.1f}"
        out["protection"] = f"{prot:.1f}"
        out["consistency"] = f"{cons:.1f}"
        out["adaptation"] = f"{adapt:.1f}"
        out["total"] = f"{total:.1f}"
        out["performance_pct"] = str(int(perf))
        out["discipline_pct"] = str(int(disc))
        out["protection_pct"] = str(int(prot))
        out["consistency_pct"] = str(int(cons))
        out["adaptation_pct"] = str(int(adapt))
        out["class_upper"] = cls.upper()
        class_colors = {
            "novice": "#666", "apprentice": "#888",
            "operator": "#ffb000", "veteran": "#c8ff00", "elite": "#ff3333",
        }
        out["class_color"] = class_colors.get(cls, "#888")

    elif template_name == "milestone_card":
        milestones = data.get("milestones", [])
        earned = data.get("earned", 0)
        total = data.get("total", len(milestones))
        out["earned"] = str(earned)
        out["total"] = str(total)
        out["milestones_grid"] = _build_milestones_grid(milestones)
        # Latest earned milestone
        earned_list = [m for m in milestones if m.get("achieved")]
        if earned_list:
            latest = earned_list[-1]
            out["latest_label"] = f"latest: {latest.get('name', '?')} — {latest.get('description', '')}"
        else:
            out["latest_label"] = "no milestones earned yet"

    elif template_name == "streak_card":
        current = data.get("current", 0)
        best = data.get("best", 0)
        stype = data.get("streak_type", "none")
        badge = data.get("badge") or "---"
        sessions_to_next = data.get("sessions_to_next", 0)
        out["current"] = str(current)
        out["best"] = str(best)
        out["streak_type_upper"] = stype.upper()
        out["badge_upper"] = badge.upper() if badge != "---" else "---"
        # Colors
        streak_colors = {"winning": "#c8ff00", "losing": "#ff3333", "none": "#666"}
        out["streak_color"] = streak_colors.get(stype, "#666")
        badge_colors = {
            "bronze": "#cd7f32", "silver": "#c0c0c0",
            "gold": "#ffd700", "diamond": "#b9f2ff", "---": "#555",
        }
        out["badge_color"] = badge_colors.get(badge, "#555")
        # Next badge text
        if sessions_to_next > 0:
            next_badges = {"bronze": 3, "silver": 5, "gold": 10, "diamond": 20}
            # Find what the next badge is
            current_winning = current if stype == "winning" else 0
            next_name = "bronze"
            for threshold_name, threshold_val in [("bronze", 3), ("silver", 5), ("gold", 10), ("diamond", 20)]:
                if current_winning < threshold_val:
                    next_name = threshold_name
                    break
            out["next_badge_text"] = f"{sessions_to_next} more win{'s' if sessions_to_next != 1 else ''} until {next_name}"
        else:
            out["next_badge_text"] = "diamond achieved" if badge == "diamond" else ""

    return out


class CardRenderer:
    """Renders HTML templates to PNG using Playwright."""

    def render(self, template_name: str, data: dict, width: int = 800, height: int = 400) -> bytes:
        """Load template, inject data, screenshot to PNG bytes."""
        tmpl_path = TEMPLATES_DIR / f"{template_name}.html"
        html = tmpl_path.read_text()

        expanded = _preprocess(template_name, data)
        for key, val in expanded.items():
            html = html.replace("{{" + key + "}}", val)

        # Strip any remaining unreplaced placeholders
        html = re.sub(r"\{\{[a-z_]+\}\}", "---", html)

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": width, "height": height})
            page.set_content(html, wait_until="networkidle")
            png = page.screenshot(type="png")
            browser.close()
        return png

    async def render_async(self, template_name: str, data: dict, width: int = 800, height: int = 400) -> bytes:
        """Async-safe render — runs sync Playwright in a thread pool."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(_thread_pool, self.render, template_name, data, width, height)

    def render_to_file(self, template_name: str, data: dict, output_path: str,
                       width: int = 800, height: int = 400) -> str:
        """Render and save to file. Returns path."""
        png = self.render(template_name, data, width, height)
        Path(output_path).write_bytes(png)
        return output_path
