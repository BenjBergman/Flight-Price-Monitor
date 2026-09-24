"""Email notifications: price alerts, first-run confirmation, weekly summary."""
from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import date, datetime
from email.message import EmailMessage
from html import escape
from pathlib import Path

from charts import daily_best

BLUE = "#2a78d6"
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#898781"
LINE = "#e1e0d9"
GOOD = "#006300"
LEVEL_TXT = {"low": ("Low", GOOD), "typical": ("Typical", INK2), "high": ("High", "#b3261e")}


@dataclass
class Context:
    cfg: dict
    best: dict
    history: list
    latest: dict
    today: date
    chart_png: bytes | None
    errors: list = field(default_factory=list)
    state: dict = field(default_factory=dict)


# ───────────────────────── formatting ─────────────────────────

def dm(d: str) -> str:
    y, m, dd = d[:10].split("-")
    return f"{int(dd)}.{int(m)}."


def weekday_dm(d: str) -> str:
    return datetime.fromisoformat(d[:10]).strftime("%a ") + dm(d)


def hm(t: str) -> str:
    return t.split(" ")[1] if t and " " in t else (t or "")


def dur(mins) -> str:
    if not mins:
        return ""
    return f"{mins // 60} h {mins % 60:02d} min"


def eur(v) -> str:
    return f"€{v:,.0f}".replace(",", " ") if v is not None else "–"


def leg_html(title: str, date_s: str, leg: dict) -> str:
    via = f"1 stop via {escape(leg['via'])}" if leg.get("via") else "Direct"
    return f"""
    <tr><td style="padding:10px 0;border-top:1px solid {LINE}">
      <div style="font-size:12px;color:{MUTED};text-transform:uppercase;letter-spacing:.04em">{title} · {weekday_dm(date_s)}</div>
      <div style="font-size:16px;color:{INK};margin-top:2px"><b>{hm(leg.get('depart'))} → {hm(leg.get('arrive'))}</b>
        &nbsp;<span style="color:{INK2}">{escape(leg.get('airlines', ''))}</span></div>
      <div style="font-size:13px;color:{INK2}">{via} · {dur(leg.get('duration_min'))}
        {('· ' + escape(leg['flight_numbers'])) if leg.get('flight_numbers') else ''}</div>
    </td></tr>"""


def grid_html(ctx: Context) -> str:
    d = ctx.cfg["dates"]
    cells = {}
    for k, v in ctx.latest.items():
        if k.startswith("_") or not v.get("options"):
            continue
        cells[k] = v["options"][0]["price_pp"]
    if not cells:
        return ""
    lo = min(cells.values())
    head = "".join(f'<th style="padding:6px 8px;font-weight:600;color:{INK2};font-size:12px">back {dm(r)}</th>'
                   for r in d["return"])
    rows = ""
    for o in d["outbound"]:
        tds = ""
        for r in d["return"]:
            v = cells.get(f"{o}|{r}")
            style = f"padding:6px 8px;text-align:right;font-size:14px;border-top:1px solid {LINE}"
            if v == lo:
                tds += f'<td style="{style};color:{GOOD};font-weight:700">{eur(v)} ★</td>'
            else:
                tds += f'<td style="{style};color:{INK}">{eur(v)}</td>'
        rows += (f'<tr><td style="padding:6px 8px;color:{INK2};font-size:12px;font-weight:600;'
                 f'border-top:1px solid {LINE}">out {dm(o)}</td>{tds}</tr>')
    return f"""
    <h3 style="font-size:14px;color:{INK};margin:24px 0 6px">Price per person by date (latest check)</h3>
    <table style="border-collapse:collapse;width:100%"><tr><th></th>{head}</tr>{rows}</table>"""


def button(url: str, label: str, primary=True) -> str:
    if not url:
        return ""
    bg, fg = (BLUE, "#ffffff") if primary else ("#ffffff", BLUE)
    return (f'<a href="{escape(url)}" style="display:inline-block;padding:10px 16px;margin:4px 8px 4px 0;'
            f'border-radius:6px;background:{bg};color:{fg};border:1px solid {BLUE};text-decoration:none;'
            f'font-weight:600;font-size:14px">{label}</a>')


