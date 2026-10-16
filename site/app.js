/* Rarebird Forecast — static front end.
   Reads precomputed per-country JSON from ./data/ (built nightly by the
   GitHub Action). No build step, no keys. */
"use strict";

const map = L.map("map", { worldCopyJump: true }).setView([30, 0], 3);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 18,
  attribution: "&copy; OpenStreetMap contributors",
}).addTo(map);

const state = {
  regions: null,       // regions.json
  country: null,       // e.g. "GB"
  data: null,          // data/{CC}.json
  sub1: "", sub2: "",
  lifers: null,        // Set of common names the user has already seen
  lifersOnly: false,
  siteLayer: L.layerGroup().addTo(map),
  recentLayer: L.layerGroup().addTo(map),
  heat: null,
};

const $ = (id) => document.getElementById(id);

function colorFor(idx) {
  if (idx >= 80) return "#d7301f";
  if (idx >= 60) return "#f68a3c";
  if (idx >= 40) return "#f0d75a";
  if (idx >= 20) return "#7fcdbb";
  return "#2c7fb0";
}

async function loadJSON(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

/* ------------------------------------------------------------- pickers */

async function init() {
  try {
    state.regions = await loadJSON("data/regions.json");
  } catch {
    $("topSites").innerHTML =
      "<li>No data yet — run the pipeline (see README).</li>";
    return;
  }
  const countries = state.regions.countries;
  // keep only countries that have a forecast file
  let have = new Set();
  try {
    have = new Set((await loadJSON("data/meta.json")).countries);
  } catch {}
  const listed = have.size
    ? countries.filter((c) => have.has(c.code))
    : countries;
  for (const c of listed) {
    $("country").insertAdjacentHTML(
      "beforeend",
      `<option value="${c.code}">${c.name}</option>`
    );
  }
  $("country").addEventListener("change", onCountry);
  $("sub1").addEventListener("change", () => {
    state.sub1 = $("sub1").value;
    fillSub2();
    render();
  });
  $("sub2").addEventListener("change", () => {
    state.sub2 = $("sub2").value;
    render();
  });
  $("heatToggle").addEventListener("change", render);
  $("recentToggle").addEventListener("change", render);
  initLifers();
  loadSkill();

  // restore last selection (?c= deep link beats localStorage)
  const params = new URLSearchParams(location.search);
  const saved = params.get("c") || localStorage.getItem("rbf.country");
  if (saved && listed.some((c) => c.code === saved)) {
    $("country").value = saved;
    onCountry();
  }
}

async function onCountry() {
  state.country = $("country").value;
  localStorage.setItem("rbf.country", state.country);
  state.sub1 = state.sub2 = "";
  resetPicker($("sub1"));
  resetPicker($("sub2"));
  state.data = await loadJSON(`data/${state.country}.json`);

  $("feedLink").href = `feeds/${state.country}.xml`;
  $("feedLink").style.display = "";

  $("week").textContent = `forecast ${state.data.week}`;
  $("generated").textContent =
    `updated ${state.data.generated.slice(0, 16).replace("T", " ")}Z`;

  const kids = state.regions.children[state.country] || [];
  if (kids.length) {
    $("sub1").disabled = false;
    for (const r of kids) {
      $("sub1").insertAdjacentHTML(
        "beforeend",
        `<option value="${r.code}">${r.name}</option>`
      );
    }
  }
  render();
}

function resetPicker(sel) {
  sel.innerHTML = `<option value="">${sel.id === "sub1" ? "State / province…" : "County…"}</option>`;
  sel.disabled = true;
}

function fillSub2() {
  resetPicker($("sub2"));
  state.sub2 = "";
  if (!state.sub1) return;
  const kids = (state.regions.children[state.sub1] || []).filter(
    (r) => r.type === "subnational2"
  );
  if (!kids.length) return;
  $("sub2").disabled = false;
  for (const r of kids) {
    $("sub2").insertAdjacentHTML(
      "beforeend",
      `<option value="${r.code}">${r.name}</option>`
    );
  }
}

/* -------------------------------------------------------------- render */

function filtered() {
  const d = state.data;
  if (!d) return [];
  return d.hotspots.filter(
    (h) =>
      (!state.sub1 || h.sub1 === state.sub1) &&
      (!state.sub2 || h.sub2 === state.sub2)
  );
}

function render() {
  const d = state.data;
  if (!d) return;
  state.siteLayer.clearLayers();
  state.recentLayer.clearLayers();
  if (state.heat) { map.removeLayer(state.heat); state.heat = null; }

  const spots = filtered();
  const heatPts = [];
  const bounds = [];

  for (const h of spots.slice(0, 800)) {
    const color = colorFor(h.index);
    const marker = L.circleMarker([h.lat, h.lng], {
      radius: 4 + h.index / 12,
      color, weight: 1.5, fillColor: color, fillOpacity: 0.65,
    });
    marker.bindPopup(popupHTML(h));
    state.siteLayer.addLayer(marker);
    heatPts.push([h.lat, h.lng, h.index / 100]);
    bounds.push([h.lat, h.lng]);
  }

  if ($("heatToggle").checked && heatPts.length) {
    state.heat = L.heatLayer(heatPts, {
      radius: 30, blur: 25, maxZoom: 9,
      gradient: { 0.2: "#2c7fb0", 0.4: "#7fcdbb", 0.6: "#f0d75a",
                  0.8: "#f68a3c", 1.0: "#d7301f" },
    }).addTo(map);
    state.heat.addTo(map);
  }

  if ($("recentToggle").checked) {
    for (const r of d.recent) {
      if (r.lat == null) continue;
      if (state.sub1 && r.sub1 && r.sub1 !== state.sub1) continue;
      if (state.sub2 && r.sub2 && r.sub2 !== state.sub2) continue;
      state.recentLayer.addLayer(
        L.circleMarker([r.lat, r.lng], {
          radius: 3, color: "#fff", weight: 1,
          fillColor: "#ffffff", fillOpacity: 0.5, dashArray: "2 2",
        }).bindPopup(
          `<b>${r.comName}</b><br>${r.locName}<br>` +
          `<span class="pop-row">${r.obsDt}</span>`
        )
      );
    }
  }

  renderPanels(spots);
  if (bounds.length) {
    map.fitBounds(L.latLngBounds(bounds).pad(0.15), { maxZoom: 8 });
  }
}

function popupHTML(h) {
  const f = h.factors || {};
  const bits = [];
  if (h.notables) bits.push(`${h.notables} historic notable(s)`);
  if (h.lastNotable) bits.push(`last: ${h.lastNotable}`);
  if (h.weather && h.weather.length) bits.push(`weather: ${h.weather.join(", ")}`);
  return `
    <div><b>${h.name}</b></div>
    <div class="pop-idx">${h.index}<small style="font-size:11px;
      color:var(--ink-dim)">/100</small></div>
    <div class="pop-row">~${h.expected} notable report(s)/week expected</div>
    <div class="pop-row">season ${f.seasonal ?? "—"}× · weather
      ${f.weather ?? "—"}× · activity ${f.recent ?? "—"}×</div>
    <div class="pop-row">${bits.join(" · ")}</div>`;
}

function renderPanels(spots) {
  const top = spots.slice(0, 15);
  $("topSites").innerHTML = top.map((h, i) => `
    <li data-i="${i}">
      <span class="rank">${i + 1}</span>
      <span class="nm" title="${h.name}">${h.name}</span>
      <span class="idx" style="color:${colorFor(h.index)}">${h.index}</span>
    </li>`).join("") || "<li>No sites in this area.</li>";
  $("topSites").querySelectorAll("li[data-i]").forEach((li) => {
    li.onclick = () => {
      const h = top[+li.dataset.i];
      map.flyTo([h.lat, h.lng], 11);
      state.siteLayer.eachLayer((l) => {
        const ll = l.getLatLng();
        if (ll && ll.lat === h.lat && ll.lng === h.lng) l.openPopup();
      });
    };
  });

  const d = state.data;
  let species = d.species || [];
  if (state.lifers && state.lifersOnly) {
    species = species.filter((s) => !state.lifers.has(s.species));
  }
  $("species").innerHTML = species.map((s) => {
    const seen = state.lifers && state.lifers.has(s.species);
    return `
    <li class="${seen ? "seen" : ""}"><div class="nm">${s.species}${
      state.lifers && !seen ? '<span class="lifer">★ lifer</span>' : ""
    }</div>
    <div class="rs">${s.reason}</div></li>`;
  }).join("") || "<li>No notable species signal yet.</li>";

  const rec = (d.recent || []).filter(
    (r) =>
      (!state.sub1 || !r.sub1 || r.sub1 === state.sub1) &&
      (!state.sub2 || !r.sub2 || r.sub2 === state.sub2)
  );
  $("recent").innerHTML = rec.slice(0, 20).map((r) => `
    <li><span class="dt">${r.obsDt}</span> ·
        <span class="nm">${r.comName}</span> — ${r.locName}</li>`
  ).join("") || "<li>No recent notable reports.</li>";
}

/* ------------------------------------------------------------- lifers */

function parseCSVLine(line) {
  const cells = [];
  let cur = "", inQ = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (inQ) {
      if (ch === '"') {
        if (line[i + 1] === '"') { cur += '"'; i++; }
        else inQ = false;
      } else cur += ch;
    } else if (ch === '"') inQ = true;
    else if (ch === ",") { cells.push(cur); cur = ""; }
    else cur += ch;
  }
  cells.push(cur);
  return cells;
}

