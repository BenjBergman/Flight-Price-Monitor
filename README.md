# ✈ Flight price monitor — Helsinki ⇄ Athens, June 2027

Checks Google Flights every morning for **HEL → ATH round trips with at most one stop**, for **6 adults**,
across a date grid around **18.6 → 26.6.2027** (outbound 17–19.6, return 25–27.6).
It emails you when the price is good and keeps a dashboard with price graphs.

**You get an email when**

- the cheapest round trip drops **under your limit** (default €350 per person), or
- it hits a **new all-time low** (at least €10 per person below the previous low), or
- **Google Flights rates the price "low"** for this route.

After a ping it stays quiet until the price drops another €10 per person (or climbs 10 % and comes back down),
so you don't get the same news every day. On top of that: a **start email** after the first run and a
**weekly summary** every Monday.

**Features**

| | |
|---|---|
| Date grid | 9 outbound × return pairs. The preferred pair (18.6 → 26.6) is checked every day, the rest rotate |
| Itinerary filters | max layover 5 h, min connection 45 min, no overnight layovers, max 11 h travel, outbound not before 06:00, optional airline blacklist |
| Return leg | details of the cheapest acceptable return flight are fetched for the best deal |
| Email | price per person and total for 6, both legs, Google's verdict and typical range, price graph, price grid, "Open in Google Flights" button |
| Dashboard | price-over-time graph (all pairs or one pair, 30/90 days/all), Google's own price history, typical-price band, your limit line, colour-coded date grid, all flights for a pair, alert log, table view, dark mode |
| Quota guard | stays inside SerpApi's free 250 searches/month (≈180 used), skips a run if the account is nearly empty |
| Auto stop | stops checking after 17.6.2027 |

All settings are in **`config.yaml`** — edit it on GitHub, commit, and the next run uses it.

---

## Setup (≈ 20 minutes, once)

### 1. Get a SerpApi key (the flight data source)
1. Sign up at <https://serpapi.com> (free plan: 250 searches/month, no card needed).
2. Copy your **API key** from <https://serpapi.com/manage-api-key>.

### 2. Put the code on GitHub
1. Create a free account at <https://github.com> if you don't have one.
2. **New repository** → name it `flight-monitor`.
   - **Public** = the dashboard web page works for free (it only contains flight prices, no passwords).
   - **Private** = also fine, you get the graph in the emails but GitHub Pages needs a paid plan.
3. On the empty repo page choose **uploading an existing file**, drag in **all files and folders** from this
   package, and click **Commit changes**.
   > The `.github` folder is hidden on Mac/Windows. If it doesn't upload, use **Add file → Create new file**,
   > type the name `.github/workflows/monitor.yml`, paste the content of that file, and commit.

### 3. Add your secrets
Repository → **Settings → Secrets and variables → Actions → New repository secret**. Add:

| Secret | Value |
|---|---|
| `SERPAPI_KEY` | your SerpApi key |
| `SMTP_HOST` | mail server, e.g. `smtp.gmail.com` |
| `SMTP_PORT` | `587` (or `465` for SSL) |
| `SMTP_USER` | the mailbox login, e.g. `something@gmail.com` |
| `SMTP_PASSWORD` | its password — for Gmail an **app password** (Google account → Security → 2-step verification → App passwords) |
| `EMAIL_FROM` | *(optional)* sender address, defaults to `SMTP_USER` |
| `EMAIL_TO` | *(optional)* recipients, comma-separated — overrides `config.yaml`, e.g. to add the whole crew |

> Tip: a separate free Gmail account for the bot is the easiest. Microsoft 365 mailboxes often have SMTP login
> switched off by the admin.

### 4. Allow the workflow to save data
**Settings → Actions → General → Workflow permissions → Read and write permissions → Save.**

### 5. First run
**Actions** tab → *Check flight prices* → **Run workflow**. After a minute you should get the
**"Started — cheapest now €… pp"** email. From then on it runs by itself every morning at ~08:20 Finnish time.

👉 Open the "Open in Google Flights" link once and check that the total matches what Google shows for 6 passengers.

### 6. Dashboard (public repo)
**Settings → Pages → Build and deployment → Deploy from a branch → `main` / `/docs` → Save.**
After a minute the page is at `https://<your-github-name>.github.io/flight-monitor/`.
Put that address in `dashboard_url` in `config.yaml` so the emails get a *Dashboard* button.

---

## Changing things

| I want to… | Edit in `config.yaml` |
|---|---|
| change my price limit | `alerts.price_limit_per_person` |
| only 1-stop flights, no direct | `route.include_direct: false` |
| other dates | `dates.outbound`, `dates.return`, `dates.preferred` |
| a different group size | `passengers.adults` |
| avoid an airline | `filters.exclude_airlines: ["Ryanair"]` |
| check more combinations | `search.searches_per_run` (keep × 30 under 250 on the free plan) |
| check twice a day | add a second `cron` line in `.github/workflows/monitor.yml` and halve `searches_per_run` |

## Try it without any keys

```bash
pip install -r requirements.txt
python monitor.py --demo                 # fake prices; emails saved to out/emails/, dashboard to demo-docs/
python monitor.py --dry-run              # real prices (needs SERPAPI_KEY), emails saved instead of sent
```

## Files

```
config.yaml                 all settings
monitor.py                  main program (search → store → alert → dashboard)
serp.py                     Google Flights via SerpApi (+ demo data generator)
notify.py                   emails
charts.py                   price graph image
dashboard.py + dashboard_template.html   the dashboard page
data/                       price history (history.csv), alerts, state — written by the workflow
docs/                       the dashboard (index.html, price-chart.png)
.github/workflows/monitor.yml   the daily schedule
```

Prices come from Google Flights at the time of the check and can change quickly — always confirm before booking.
