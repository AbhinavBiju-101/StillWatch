# Stored-water early-warning monitor: backend

Flask website + JSON API for the ESP32 node (pH, turbidity, TDS now; temperature and ORP later).
It stores readings in Postgres (Supabase), runs statistical change detectors, and optionally asks an
LLM to explain alerts in plain language.

**Positioning rule:** the system only says "no unusual change detected" or "unusual change detected,
test recommended". It never says the water is safe. The LLM output validator enforces this.

## Layout

```
app/
  detectors.py     pure statistical detectors (baseline departure, EWMA, rate spike, CUSUM, hard limits)
  schema.py        validation of the versioned JSON message (sensors not wired are simply omitted)
  simulator.py     fake tank data in the device's JSON shape (demo account + tools/fake_device.py)
  calibration.py   two-point pH calibration maths (raw mV is always stored)
  service.py       ingest, analysis, alerts, demo seeding, tank status
  llm.py           Gemini / Ollama / none, with an output validator
  routes_api.py    POST/GET /api/readings, GET /api/health
  routes_web.py    landing page, login + quick sign-in, tanks, tank detail (rename, pH calibration), alerts, events, lab results
  static/          style.css (light theme), landing.js (interactive figures), favicon.svg/.ico/.png (half-clear, half-murky drop)
schema.sql         tables (run with `flask --app app init-db`)
tools/fake_device.py   pretend to be the ESP32
firmware/              sensor_serial_test (bench check) and water_node (full node firmware), see firmware/README.md
tests/                 python -m unittest discover -s tests -t .
```

## Accounts

| Username | Sees | Data |
|---|---|---|
| `demo` | tanks in the `demo` dataset | simulated, regenerated automatically on login when older than `DEMO_REFRESH_HOURS` |
| `testing` | tanks in the `pilot` dataset | real device data posted to `/api/readings` |

Passwords come from `DEMO_PASSWORD` / `TESTING_PASSWORD`. An empty password disables the account.

**Home page and one-click sign-in.** `/` is a public landing page with interactive figures; the dashboard is at `/tanks`.
The home and sign-in pages have **Open demo** and **Open testing** buttons:

| Setting | Default | Effect |
|---|---|---|
| `QUICK_LOGIN_DEMO` | `1` | Open demo signs straight in. Safe: it only shows simulated data. |
| `QUICK_LOGIN_TESTING` | `0` | When `0`, Open testing opens the sign-in form with the username filled in. When `1`, it signs straight in to the **real pilot data with no password**. Set it to `1` only in your local `.env`, never on a public deployment. |

Devices can only create or write `pilot` tanks; a device id that matches a demo tank is rejected (409).

## Run locally (Windows PowerShell / VS Code)

