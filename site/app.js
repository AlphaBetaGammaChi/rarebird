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
  spiceK: 0,           // -1 conservative … +1 spicy (weather/recency weight)
  compare: new Set(),  // locIds picked for head-to-head
  siteLayer: L.layerGroup().addTo(map),
  recentLayer: L.layerGroup().addTo(map),
  trackLayer: L.layerGroup().addTo(map),
  replayLayer: L.layerGroup().addTo(map),
  heat: null,
  replay: { dates: [], i: 0, timer: null },
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
  $("trackToggle").addEventListener("change", render);
  $("spice").addEventListener("input", () => {
    state.spiceK = $("spice").value / 50 - 1;   // -1 … +1
    render();
  });
  $("compareBtn").onclick = showCompareCard;
  $("shareBtn").onclick = shareCard;
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

  const fps = Object.values(state.data.windFingerprint || {});
  $("fp").textContent = fps.length
    ? `arrival winds: ${fps.map((f) => f.peakDir).join(" / ")}`
    : "";

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
  initReplay();
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

/** Speculation slider: re-score with weather/recency factors raised to k. */
function rescored(spots) {
  const k = state.spiceK;
  const withIdx = spots.map((h) => {
    const f = h.factors || {};
    const spiced = h.expected *
      Math.pow(f.weather || 1, k) * Math.pow(f.recent || 1, k);
    return [h, spiced];
  });
  const max = Math.max(1e-9, ...withIdx.map(([, s]) => s));
  for (const [h, s] of withIdx) h._disp = Math.round((100 * s) / max);
  return withIdx;
}