function parseLifeList(text) {
  const names = new Set();
  const lines = text.split(/\r?\n/).filter((l) => l.trim());
  if (!lines.length) return names;
  const header = parseCSVLine(lines[0]);
  const col = header.findIndex(
    (h) => h.trim().toLowerCase() === "common name"
  );
  for (const line of lines.slice(col >= 0 ? 1 : 0)) {
    const cells = parseCSVLine(line);
    const nm = (col >= 0 ? cells[col] : cells[0] || "").trim();
    if (nm) names.add(nm);
  }
  return names;
}

function initLifers() {
  try {
    const saved = JSON.parse(localStorage.getItem("rbf.lifelist") || "null");
    if (Array.isArray(saved) && saved.length) {
      state.lifers = new Set(saved);
    }
  } catch {}
  updateLiferUI();

  $("lifeBtn").onclick = () => $("lifeFile").click();
  $("lifeFile").addEventListener("change", (e) => {
    const f = e.target.files[0];
    if (!f) return;
    f.text().then((text) => {
      state.lifers = parseLifeList(text);
      localStorage.setItem(
        "rbf.lifelist", JSON.stringify([...state.lifers])
      );
      updateLiferUI();
      if (state.data) render();
    });
  });
  $("liferOnly").addEventListener("change", (e) => {
    state.lifersOnly = e.target.checked;
    render();
  });
}

function updateLiferUI() {
  if (state.lifers && state.lifers.size) {
    $("lifeBtn").textContent = `Life list (${state.lifers.size})`;
    $("liferOnlyWrap").style.display = "";
  }
}

/* -------------------------------------------------------------- skill */

async function loadSkill() {
  let cal;
  try {
    cal = await loadJSON("data/calibration.json");
  } catch {
    return; // no data yet
  }
  if (!cal || !cal.total) return;
  $("skillPanel").style.display = "";
  const pct = (x) => `${(x * 100).toFixed(1)}%`;
  $("skill").innerHTML = `
    <div class="skill-meta">${cal.hits}/${cal.total} scored sites produced
      a rarity report within 7 days (${pct(cal.rate)}), as of
      ${cal.asOf}.</div>` +
    (cal.buckets || []).map((b) => `
      <div class="skill-row">
        <span class="lbl">${b.label}</span>
        <span class="bar"><i style="width:${Math.min(100, b.rate * 100)
          }%"></i></span>
        <span class="val">${b.hits}/${b.n}</span>
      </div>`).join("");
}

init();
