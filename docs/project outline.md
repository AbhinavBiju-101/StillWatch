# [Project Name TBD] — Early-Warning Water Quality Monitoring for Stored Water

> A low-cost hardware + software system that continuously monitors water in storage tanks, detects abnormal changes before anyone gets sick or a lab test comes back, and tells the person responsible what to do about it.

**Status:** Concept and planning (Oct 2026) · **Time available:** 1 year (plan stays flexible) · **Stage:** Pre-prototype

---

## 1. The Problem

In many places, including villages, hostels, schools and apartment complexes in India, water arrives from the municipality or a supplier at intervals and is stored in overhead or underground tanks. The water is usually clean and tested **when it arrives**. During storage it can degrade: sediment gets stirred up, the tank warms, biofilm grows, disinfectant decays, rain runoff or animals get in, pipes leak.

Today, quality is checked mainly through lab testing:

- Samples are taken infrequently (weekly, monthly, or only after complaints).
- Results take days (bacterial tests need roughly 24 to 48 hours of incubation).
- The sample captures one moment, not what happened in between.

**The core gap is detection delay.** Contamination or degradation happens between tests, and people drink the water before anyone knows. In many communities, people also tolerate bad water and wait for the next supply, so problems are never reported at all.

## 2. What This Project Is (and Is Not)

**It is** a continuous, cheap, at-the-source *change detector* for stored water. It answers: "Has something unusual happened to this tank's water, and what should be done?"

**It is not** a safety certifier. Low-cost sensors measure proxies (pH, turbidity, temperature, conductivity, ORP). They cannot detect bacteria, heavy metals (lead, arsenic), pesticides or fluoride. The system must **never claim water is "safe."** Wording is always "no unusual change detected" or "unusual change detected, test recommended."

**Positioning:** the smoke detector, not the fire investigation lab. It is crude but always present, and its job is to say "go investigate." The lab remains the confirmation step the system triggers.

## 3. Sources of Confusion, Resolved

This project started with a lot of reasonable doubts. Each one is recorded here with where it landed, so the idea has a solid footing and the same questions don't need to be re-asked. Items marked **Tentative** are decisions to revisit once there is real data.

### 3.1 Problem and purpose

| The doubt | What was actually unclear | Where it landed |
|---|---|---|
| "Labs already have cheap pH and turbidity sensors, so what am I even tackling?" | Mixing up *sensor accuracy* with *monitoring*. | Labs measure one sample, once, after someone carries it there. This project covers the time **between** tests. The problem is **detection delay**, not measurement. Compete on continuity, location and context, never on accuracy. |
| "Where and who does lab testing?" | Unclear who tests, how often, and how fast. | Utilities and municipal bodies test at plants and sample points on a schedule. Public health labs and pollution boards test submitted samples (days to weeks). Private NABL labs serve paying clients. Rural schemes use field kits with patchy coverage. Bacterial tests need roughly 24 to 48 hours. All are sparse, slow and reactive. |
| "The idea is prevention before lab reports, so why do lab reports matter?" | Whether lab data is a permanent part of the system. | Lab reports are for the **pilot and training phase only**: they label which sensor patterns mean trouble. After validation, deployed devices run without them, with occasional spot checks to catch drift or missed events. |
| "Does the device tell people if water is safe?" | Overpromising from proxy sensors. | No. pH, turbidity, TDS, temperature and ORP detect *change*. They cannot see bacteria, heavy metals, pesticides or fluoride. Messaging is always "no unusual change detected", never "safe". |

### 3.2 Hardware and placement

| The doubt | What was actually unclear | Where it landed |
|---|---|---|
| Smart water bottle with a display on the cap? | Whether the current parts could be miniaturized. | **Rejected.** Parts are breadboard-scale, pH probes must stay wet and be calibrated, and the proxy limits remain. A fixed tank node suits both the parts and the problem. |
| Stick it on the tank wall, mount it outside with sensors dangling in, or drill into a pipe? | Whether sensors can read through a wall. | Probes must touch the water, so electronics are outside and probes are inside. **Start lid-mounted with probes dangling in.** Pipe monitoring is a later version using a small bypass cell, because live pipes run dry between supplies, carry bubbles, and add pressure and leak risk. |
| Should there be a chemical dosing module? | Value versus maintenance and liability. | **Deferred.** Dosing without reliable detection can create false safety, and natural agents act inconsistently. Revisit only after monitoring works. |
| Are the current parts enough? | What is missing. | Enough for a first prototype. Add temperature (DS18B20), TDS and ORP, an I2C backpack for the LCD, and voltage dividers (ESP32 ADC tolerates 3.3V only). Note the ESP32-S2 has WiFi but no Bluetooth. |

