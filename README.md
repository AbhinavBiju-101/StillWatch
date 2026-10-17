# StillWatch: stored-water early-warning monitor

Flask website + JSON API for the ESP32 node (pH, turbidity, TDS now; temperature and ORP later).
It stores readings in Postgres (Supabase), runs statistical change detectors, and optionally asks an
LLM to explain alerts in plain language.

**Positioning rule:** the system only says "no unusual change detected" or "unusual change detected,
test recommended". It never says the water is safe. The LLM output validator enforces this.

## Layout

```
app/
  features.py      pure derived features: recovery time and settling half-life after an excursion
  notify.py        SMS / WhatsApp alerts through Twilio (standard library only)
  detectors.py     pure statistical detectors (baseline departure, EWMA, rate spike, CUSUM, hard limits)
  schema.py        validation of the versioned JSON message (sensors not wired are simply omitted)
  simulator.py     fake tank data in the device's JSON shape (demo account + tools/fake_device.py)
  calibration.py   two-point pH calibration maths (raw mV is always stored)
  service.py       ingest, analysis, alerts, demo seeding, tank status
  llm.py           Gemini / Ollama / none, with an output validator
  routes_api.py    device endpoints (readings, batch, check-in, firmware), operator endpoints, sweep, health
  devices.py       per-node tokens, telemetry, settings sent at check-in, firmware releases
  labs.py          lab parameters with qualifiers ("<1", "absent"), report-file checks
  training.py      labelled training-set export (one row per lab sample)
  cli.py           admin commands (flask --app app <command>)
  routes_web.py    landing page, login + quick sign-in, tanks, tank detail (rename, pH calibration), alerts, events, lab results
  static/          theme.css (tokens), site.css (overview deck + sign-in), app.css (operator app), landing.js (deck interactions),
                   app.js (sparklines, alert filter), img/caustics.jpg (background), favicon.svg/.ico/.png
schema.sql         tables (run with `flask --app app init-db`)
tools/fake_device.py   pretend to be the ESP32
firmware/              sensor_serial_test (bench check) and water_node (full node firmware), see firmware/README.md
tests/                 python -m unittest discover -s tests -t .   (database tests: see "Tests")
```

## Accounts

| Username | Sees | Data |
|---|---|---|
| `demo` | tanks in the `demo` dataset | simulated, regenerated automatically on login when older than `DEMO_REFRESH_HOURS` |
| `testing` | tanks in the `pilot` dataset | real device data posted to `/api/readings` |

Passwords come from `DEMO_PASSWORD` / `TESTING_PASSWORD`. An empty password disables the account.

**Home page and one-click sign-in.** `/` is a public, slide-deck style overview (ripple hero, diagrams, interactive labels); the dashboard is at `/tanks`.
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
| `DEMO_PASSWORD`, `TESTING_PASSWORD` | account passwords (named users: `flask create-user`) |
| `ALLOW_SHARED_DEVICE_TOKEN` | `1` (default): `DEVICE_TOKEN` still works for nodes that have no token of their own. `0` refuses it entirely |
| `SWEEP_TOKEN` | enables `POST /api/sweep`; empty = off |
| `DEVICE_SILENT_MIN` | minutes without contact before a node is flagged (default 30) |
| `DEVICE_READ_PERIOD_S`, `DEVICE_SEND_PERIOD_S` | periods nodes are told at check-in (default 10 / 60) |
| `MAX_UPLOAD_MB` | request size limit, also bounds lab report files (default 6) |
| `LLM_PROVIDER` | `none` (default), `gemini`, or `ollama` |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | check the current model names and free-tier limits |
| `OLLAMA_URL`, `OLLAMA_MODEL` | local development only; Render's free tier cannot run a model |
| `QUICK_LOGIN_DEMO`, `QUICK_LOGIN_TESTING` | see Accounts above |
| `NOTIFY_PROVIDER`, `TWILIO_*`, `PUBLIC_BASE_URL` | SMS / WhatsApp alerts, see below |
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

