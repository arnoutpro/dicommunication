// Status dashboard: render one tile per rule from /lite/api/status, every 10 s.
(function () {
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function ago(s) {
    if (s < 90) return s + " s ago";
    if (s < 5400) return Math.round(s / 60) + " min ago";
    return Math.round(s / 3600) + " h ago";
  }
  var SVGNS = "http://www.w3.org/2000/svg";
  var history = { days: [], ranges: [7, 14, 30], rules: [] };
  var range = 7;
  try { range = parseInt(localStorage.getItem("liteRange"), 10) || 7; } catch (e) { range = 7; }
  function svg(tag, attrs, text) {
    var node = document.createElementNS(SVGNS, tag);
    Object.keys(attrs || {}).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function niceMax(n) {
    if (n <= 4) return Math.max(n, 1);
    var pow = Math.pow(10, Math.floor(Math.log10(n)));
    return [1, 1.5, 2, 3, 4, 5, 6, 8, 10].find(function (m) { return m * pow >= n; }) * pow;
  }
  function dayLabel(iso, index, total) {
    var d = new Date(iso + "T00:00:00");
    if (total <= 7) return [d.toLocaleDateString(undefined, { weekday: "short" }), String(d.getDate())];
    var every = total <= 14 ? 2 : 5;
    return (total - 1 - index) % every === 0 ? [String(d.getDate()), String(d.getMonth() + 1)].join("/") : "";
  }
  function chart(h) {
    var box = el("div", "chart");
    box.appendChild(el("div", "chart-title", "Exams per day, last " + history.days.length + " days"));
    var n = history.days.length;
    var observed = h.totals.filter(function (v) { return v !== null; });
    if (!observed.length) {
      box.appendChild(el("div", "msg mute", "No history yet. It builds up as the rule runs."));
      return box;
    }
    var statuses = Object.keys(h.series);
    var W = 240, H = 110, left = 22, top = 12, bottom = 24;
    var plotH = H - top - bottom, plotW = W - left;
    var max = niceMax(Math.max.apply(null, observed));
    var s = svg("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": "Exams per day for " + h.rule_name });
    [0, max].forEach(function (g) {
      var y = top + plotH - (g / max) * plotH;
      s.appendChild(svg("line", { class: "axis", x1: left, x2: W, y1: y, y2: y }));
      s.appendChild(svg("text", { x: left - 3, y: y + 3, "text-anchor": "end" }, String(g)));
    });
    var slot = plotW / n, bw = Math.max(2, slot * 0.7);
    history.days.forEach(function (day, i) {
      var x = left + i * slot + (slot - bw) / 2;
      var total = h.totals[i];
      var group = svg("g", {});
      if (total === null) {
        group.appendChild(svg("line", { class: "none", x1: x, x2: x + bw, y1: top + plotH - 1, y2: top + plotH - 1 }));
        group.appendChild(svg("title", {}, day + ": no data"));
      } else {
        var base = top + plotH;
        statuses.forEach(function (st, k) {
          var v = h.series[st][i] || 0;
          if (!v) return;
          var bh = (v / max) * plotH;
          base -= bh;
          group.appendChild(svg("rect", { class: "c" + (k % 6), x: x, y: base, width: bw, height: bh }));
        });
        var parts = statuses.filter(function (st) { return h.series[st][i]; }).map(function (st) { return (st === "(none)" ? "No status returned" : st) + " " + h.series[st][i]; });
        group.appendChild(svg("title", {}, day + ": " + total + (h.keyed && parts.length ? " (" + parts.join(", ") + ")" : "")));
        if (n <= 14) group.appendChild(svg("text", { class: "val", x: x + bw / 2, y: base - 2, "text-anchor": "middle" }, String(total)));
      }
      s.appendChild(group);
      var label = dayLabel(day, i, n);
      if (Array.isArray(label)) {
        s.appendChild(svg("text", { x: x + bw / 2, y: H - 12, "text-anchor": "middle" }, label[0]));
        s.appendChild(svg("text", { x: x + bw / 2, y: H - 3, "text-anchor": "middle" }, label[1]));
      } else if (label) {
        s.appendChild(svg("text", { x: x + bw / 2, y: H - 3, "text-anchor": "middle" }, label));
      }
    });
    box.appendChild(s);
    if (h.keyed && statuses.length > 1) {
      var legend = el("div", "legend");
      statuses.forEach(function (st, k) {
        var item = el("span", "");
        var dot = el("i", "c" + (k % 6));
        item.appendChild(dot);
        item.appendChild(document.createTextNode(st === "(none)" ? "No status returned" : st));
        legend.appendChild(item);
      });
      box.appendChild(legend);
    }
    return box;
  }
  function chartFor(x) {
    var h = history.rules.filter(function (r) { return r.rule_id === x.rule_id; })[0];
    return h ? chart(h) : null;
  }
  function tile(x) {
    var t = tileBody(x);
    var c = chartFor(x);
    if (c) t.appendChild(c);
    return t;
  }
  function tileBody(x) {
    var t = el("div", "tile " + x.state);
    t.appendChild(el("div", "name", x.rule_name));
    if (x.state === "waiting") {
      t.appendChild(el("div", "msg mute", "Waiting for the first check"));
      return t;
    }
    t.appendChild(el("div", "meta", "Checked " + ago(x.age_seconds) + " · " + x.total + " exams"));
    if (x.state === "error") {
      t.appendChild(el("div", "msg", x.error || "Query failed"));
      return t;
    }
    var entries = Object.keys(x.by_status || {});
    if (!entries.length) entries = null;
    (entries || ["No exams"]).forEach(function (k) {
      var item = el("div", "item");
      var row = el("div", "row");
      row.appendChild(el("span", "", k === "(none)" ? "No status returned" : k));
      row.appendChild(el("b", "", entries ? x.by_status[k] : 0));
      item.appendChild(row);
      var kinds = entries && x.modalities ? x.modalities[k] : null;
      if (kinds && Object.keys(kinds).length) {
        var mods = el("div", "mods");
        Object.keys(kinds).forEach(function (m) {
          var chip = el("span", "chip", m + " ");
          chip.appendChild(el("b", "", kinds[m]));
          mods.appendChild(chip);
        });
        item.appendChild(mods);
      }
      t.appendChild(item);
    });
    if (x.state === "stale") {
      t.appendChild(el("div", "msg mute", "No new check since " + ago(x.age_seconds)));
    }
    if (x.rule_status && x.rule_status !== "active") {
      t.appendChild(el("div", "msg mute", "Rule is " + x.rule_status));
    }
    return t;
  }
  function render(tiles) {
    var bad = tiles.filter(function (x) { return x.state === "error"; }).length;
    var quiet = tiles.filter(function (x) { return x.state === "stale"; }).length;
    document.body.className = bad ? "error" : (quiet || !tiles.length || tiles.some(function (x) { return x.state === "waiting"; }) ? "" : "ok");
    document.getElementById("summary").textContent = !tiles.length ? "No Dicom Router rules yet" :
      bad ? bad + " failing" : quiet ? quiet + " without recent data" : "All checks running";
    var main = document.getElementById("tiles");
    main.replaceChildren();
    if (!tiles.length) {
      main.appendChild(el("div", "empty", "Add a Dicom Router rule to see its latest result here."));
    }
    tiles.forEach(function (x) { main.appendChild(tile(x)); });
  }
  function load() {
    fetch("/lite/api/status", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) { lastTiles = d.tiles; render(d.tiles); })
      .catch(function () {
        document.getElementById("summary").textContent = "App unreachable";
        document.body.className = "";
      });
  }
  var lastTiles = [];
  function loadHistory() {
    fetch("/lite/api/history?days=" + range, { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) { history = d; renderRange(); render(lastTiles); })
      .catch(function () {});
  }
  function renderRange() {
    var box = document.getElementById("range");
    if (!box) return;
    box.replaceChildren();
    (history.ranges || [7, 14, 30]).forEach(function (n) {
      var b = el("button", "", n + " days");
      b.type = "button";
      b.setAttribute("aria-pressed", n === range ? "true" : "false");
      b.addEventListener("click", function () {
        range = n;
        try { localStorage.setItem("liteRange", String(n)); } catch (e) { /* not available */ }
        loadHistory();
      });
      box.appendChild(b);
    });
  }
  load();
  loadHistory();
  setInterval(load, 10000);
  setInterval(loadHistory, 60000);
})();
