// StillWatch overview deck. Vanilla JS, no libraries. Everything interactive here is a simplified
// illustration with simulated numbers; it is not the production detector.
(function () {
  "use strict";
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const NS = "http://www.w3.org/2000/svg";

  /** Roving-tabindex tab list: arrows move and select. */
  function tablist(tabs, onSelect) {
    function select(tab, focus) {
      tabs.forEach(t => { const on = t === tab; t.setAttribute("aria-selected", String(on)); t.tabIndex = on ? 0 : -1; });
      if (focus) tab.focus();
      onSelect(tab);
    }
    tabs.forEach((t, i) => {
      t.addEventListener("click", () => select(t, false));
      t.addEventListener("keydown", e => {
        if (e.key === "ArrowRight" || e.key === "ArrowDown") { e.preventDefault(); select(tabs[(i + 1) % tabs.length], true); }
        else if (e.key === "ArrowLeft" || e.key === "ArrowUp") { e.preventDefault(); select(tabs[(i - 1 + tabs.length) % tabs.length], true); }
      });
    });
    select(tabs.find(t => t.getAttribute("aria-selected") === "true") || tabs[0], false);
  }

  /** Visible-only helper: calls cb(true/false) as the element enters and leaves the viewport. */
  function whenVisible(el, cb, threshold = 0.12) {
    if (!("IntersectionObserver" in window)) { cb(true); return; }
    new IntersectionObserver(es => es.forEach(e => cb(e.isIntersecting)), { threshold }).observe(el);
  }

  // ---------------------------------------------------------------- deck chrome
  function initDeck() {
    const slides = $$(".slide"), rail = $("#rail"), body = document.body;
    const cn = $("#count-n"), ct = $("#count-t"), ctitle = $("#count-title");
    const navLinks = $$(".deck-nav nav a");
    ct.textContent = String(slides.length).padStart(2, "0");
    slides.forEach((s, i) => {
      const a = document.createElement("a");
      a.href = "#" + s.id; a.setAttribute("aria-label", s.dataset.title || "Slide " + (i + 1));
      a.title = s.dataset.title || "";
      rail.appendChild(a);
    });
    const dots = $$("a", rail);
    let current = -1;
    function update() {
      const mid = window.innerHeight * 0.45;
      let idx = 0;
      slides.forEach((s, i) => { if (s.getBoundingClientRect().top <= mid) idx = i; });
      if (idx === current) return;
      current = idx;
      const s = slides[idx];
      cn.textContent = String(idx + 1).padStart(2, "0");
      ctitle.textContent = s.dataset.title || "";
      dots.forEach((d, i) => i === idx ? d.setAttribute("aria-current", "true") : d.removeAttribute("aria-current"));
      navLinks.forEach(l => l.getAttribute("href") === "#" + s.id ? l.setAttribute("aria-current", "true") : l.removeAttribute("aria-current"));
      const dark = s.classList.contains("dark");
      body.classList.toggle("on-dark", dark); body.classList.toggle("on-light", !dark);
    }
    let raf = 0;
    window.addEventListener("scroll", () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; update(); }); }, { passive: true });
    window.addEventListener("resize", update);
    update();

    // PageDown / PageUp / arrow left-right step through slides on wide screens (up/down stay native).
    document.addEventListener("keydown", e => {
      if (e.altKey || e.ctrlKey || e.metaKey || window.innerWidth <= 900) return;
      const t = e.target;
      if (t && (/^(INPUT|SELECT|TEXTAREA)$/.test(t.tagName) || t.closest("[role=tablist]"))) return;
      let to = null;
      if (e.key === "PageDown" || e.key === "ArrowRight") to = Math.min(current + 1, slides.length - 1);
      else if (e.key === "PageUp" || e.key === "ArrowLeft") to = Math.max(current - 1, 0);
      else if (e.key === "Home") to = 0; else if (e.key === "End") to = slides.length - 1;
      if (to === null) return;
      e.preventDefault();
      slides[to].scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
    });
  }

  // ---------------------------------------------------------------- glossary tooltips
  function initTerms() {
    const TERMS = {
      ntu: "NTU (nephelometric turbidity units): how cloudy the water is from suspended particles. Higher is cloudier.",
      tds: "TDS (total dissolved solids): a rough measure of everything dissolved in the water, in parts per million.",
      orp: "ORP (oxidation-reduction potential): a hint of how much disinfectant is still active in the water.",
    };
    const tip = $("#tip");
    function show(el) {
      tip.textContent = TERMS[el.dataset.term]; tip.hidden = false;
      const r = el.getBoundingClientRect(), tw = tip.offsetWidth, th = tip.offsetHeight;
      const left = Math.min(Math.max(8, r.left + r.width / 2 - tw / 2), window.innerWidth - tw - 8);
      const top = r.top - th - 10 > 64 ? r.top - th - 10 : r.bottom + 10;
      tip.style.left = left + "px"; tip.style.top = top + "px";
    }
    const hide = () => { tip.hidden = true; };
    $$(".term").forEach(el => {
      el.addEventListener("mouseenter", () => show(el)); el.addEventListener("mouseleave", hide);
      el.addEventListener("focus", () => show(el)); el.addEventListener("blur", hide);
      el.addEventListener("click", () => show(el));
    });
    document.addEventListener("keydown", e => { if (e.key === "Escape") hide(); });
    window.addEventListener("scroll", hide, { passive: true });
  }

  // ---------------------------------------------------------------- hero: touch the water
  function initHero() {
    const zone = $("#water-zone"), cv = $("#water"), hero = $("#top");
    if (!zone || !cv) return;
    const ctx = cv.getContext("2d");
    const SC = 3, DAMP = 0.985;
    let w = 0, h = 0, cur, prev, img, running = false, visible = true, quiet = 0, lastDrop = 0;

    function size() {
      const r = zone.getBoundingClientRect();
      w = Math.max(40, Math.floor(r.width / SC)); h = Math.max(24, Math.floor(r.height / SC));
      cv.width = w; cv.height = h;
      cur = new Float32Array(w * h); prev = new Float32Array(w * h); img = ctx.createImageData(w, h);
    }
    function drop(cx, cy, strength, radius) {
      const r2 = radius * radius;
      for (let y = Math.max(1, cy - radius); y < Math.min(h - 1, cy + radius); y++) {
        for (let x = Math.max(1, cx - radius); x < Math.min(w - 1, cx + radius); x++) {
          const d2 = (x - cx) * (x - cx) + (y - cy) * (y - cy);
          if (d2 < r2) cur[y * w + x] -= strength * (1 - d2 / r2);
        }
      }
      quiet = 0; start();
    }
    function step() {
      let energy = 0;
      for (let y = 1; y < h - 1; y++) {
        for (let x = 1; x < w - 1; x++) {
          const i = y * w + x;
          const n = (cur[i - 1] + cur[i + 1] + cur[i - w] + cur[i + w]) * 0.5 - prev[i];
          prev[i] = n * DAMP; energy += Math.abs(n);
        }
      }
      const t = cur; cur = prev; prev = t;   // cur is now the newest field, prev the one before
      return energy / (w * h);
    }
    function paint() {
      const d = img.data;
      for (let y = 1; y < h - 1; y++) {
        for (let x = 1; x < w - 1; x++) {
          const i = y * w + x, p = i * 4;
          const v = (cur[i - 1] - cur[i + 1]) * 0.9 + (cur[i - w] - cur[i + w]) * 1.1;
          if (v > 0) { d[p] = 225; d[p + 1] = 250; d[p + 2] = 247; d[p + 3] = Math.min(200, v * 3.4); }
          else { d[p] = 4; d[p + 1] = 38; d[p + 2] = 48; d[p + 3] = Math.min(110, -v * 1.5); }
        }
      }
      ctx.putImageData(img, 0, 0);
    }
    function frame() {
      if (!running) return;
      const e = step(); paint();
      quiet = e < 0.05 ? quiet + 1 : 0;
      if (quiet > 30 || !visible) { running = false; ctx.clearRect(0, 0, w, h); return; }
      requestAnimationFrame(frame);
    }
    function start() { if (!running && visible && !reduceMotion) { running = true; requestAnimationFrame(frame); } }

    // pointer -> drops
    let dist = 0;
    const hint = $("#hero-hint");
    function toGrid(e) {
      const r = cv.getBoundingClientRect();
      if (e.clientY < r.top || e.clientY > r.bottom || e.clientX < r.left || e.clientX > r.right) return null;
      return [Math.floor((e.clientX - r.left) / r.width * w), Math.floor((e.clientY - r.top) / r.height * h)];
    }
    hero.addEventListener("pointermove", e => {
      const now = performance.now(); if (now - lastDrop < 45) return;
      const g = toGrid(e); if (!g) return;
      lastDrop = now; drop(g[0], g[1], 190, 3); dist = Math.min(1, dist + 0.07); touched();
    });
    hero.addEventListener("pointerdown", e => {
      const g = toGrid(e); if (!g) return;
      drop(g[0], g[1], 650, 6); dist = Math.min(1, dist + 0.42); touched();
    });
    function touched() { if (hint) hint.classList.add("gone"); }

    // readout
    const ntuEl = $("#hero-ntu"), badge = $("#hero-badge"), note = $("#hero-note"), spark = $("#hero-spark"), card = $(".hero-read");
    const hist = new Array(48).fill(1.5);
    let hadEvent = false;
    function tick() {
      dist *= 0.94; if (dist < 0.004) dist = 0;
      const ntu = 1.5 + 4.4 * dist + (Math.random() - 0.5) * 0.06;
      hist.push(ntu); hist.shift();
      ntuEl.textContent = ntu.toFixed(1);
      const flagged = dist > 0.28;
      badge.className = "badge " + (flagged ? "red" : "green");
      badge.textContent = flagged ? "Unusual change detected, test recommended" : "No unusual change detected";
      card.classList.toggle("alert", flagged);
      if (flagged) { hadEvent = true; note.textContent = "Turbidity is well above this tank's usual 1.5 NTU. Inspect the tank and send a sample to a lab."; }
      else note.textContent = hadEvent ? "Back to its usual level. The event stays on record for the operator." : "Still water. Nothing to report.";
      spark.setAttribute("d", hist.map((v, i) => (i ? "L" : "M") + (i * 160 / 47).toFixed(1) + " " + (33 - Math.min(Math.max((v - 1) / 5.5, 0), 1) * 31).toFixed(1)).join(" "));
    }
    let timer = null;
    function setTimer(on) { clearInterval(timer); if (on) timer = setInterval(tick, 250); }

    size(); tick();
    let rt; window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(size, 200); });
    whenVisible(hero, v => { visible = v; setTimer(v); if (v && !reduceMotion) start(); }, 0.05);
    if (!reduceMotion) setTimeout(() => drop(Math.floor(w * 0.64), Math.floor(h * 0.4), 520, 6), 900);   // one intro ripple
  }

  // ---------------------------------------------------------------- problem: which signals notice which cause
  function initCauses() {
    const tabs = $$(".causes [role=tab]"); if (!tabs.length) return;
    const CAUSES = {
      sed: { text: "Sediment is stirred up and the tank warms. The water turns cloudier, and warmth speeds up everything else.", on: ["turb", "temp"] },
      bio: { text: "Biofilm grows on the walls while disinfectant fades. The change is slow, so no single reading looks alarming.", on: ["orp", "tds"] },
      run: { text: "Rain runoff, animals or a pipe leak get in. Cloudiness, acidity and dissolved matter can all move at once.", on: ["turb", "ph", "tds"] },
    };
    const text = $("#cause-text"), sigs = $$(".sig");
    tablist(tabs, tab => {
      const c = CAUSES[tab.dataset.cause];
      text.textContent = c.text;
      sigs.forEach(s => {
        const on = c.on.includes(s.dataset.sig);
        s.classList.toggle("on", on);
        s.classList.toggle("later", on && (s.dataset.sig === "temp" || s.dataset.sig === "orp"));
      });
    });
  }

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
      const s = +startIn.value || 30, interval = +ivSel.value || 24;
      $("#gap-start-out").textContent = s;
      const pts = [];
      let alertT = null;
      for (let t = 0; t <= HOURS + 1e-9; t += 0.25) {
        let v = 1 + 0.08 * Math.sin(t * 3.1) + 0.05 * Math.sin(t * 7.3);
        if (t >= s) { const dt = t - s; v += 4.6 * Math.min(dt / 0.5, 1) * Math.exp(-Math.max(dt - 0.5, 0) / 2.4); }
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
      html += `<path pathLength="1" d="M${pts.map(p => p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" L")}" class="g-line"/>`;
      if (alertT !== null) {
        html += `<circle cx="${x(alertT)}" cy="${y(THRESH)}" r="6" class="g-alert"/>`;
        html += `<text x="${Math.min(x(alertT) + 10, W - 110)}" y="${y(THRESH) + 20}" class="g-label alert">Sensor alert</text>`;
      }
      html += `<line x1="${L}" y1="${H - B + 30}" x2="${W - R}" y2="${H - B + 30}" class="g-axis"/>`;
      for (let t = 0; t <= HOURS; t += 12) html += `<text x="${x(t)}" y="${H - 2}" text-anchor="middle" class="g-label tick">${t}h</text>`;
      html += `<text x="${L - 6}" y="${labY + 4}" text-anchor="end" class="g-label lab">Lab</text>`;
      labs.forEach(t => { const hit = t >= s && t <= s + DURATION; html += `<circle cx="${x(t)}" cy="${labY}" r="6" class="${hit ? "g-lab hit" : "g-lab"}"/>`; });
      svg.innerHTML = html;

      const sensorEl = $("#gap-sensor"), labEl = $("#gap-lab");
      if (alertT !== null) sensorEl.textContent = `Flags the change about ${Math.max(1, Math.round((alertT - s) * 60))} minutes after it starts.`;
      else sensorEl.textContent = "No alert in this window.";
      if (caught !== undefined) labEl.textContent = `A sample happens to fall inside the event (hour ${caught}), and its result still takes time to come back.`;
      else if (nextLab !== undefined) labEl.textContent = `No sample falls inside the event. The next one is at hour ${nextLab}, after the water has changed back.`;
      else labEl.textContent = "No sample is taken before this window ends. The event passes unseen.";
    }
    startIn.addEventListener("input", draw);
    ivSel.addEventListener("change", draw);
    draw();
    // draw the trace once, the first time the slide is seen; later redraws stay still while dragging
    svg.classList.remove("intro");
    if (!reduceMotion) {
      let done = false;
      whenVisible(svg, v => { if (v && !done) { done = true; svg.classList.add("intro"); draw(); setTimeout(() => svg.classList.remove("intro"), 1800); } }, 0.4);
    }
  }

  // ---------------------------------------------------------------- who tests today
  function initTesters() {
    const root = $("#testers"); if (!root) return;
    const TICKS = { util: [40, 150, 260, 370], pub: [70, 300], nabl: [100, 210, 320], field: [18, 40, 62, 250] };
    $$(".trow[data-row]").forEach(row => {
      const svg = $(".strip", row);
      let html = '<line class="base" x1="0" y1="10" x2="400" y2="10"/>';
      TICKS[row.dataset.row].forEach(t => { html += `<line class="tk" x1="${t}" y1="3" x2="${t}" y2="17"/>`; });
      svg.innerHTML = html;
      const btn = $(".trow-btn", row), body = $(".trow-body", row);
      btn.addEventListener("click", () => {
        const open = btn.getAttribute("aria-expanded") === "true";
        $$(".trow[data-row]", root).forEach(r => { $(".trow-btn", r).setAttribute("aria-expanded", "false"); $(".trow-body", r).hidden = true; });
        if (!open) { btn.setAttribute("aria-expanded", "true"); body.hidden = false; }
      });
    });
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

    function gauss() { return Math.sqrt(-2 * Math.log(1 - Math.random())) * Math.cos(2 * Math.PI * Math.random()); }
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
        msgEl.textContent = eventAge !== null ? "Something is changing. Checking against the tank's own history..."
          : "Turbidity is steady around its usual level.";
      }
      statsEl.textContent = `usual ${a.med.toFixed(2)} NTU · now ${a.now.toFixed(2)} NTU · ${a.z >= 0 ? "+" : ""}${a.z.toFixed(1)} σ`;
    }

    function reset() { data = []; eventAge = null; eventStart = null; detectedAt = null; for (let i = 0; i < N; i++) data.push(nextValue()); render(); }
    function step() { tick++; data.push(nextValue()); if (data.length > N) data.shift(); render(); }
    function setRunning(on) {
      running = on; clearInterval(timer);
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

    // pause while off-screen so it does not run in the background
    if ("IntersectionObserver" in window) {
      new IntersectionObserver(entries => {
        entries.forEach(e => { if (!e.isIntersecting && running) { setRunning(false); svg.dataset.autopaused = "1"; }
                               else if (e.isIntersecting && svg.dataset.autopaused) { svg.dataset.autopaused = ""; setRunning(true); } });
      }, { threshold: 0.1 }).observe(svg);
    }
    reset();
    setRunning(running);
  }

  // ---------------------------------------------------------------- pipeline
  function initPipe() {
    const tabs = $$("#pipe [role=tab]"); if (!tabs.length) return;
    const STAGES = {
      sense: ["Tank sensors", "pH, TDS and turbidity probes hang in the water at mid-depth. Temperature and ORP probes are planned.", "Only the probes touch the water. The electronics stay dry."],
      node: ["ESP32 node", "Reads each sensor many times and averages the noise out, applies calibration, and keeps the raw values too.", "A local LED and buzzer still work when the network does not."],
      msg: ["JSON over WiFi", "One small message every few minutes, in the same format for every device, so firmware and backend can change independently.", "Unsent readings are kept and retried, so insights are delayed, never lost."],
      back: ["Backend", "Stores every reading, together with the events (cleaning, refill, rain) and lab results that operators log.", "Raw values are kept, so old readings can be recalibrated later."],
      brain: ["Detectors and explainer", "Small, explainable checks compare each tank with its own recent behaviour. When one fires, a language model turns the findings into a plain explanation and a suggested next step.", "The explainer is not allowed to call any water safe."],
      alert: ["Alerts", "The operator sees a status for every tank, why it changed and what to do. They mark each alert as a real problem or a false alarm, which teaches the system. WhatsApp and SMS are planned for people who will not open a dashboard.", "Alerts go to the person responsible, not to residents."],
    };
    const h = $("#pd-h"), p = $("#pd-p"), k = $("#pd-k");
    tablist(tabs, tab => { const s = STAGES[tab.dataset.stage]; h.textContent = s[0]; p.textContent = s[1]; k.textContent = s[2]; });
  }

  // ---------------------------------------------------------------- node probes
  function initProbes() {
    const probes = $$(".probe"); if (!probes.length) return;
    const INFO = {
      ph: ["pH probe", "Acidity or alkalinity. A sudden shift can hint at runoff, a leak or something added to the tank.", "What caused the change, or whether anything harmful is present."],
      tds: ["TDS probe", "The overall amount of dissolved material. A rise can mean salts or minerals are entering the water.", "Which substances are dissolved. It cannot see individual contaminants."],
      turbidity: ["Turbidity sensor", "Cloudiness from suspended particles: sediment stirred up, runoff, or material shed from the tank walls.", "Clear water is not a certificate. Many problems do not make water cloudy."],
      next: ["Coming next: temperature and ORP", "Temperature shows how warmth changes the tank and helps explain drifts in other readings. ORP hints at how quickly a disinfectant is fading.", "Neither can say what is in the water. They add context, not a verdict."],
    };
    const h = $("#pc-h"), yes = $("#pc-yes"), no = $("#pc-no");
    function show(el) {
      probes.forEach(p => p.classList.toggle("active", p === el));
      const i = INFO[el.dataset.probe]; h.textContent = i[0]; yes.textContent = i[1]; no.textContent = i[2];
    }
    probes.forEach(el => {
      el.addEventListener("click", () => show(el));
      el.addEventListener("mouseenter", () => show(el));
      el.addEventListener("focus", () => show(el));
      el.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); show(el); } });
    });
    show(probes[0]);
  }

  // ---------------------------------------------------------------- what it may say
  function initSay() {
    const boxes = $$("#say .say-box"); if (!boxes.length) return;
    const OUT = {
      a: "Used when a tank's readings are in line with its own recent behaviour. It is a statement about change, not about the quality of the water.",
      b: "Used when something moves outside the tank's usual behaviour. It means: go and look, and consider sending a sample to a lab.",
      never: "Blocked. The sensors measure proxies and cannot see bacteria, lead, arsenic, pesticides or fluoride. A message that claims otherwise is rejected before it reaches an operator.",
    };
    const out = $("#say-out");
    boxes.forEach(b => b.addEventListener("click", () => {
      out.textContent = OUT[b.dataset.say];
      if (b.dataset.say === "never" && !reduceMotion) { b.classList.remove("shake"); void b.offsetWidth; b.classList.add("shake"); }
    }));
  }

  // ---------------------------------------------------------------- roadmap
  function initRoadmap() {
    const btns = $$("#steps button"); if (!btns.length) return;
    const STEPS = [
      "Serial output only, with no WiFi and no server: tap water against soil or milk, vinegar against baking soda, raw readings recorded. Firmware for this bench check exists.",
      "A small backend that stores readings, runs the checks and serves an API for the node. It is tested with simulated devices before any hardware is connected.",
      "The full node firmware sends the agreed message over WiFi and retries until the server confirms, so a dropped connection loses nothing.",
      "The dashboard you can open from this page: tank status, history charts, alerts, events and lab results, on one site.",
      "Run the node in a bucket, then in a real tank, for several days. Lab samples taken along the way label which patterns mean trouble. Prototypes are used only on water that is not being drunk.",
    ];
    const p = $("#sd-p");
    function pick(b) {
      btns.forEach(x => x.setAttribute("aria-expanded", String(x === b)));
      p.textContent = STEPS[+b.dataset.step];
    }
    btns.forEach(b => b.addEventListener("click", () => pick(b)));
    pick(btns[0]);
  }

  initDeck();
  initTerms();
  initHero();
  initCauses();
  initGap();
  initTesters();
  initLive();
  initPipe();
  initProbes();
  initSay();
  initRoadmap();
})();