def body_html(ctx: Context, heading: str, intro_html: str) -> str:
    b, cfg = ctx.best, ctx.cfg
    adults = cfg["passengers"]["adults"]
    lvl_txt, lvl_col = LEVEL_TXT.get(b.get("price_level") or "", ("n/a", MUTED))
    typical = (f"{eur(b['typical_low_pp'])}–{eur(b['typical_high_pp'])} pp"
               if b.get("typical_low_pp") else "not available")
    ret = leg_html("Return", b["return"], b["return_leg"]) if b.get("return_leg") else (
        f'<tr><td style="padding:10px 0;border-top:1px solid {LINE};color:{INK2};font-size:13px">'
        f'Return {weekday_dm(b["return"])} — {escape(b.get("return_note", "details in Google Flights"))}</td></tr>')
    pts = daily_best(ctx.history)
    all_low = min((p[1] for p in pts), default=None)
    days_left = (date.fromisoformat(b["outbound"]) - ctx.today).days
    chart = ('<img src="cid:chart" alt="Price chart" style="width:100%;max-width:600px;margin-top:16px;'
             'border:1px solid #eee;border-radius:6px">') if ctx.chart_png else ""
    errs = ""
    if ctx.errors:
        errs = (f'<p style="font-size:12px;color:#b3261e">Some searches failed: '
                f'{escape("; ".join(ctx.errors))}</p>')
    left = ctx.state.get("searches_left")
    return f"""<!doctype html><html><body style="margin:0;background:#f4f4f2">
<div style="max-width:620px;margin:0 auto;padding:24px 20px;background:#fcfcfb;font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:{INK}">
  <div style="font-size:12px;color:{MUTED}">{escape(cfg.get('trip_name', 'Flight monitor'))}</div>
  <h1 style="font-size:22px;margin:4px 0 10px">{heading}</h1>
  {intro_html}
  <table style="width:100%;border-collapse:collapse;margin-top:14px">
    <tr><td style="padding:0 0 10px">
      <span style="font-size:34px;font-weight:700">{eur(b['price_pp'])}</span>
      <span style="font-size:14px;color:{INK2}">per person, round trip</span><br>
      <span style="font-size:14px;color:{INK2}">{eur(b['price_total'])} total for {adults} ·
        Google rates it <b style="color:{lvl_col}">{lvl_txt}</b> (typical {typical})</span>
    </td></tr>
    {leg_html("Outbound", b["outbound"], b)}
    {ret}
  </table>
  <div style="margin-top:14px">{button(b.get('google_url'), 'Open in Google Flights')}{button(cfg.get('dashboard_url'), 'Dashboard', False)}</div>
  {chart}
  {grid_html(ctx)}
  <p style="font-size:12px;color:{MUTED};margin-top:24px;line-height:1.5">
    All-time low so far: {eur(all_low)} pp · {days_left} days to departure ·
    {f'{left} SerpApi searches left this month · ' if left is not None else ''}
    Prices are from Google Flights at check time and can change quickly — always confirm before booking.
  </p>{errs}
</div></body></html>"""


def plain_text(ctx: Context, heading: str) -> str:
    b = ctx.best
    t = [heading, "",
         f"{eur(b['price_pp'])} per person ({eur(b['price_total'])} total) — {b['airlines']}",
         f"Out {b['outbound']} {hm(b['depart'])}→{hm(b['arrive'])} {'via ' + b['via'] if b['via'] else 'direct'}"]
    if b.get("return_leg"):
        r = b["return_leg"]
        t.append(f"Back {b['return']} {hm(r['depart'])}→{hm(r['arrive'])} {'via ' + r['via'] if r['via'] else 'direct'}")
    t += ["", b.get("google_url", "")]
    return "\n".join(t)


# ───────────────────────── sending ─────────────────────────