### Devices (header `X-Device-Token`)

A node's own token (`sw_...`, from `register-device`) only works for its own `device_id` (403 otherwise). Once a node has its own
token, the shared `DEVICE_TOKEN` stops working for that node.

| Endpoint | Purpose |
|---|---|
| `POST /api/readings` | one reading. `201` stored, `200` duplicate (same `device_id` + `ts`; safe to retry), `400` invalid, `401`/`403` token, `409` id belongs to a demo tank |
| `POST /api/readings/batch` | `{"schema":1,"device_id":...,"rssi":..., "readings":[...]}` up to 200. `200`/`201` means the request is finished: drop everything; invalid rows are listed in `rejected` |
| `POST /api/device/checkin` | heartbeat (`rssi`, `uptime_s`, `queued`, `boot`, `faults`, `fw`). Reply: `server_time`, `config` (periods, limits), `update` (version, sha256, size, path) or null |
| `GET /api/firmware/<version>.bin?device_id=` | only the release assigned to that device |

```json
{"schema": 1, "device_id": "tank-hostel-01", "ts": "2026-10-09T10:15:00Z",
 "ph": 7.21, "ph_raw_mv": 2480, "turbidity_ntu": 3.4, "turbidity_raw_mv": 3100,
 "tds_ppm": 312, "tds_raw_mv": 1480, "temp_c": 29.5, "orp_mv": 410, "orp_raw_mv": 2900, "fw": "0.5.0"}
```

Omit any sensor that is not wired or is faulty; at least one value is required (a check-in needs none). `ts` is optional;
timestamps more than 10 minutes in the future or older than 90 days are rejected (a node with an unset clock). Keep unsent readings
and retry with the **same `ts`**; never block local LED/buzzer alerts on the network.

### Operators

Browser session, or `Authorization: Bearer <token>` (from `flask api-token USERNAME`). **Writes need the bearer token**, so a
logged-in browser cannot be tricked into changing data by another site.

| Endpoint | Purpose |
|---|---|
| `GET /api/tanks`, `/api/tanks/<id>` | overview with latest values; tank profile plus device status |
| `GET /api/readings?device_id=&limit=` | recent readings (a node token may read only its own) |
| `GET /api/alerts?status=open\|closed` | water and device alerts (`kind`) |
| `POST /api/events` | `{device_id, kind, note?, when?}` |
| `POST /api/lab-results` | `{device_id, collected_at, lab_name?, sample_location?, note?, values:[{parameter, text:"<1"}]}` |

### Scheduler

`POST /api/sweep` with header `X-Sweep-Token: $SWEEP_TOKEN` opens a **device** alert for any node silent longer than
`DEVICE_SILENT_MIN`. Device alerts say "about the device, not the water" and close when the node reports again. Options for
calling it: an uptime pinger that can send a POST with a header, `flask --app app sweep` from cron, or do nothing: opening the
tanks page also runs a throttled sweep.

## Admin commands

`python -m flask --app app <command>`:

