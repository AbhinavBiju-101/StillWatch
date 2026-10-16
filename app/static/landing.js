// Landing page interactivity. Vanilla JS, no libraries. Everything here is a simplified illustration
// with simulated numbers; it is not the production detector.
(function () {
  "use strict";
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ---------------------------------------------------------------- hero: probe captions
  const PROBES = {
    ph: "pH probe: watches acidity or alkalinity. A sudden shift can hint at runoff or a leak.",
    tds: "TDS probe: tracks the amount of dissolved material. A rise can mean salts or minerals are entering.",
    turbidity: "Turbidity sensor: measures cloudiness from suspended particles, like stirred-up sediment.",
  };
  function showProbe(el) {
    $$(".probe").forEach(p => p.classList.toggle("active", p === el));
    $("#probe-caption").textContent = PROBES[el.dataset.probe];
  }
  $$(".probe").forEach(el => {
    el.addEventListener("click", () => showProbe(el));
    el.addEventListener("mouseenter", () => showProbe(el));
    el.addEventListener("focus", () => showProbe(el));
    el.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); showProbe(el); } });
  });

  // ---------------------------------------------------------------- the gap explorer
  function initGap() {
    const svg = $("#gap-svg");
    if (!svg) return;
    const startIn = $("#gap-start"), ivSel = $("#gap-interval");
    const W = 720, H = 240, L = 44, R = 12, T = 16, B = 62, HOURS = 72, VMAX = 6.5;
    const x = t => L + (W - L - R) * t / HOURS;
    const y = v => T + (H - T - B) * (1 - v / VMAX);
    const labY = H - 24;
    const THRESH = 2.5, DURATION = 6;

    function draw() {
      const s = +startIn.value || 30, interval = +ivSel.value || 24;   // fallbacks guard against empty values
      $("#gap-start-out").textContent = s;

      // sensor trace: gentle baseline noise plus an event that rises fast and fades slowly
      const pts = [];
      let alertT = null;
      for (let t = 0; t <= HOURS + 1e-9; t += 0.25) {
        let v = 1 + 0.08 * Math.sin(t * 3.1) + 0.05 * Math.sin(t * 7.3);
        if (t >= s) {
          const dt = t - s;
          v += 4.6 * Math.min(dt / 0.5, 1) * Math.exp(-Math.max(dt - 0.5, 0) / 2.4);
        }
        if (alertT === null && v > THRESH) alertT = t;
        pts.push([x(t), y(v)]);
      }
      const evEnd = Math.min(s + DURATION, HOURS);
      const labs = [];
      for (let t = interval; t <= HOURS; t += interval) labs.push(t);
      const caught = labs.find(t => t >= s && t <= s + DURATION);
      const nextLab = labs.find(t => t >= s);

      let html = "";
      html += `<rect x="${x(s)}" y="${T}" width="${x(evEnd) - x(s)}" height="${H - T - B + 30}" class="g-event"/>`;
      html += `<text x="${x(s) + 6}" y="${T + 12}" class="g-label ev">Contamination event</text>`;
      html += `<line x1="${L}" y1="${y(THRESH)}" x2="${W - R}" y2="${y(THRESH)}" class="g-thresh"/>`;
      html += `<text x="${W - R}" y="${y(THRESH) - 5}" text-anchor="end" class="g-label thr">Alert threshold</text>`;
      html += `<path d="M${pts.map(p => p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" L")}" class="g-line"/>`;
      if (alertT !== null) {
        html += `<circle cx="${x(alertT)}" cy="${y(THRESH)}" r="6" class="g-alert"/>`;
        html += `<text x="${Math.min(x(alertT) + 10, W - 110)}" y="${y(THRESH) + 20}" class="g-label alert">Sensor alert</text>`;
      }
      // axis + ticks
      html += `<line x1="${L}" y1="${H - B + 30}" x2="${W - R}" y2="${H - B + 30}" class="g-axis"/>`;
      for (let t = 0; t <= HOURS; t += 12) {
        html += `<text x="${x(t)}" y="${H - 2}" text-anchor="middle" class="g-label tick">${t}h</text>`;
      }
      // lab sample row
      html += `<text x="${L - 6}" y="${labY + 4}" text-anchor="end" class="g-label lab">Lab</text>`;
      labs.forEach(t => {
        const hit = t >= s && t <= s + DURATION;
        html += `<circle cx="${x(t)}" cy="${labY}" r="6" class="${hit ? "g-lab hit" : "g-lab"}"/>`;
      });
      svg.innerHTML = html;

      // readouts
      const sensorEl = $("#gap-sensor"), labEl = $("#gap-lab");
      if (alertT !== null) {
        const mins = Math.max(1, Math.round((alertT - s) * 60));
        sensorEl.textContent = `Flags the change about ${mins} minutes after it starts.`;
      } else {
        sensorEl.textContent = "No alert in this window.";
      }
      if (caught !== undefined) {
        labEl.textContent = `A sample happens to fall inside the event (hour ${caught}), and its result still takes time to come back.`;
      } else if (nextLab !== undefined) {
        labEl.textContent = `No sample falls inside the event. The next one is at hour ${nextLab}, after the water has changed back.`;
      } else {
        labEl.textContent = "No sample is taken before this window ends. The event passes unseen.";
      }
    }
    startIn.addEventListener("input", draw);
    ivSel.addEventListener("change", draw);
    draw();
  }

  // ---------------------------------------------------------------- live detector illustration
  function initLive() {
    const svg = $("#live-svg");
    if (!svg) return;
    const N = 90, W = 720, H = 240, L = 44, R = 12, T = 14, B = 26, VMAX = 7;
    const x = i => L + (W - L - R) * i / (N - 1);
    const y = v => T + (H - T - B) * (1 - Math.min(Math.max(v, 0), VMAX) / VMAX);
    const statusEl = $("#live-status"), msgEl = $("#live-message"), statsEl = $("#live-stats");
    const toggleBtn = $("#live-toggle");

    let data = [], eventAge = null, eventStart = null, detectedAt = null, tick = 0;
    let running = !reduceMotion, timer = null;

    function gauss() {                               // Box-Muller
      return Math.sqrt(-2 * Math.log(1 - Math.random())) * Math.cos(2 * Math.PI * Math.random());
    }
    function nextValue() {
      let v = 1.5 + gauss() * 0.1;
      if (eventAge !== null) {
        v += 4.5 * Math.min(eventAge / 5, 1) * Math.pow(0.94, Math.max(eventAge - 5, 0));
        eventAge++;
        if (eventAge > 90) eventAge = null;
      }
      return Math.max(v, 0.1);
    }
    const median = a => { const s = [...a].sort((p, q) => p - q), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };

    function analyse() {
      const recent = data.slice(-6), base = data.slice(0, -6);
      const med = median(base);
      const sigma = Math.max(median(base.map(v => Math.abs(v - med))) * 1.4826, 0.08);
      const now = median(recent);
      const z = (now - med) / sigma;
      return { med, sigma, now, z, flagged: Math.abs(z) > 4 };
    }

    function render() {
      const a = analyse();
      const band = `<rect x="${L}" y="${y(a.med + 3 * a.sigma)}" width="${W - L - R}" height="${y(a.med - 3 * a.sigma) - y(a.med + 3 * a.sigma)}" class="l-band"/>`;
      const grid = [0, 2, 4, 6].map(v =>
        `<line x1="${L}" y1="${y(v)}" x2="${W - R}" y2="${y(v)}" class="l-grid"/><text x="${L - 8}" y="${y(v) + 4}" text-anchor="end" class="g-label tick">${v}</text>`).join("");
      const path = data.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
      const last = data.length - 1;
      svg.innerHTML = grid + band +
        `<text x="${W - R - 4}" y="${y(a.med + 3 * a.sigma) - 5}" text-anchor="end" class="g-label thr">usual range</text>` +
        `<path d="${path}" class="${a.flagged ? "l-line flagged" : "l-line"}"/>` +
        `<circle cx="${x(last)}" cy="${y(data[last])}" r="5" class="${a.flagged ? "l-dot flagged" : "l-dot"}"/>` +
        `<text x="${L - 34}" y="${T + 4}" class="g-label tick">NTU</text>`;

      statusEl.className = "badge " + (a.flagged ? "red" : "green");
      statusEl.textContent = a.flagged ? "Unusual change detected, test recommended" : "No unusual change detected";
      if (a.flagged) {
        if (detectedAt === null) detectedAt = tick;
        const lag = eventStart !== null ? detectedAt - eventStart : null;
        msgEl.textContent = `Turbidity is about ${a.now.toFixed(1)} NTU against a usual ${a.med.toFixed(1)}. ` +
          "Inspect the tank and send a sample for lab testing." +
          (lag !== null ? ` Flagged ${lag} reading${lag === 1 ? "" : "s"} after the disturbance began.` : "");
      } else {
        detectedAt = null;
        msgEl.textContent = eventAge !== null ? "Something is changing, checking against the tank's own history..."
          : "Turbidity is steady around its usual level.";
      }
      statsEl.textContent = `usual ${a.med.toFixed(2)} NTU · now ${a.now.toFixed(2)} NTU · ${a.z >= 0 ? "+" : ""}${a.z.toFixed(1)} σ`;
    }

    function reset() {
      data = []; eventAge = null; eventStart = null; detectedAt = null;
      for (let i = 0; i < N; i++) data.push(nextValue());
      render();
    }
    function step() {
      tick++;
      data.push(nextValue());
      if (data.length > N) data.shift();
      render();
    }
    function setRunning(on) {
      running = on;
      clearInterval(timer);
      if (on) timer = setInterval(step, 450);
      toggleBtn.textContent = on ? "Pause" : "Play";
      toggleBtn.setAttribute("aria-pressed", String(!on));
    }

    $("#live-event").addEventListener("click", () => {
      if (eventAge === null) { eventAge = 0; eventStart = tick; detectedAt = null; }
      if (!running) setRunning(true);
    });
    $("#live-reset").addEventListener("click", reset);
    toggleBtn.addEventListener("click", () => setRunning(!running));

    // pause while the figure is off-screen so it does not run in the background
    if ("IntersectionObserver" in window) {
      new IntersectionObserver(entries => {
        entries.forEach(e => { if (!e.isIntersecting && running) { setRunning(false); svg.dataset.autopaused = "1"; }
                               else if (e.isIntersecting && svg.dataset.autopaused) { svg.dataset.autopaused = ""; setRunning(true); } });
      }, { threshold: 0.1 }).observe(svg);
    }
    reset();
    setRunning(running);
  }

  // ---------------------------------------------------------------- tabs
  function initTabs() {
    const tabs = $$("[role=tab]");
    function select(tab, focus) {
      tabs.forEach(t => {
        const on = t === tab;
        t.setAttribute("aria-selected", String(on));
        t.tabIndex = on ? 0 : -1;
        $("#" + t.getAttribute("aria-controls")).hidden = !on;
      });
      if (focus) tab.focus();
    }
    tabs.forEach((t, i) => {
      t.addEventListener("click", () => select(t, false));
      t.addEventListener("keydown", e => {
        const k = e.key;
        if (k === "ArrowRight") select(tabs[(i + 1) % tabs.length], true);
        else if (k === "ArrowLeft") select(tabs[(i - 1 + tabs.length) % tabs.length], true);
      });
    });
  }

  initGap();
  initLive();
  initTabs();
})();
