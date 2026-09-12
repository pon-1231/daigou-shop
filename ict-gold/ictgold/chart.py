"""Render a trade journal (backtest --trades output) as candlestick charts.

Pure stdlib, same as the rest of this package: every candle is drawn as an
inline SVG string built by hand, no charting library, no browser dependency
beyond opening a static HTML file. One card per trade - candles, entry/stop/
target lines, entry and exit markers - so you can actually SEE what the pipe
did, not just read the numbers.

This is deliberately a static, after-the-fact renderer (it reads a finished
trade journal), not a live dashboard. See docs/ROADMAP.md for why a live
version comes later, not first.
"""

from __future__ import annotations

import bisect
import html
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .core import Candle

UP = "#26a69a"
DOWN = "#ef5350"
STOP_COLOR = "#e67e22"
TARGET_COLOR = "#3498db"
ENTRY_COLOR = "#7f8c8d"


@dataclass
class ChartTrade:
    """The subset of a trade-journal record this renderer actually needs.

    Deliberately decoupled from ictgold.backtest.Trade: this reads whatever
    JSON `backtest --trades` wrote, which may come from a different version
    of the engine than the one rendering it.
    """

    setup: str
    side: str
    entry_ts: datetime
    exit_ts: datetime | None
    entry: float
    stop: float
    target: float
    exit_price: float | None
    r: float
    exit_reason: str
    score: float
    killzone: str

    @staticmethod
    def from_dict(d: dict) -> "ChartTrade":
        return ChartTrade(
            setup=d["setup"], side=d["side"],
            entry_ts=datetime.fromisoformat(d["entry_ts"]),
            exit_ts=datetime.fromisoformat(d["exit_ts"]) if d.get("exit_ts") else None,
            entry=d["entry"], stop=d["stop"], target=d["target"],
            exit_price=d.get("exit"), r=d.get("r", 0.0),
            exit_reason=d.get("exit_reason", "?"), score=d.get("score", 0.0),
            killzone=d.get("killzone", "?"),
        )


def _find_index(candles: list[Candle], ts: datetime) -> int | None:
    """Index of the candle at or nearest-before `ts`. None if ts is before
    the first candle."""
    times = [c.ts for c in candles]
    i = bisect.bisect_right(times, ts) - 1
    return i if i >= 0 else None