function render() {
  const d = state.data;
  if (!d) return;
  state.siteLayer.clearLayers();
  state.recentLayer.clearLayers();
  if (state.heat) { map.removeLayer(state.heat); state.heat = null; }

  const spots = filtered();
  rescored(spots);
  spots.sort((a, b) => b._disp - a._disp);
  const heatPts = [];
  const bounds = [];

  state.trackLayer.clearLayers();
  if ($("trackToggle").checked) {
    for (const t of d.tracks || []) {
      if (!t.path || t.path.length < 2) continue;
      state.trackLayer.addLayer(
        L.polyline(t.path, {
          color: "#e879f9", weight: 1.5, dashArray: "4 4", opacity: 0.8,
        }).bindPopup(
          `<b>${t.comName}</b> — ${t.locName}<br>` +
          `<span class="pop-row">${t.obsDt} · est. 72h origin fan</span>`
        )
      );
    }
  }

  for (const h of spots.slice(0, 800)) {
    const color = colorFor(h._disp);
    const marker = L.circleMarker([h.lat, h.lng], {
      radius: 4 + h._disp / 12,
      color, weight: 1.5, fillColor: color, fillOpacity: 0.65,
    });
    marker.bindPopup(popupHTML(h));
    state.siteLayer.addLayer(marker);
    heatPts.push([h.lat, h.lng, h._disp / 100]);
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
  renderExtras(d);
  if (bounds.length) {
    map.fitBounds(L.latLngBounds(bounds).pad(0.15), { maxZoom: 8 });
  }
}

function renderExtras(d) {
  // conditions explainer + storm line
  if (d.explainer || (d.storms || []).length) {
    $("explainerPanel").style.display = "";
    $("explainer").textContent = d.explainer || "";
    $("stormLine").textContent = (d.storms || [])
      .map((s) => `⚠ ${s.name} (${s.class}) ~${s.distKm} km — ` +
                  `seabird displacement watch`)
      .join("; ");
  }

  // external tool links (BirdCast is US-only; Trektellen is Europe-heavy)
  const EU = new Set(("GB,IE,NL,BE,DE,FR,ES,PT,IT,NO,SE,FI,DK,PL,EE,LV,LT," +
    "CZ,SK,AT,CH,HU,HR,SI,GR,BG,RO,UA,TR,CY,MT,IS,EE").split(","));
  const links = [];
  if (state.country === "US") {
    links.push(
      `<a href="https://birdcast.org/migration-tools/migration-dashboard/" ` +
      `target="_blank" rel="noopener">BirdCast migration dashboard</a>`);
  }
  if (EU.has(state.country)) {
    links.push(
      `<a href="https://www.trektellen.org/home/counts" ` +
      `target="_blank" rel="noopener">Trektellen vizmig counts</a>`);
  }
  $("extLinks").innerHTML = links.length ? "See also: " + links.join(" · ") : "";

  // missing birds / arrival windows
  $("missing").innerHTML = (d.missing || []).map((m) => `
    <li><span class="nm">${m.species}</span>
      <span class="st ${m.status.replace(" ", "-")}">${m.status}</span>
      <div class="rs">window ${m.window}${
        m.medianFirst ? ` · median first ${m.medianFirst}` : ""
      } · ${m.records} records</div></li>`).join("") ||
    "<li>No due/overdue species signal.</li>";

  // underwatched gems
  if ((d.gems || []).length) {
    $("gemsPanel").style.display = "";
    $("gems").innerHTML = d.gems.map((g) => `
      <li data-lat="${g.lat}" data-lng="${g.lng}">
        <b>${g.name}</b>
        <div class="rs">${g.reason}</div></li>`).join("");
    $("gems").querySelectorAll("li").forEach((li) => {
      li.onclick = () =>
        map.flyTo([+li.dataset.lat, +li.dataset.lng], 11);
    });
  }

  // drought board
  if ((d.droughts || []).length) {
    $("droughtPanel").style.display = "";
    $("droughts").innerHTML = d.droughts.map((r) => `
      <li><b>${r.name}</b>${
        r.days == null
          ? `<div class="rs">no notable on record yet</div>`
          : `<div class="rs">${r.days}d since ${r.lastSpecies}
             (${r.lastDate})</div>`
      }</li>`).join("");
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
    <div class="pop-idx">${h._disp ?? h.index}<small style="font-size:11px;
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
      <span class="vs ${state.compare.has(h.locId) ? "on" : ""}"
            title="Add to head-to-head" data-vs="${h.locId}">vs</span>
      <span class="nm" title="${h.name}">${h.name}</span>
      <span class="idx" style="color:${colorFor(h._disp)}">${h._disp}</span>
    </li>`).join("") || "<li>No sites in this area.</li>";
  $("topSites").querySelectorAll("li[data-i]").forEach((li) => {
    li.onclick = (e) => {
      if (e.target.classList.contains("vs")) return;
      const h = top[+li.dataset.i];
      map.flyTo([h.lat, h.lng], 11);
      state.siteLayer.eachLayer((l) => {
        const ll = l.getLatLng();
        if (ll && ll.lat === h.lat && ll.lng === h.lng) l.openPopup();
      });
    };
  });
  $("topSites").querySelectorAll(".vs").forEach((v) => {
    v.onclick = () => {
      const id = v.dataset.vs;
      if (state.compare.has(id)) state.compare.delete(id);
      else if (state.compare.size < 3) state.compare.add(id);
      else return;
      v.classList.toggle("on");
      renderCompareBar();
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
      s.tier ? `<span class="tier ${s.tier}">${s.tier}</span>` : ""
    }${
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

/* ------------------------------------------------------------- compare */

function renderCompareBar() {
  const bar = $("compareBar");
  if (!state.compare.size) { bar.style.display = "none"; return; }
  bar.style.display = "";
  const names = {};
  (state.data?.hotspots || []).forEach((h) => { names[h.locId] = h.name; });
  bar.innerHTML =
    [...state.compare].map((id) =>
      `<span class="chip">${names[id] || id}<b data-x="${id}">×</b></span>`
    ).join("") +
    `<button id="goCompare">compare</button>`;
  bar.querySelectorAll("[data-x]").forEach((x) => {
    x.onclick = () => {
      state.compare.delete(x.dataset.x);
      renderCompareBar(); render();
    };
  });
  $("goCompare").onclick = showCompareCard;
}

function showCompareCard() {
  const card = $("compareCard");
  const spots = (state.data?.hotspots || []).filter((h) =>
    state.compare.has(h.locId)
  );
  if (!spots.length) {
    card.style.display = "none";
    return;
  }
  rescored(spots);
  const rows = [
    ["index", (h) => `${h._disp}/100`],
    ["expected rarities/wk", (h) => h.expected],
    ["season ×", (h) => h.factors?.seasonal ?? "—"],
    ["weather ×", (h) => h.factors?.weather ?? "—"],
    ["recent ×", (h) => h.factors?.recent ?? "—"],
    ["historic notables", (h) => h.notables],
    ["last notable", (h) => h.lastNotable || "—"],
    ["species recorded", (h) => h.numSpecies],
  ];
  card.innerHTML =
    `<span class="close" id="ccClose">×</span><table><tr><th></th>` +
    spots.map((h) => `<th>${h.name}</th>`).join("") + "</tr>" +
    rows.map(([lbl, fn]) =>
      `<tr><td>${lbl}</td>` +
      spots.map((h) => `<td>${fn(h)}</td>`).join("") + "</tr>"
    ).join("") + "</table>";
  card.style.display = "";
  $("ccClose").onclick = () => (card.style.display = "none");
}

/* --------------------------------------------------------------- share */

async function shareCard() {
  const d = state.data;
  if (!d) return;
  const top = filtered().slice(0, 5);
  const lines = [
    `Rarebird outlook — ${d.country} · ${d.week}`,
    "",
    ...top.map((h, i) => `${i + 1}. ${h.name} — ${h._disp}/100`),
    "",
    `Species to watch: ${(d.species || [])
      .slice(0, 5).map((s) => s.species).join(", ")}`,
  ];
  if (d.explainer) lines.push("", d.explainer);
  lines.push("", location.origin + location.pathname + "?c=" + d.country);
  try {
    await navigator.clipboard.writeText(lines.join("\n"));
    $("shareBtn").textContent = "Copied!";
    setTimeout(() => ($("shareBtn").textContent = "Share"), 1500);
  } catch {
    prompt("Copy the outlook:", lines.join("\n"));
  }
}

/* -------------------------------------------------------------- replay */

function initReplay() {
  const d = state.data;
  const hist = (d?.history || []).filter((h) => h.d);
  const bar = $("replayBar");
  if (!hist.length) { bar.style.display = "none"; return; }
  bar.style.display = "";

  const dates = [...new Set(hist.map((h) => h.d.slice(0, 10)))].sort();
  state.replay.dates = dates;
  state.replay.i = dates.length - 1;
  $("replaySlider").max = dates.length - 1;
  $("replaySlider").value = dates.length - 1;
  $("replayDate").textContent = dates[dates.length - 1];

  $("replaySlider").oninput = () => {
    stopReplay();
    drawReplayTo(+$("replaySlider").value);
  };
  $("playBtn").onclick = () => {
    if (state.replay.timer) { stopReplay(); return; }
    drawReplayTo(0);
    $("playBtn").innerHTML = "&#9646;&#9646;";
    state.replay.timer = setInterval(() => {
      if (state.replay.i >= dates.length - 1) { stopReplay(); return; }
      drawReplayTo(state.replay.i + 1);
    }, 120);
  };
}

function drawReplayTo(idx) {
  const d = state.data;
  const dates = state.replay.dates;
  state.replay.i = idx;
  $("replaySlider").value = idx;
  $("replayDate").textContent = dates[idx];
  state.replayLayer.clearLayers();
  const cutoff = dates[idx];
  for (const h of d.history) {
    const hd = h.d.slice(0, 10);
    if (hd > cutoff) break; // history is date-sorted
    const age =
      (new Date(cutoff) - new Date(hd)) / 864e5;
    state.replayLayer.addLayer(
      L.circleMarker([h.lat, h.lng], {
        radius: 5, color: "#fff", weight: 1,
        fillColor: "#e879f9",
        fillOpacity: Math.max(0.15, 0.9 - age / 60),
      }).bindPopup(`<b>${h.n}</b><br>${h.loc}<br>
        <span class="pop-row">${hd}</span>`)
    );
  }
}

function stopReplay() {
  clearInterval(state.replay.timer);
  state.replay.timer = null;
  $("playBtn").innerHTML = "&#9654;";
}

init();