### 3.3 Users and business

| The doubt | What was actually unclear | Where it landed |
|---|---|---|
| Villagers ignore bad water and wait for the next supply, so who is this for? | Who the real user is. | The **operator**: panchayat water operator, hostel warden, school administrator, supplier service team. Alerts go to someone with responsibility and a reason to act. |
| Sell through tank suppliers and builders? | Whether they would bundle it. | **Tentative.** Plausible, but they have thin margins and need buyer demand. Test with a few dealers and operators first. Early buyers with a mandate or liability (government programs, hostel chains, school networks) may be stronger. |
| Build a citizen-level dashboard too? | Who it serves and the risks. | **Operator dashboard first.** A citizen view comes later as a minimal, operator-confirmed status (for example a QR code on the tank). Public alerts carry panic and liability risk, and citizens are unlikely to act. |
| Can this be patented? | What is novel. | Intent is recorded. Generic "sensors in a tank plus IoT alerts" is crowded prior art, so any claim needs something specific (chamber design, a derived feature, a method with a technical result). Avoid public disclosure for now. Detailed planning is deferred. |

### 3.4 Software and data

| The doubt | What was actually unclear | Where it landed |
|---|---|---|
| Does the device run TinyML for detectors while the cloud holds the database and LLM? | What lives where. | Cloud holds the database, detectors and LLM. The device only senses, calibrates, sends, buffers offline, and runs a few hard-coded safety rules. Feature extraction needs history and context a microcontroller lacks. TinyML is a possible later optimization. |
| Kotlin Android app or Flask web app? | These were treated as alternatives. | They sit at different layers. Flask (or similar) is the **backend**, needed either way. Kotlin versus web is only the **frontend**. **Web app chosen**: one codebase and instant updates. |
| How does C++ firmware connect to the Python backend? Shared database? | Whether they share code or storage. | They share nothing but an agreed, versioned **JSON message format** sent over HTTP. The device never touches the database. The shared database is among backend pieces (ingest, detector job, dashboard). |
| What format is the data, and how is it used to train models? | The path from sensor to model. | Small JSON messages with raw and calibrated values. Training exports data, labels time windows using the events log and lab results, computes features, and tests whether they predict the labels. Start with statistics and rules, then try simple anomaly models. |
| What about databases, cache, memory and WiFi? | Which pieces are needed now. | SQLite to start (PostgreSQL later if needed). No cache needed early. "Memory" is just the events log and alert history retrieved into each LLM call. Devices buffer readings offline. Villages will likely need GSM or LoRa later. |
| How do lab reports get integrated? | Whether to automate PDF parsing. | Manual entry form at first: tank, lab, **sample collection time**, parameters (with qualifiers), sample location, PDF upload. Use collection time, not report date. |
| Which libraries and frameworks are required versus optional? What does "boilerplate" mean here? | Template-driven versus raw, minimal code. | Preference is **raw code with minimal imports**: Arduino core on the device, Python standard library plus one small web framework, SQLite, one HTML page with Chart.js. Add pandas, numpy and scikit-learn only when real analysis begins. |
| Where do I start? | Which piece to build first. | Make the **sensors trustworthy first** (serial output only, no WiFi, no server). Then a tiny server, then connect them, then one chart page, then a bucket and a real tank. |

### 3.5 Analysis and AI

| The doubt | What was actually unclear | Where it landed |
|---|---|---|
| Train several agents on the same data, each analysing a different feature (frequency, variation, amplitude)? | Whether each feature needs an LLM agent. | Those are classical signal-processing features, best handled by small statistical detectors that are faster, cheaper and explainable. The LLM sits **above** them as one interpreter that combines detector outputs with context. Calling the detectors "agents" is optional, and may complicate a patent claim. |
| Could a new sensor and raw data reveal new features like "decay rate"? | Whether discovery works without labels. | Promising, but raw data alone cannot show which features matter. It needs **ground truth** from lab samples matched to the sensor timeline. That paired dataset may become the most defensible asset. |