def _fmt(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%d %H:%M")


def svg_for_trade(candles: list[Candle], trade: ChartTrade, *,
                   lookback: int = 15, lookahead: int = 8,
                   candle_w: int = 7, gap: int = 3, height: int = 260) -> str | None:
    entry_idx = _find_index(candles, trade.entry_ts)
    if entry_idx is None:
        return None
    exit_idx = _find_index(candles, trade.exit_ts) if trade.exit_ts else entry_idx

    start = max(0, entry_idx - lookback)
    end = min(len(candles) - 1, exit_idx + lookahead)
    window = candles[start:end + 1]
    if not window:
        return None
    e_i, x_i = entry_idx - start, exit_idx - start

    prices = [p for c in window for p in (c.high, c.low)]
    prices += [trade.entry, trade.stop, trade.target]
    if trade.exit_price is not None:
        prices.append(trade.exit_price)
    lo, hi = min(prices), max(prices)
    pad = (hi - lo) * 0.08 or 1.0
    lo, hi = lo - pad, hi + pad

    margin_l, margin_r, margin_t, margin_b = 46, 95, 10, 20
    step = candle_w + gap
    width = margin_l + len(window) * step + margin_r
    chart_h = height - margin_t - margin_b

    def y(price: float) -> float:
        return margin_t + (hi - price) / (hi - lo) * chart_h

    def x(i: int) -> float:
        return margin_l + i * step + step / 2

    parts: list[str] = []
    parts.append(f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
                 f'xmlns="http://www.w3.org/2000/svg" font-family="monospace" font-size="9">')

    # Price gridlines / axis labels (4 ticks).
    for k in range(4):
        price = lo + (hi - lo) * k / 3
        yy = y(price)
        parts.append(f'<line x1="{margin_l}" y1="{yy:.1f}" x2="{width - 8}" y2="{yy:.1f}" '
                     f'stroke="#888" stroke-opacity="0.15" stroke-width="1"/>')
        parts.append(f'<text x="2" y="{yy + 3:.1f}" fill="#888">{price:.2f}</text>')

    # Candles.
    for i, c in enumerate(window):
        cx = x(i)
        color = UP if c.is_up else DOWN
        parts.append(f'<line x1="{cx:.1f}" y1="{y(c.high):.1f}" x2="{cx:.1f}" y2="{y(c.low):.1f}" '
                     f'stroke="{color}" stroke-width="1"/>')
        top, bot = y(max(c.open, c.close)), y(min(c.open, c.close))
        body_h = max(1.0, bot - top)
        parts.append(f'<rect x="{cx - candle_w/2:.1f}" y="{top:.1f}" width="{candle_w}" '
                     f'height="{body_h:.1f}" fill="{color}"/>')

    # Entry / stop / target lines, spanning from the entry bar to a little
    # past the exit bar - not the full chart width, so the lookahead bars
    # after exit stay readable instead of getting striped over.
    x0, x1 = x(e_i) - candle_w, x(max(e_i, x_i)) + candle_w * 5
    for price, color, label in ((trade.entry, ENTRY_COLOR, "entry"),
                                (trade.stop, STOP_COLOR, "stop"),
                                (trade.target, TARGET_COLOR, "target")):
        yy = y(price)
        parts.append(f'<line x1="{x0:.1f}" y1="{yy:.1f}" x2="{x1:.1f}" y2="{yy:.1f}" '
                     f'stroke="{color}" stroke-width="1" stroke-dasharray="4,3"/>')
        parts.append(f'<text x="{x1 + 3:.1f}" y="{yy + 3:.1f}" fill="{color}">'
                     f'{label} {price:.2f}</text>')

    # Entry marker.
    ex, ey = x(e_i), y(trade.entry)
    up = trade.side == "LONG"
    tri = f'{ex-5:.1f},{ey + (8 if up else -8):.1f} {ex+5:.1f},{ey + (8 if up else -8):.1f} {ex:.1f},{ey:.1f}'
    parts.append(f'<polygon points="{tri}" fill="{ENTRY_COLOR}" stroke="white" stroke-width="1.5"/>')

    # Exit marker.
    if trade.exit_price is not None:
        exx, exy = x(x_i), y(trade.exit_price)
        win = trade.r > 0.02
        marker_color = UP if win else DOWN
        parts.append(f'<circle cx="{exx:.1f}" cy="{exy:.1f}" r="4" fill="none" '
                     f'stroke="{marker_color}" stroke-width="2"/>')

    parts.append('</svg>')
    return "".join(parts)


def render_html(candles: list[Candle], trades: list[dict], out_path: str | Path, *,
                 lookback: int = 15, lookahead: int = 8, limit: int | None = None,
                 setup: str | None = None, outcome: str | None = None) -> int:
    """Write one self-contained HTML file with a chart card per trade.

    Returns the number of trade cards actually rendered. `setup` and
    `outcome` ('win'|'loss'|'flat') filter before `limit` is applied, so
    --limit means "the most recent N matching trades", not "the first N in
    the file regardless of filter".
    """
    chosen = trades
    if setup:
        chosen = [t for t in chosen if t.get("setup") == setup]
    if outcome:
        def bucket(t: dict) -> str:
            r = t.get("r", 0.0)
            return "win" if r > 0.02 else ("loss" if r < -0.02 else "flat")
        chosen = [t for t in chosen if bucket(t) == outcome]
    if limit:
        chosen = chosen[-limit:]

    cards: list[str] = []
    rendered = 0
    for d in chosen:
        try:
            t = ChartTrade.from_dict(d)
        except (KeyError, ValueError):
            continue
        svg = svg_for_trade(candles, t, lookback=lookback, lookahead=lookahead)
        if svg is None:
            continue
        rendered += 1
        win = t.r > 0.02
        badge = "win" if win else ("loss" if t.r < -0.02 else "flat")
        badge_color = UP if badge == "win" else (DOWN if badge == "loss" else "#888")
        exit_str = _fmt(t.exit_ts) if t.exit_ts else "open"
        cards.append(f'''
<div class="card" data-setup="{html.escape(t.setup)}" data-outcome="{badge}">
  <div class="hdr">
    <span class="tag side-{t.side.lower()}">{t.side}</span>
    <span class="tag">{html.escape(t.setup)}</span>
    <span class="tag">{html.escape(t.killzone)}</span>
    <span class="r" style="color:{badge_color}">{t.r:+.2f}R</span>
    <span class="reason">{html.escape(t.exit_reason)}</span>
    <span class="score">score {t.score:.2f}</span>
  </div>
  <div class="ts">{_fmt(t.entry_ts)} &rarr; {exit_str}</div>
  <div class="chartwrap">{svg}</div>
</div>''')

    total = len(trades)
    wins = sum(1 for t in trades if t.get("r", 0) > 0.02)
    losses = sum(1 for t in trades if t.get("r", 0) < -0.02)
    setups = sorted({t.get("setup", "?") for t in trades})
    filter_buttons = "".join(
        f'<button class="filt" data-setup="{html.escape(s)}">{html.escape(s)}</button>' for s in setups)

    doc = f'''<!doctype html>
<html><head><meta charset="utf-8"><title>ictgold trade journal</title>
<style>
  body {{ font-family: -apple-system, sans-serif; background:#fafafa; color:#222; margin:0; padding:16px; }}
  h1 {{ font-size:16px; margin:0 0 4px; }}
  .summary {{ color:#555; font-size:13px; margin-bottom:12px; }}
  .filters {{ margin-bottom:14px; }}
  .filt, .outc {{ font-size:12px; padding:4px 9px; margin:0 4px 4px 0; border:1px solid #ccc;
                  border-radius:12px; background:#fff; cursor:pointer; }}
  .filt.active, .outc.active {{ background:#333; color:#fff; border-color:#333; }}
  .card {{ background:#fff; border:1px solid #e0e0e0; border-radius:8px; padding:10px 12px;
           margin-bottom:10px; }}
  .hdr {{ display:flex; align-items:center; gap:8px; font-size:12px; flex-wrap:wrap; }}
  .tag {{ background:#eee; border-radius:4px; padding:2px 6px; }}
  .side-long {{ background:#d7f4ee; }}
  .side-short {{ background:#fbe0df; }}
  .r {{ font-weight:700; margin-left:auto; }}
  .reason, .score {{ color:#888; }}
  .ts {{ color:#888; font-size:11px; margin:2px 0 6px; }}
  .chartwrap {{ overflow-x:auto; }}
  [hidden] {{ display:none !important; }}
</style></head>
<body>
<h1>ictgold trade journal</h1>
<div class="summary">{total} trades total &middot; {wins} wins &middot; {losses} losses &middot;
  showing {rendered}{f" (filtered/limited from {total})" if rendered != total else ""}</div>
<div class="filters">
  <span class="outc active" data-outcome="all">all</span>
  <span class="outc" data-outcome="win">wins</span>
  <span class="outc" data-outcome="loss">losses</span>
  <span class="outc" data-outcome="flat">flat</span>
  &nbsp;|&nbsp;
  {filter_buttons}
</div>
<div id="cards">
{"".join(cards)}
</div>
<script>
  // Vanilla JS only - no build step, no CDN, consistent with the rest of
  // this project. Filters are purely client-side; nothing to fetch.
  var activeSetup = null, activeOutcome = "all";
  function apply() {{
    document.querySelectorAll(".card").forEach(function(c) {{
      var okSetup = !activeSetup || c.dataset.setup === activeSetup;
      var okOutcome = activeOutcome === "all" || c.dataset.outcome === activeOutcome;
      c.hidden = !(okSetup && okOutcome);
    }});
  }}
  document.querySelectorAll(".filt").forEach(function(b) {{
    b.addEventListener("click", function() {{
      activeSetup = (activeSetup === b.dataset.setup) ? null : b.dataset.setup;
      document.querySelectorAll(".filt").forEach(function(x) {{ x.classList.remove("active"); }});
      if (activeSetup) b.classList.add("active");
      apply();
    }});
  }});
  document.querySelectorAll(".outc").forEach(function(b) {{
    b.addEventListener("click", function() {{
      activeOutcome = b.dataset.outcome;
      document.querySelectorAll(".outc").forEach(function(x) {{ x.classList.remove("active"); }});
      b.classList.add("active");
      apply();
    }});
  }});
</script>
</body></html>'''

    Path(out_path).write_text(doc, encoding="utf-8")
    return rendered
