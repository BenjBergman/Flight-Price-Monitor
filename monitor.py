#!/usr/bin/env python3
"""
Flight price monitor — Helsinki ⇄ Athens, max one stop.

Each run:
  1. checks a rotating subset of the outbound × return date grid on Google Flights (via SerpApi)
  2. stores the cheapest acceptable itinerary per date pair in data/history.csv
  3. emails you when the price is "suitable" (below your limit, new all-time low, or Google says "low")
  4. rebuilds the dashboard (docs/index.html) with price graphs

Usage:
  python monitor.py                 # real run (needs SERPAPI_KEY and SMTP_* env vars)
  python monitor.py --demo          # fake data, no key needed, emails saved to out/emails/
  python monitor.py --dashboard-only
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import date, datetime, timedelta
from itertools import product
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

import charts
import dashboard
import notify
from serp import DemoClient, SerpApiClient, SerpApiError

ROOT = Path(__file__).resolve().parent
TZ = ZoneInfo("Europe/Helsinki")

HISTORY_FIELDS = [
    "checked_at", "run_date", "outbound", "return", "price_total", "price_pp",
    "airlines", "stops", "via", "depart", "arrive", "duration_min", "max_layover_min",
    "price_level", "typical_low_pp", "typical_high_pp", "options_found", "google_url",
]


# ─────────────────────────── helpers ───────────────────────────

def load_config(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def read_json(path: Path, default):
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def read_history(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k in ("price_total", "price_pp", "stops", "duration_min", "max_layover_min",
                  "typical_low_pp", "typical_high_pp", "options_found"):
            r[k] = int(float(r[k])) if r.get(k) not in (None, "") else None
    return rows


def append_history(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with open(path, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HISTORY_FIELDS, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerows(rows)


def combo_key(o: str, r: str) -> str:
    return f"{o}|{r}"


# ─────────────────────── parsing & filters ───────────────────────

def _hhmm(t: str) -> str:
    return t.split(" ")[1] if " " in t else t


def normalise(opt: dict, adults: int) -> dict | None:
    flights = opt.get("flights") or []
    if not flights:
        return None
    layovers = opt.get("layovers") or []
    airlines = []
    for f in flights:
        a = f.get("airline", "?")
        if a not in airlines:
            airlines.append(a)
    price = opt.get("price")
    return {
        "airlines": " + ".join(airlines),
        "airline_list": airlines,
        "stops": len(flights) - 1,
        "via": ", ".join(l.get("id", "?") for l in layovers),
        "via_names": ", ".join(l.get("name", "") for l in layovers),
        "depart": flights[0]["departure_airport"].get("time", ""),
        "arrive": flights[-1]["arrival_airport"].get("time", ""),
        "duration_min": opt.get("total_duration") or sum(f.get("duration", 0) for f in flights),
        "max_layover_min": max((l.get("duration", 0) for l in layovers), default=0),
        "min_layover_min": min((l.get("duration", 0) for l in layovers), default=None),
        "overnight": any(l.get("overnight") for l in layovers),
        "flight_numbers": ", ".join(f.get("flight_number", "") for f in flights),
        "price_total": int(price) if price else None,
        "price_pp": round(price / adults) if price else None,
        "departure_token": opt.get("departure_token"),
    }


def passes_filters(o: dict, cfg: dict, outbound_leg: bool = True) -> bool:
    f = cfg.get("filters", {}) or {}
    route = cfg["route"]
    if o["stops"] > route.get("max_stops", 1):
        return False
    if o["stops"] == 0 and not route.get("include_direct", True):
        return False
    if f.get("max_layover_hours") and o["max_layover_min"] > f["max_layover_hours"] * 60:
        return False
    if f.get("min_layover_minutes") and o["min_layover_min"] is not None \
            and o["min_layover_min"] < f["min_layover_minutes"]:
        return False
    if f.get("no_overnight_layovers") and o["overnight"]:
        return False
    if f.get("max_travel_hours") and o["duration_min"] > f["max_travel_hours"] * 60:
        return False
    if outbound_leg and f.get("earliest_departure") and o["depart"]:
        if _hhmm(o["depart"]) < f["earliest_departure"]:
            return False
    excl = {a.lower() for a in f.get("exclude_airlines", []) or []}
    if excl and any(a.lower() in excl for a in o["airline_list"]):
        return False
    return True


def parse_results(resp: dict, cfg: dict, outbound_leg: bool = True) -> list[dict]:
    adults = cfg["passengers"]["adults"]
    raw = (resp.get("best_flights") or []) + (resp.get("other_flights") or [])
    opts = [n for n in (normalise(o, adults) for o in raw) if n]
    opts = [o for o in opts if passes_filters(o, cfg, outbound_leg)]
    opts.sort(key=lambda o: (o["price_total"] is None, o["price_total"] or 0, o["duration_min"]))
    return opts


# ─────────────────────── run planning ───────────────────────

def all_combos(cfg: dict) -> list[tuple[str, str]]:
    d = cfg["dates"]
    return [(o, r) for o, r in product(d["outbound"], d["return"]) if r > o]


def plan_combos(cfg: dict, state: dict, budget: int) -> list[tuple[str, str]]:
    combos = all_combos(cfg)
    pref = cfg["dates"].get("preferred") or {}
    pref_combo = (pref.get("outbound"), pref.get("return"))
    last = state.get("last_checked", {})
    chosen = [pref_combo] if pref_combo in combos else []
    others = [c for c in combos if c not in chosen]
    others.sort(key=lambda c: last.get(combo_key(*c), ""))  # least recently checked first
    chosen += others[: max(0, budget - len(chosen))]
    return chosen


# ─────────────────────── alert rules ───────────────────────

def evaluate_alert(best: dict, history_before: list[dict], cfg: dict, state: dict) -> list[str]:
    a = cfg.get("alerts", {})
    pp = best["price_pp"]
    reasons = []
    limit = a.get("price_limit_per_person")
    if limit and pp <= limit:
        reasons.append(f"Price is under your limit of €{limit} per person")
    prev_low = min((h["price_pp"] for h in history_before if h["price_pp"]), default=None)
    if prev_low is not None and pp <= prev_low - a.get("new_low_min_drop", 0):
        reasons.append(f"New all-time low (previous lowest €{prev_low} per person)")
    level = best.get("price_level")
    alert_state = state.setdefault("alert", {})
    if a.get("google_says_low") and level == "low":
        reasons.append("Google Flights rates this price as LOW for this route")
    if not reasons:
        return []

    last_pp = alert_state.get("last_pp")
    newly_low = level == "low" and alert_state.get("last_level") != "low"
    if last_pp is not None and pp > last_pp - a.get("realert_min_drop", 0) and not newly_low:
        return []  # already told you about this price level
    return reasons


def rearm_alerts(best_pp: int, state: dict) -> None:
    """If the price climbs 10 % above the last alerted price, allow a new alert later."""
    al = state.setdefault("alert", {})
    if al.get("last_pp") and best_pp > al["last_pp"] * 1.10:
        al["last_pp"] = None


# ─────────────────────────── main run ───────────────────────────

def run(args) -> int:
    cfg = load_config(ROOT / args.config)
    data_dir = ROOT / ("demo-data" if args.demo else "data")
    docs_dir = ROOT / ("demo-docs" if args.demo else "docs")
    hist_path, state_path, latest_path = data_dir / "history.csv", data_dir / "state.json", data_dir / "latest.json"
    alerts_path = data_dir / "alerts.json"

    now = datetime.now(TZ)
    today = date.fromisoformat(args.today) if args.today else now.date()
    if args.today:
        now = datetime.combine(today, now.time(), TZ)

    state = read_json(state_path, {"last_checked": {}, "alert": {}})
    latest = read_json(latest_path, {})
    alert_log = read_json(alerts_path, [])
    history = read_history(hist_path)

    if args.dashboard_only:
        return build_outputs(cfg, history, latest, alert_log, state, docs_dir, today)

    stop_after = cfg["search"].get("stop_after")
    if stop_after and today > date.fromisoformat(stop_after):
        print(f"Past stop date {stop_after} — nothing to do. You can disable the workflow now.")
        return 0

    try:
        client = DemoClient(cfg, today) if args.demo else SerpApiClient(os.environ.get("SERPAPI_KEY", ""), cfg)
    except SerpApiError as e:
        print(f"ERROR: {e}. Add your SerpApi key as the repository secret SERPAPI_KEY "
              f"(or run with --demo to try without a key).", file=sys.stderr)
        return 1

    budget = int(cfg["search"].get("searches_per_run", 6))
    left = client.searches_left()
    min_left = int(cfg["search"].get("min_searches_left", 0))
    if left is not None:
        print(f"SerpApi searches left this month: {left}")
        state["searches_left"] = left
        if left < min_left:
            print("Quota nearly used up — skipping this run.")
            state.setdefault("warnings", []).append(f"{today}: skipped, only {left} searches left")
            write_json(state_path, state)
            return 0
        budget = min(budget, left - min_left)

    reserve_return = 1 if cfg["search"].get("fetch_return_details") and budget >= 2 else 0
    combos = plan_combos(cfg, state, budget - reserve_return)
    print(f"Checking {len(combos)} date pairs: " + ", ".join(f"{o}→{r}" for o, r in combos))

    fresh, errors = [], []
    adults = cfg["passengers"]["adults"]
    for o, r in combos:
        try:
            resp = client.search(o, r)
        except SerpApiError as e:
            print(f"  {o}→{r}: ERROR {e}")
            errors.append(f"{o}→{r}: {e}")
            continue
        state["last_checked"][combo_key(o, r)] = now.isoformat(timespec="minutes")
        opts = [x for x in parse_results(resp, cfg) if x["price_total"]]
        pi = resp.get("price_insights") or {}
        tr = pi.get("typical_price_range") or [None, None]
        entry = {
            "outbound": o, "return": r, "checked_at": now.isoformat(timespec="minutes"),
            "price_level": pi.get("price_level"),
            "typical_low_pp": round(tr[0] / adults) if tr[0] else None,
            "typical_high_pp": round(tr[1] / adults) if tr[1] else None,
            "google_url": (resp.get("search_metadata") or {}).get("google_flights_url", ""),
            "options": opts[:6],
            "options_found": len(opts),
        }
        if pi.get("price_history"):
            entry["google_price_history"] = [[p[0], round(p[1] / adults)] for p in pi["price_history"] if p[1]]
        latest[combo_key(o, r)] = entry
        if opts:
            b = opts[0]
            print(f"  {o}→{r}: €{b['price_pp']} pp (€{b['price_total']} total) — {b['airlines']}"
                  f"{' via ' + b['via'] if b['via'] else ' direct'}  [Google: {entry['price_level']}]")
            fresh.append({**entry, "best": b})
        else:
            print(f"  {o}→{r}: no itineraries matching your filters")

    if not fresh:
        write_json(state_path, state)
        write_json(latest_path, latest)
        build_outputs(cfg, history, latest, alert_log, state, docs_dir, today)
        if errors and len(errors) == len(combos):
            print("All searches failed.", file=sys.stderr)
            return 1
        return 0

    # cheapest fresh result of this run
    top = min(fresh, key=lambda e: e["best"]["price_total"])
    best = {**top["best"], "outbound": top["outbound"], "return": top["return"],
            "price_level": top["price_level"], "typical_low_pp": top["typical_low_pp"],
            "typical_high_pp": top["typical_high_pp"], "google_url": top["google_url"]}

    # details of the return leg for the cheapest option
    if reserve_return and best.get("departure_token"):
        try:
            rresp = client.return_flights(best["outbound"], best["return"], best["departure_token"])
            rets = parse_results(rresp, cfg, outbound_leg=False)
            if rets:
                rb = rets[0]
                best["return_leg"] = {k: rb[k] for k in ("airlines", "via", "depart", "arrive",
                                                         "duration_min", "flight_numbers", "stops")}
                if rb["price_total"] and rb["price_total"] > best["price_total"]:
                    # the cheapest *acceptable* return costs more than the headline price
                    best["price_total"], best["price_pp"] = rb["price_total"], round(rb["price_total"] / adults)
            else:
                best["return_note"] = "No return flight matching your filters for this outbound."
        except SerpApiError as e:
            print(f"  return details: ERROR {e}")
    latest["_best"] = best

    history_before = list(history)
    new_rows = []
    for e in fresh:
        b = e["best"] if e is not top else best
        new_rows.append({
            "checked_at": e["checked_at"], "run_date": today.isoformat(),
            "outbound": e["outbound"], "return": e["return"],
            "price_total": b["price_total"], "price_pp": b["price_pp"],
            "airlines": b["airlines"], "stops": b["stops"], "via": b["via"],
            "depart": b["depart"], "arrive": b["arrive"], "duration_min": b["duration_min"],
            "max_layover_min": b["max_layover_min"], "price_level": e["price_level"],
            "typical_low_pp": e["typical_low_pp"], "typical_high_pp": e["typical_high_pp"],
            "options_found": e["options_found"], "google_url": e["google_url"],
        })
    append_history(hist_path, new_rows)
    history = read_history(hist_path)

    # ── notifications ──
    first_run = not state.get("started")
    rearm_alerts(best["price_pp"], state)
    reasons = evaluate_alert(best, history_before, cfg, state)
    chart_png = charts.price_chart_png(history, cfg, today)
    ctx = notify.Context(cfg=cfg, best=best, history=history, latest=latest, today=today,
                         chart_png=chart_png, errors=errors, state=state)
    outbox = ROOT / "out" / "emails" if args.demo or args.dry_run else None

    if reasons:
        notify.send_alert(ctx, reasons, outbox)
        state["alert"].update({"last_pp": best["price_pp"], "last_ts": now.isoformat(timespec="minutes")})
        alert_log.append({"date": today.isoformat(), "price_pp": best["price_pp"],
                          "outbound": best["outbound"], "return": best["return"],
                          "airlines": best["airlines"], "reasons": reasons})
    elif first_run and cfg["alerts"].get("send_start_email", True):
        notify.send_start(ctx, outbox)

    weekday = (cfg["alerts"].get("weekly_summary") or "").lower()
    if weekday and today.strftime("%A").lower() == weekday and state.get("weekly_last") != today.isoformat() \
            and not first_run:
        if not reasons:  # an alert email today already carries the same overview
            notify.send_weekly(ctx, outbox)
        state["weekly_last"] = today.isoformat()

    state["alert"]["last_level"] = best.get("price_level")
    state["started"] = state.get("started") or today.isoformat()
    state["last_run"] = now.isoformat(timespec="minutes")
    state["searches_used_last_run"] = client.searches_used
    write_json(state_path, state)
    write_json(latest_path, latest)
    write_json(alerts_path, alert_log)
    build_outputs(cfg, history, latest, alert_log, state, docs_dir, today, chart_png)
    print(f"Done. Cheapest now: €{best['price_pp']} pp ({best['outbound']}→{best['return']}). "
          f"Searches used: {client.searches_used}. Alert sent: {'yes' if reasons else 'no'}")
    return 0


def build_outputs(cfg, history, latest, alert_log, state, docs_dir, today, chart_png=None) -> int:
    docs_dir.mkdir(parents=True, exist_ok=True)
    png = chart_png or charts.price_chart_png(history, cfg, today)
    if png:
        (docs_dir / "price-chart.png").write_bytes(png)
    dashboard.build(cfg, history, latest, alert_log, state, today, docs_dir / "index.html")
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--demo", action="store_true", help="use fake flight data (no API key needed)")
    p.add_argument("--dry-run", action="store_true", help="real search, but save emails to out/emails instead of sending")
    p.add_argument("--today", help="pretend today is YYYY-MM-DD (for testing)")
    p.add_argument("--dashboard-only", action="store_true", help="just rebuild the dashboard from stored data")
    sys.exit(run(p.parse_args()))


if __name__ == "__main__":
    main()