def _send(ctx: Context, subject: str, html: str, text: str, outbox: Path | None) -> None:
    cfg = ctx.cfg
    subject = f"{cfg['email'].get('subject_prefix', '')} {subject}".strip()
    to = [a.strip() for a in (os.environ.get("EMAIL_TO") or ",".join(cfg["email"]["to"])).split(",") if a.strip()]

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["To"] = ", ".join(to)
    msg["From"] = os.environ.get("EMAIL_FROM") or os.environ.get("SMTP_USER") or "flight-monitor@localhost"
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    if ctx.chart_png:
        msg.get_payload()[1].add_related(ctx.chart_png, maintype="image", subtype="png", cid="<chart>")

    if outbox is not None:
        outbox.mkdir(parents=True, exist_ok=True)
        stem = f"{ctx.today.isoformat()}-{subject.split(']')[-1].strip()[:40]}".replace(" ", "_").replace("/", "-")
        preview = html.replace("cid:chart", f"{stem}.png")
        (outbox / f"{stem}.html").write_text(preview, encoding="utf-8")
        if ctx.chart_png:
            (outbox / f"{stem}.png").write_bytes(ctx.chart_png)
        print(f"  ✉  (saved, not sent) {subject} → {outbox / (stem + '.html')}")
        return

    host = os.environ.get("SMTP_HOST")
    if not host:
        print("  ✉  SMTP_HOST not set — email skipped:", subject)
        return
    port = int(os.environ.get("SMTP_PORT", "587"))
    user, pwd = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASSWORD")
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=60) as s:
            if user:
                s.login(user, pwd)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=60) as s:
            s.starttls(context=context)
            if user:
                s.login(user, pwd)
            s.send_message(msg)
    print(f"  ✉  sent: {subject} → {', '.join(to)}")


def send_alert(ctx: Context, reasons: list[str], outbox: Path | None) -> None:
    b = ctx.best
    heading = f"Good price: {eur(b['price_pp'])} per person"
    items = "".join(f'<li style="margin:2px 0">{escape(r)}</li>' for r in reasons)
    intro = f'<ul style="margin:0;padding-left:18px;font-size:14px;color:{INK}">{items}</ul>'
    subj = f"✈ {eur(b['price_pp'])} pp HEL⇄ATH {dm(b['outbound'])}–{dm(b['return'])} ({b['airlines']})"
    _send(ctx, subj, body_html(ctx, heading, intro), plain_text(ctx, heading + "\n" + "\n".join(reasons)), outbox)


def send_start(ctx: Context, outbox: Path | None) -> None:
    a = ctx.cfg["alerts"]
    heading = "Monitor is running"
    intro = (f'<p style="font-size:14px;color:{INK2};line-height:1.5;margin:0">Your flight price monitor made its first '
             f'check. From now on you get an email when the price drops under <b>€{a["price_limit_per_person"]}</b> per '
             f'person, reaches a new all-time low, or Google rates it “low” — plus a short summary every '
             f'{(a.get("weekly_summary") or "week").capitalize()}. Here is the starting point:</p>')
    _send(ctx, f"Started — cheapest now {eur(ctx.best['price_pp'])} pp", body_html(ctx, heading, intro),
          plain_text(ctx, heading), outbox)


def send_weekly(ctx: Context, outbox: Path | None) -> None:
    pts = daily_best(ctx.history)
    week = [p for p in pts if (ctx.today - p[0]).days <= 7]
    prev = [p for p in pts if 7 < (ctx.today - p[0]).days <= 14]
    now_pp = ctx.best["price_pp"]
    change = ""
    if prev:
        diff = now_pp - min(p[1] for p in prev)
        word = "down" if diff < 0 else "up" if diff > 0 else "unchanged"
        change = f" — {word} {eur(abs(diff))} vs. last week" if diff else " — unchanged vs. last week"
    rng = f"{eur(min(p[1] for p in week))}–{eur(max(p[1] for p in week))}" if week else "–"
    intro = (f'<p style="font-size:14px;color:{INK2};line-height:1.5;margin:0">Cheapest this week ranged {rng} '
             f'per person{escape(change)}. No action needed unless the price looks good to you.</p>')
    _send(ctx, f"Weekly: cheapest {eur(now_pp)} pp", body_html(ctx, "Weekly price summary", intro),
          plain_text(ctx, "Weekly price summary"), outbox)