Open the project folder in VS Code, open a terminal (Ctrl+`), and run:

```powershell
py -m venv .venv                      # once: creates an isolated Python environment in .venv
.venv\Scripts\Activate.ps1           # every new terminal: you should see (.venv) at the start of the prompt
pip install -r requirements.txt       # once: installs Flask, psycopg, etc. into .venv
Copy-Item .env.example .env           # once: then open .env and fill it in
python -m flask --app app init-db     # once: creates the tables in Supabase
python run.py                         # starts the server at http://127.0.0.1:5000
```

- If `Activate.ps1` says scripts are disabled, run `Set-ExecutionPolicy -Scope Process Bypass` first (affects only
  that terminal), or use `.venv\Scripts\activate.bat` from a Command Prompt.
- In VS Code, press Ctrl+Shift+P, choose **Python: Select Interpreter**, and pick the one in `.venv`.
- `python -m flask ...` works even when the bare `flask` command is not found. Use it everywhere below.
- macOS/Linux: `python3 -m venv .venv && source .venv/bin/activate`, and `cp` instead of `Copy-Item`.

Optional: `python -m flask --app app seed-demo` fills the demo data now (it also fills itself when you log in as `demo`).

### Try it with a simulated device

With the server running, open a **second** terminal (activate `.venv` there too) and run, as one line:

```powershell
python tools/fake_device.py --url http://localhost:5000 --token YOUR_DEVICE_TOKEN --device-id tank-test-01 --event-minutes-ago 100
```

`YOUR_DEVICE_TOKEN` is the `DEVICE_TOKEN` value from your `.env`. Log in as `testing` and you should see
`tank-test-01` with a turbidity alert.

### How `python -m flask --app app init-db` works

`--app app` tells Flask where the application is: the Python package named `app` (the `app/` folder), whose
`create_app()` function builds it. `init-db` is a custom command we registered in `app/__init__.py` with
`@app.cli.command("init-db")`. It is the same idea as `npm run <script>` or `streamlit run`, except commands
are defined in Python. `python run.py` just calls `create_app()` and starts the development server.

## Environment variables

| Name | Notes |
|---|---|
| `DATABASE_URL` | Supabase: Project settings, Database, connection string. If the direct string fails from your network or host, try the **session pooler** string. |
| `SECRET_KEY`, `DEVICE_TOKEN` | long random strings: `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `DEMO_PASSWORD`, `TESTING_PASSWORD` | account passwords |
| `LLM_PROVIDER` | `none` (default), `gemini`, or `ollama` |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | check the current model names and free-tier limits |
| `OLLAMA_URL`, `OLLAMA_MODEL` | local development only; Render's free tier cannot run a model |
| `QUICK_LOGIN_DEMO`, `QUICK_LOGIN_TESTING` | see Accounts above |
| `COOKIE_SECURE` | `1` when served over HTTPS |
| `BEHIND_PROXY` | `1` on Render so the real client IP is used for the login limit |

Never commit `.env`. `schema.sql` turns on row-level security so Supabase's public REST API cannot read
your tables; this app connects directly to Postgres and is unaffected.

## Deploy on Render

1. Push to a **private** GitHub repo; create a Render Web Service from it (`render.yaml` is included).
2. Set `DATABASE_URL`, `DEVICE_TOKEN`, `DEMO_PASSWORD`, `TESTING_PASSWORD` (and `GEMINI_API_KEY` if used).
3. Run `flask --app app init-db` once (Render shell, or locally with the same `DATABASE_URL`).
4. Free web services sleep after idle time and wake slowly (check Render's current terms). Either point a
   free uptime pinger at `/api/health`, or make the firmware tolerate a long first response (see below).

## API contract (schema 1)

`POST /api/readings` with header `X-Device-Token: <DEVICE_TOKEN>` and body:

```json
{"schema": 1, "device_id": "tank-hostel-01", "ts": "2026-10-09T10:15:00Z",
 "ph": 7.21, "ph_raw_mv": 2480, "turbidity_ntu": 3.4, "turbidity_raw_mv": 3100,
 "tds_ppm": 312, "temp_c": 29.5, "orp_mv": 410, "fw": "0.3.1"}
```

- Omit any sensor that is not wired. At least one sensor value is required. `ts` is optional (server time).
- Responses: `201` stored, `200` duplicate (same `device_id` + `ts`, safe to retry), `400` invalid,
  `401` bad token, `409` id belongs to a demo tank.
- Firmware guidance: use a long HTTP timeout (60 s or more), keep unsent readings and retry with the
  **same `ts`**, and never block local LED/buzzer alerts on the network.
- `GET /api/readings?device_id=...&limit=100` returns recent readings (device token or web session).

## pH calibration

Each tank has a **pH calibration** page: enter the raw mV read in the pH 4 and pH 7 buffers and a start time.
Calibrations are stored with their validity period; "Recompute pH" re-derives stored pH from raw mV, using the
calibration in force at each reading's time (readings before the first calibration are untouched). The device
can also calibrate itself over serial (`cal4` / `cal7`); a calibration saved here takes precedence for the
periods it covers once you recompute.

## How alerts work

1. Each new reading triggers analysis over the last 72 h, resampled to 5-minute buckets.
2. Detectors compare the latest ~30 min with the tank's own earlier behaviour (median/MAD, so a past event
   does not distort the baseline). Nothing fires until ~2.5 h of history exists. Hard limits always apply.
3. Severity: **red** for a hard-limit breach, two or more metrics changing, or a very large change; else **yellow**.
4. An alert is stored immediately with a template message. If an LLM is configured, it explains the alert in
   the background and replaces the message only if its output passes the validator.
5. Operators close alerts as "real problem" or "false alarm"; lab results and events are kept as ground truth
   for tuning.

## Known limitations

- The landing page is public. It stays high-level on purpose (no thresholds, detector internals or prompts), which matters if
  you intend to seek patent protection. Its interactive figures are simplified, simulated illustrations.
- The front end has been checked with unit tests and a scripted DOM test of `landing.js`, not in a real browser.
  Look at it once in Chrome and on a phone-width window.

- Hard limits in `detectors.py` are placeholders: verify against the standard you cite (e.g. BIS IS 10500).
- Detector thresholds are tuned on simulated data only. Retune with real pilot data and lab results.
- Temperature is watched only for sudden spikes (it has a natural daily cycle).
- Login is limited to 5 failures per IP per 5 minutes, held in memory (resets on restart; fine for one worker).
- SQL has not been run against a live Supabase instance in development; run the local steps above first.