| Command | What it does |
|---|---|
| `register-device ID [--name N]` | creates the tank (pilot) and the node; prints its token once. Run again to replace the token |
| `revoke-device ID` | stops that token working |
| `fw-release FILE.bin VERSION` | stores a firmware image (checked: size, starts with 0xE9) |
| `fw-assign VERSION --device ID` / `--all` | offer it at the next check-in |
| `sweep` | run the silent-device check once |
| `create-user`, `set-password`, `disable-user [--enable]`, `api-token USER` | named accounts (passwords of 10+ characters) and API tokens |
| `export-training [--dataset pilot\|demo] [--out DIR] [--window-hours N]` | writes `windows.csv` (one row per lab sample: features from the hours before it plus the lab values) and `alerts.csv` (every water alert with the operator's verdict) |

The training export only looks backwards from each sample, so it can be used to tune detectors without leaking the answer.

## pH calibration

Each tank has a **pH calibration** page: enter the raw mV read in the pH 4 and pH 7 buffers and a start time.
Calibrations are stored with their validity period; "Recompute pH" re-derives stored pH from raw mV, using the
calibration in force at each reading's time (readings before the first calibration are untouched). The device
can also calibrate itself over serial (`cal4` / `cal7`); a calibration saved here takes precedence for the
periods it covers once you recompute.

## Operator features beyond the charts

- **Public status page.** On a tank's page, turn on *Public status page*, pick a status and an optional note, then print the
  QR code. `/s/<token>` shows only what the operator set (never readings), is marked `noindex`, and warns visitors when it has
  not been confirmed for a week. Status changes only when the operator saves it.
- **SMS and WhatsApp.** Set `NOTIFY_PROVIDER=twilio` plus the Twilio values, then add contacts under Alerts > SMS and WhatsApp.
  Messages are fixed templates ("unusual change detected, test recommended"), sent only for **pilot** tanks, and every attempt is
  logged. Run `flask --app app init-db` again to create the new tables and columns (it is safe to re-run).
- **Data export.** Each tank page has CSV downloads (readings with raw values, events, lab results, alerts with the operator's
  feedback). Times are UTC. This is the labelled dataset for tuning detectors.
- **Tank profile.** Location and probe position (depth, distance from inlet and outlet).
- **Insights.** After an unusual change, the tank page shows how long each metric took to return to normal and its settling
  half-life. These are experimental signals; whether they predict lab results is what the pilot data should answer.

## How alerts work

1. Each new reading triggers analysis over the last 72 h, resampled to 5-minute buckets.
2. Detectors compare the latest ~30 min with the tank's own earlier behaviour (median/MAD, so a past event
   does not distort the baseline). Nothing fires until ~2.5 h of history exists. Hard limits always apply.
3. Severity: **red** for a hard-limit breach, two or more metrics changing, or a very large change; else **yellow**.
4. An alert is stored immediately with a template message. If an LLM is configured, it explains the alert in
   the background and replaces the message only if its output passes the validator.
5. Operators close alerts as "real problem" or "false alarm"; lab results and events are kept as ground truth
   for tuning.

## Tests

```
python -m unittest discover -s tests -t .      # unit tests, no database needed
```

Database and firmware tests are skipped unless their prerequisites exist: `tests/test_integration_pg.py` needs `psycopg` and
`TEST_DATABASE_URL` pointing at an **empty database whose name contains "test"**; `tests/test_firmware_contract.py` needs `g++`;
`sh firmware/tests/run.sh` and `compile_check.sh` are described in `firmware/README.md`.

## Known limitations

- The landing page is public and stays high-level on purpose (no thresholds, detector internals or prompts), which matters if
  you intend to seek patent protection. Its interactive figures are simplified, simulated illustrations.
- Hard limits in `detectors.py` are placeholders: verify against the standard you cite (e.g. BIS IS 10500). Detector thresholds
  are tuned on simulated data only; retune with real pilot data and lab results. Temperature is watched only for sudden spikes.
- Login is limited to 5 failures per IP per 5 minutes, held in memory (resets on restart; fine for one worker).
- Only two datasets exist (demo, pilot): there is no per-organisation separation.
- Lab report files (PDF, PNG, JPEG, up to 5 MB) are stored in the database, not on disk, because free hosts have an ephemeral
  filesystem. Keep an eye on database size.
- Firmware updates: no automatic rollback and images are not signed (see `firmware/README.md`).
- **Verification status.** The backend was run against a real PostgreSQL 16 using a small test-only driver shim, not psycopg
  itself and not Supabase. The firmware logic was tested on a PC; the sketch has not been compiled with the real ESP32 core and
  nothing has run on hardware (DS18B20, ORP, flash wear, portal, OTA). The Twilio calls, and the CDN-loaded charts and QR code,
  were not exercised. Do these first on your bench.