### 3.6 Still open

These are genuine unknowns, not misunderstandings:

- Who the first real operator is (hostel, school, panchayat, apartment complex), and what they do today when water looks off
- Which tank manufacturers or installers would actually bundle a device
- Whether derived features such as recovery time or settling rate predict lab outcomes
- How reliable the probes stay over weeks in real tanks
- Final sensor set for the pilot, and connectivity choice for villages
- What specifically will be claimed if a patent is pursued
- Project name

## 4. Users

| Role | Who | What they get |
|---|---|---|
| **Primary: Operator** | Hostel warden, panchayat water operator, school administrator, apartment maintenance committee, supplier service team | Dashboard, alerts, diagnosis, recommended actions |
| Secondary: Buyer / channel | Tank manufacturers and installers, government water programs, hostel chains, school networks | Pre-installed monitoring as a differentiator or compliance tool |
| Later: Citizens | Villagers, students | A minimal public status (e.g. QR code on the tank), only after operators trust the alerts |

The product is designed around the operator's workflow ("Tank 3 needs inspection, here's why"), not around a display that residents are expected to read and act on.

## 5. Solution Overview

```
 Sensors in tank
      │
 ESP32 firmware ── (WiFi now; GSM/LoRa later) ── HTTPS POST (JSON) ──►  Backend API
                                                                          │
                                                                       Database
                                                                          │
                                                   Detectors (Python) ──► Events
                                                                          │
                                                       LLM interpreter ──► Alerts + diagnosis
                                                                          │
                                                          Operator dashboard / WhatsApp / SMS
```

### Split of responsibilities

| On the device | In the backend |
|---|---|
| Read sensors, average samples, apply calibration | Store all history |
| Send raw and calibrated values | Run detectors, derive features (drift, recovery time, settling rate) |
| Buffer readings in flash when offline | Combine with context (rain, refills, cleaning, past alerts) |
| A few hard-coded safety rules (e.g. turbidity above X turns LED red and sounds buzzer) | LLM diagnosis, alerts, dashboards |

The device does **no machine learning** in the first year. TinyML is a possible later optimization, not a requirement.

## 6. Hardware

### 5.1 Form factor

A **lid- or top-mounted unit with probes hanging into the tank**. Electronics stay outside the water; only probes are submerged (pH and turbidity probes must touch the water, and cannot read through a tank wall).

Design considerations:

- Probes sit mid-depth, away from bottom sediment and surface scum, and must stay submerged even when the level drops (a dry pH probe is damaged).
- The enclosure is sealed against condensation and humidity.
- Readings represent one spot in the tank; position relative to inlet and outlet is recorded.
- Hobby-grade probes are not certified for drinking water. Prototypes are used only on water that is not being drunk.

A smart bottle was considered and rejected: components are too large, probes need to stay wet and be calibrated, and the proxy-sensor limits make it a weak fit.

A **pipe-mounted bypass cell** (a tee feeding a small sealed chamber that stays full, with the probes inside) is a possible version 2. Probes should not be drilled directly into live pipes because of dry pipes between supply windows, bubbles, pressure and leak risk.

### 5.2 Current parts

| Part | Role |
|---|---|
| ESP32-S2 DevKitM-1 | Microcontroller, WiFi (no Bluetooth on the S2) |
| pH sensor | pH |
| Turbidity sensor with module | Turbidity |
| 16x2 LCD | Local readout |
| RGB LED | At-a-glance status (green / yellow / red) |
| 5V passive buzzer | Critical alerts only |
| Breadboard MB102 + jumper wires | Prototyping |

### 5.3 Planned additions

- **DS18B20 temperature sensor** (needed for pH temperature compensation, and a useful signal itself)
- **TDS / conductivity sensor** (catches dissolved changes that pH and turbidity miss)
- **ORP sensor** (proxy for disinfectant presence and microbial risk)
- I2C backpack for the LCD (saves pins)
- Voltage dividers on any sensor output above 3.3V (the ESP32 ADC tolerates 3.3V only)
- Later: waterproof enclosure and cable glands, solar or battery power, GSM / LTE-M or LoRa connectivity for villages with weak WiFi

