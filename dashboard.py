"""Builds docs/index.html — a self-contained dashboard with price graphs."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from charts import daily_best, typical_band

TEMPLATE = Path(__file__).resolve().parent / "dashboard_template.html"
CHARTJS = Path(__file__).resolve().parent / "vendor" / "chart.umd.min.js"  # Chart.js 4.4.1, MIT


def build(cfg, history, latest, alert_log, state, today: date, out_path: Path) -> None:
    adults = cfg["passengers"]["adults"]
    pairs = {}
    for h in history:
        if not h.get("price_pp"):
            continue
        k = f"{h['outbound']}|{h['return']}"
        pairs.setdefault(k, {})
        # keep cheapest per day per pair
        cur = pairs[k].get(h["run_date"])
        if cur is None or h["price_pp"] < cur:
            pairs[k][h["run_date"]] = h["price_pp"]

    pref = cfg["dates"].get("preferred") or {}
    pref_key = f"{pref.get('outbound')}|{pref.get('return')}"
    google_hist = (latest.get(pref_key) or {}).get("google_price_history") or []

    grid = {}
    options = {}
    for k, v in latest.items():
        if k.startswith("_"):
            continue
        opts = v.get("options") or []
        grid[k] = {"pp": opts[0]["price_pp"] if opts else None, "checked_at": v.get("checked_at"),
                   "level": v.get("price_level"), "url": v.get("google_url")}
        options[k] = [{kk: o.get(kk) for kk in ("airlines", "via", "depart", "arrive", "duration_min",
                                                   "max_layover_min", "price_pp", "price_total", "stops")}
                      for o in opts]

    data = {
        "generated": max(today.isoformat(), (state.get("last_run") or "")[:10]),
        "last_run": state.get("last_run"),
        "searches_left": state.get("searches_left"),
        "trip_name": cfg.get("trip_name"),
        "origin": cfg["route"]["origin"], "destination": cfg["route"]["destination"],
        "adults": adults,
        "limit": cfg.get("alerts", {}).get("price_limit_per_person"),
        "outbound_dates": cfg["dates"]["outbound"], "return_dates": cfg["dates"]["return"],
        "preferred": pref_key,
        "daily": [{"d": d.isoformat(), "pp": pp, "o": r["outbound"], "r": r["return"], "a": r["airlines"],
                   "via": r["via"]} for d, pp, r in daily_best(history)],
        "band": [{"d": d.isoformat(), "lo": lo, "hi": hi} for d, lo, hi in typical_band(history)],
        "pairs": pairs,
        "google_hist": google_hist,
        "grid": grid,
        "options": options,
        "best": latest.get("_best"),
        "alerts": alert_log[-15:][::-1],
    }
    html = TEMPLATE.read_text(encoding="utf-8").replace(
        "/*__DATA__*/null", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    html = html.replace("/*__CHARTJS__*/", CHARTJS.read_text(encoding="utf-8"), 1)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
