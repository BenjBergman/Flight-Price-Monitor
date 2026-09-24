"""Flight search via SerpApi's Google Flights engine (plus an offline demo client)."""
from __future__ import annotations

import hashlib
import math
import random
from datetime import date, datetime, timedelta

import requests

SERPAPI_URL = "https://serpapi.com/search.json"
ACCOUNT_URL = "https://serpapi.com/account.json"


class SerpApiError(RuntimeError):
    pass


def _stops_param(max_stops: int) -> int:
    # SerpApi: 0 any, 1 nonstop only, 2 one stop or fewer, 3 two stops or fewer
    return {0: 1, 1: 2, 2: 3}.get(int(max_stops), 0)


class SerpApiClient:
    def __init__(self, api_key: str, cfg: dict):
        if not api_key:
            raise SerpApiError("SERPAPI_KEY is not set")
        self.api_key = api_key
        self.cfg = cfg
        self.searches_used = 0

    def _base_params(self, outbound: str, ret: str) -> dict:
        c = self.cfg
        return {
            "engine": "google_flights",
            "type": 1,  # round trip
            "departure_id": c["route"]["origin"],
            "arrival_id": c["route"]["destination"],
            "outbound_date": outbound,
            "return_date": ret,
            "adults": c["passengers"]["adults"],
            "stops": _stops_param(c["route"]["max_stops"]),
            "currency": c.get("currency", "EUR"),
            "gl": c.get("market", "fi"),
            "hl": c.get("language", "en"),
            "api_key": self.api_key,
        }

    def _get(self, params: dict) -> dict:
        r = requests.get(SERPAPI_URL, params=params, timeout=90)
        self.searches_used += 1
        try:
            data = r.json()
        except ValueError:
            raise SerpApiError(f"HTTP {r.status_code}: non-JSON response")
        if r.status_code != 200 or data.get("error"):
            # "Google hasn't returned any results" is not fatal — just no flights
            err = data.get("error", f"HTTP {r.status_code}")
            if "hasn't returned any results" in str(err):
                return {"best_flights": [], "other_flights": []}
            raise SerpApiError(str(err))
        return data

    def search(self, outbound: str, ret: str) -> dict:
        return self._get(self._base_params(outbound, ret))

    def return_flights(self, outbound: str, ret: str, departure_token: str) -> dict:
        p = self._base_params(outbound, ret)
        p["departure_token"] = departure_token
        return self._get(p)

    def searches_left(self) -> int | None:
        """Free call — does not count against the quota."""
        try:
            r = requests.get(ACCOUNT_URL, params={"api_key": self.api_key}, timeout=30)
            d = r.json()
            for k in ("total_searches_left", "plan_searches_left"):
                if k in d:
                    return int(d[k])
        except Exception:
            pass
        return None


# ─────────────────────────────────────────────────────────────
#  Demo client: realistic fake results so the whole pipeline
#  (history, alerts, email, dashboard) can be tested without a key.
# ─────────────────────────────────────────────────────────────

_ROUTES = [
    # airline, via, (out dep, out leg1 min, layover min, leg2 min), price factor, direct?
    ("Finnair", None, ("07:25", 215, 0, 0), 1.18, True),
    ("Aegean", None, ("13:40", 210, 0, 0), 1.10, True),
    ("Lufthansa", ("MUC", "Munich Airport"), ("06:20", 150, 85, 140), 1.00, False),
    ("SAS", ("CPH", "Copenhagen Airport"), ("08:05", 100, 70, 200), 0.96, False),
    ("airBaltic", ("RIX", "Riga International Airport"), ("10:15", 60, 95, 190), 0.88, False),
    ("LOT", ("WAW", "Warsaw Chopin Airport"), ("15:30", 110, 120, 160), 0.92, False),
    ("Austrian", ("VIE", "Vienna International Airport"), ("06:45", 150, 60, 120), 1.03, False),
]