### 5.4 Known hardware risks

ADC noise (average many samples), pH probe calibration (pH 4 and 7 buffers) and drift over weeks, probe fouling, condensation, and power and connectivity in the field. **Probe reliability is expected to be the biggest early risk, not software.**

## 7. Firmware

- Arduino framework on the ESP32, minimal dependencies (built-in WiFi and HTTP; JSON built with `snprintf`).
- Reads each sensor many times, averages, converts to calibrated values, and keeps raw values.
- Sends one reading every few minutes (every ~10 seconds during testing).
- Later: NTP timestamps, offline buffering in flash (LittleFS) with backlog upload, OTA updates, WiFi setup portal.
- Local safety rules work even when offline.

## 8. Data Contract

The only thing the firmware and backend share is an agreed, versioned message format. The device never touches the database; it only calls the API with its device token.

```json
{
  "schema": 1,
  "device_id": "tank-hostel-01",
  "ts": "2026-10-09T10:15:00Z",
  "ph": 7.21,
  "ph_raw_mv": 2480,
  "turbidity_ntu": 3.4,
  "turbidity_raw_mv": 3100,
  "temp_c": 29.5,
  "tds_ppm": 312,
  "orp_mv": 410,
  "fw": "0.3.1"
}
```

**Always store raw values alongside calibrated ones.** Improved calibration can then be applied retroactively, and future feature discovery depends on raw data being preserved. Early versions may use server-side timestamps.

## 9. Backend

- **Language:** Python.
- **API:** minimal (Flask or plain `http.server` to start), endpoints such as `POST /readings` and `GET /readings`.
- **Database:** SQLite to start (single file, ample for this scale); PostgreSQL (optionally with TimescaleDB) if needed later.
- **Core tables (eventual):** `devices`, `tanks`, `readings`, `events`, `lab_results`, `alerts`, `users`.
- **Detector job:** a scheduled Python script that pulls recent readings, computes features and writes events and alerts.
- **Dependencies are kept minimal by design.** Standard library first; pandas, numpy, scipy and scikit-learn added only when real feature work begins.

## 10. Analysis: Detectors and Features

Detectors are small, explainable Python functions, not LLM agents:

- Rolling mean and standard deviation against the tank's **own baseline**
- EWMA for slow drift
- Rate of change for sudden spikes
- Change-point detection for behavior shifts
- Later, an anomaly model such as Isolation Forest once weeks of data exist

### Derived features (the research-flavored part)

Candidate signals beyond raw values:

- **Recovery time:** how long turbidity or pH takes to return to baseline after an inflow event
- **Settling rate:** how fast turbidity falls during stagnation (sediment vs. suspended organic matter)
- **Disinfectant decay:** how fast protection drops after arrival (needs ORP or chlorine sensing)
- **Temperature-linked drift:** how quality degrades as the tank warms

Whether any of these are meaningful is an empirical question answered by pairing sensor data with ground truth (see Ground Truth and Lab Reports).

## 11. LLM Interpreter

The LLM sits **after** the detectors. When a detector fires, the backend assembles a context packet: recent readings, which detectors triggered, tank profile, recent events (last cleaning, last refill, rain), and past alerts with outcomes. The LLM produces a plain-language diagnosis and recommended action, e.g.:

> "Tank 3: turbidity tripled since morning with no refill logged. Likely sediment disturbance or ingress. Recommend inspecting the lid and cleaning the tank; consider sending a sample for lab testing."

"Memory" is simply the events log and alert history in the database, retrieved into each call. Every alert and the operator's response is stored so recommendation quality can later be measured.

## 12. Ground Truth and Lab Reports

Lab testing is needed in the **pilot / training phase**, not in deployed operation. Periodic samples from pilot tanks are matched to the sensor timeline to learn which patterns actually indicate trouble. After validation, deployed devices run without lab reports, with occasional spot checks to confirm the system isn't drifting or missing things.

**Lab result handling (manual at first):**

- A dashboard form: tank, lab name, **sample collection time**, parameter values, sample location in tank, PDF upload.
- Stored one row per parameter, with qualifiers (e.g. "absent", "<1") and units.
- Use sample collection time (not report date) to align with sensor data.
- PDFs are stored as files; only the path lives in the database.
- Lab results display as markers on sensor charts, and training code joins them to the sensor window around each sample.

