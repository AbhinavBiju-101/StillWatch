// StillWatch operator app: small progressive enhancements. Every page works without this file.
(function () {
  "use strict";
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

  // sparklines on the tank cards
  $$("svg[data-spark]").forEach(svg => {
    const v = svg.dataset.spark.split(",").map(Number).filter(n => !Number.isNaN(n));
    if (v.length < 2) return;
    const lo = Math.min(...v), hi = Math.max(...v), span = Math.max(hi - lo, 0.5);
    const pts = v.map((n, i) => [i * 100 / (v.length - 1), 40 - ((n - lo) / span) * 34]);
    const line = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
    svg.querySelector(".ln").setAttribute("d", line);
    svg.querySelector(".area").setAttribute("d", line + " L100 44 L0 44 Z");
  });

  // alerts filter
  const filter = document.getElementById("alert-filter");
  if (filter) {
    const cards = $$("#alert-list .alert-card"), none = document.getElementById("alert-none");
    function apply(f) {
      $$("button", filter).forEach(b => b.setAttribute("aria-pressed", String(b.dataset.f === f)));
      let shown = 0;
      cards.forEach(c => { const show = f === "all" || c.dataset.state === f; c.hidden = !show; if (show) shown++; });
      if (none) none.hidden = shown > 0;
    }
    $$("button", filter).forEach(b => b.addEventListener("click", () => apply(b.dataset.f)));
    apply(filter.dataset.default || "all");
  }
})();

/* lab form: add a parameter row, fill the unit from the datalist */
(function () {
  var add = document.getElementById('add-param');
  if (!add) return;
  var rows = document.getElementById('param-rows');
  var tpl = document.getElementById('param-row-tpl');
  var units = {};
  document.querySelectorAll('#param-list option').forEach(function (o) { units[o.value] = o.dataset.unit || ''; });
  add.addEventListener('click', function () {
    rows.appendChild(tpl.content.cloneNode(true));
    rows.querySelector('.param-row:last-child input[name=parameter]').focus();
  });
  rows.addEventListener('change', function (e) {
    if (e.target.name !== 'parameter' || e.target.type === 'hidden') return;
    var u = units[e.target.value.trim().toLowerCase().replace(/ /g, '_')];
    var unit = e.target.closest('.param-row').querySelector('input[name=unit]');
    if (u !== undefined && !unit.value) unit.value = u;
  });
})();