class DemoClient:
    def __init__(self, cfg: dict, today: date):
        self.cfg = cfg
        self.today = today
        self.searches_used = 0

    def _rng(self, *key) -> random.Random:
        h = hashlib.sha256("|".join(map(str, key)).encode()).hexdigest()
        return random.Random(int(h[:12], 16))

    def _pp_base(self, outbound: str, ret: str) -> float:
        # slow downward drift with a mid-period dip and daily noise
        t = (self.today - date(2026, 9, 24)).days
        drift = 470 - 2.2 * t + 28 * math.sin(t / 4.5)
        combo_adj = self._rng("combo", outbound, ret).uniform(-35, 40)
        noise = self._rng("day", self.today, outbound, ret).uniform(-14, 14)
        return max(240.0, drift + combo_adj + noise)

    def search(self, outbound: str, ret: str) -> dict:
        self.searches_used += 1
        adults = self.cfg["passengers"]["adults"]
        base = self._pp_base(outbound, ret)
        rng = self._rng("opts", self.today, outbound, ret)
        opts = []
        for airline, via, (dep, l1, lay, l2), factor, direct in _ROUTES:
            if direct and not self.cfg["route"].get("include_direct", True):
                continue
            pp = base * factor * rng.uniform(0.97, 1.05)
            d0 = datetime.fromisoformat(f"{outbound} {dep}")
            if direct:
                a0 = d0 + timedelta(minutes=l1 + 60)  # +1h timezone
                flights = [_leg("HEL", "Helsinki Airport", "ATH", "Athens International Airport",
                                d0, a0, l1, airline)]
                layovers = []
                total = l1
            else:
                code, name = via
                a1 = d0 + timedelta(minutes=l1 - 60)
                d1 = a1 + timedelta(minutes=lay)
                a2 = d1 + timedelta(minutes=l2 + 60)
                flights = [
                    _leg("HEL", "Helsinki Airport", code, name, d0, a1, l1, airline),
                    _leg(code, name, "ATH", "Athens International Airport", d1, a2, l2, airline),
                ]
                layovers = [{"duration": lay, "name": name, "id": code}]
                total = l1 + lay + l2
            opts.append({
                "flights": flights, "layovers": layovers, "total_duration": total,
                "price": int(round(pp * adults)), "type": "Round trip",
                "departure_token": f"demo:{airline}:{outbound}:{ret}",
            })
        opts.sort(key=lambda o: o["price"])
        lo, hi = 330 * adults, 460 * adults
        cheapest = opts[0]["price"]
        level = "low" if cheapest < lo else "high" if cheapest > hi else "typical"
        hist = []
        for i in range(60, 0, -3):
            d = datetime.combine(self.today - timedelta(days=i), datetime.min.time())
            hist.append([int(d.timestamp()), int(cheapest * (1 + 0.004 * i) * self._rng("h", i).uniform(.96, 1.04))])
        return {
            "search_metadata": {"google_flights_url": "https://www.google.com/travel/flights?q=Flights%20from%20HEL%20to%20ATH"},
            "best_flights": opts[:3], "other_flights": opts[3:],
            "price_insights": {"lowest_price": cheapest, "price_level": level,
                               "typical_price_range": [lo, hi], "price_history": hist},
        }

    def return_flights(self, outbound: str, ret: str, departure_token: str) -> dict:
        self.searches_used += 1
        airline = departure_token.split(":")[1]
        route = next(r for r in _ROUTES if r[0] == airline)
        _, via, (_, l1, lay, l2), _, direct = route
        d0 = datetime.fromisoformat(f"{ret} 16:10")
        if direct:
            flights = [_leg("ATH", "Athens International Airport", "HEL", "Helsinki Airport",
                            d0, d0 + timedelta(minutes=l1 + 60), l1, airline)]
            layovers, total = [], l1
        else:
            code, name = via
            a1 = d0 + timedelta(minutes=l2 - 60)
            d1 = a1 + timedelta(minutes=lay)
            flights = [_leg("ATH", "Athens International Airport", code, name, d0, a1, l2, airline),
                       _leg(code, name, "HEL", "Helsinki Airport", d1, d1 + timedelta(minutes=l1 + 60), l1, airline)]
            layovers, total = [{"duration": lay, "name": name, "id": code}], l1 + lay + l2
        return {"best_flights": [{"flights": flights, "layovers": layovers,
                                  "total_duration": total, "price": None}], "other_flights": []}

    def searches_left(self) -> int:
        return 240


def _leg(fr, frn, to, ton, dep, arr, dur, airline):
    return {
        "departure_airport": {"id": fr, "name": frn, "time": dep.strftime("%Y-%m-%d %H:%M")},
        "arrival_airport": {"id": to, "name": ton, "time": arr.strftime("%Y-%m-%d %H:%M")},
        "duration": dur, "airline": airline, "flight_number": f"{airline[:2].upper()} {random.randint(100, 999)}",
    }