The same pattern covers an **events log** (cleaning, refill, rain, repairs).

## 13. Operator Dashboard and Alerts

Web app (not native), because there is one codebase, instant updates, and all AI/analysis lives in Python anyway.

**Build order:**

1. A single chart page showing pH and turbidity over time
2. Tank list with green / yellow / red status
3. Event and lab-result forms
4. Alerts with diagnosis and recommended actions
5. Feedback buttons ("mark as cleaned", "false alarm"); every tap becomes a training label
6. WhatsApp / SMS alerts for people who will never open a dashboard
7. Late in the pilot: a minimal QR-code public status page, operator-confirmed

Grafana may be used on the side for debugging.

## 14. Connectivity and Power (Field Reality)

- WiFi is fine for college and home pilots.
- Villages will likely need GSM / LTE-M or LoRa to a gateway, plus solar or battery power.
- The device must keep sensing, keep local safety rules, and cache data when offline. Insights are delayed, never lost.

## 15. Go-to-Market Hypotheses (To Be Tested)

- Alerts go to people with **responsibility**, not to residents.
- Early customers are those with a mandate or liability: government water programs, hostel chains, school networks, community-tank contractors.
- Bundling with tank manufacturers is plausible, but they sell on thin margins, so it needs buyer demand. Validate by talking to a few tank dealers and operators before committing to a channel.

Key question to ask real users: *"What do you do today when the water looks or smells off?"*

## 16. Intellectual Property Intent

The intent is to pursue a defensible, patentable contribution. Generic "sensors in a tank with IoT alerts" is crowded prior art, so any claim will likely need to rest on something specific, such as a sensing-chamber design, a novel derived feature, or a method that yields a concrete technical result. **Until a decision is made, avoid publicly disclosing technical details** (demos, blogs, public repos). Detailed IP planning is deferred; a prior-art search and attorney consultation come before any filing.

## 17. Immediate Next Steps

1. Wire up and test pH and turbidity readings with serial output only (tap water vs. soil / milk; vinegar vs. baking soda; record raw ADC values).
2. Build the tiny server with `POST /readings` and `GET /readings`, tested with `curl`.
3. Connect the ESP32 to the server over home WiFi.
4. Build one chart page.
5. Run it in a bucket, then a real tank, for several days.

## 18. Future Directions

- A proprietary sensor type producing raw signals, with detectors trained to discover new water features
- Pathogen-relevant sensing as costs allow
- On-device TinyML if offline intelligence becomes necessary
- Chemical dosing module (deferred: dosing without reliable detection can create false safety, and maintenance and liability grow quickly; at most a later, controlled turbidity-triggered flocculant step)
- Pipe-mounted bypass cell for flow monitoring
- Citizen-facing status views once alerts are trusted

## 19. Risks and Open Questions

| Risk / question | Mitigation |
|---|---|
| Probes drift, foul or fail in real tanks | Early field deployment; regular calibration; log raw data |
| Proxy sensors miss the real hazards (bacteria, metals) | Honest messaging; lab confirmation workflow; add sensors over time |
| False alarms erode trust, missed alarms create liability | Operator-in-the-loop; tune against lab results; feedback buttons |
| Villages lack connectivity and power | Offline buffering; GSM / LoRa; solar |
| Channel assumption (tank builders) is untested | Talk to dealers and operators early |
| Patent novelty is uncertain | Prior-art search; avoid early public disclosure; define a specific claim after data exists |
| Unknown whether derived features like "recovery time" predict lab outcomes | Gather paired sensor and lab data in the pilot |

## 20. Glossary

- **Turbidity (NTU):** cloudiness of water from suspended particles
- **TDS:** total dissolved solids, a rough measure of dissolved content
- **ORP:** oxidation-reduction potential, a proxy for disinfectant presence
- **EWMA:** exponentially weighted moving average, used to track slow drift
- **Change-point detection:** finding moments where a signal's behavior shifts
- **Ground truth:** independently verified results (lab reports) used to validate sensor-based detection
- **NABL:** India's accreditation body for testing laboratories
