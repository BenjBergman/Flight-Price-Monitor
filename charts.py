"""Static PNG price chart (used in emails and as docs/price-chart.png)."""
from __future__ import annotations

import io
from collections import defaultdict
from datetime import date

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.ticker  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
BLUE = "#2a78d6"
BAND = "#86b6ef"
LIMIT = "#d03b3b"


def daily_best(history: list[dict]) -> list[tuple[date, int, dict]]:
    """Cheapest price per person per run day, across all date pairs checked that day."""
    by_day: dict[str, dict] = {}
    for h in history:
        if not h.get("price_pp"):
            continue
        d = h["run_date"]
        if d not in by_day or h["price_pp"] < by_day[d]["price_pp"]:
            by_day[d] = h
    return [(date.fromisoformat(d), r["price_pp"], r) for d, r in sorted(by_day.items())]


def typical_band(history: list[dict]) -> list[tuple[date, int, int]]:
    lo, hi = defaultdict(list), defaultdict(list)
    for h in history:
        if h.get("typical_low_pp") and h.get("typical_high_pp"):
            lo[h["run_date"]].append(h["typical_low_pp"])
            hi[h["run_date"]].append(h["typical_high_pp"])
    return [(date.fromisoformat(d), round(sum(lo[d]) / len(lo[d])), round(sum(hi[d]) / len(hi[d])))
            for d in sorted(lo)]


def price_chart_png(history: list[dict], cfg: dict, today: date) -> bytes | None:
    pts = daily_best(history)
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})
    fig, ax = plt.subplots(figsize=(8, 3.6), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    band = typical_band(history)
    if len(band) >= 2:
        ax.fill_between([b[0] for b in band], [b[1] for b in band], [b[2] for b in band],
                        color=BAND, alpha=0.18, linewidth=0, zorder=1)
        ax.text(band[0][0], band[0][2], "  Google's typical range", color=INK2, fontsize=8,
                va="bottom", ha="left")

    limit = cfg.get("alerts", {}).get("price_limit_per_person")
    if limit:
        ax.axhline(limit, color=LIMIT, linewidth=1, zorder=2)
        ax.text(xs[0], limit, f"  your limit €{limit}", color=INK2, fontsize=8, va="bottom", ha="left")

    ax.plot(xs, ys, color=BLUE, linewidth=2, solid_capstyle="round", solid_joinstyle="round", zorder=3)
    if len(xs) == 1:
        ax.scatter(xs, ys, s=36, color=BLUE, zorder=4)

    # end point + all-time low
    ax.scatter([xs[-1]], [ys[-1]], s=48, color=BLUE, edgecolor=SURFACE, linewidth=2, zorder=5)
    ax.annotate(f"€{ys[-1]}", (xs[-1], ys[-1]), xytext=(8, 0), textcoords="offset points",
                color=INK, fontsize=9, fontweight="bold", va="center")
    i_low = min(range(len(ys)), key=lambda i: ys[i])
    if i_low != len(ys) - 1:
        ax.scatter([xs[i_low]], [ys[i_low]], s=40, color=BLUE, edgecolor=SURFACE, linewidth=2, zorder=5)
        ax.annotate(f"low €{ys[i_low]}", (xs[i_low], ys[i_low]), xytext=(0, -14),
                    textcoords="offset points", color=INK2, fontsize=8, ha="center")

    lows = ys + ([limit] if limit else []) + [b[1] for b in band]
    highs = ys + [b[2] for b in band]
    pad = max(20, (max(highs) - min(lows)) * 0.12)
    ax.set_ylim(min(lows) - pad, max(highs) + pad)
    if len(xs) > 1:
        span = (xs[-1] - xs[0]).days
        ax.set_xlim(xs[0], xs[-1] + (xs[-1] - xs[0]) * 0.08)
    else:
        span = 0

    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"€{v:,.0f}"))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%-d.%-m."))
    if span > 60:
        ax.xaxis.set_major_locator(mdates.MonthLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    ax.grid(axis="y", color=GRID, linewidth=1)
    ax.set_axisbelow(True)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(AXIS)
    ax.tick_params(colors=MUTED, length=0)
    ax.set_title("Cheapest round trip per person, all date pairs", loc="left", color=INK,
                 fontsize=11, fontweight="bold", pad=10)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=SURFACE)
    plt.close(fig)
    return buf.getvalue()
