"""Card renderer — turns HTML templates into PNG via Playwright."""

import asyncio
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


def _build_approaching_rows(approaching: list) -> str:
    """Build rows for approaching coins."""
    rows = []
    for a in approaching[:5]:
        cons = a.get("consensus", 0)
        thresh = a.get("threshold", 5)
        pct = int((cons / thresh) * 100) if thresh else 0
        dir_color = "#ff3333" if a.get("direction") == "SHORT" else "#c8ff00"
        bn = a.get("bottleneck", "---")
        rows.append(
            f'<div class="ap-row">'
            f'<div class="ap-top">'
            f'<span class="ap-coin">{a.get("coin", "?")}</span>'
            f'<span class="ap-dir" style="color:{dir_color}">{a.get("direction", "---")}</span>'
            f'<span class="ap-frac">{cons}/{thresh}</span>'
            f'</div>'
            f'<div class="ap-bar"><div class="ap-fill" style="width:{pct}%"></div></div>'
            f'<div class="ap-bn">bottleneck: <span style="color:#ffb000">{bn}</span></div>'
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
