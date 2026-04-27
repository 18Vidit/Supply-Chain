const API_BASE = window.location.protocol.startsWith("http")
  ? window.location.origin
  : "http://127.0.0.1:8000";

const money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const number = new Intl.NumberFormat("en-US");

let map;
let layers = {};
let lastTrucks = [];
let lastRisks = [];
let lastHazards = [];
let lastNetwork = null;
let selectedTruckId = null;
let transportMode = "land";
let timer;

const riskColors = {
  CRITICAL: "#dc2626",
  HIGH: "#d97706",
  MODERATE: "#2563eb",
  LOW: "#059669",
};

document.addEventListener("DOMContentLoaded", () => {
  initIcons();
  initMap();
  bindEvents();
  refreshAll();
  timer = setInterval(refreshAll, 5000);
});

function initIcons() {
  if (window.lucide) window.lucide.createIcons();
}

function bindEvents() {
  document.getElementById("refresh-btn").addEventListener("click", refreshAll);
  document.getElementById("zoom-world").addEventListener("click", zoomWorld);
  document.getElementById("reroute-all-btn").addEventListener("click", rerouteAllCritical);
  document.getElementById("ask-btn").addEventListener("click", askDispatcher);
  document.getElementById("ask-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter") askDispatcher();
  });

  document.querySelectorAll("[data-transport-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll("[data-transport-mode]").forEach((el) => el.classList.remove("selected"));
      button.classList.add("selected");
      transportMode = button.dataset.transportMode;
      renderShipments(lastTrucks, lastRisks);
      renderLanes(lastNetwork?.lanes || []);
      updateMapMeta(lastNetwork);
      redrawMap();
      updateSystemLine();
    });
  });

  document.querySelectorAll("[data-nav-target]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll("[data-nav-target]").forEach((el) => el.classList.remove("active"));
      button.classList.add("active");
      const target = document.getElementById(button.dataset.navTarget);
      if (target) {
        target.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });
  });
}

function initMap() {
  if (typeof L === "undefined") return;
  map = L.map("map", { zoomControl: true, worldCopyJump: true }).setView([20, 20], 2);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18,
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);
  layers.lanes = L.layerGroup().addTo(map);
  layers.trucks = L.layerGroup().addTo(map);
  layers.hazards = L.layerGroup().addTo(map);
  layers.selection = L.layerGroup().addTo(map);
}

async function refreshAll() {
  setSystemLine("Syncing live network...");
  try {
    const [analytics, network, trucks, hazards, risks, alerts] = await Promise.all([
      fetchJson("/analytics").catch(() => null),
      fetchJson("/global-network"),
      fetchJson("/trucks"),
      fetchJson("/hazards"),
      fetchJson("/risk"),
      fetchJson("/ai-alerts").catch(() => []),
    ]);

    lastNetwork = network;
    lastTrucks = Array.isArray(trucks) ? trucks : [];
    lastHazards = Array.isArray(hazards) ? hazards : [];
    lastRisks = Array.isArray(risks) ? risks : [];

    renderAnalytics(analytics, network, lastHazards);
    renderMix("mode-mix", analytics?.mode_mix || {});
    renderMix("flow-mix", analytics?.flow_mix || {});
    renderShipments(lastTrucks, lastRisks);
    renderLanes(network.lanes || []);
    renderAlerts(alerts, lastRisks);
    redrawMap();
    if (selectedTruckId) loadBrief(selectedTruckId, false);
    updateSystemLine();
  } catch (error) {
    setSystemLine(`API offline: ${error.message || "refresh failed"}`);
  } finally {
    initIcons();
  }
}

async function fetchJson(path) {
  const response = await fetch(`${API_BASE}${path}`);
  if (!response.ok) throw new Error(`${path} returned ${response.status}`);
  return response.json();
}

function renderAnalytics(analytics, network, hazards) {
  const kpis = analytics?.kpis || {};
  setText("s-active", number.format(kpis.active_shipments || lastTrucks.length));
  setText("s-countries", number.format(kpis.countries || network?.stats?.country_count || 0));
  setText("s-hazards", number.format(kpis.active_hazards || hazards?.length || 0));
  setText("s-risk-value", money.format(kpis.value_at_risk_usd || 0));
  updateMapMeta(network);
}

function renderMix(id, data) {
  const container = document.getElementById(id);
  const entries = Object.entries(data);
  if (!entries.length) {
    container.innerHTML = `<div class="mini-bar-row"><span>empty</span><div class="mini-track"><div class="mini-fill" style="width:0"></div></div><b>0</b></div>`;
    return;
  }
  const max = Math.max(...entries.map(([, value]) => value), 1);
  container.innerHTML = entries.map(([label, value]) => `
    <div class="mini-bar-row">
      <span>${escapeHtml(label)}</span>
      <div class="mini-track"><div class="mini-fill" style="width:${Math.round((value / max) * 100)}%"></div></div>
      <b>${value}</b>
    </div>
  `).join("");
}

function renderShipments(trucks, risks) {
  const riskByTruck = new Map(risks.map((risk) => [String(risk.truck_id), risk]));
  const visibleTrucks = trucks.filter((truck) => truckTransportCategory(truck) === transportMode);
  const rows = visibleTrucks
    .slice()
    .sort((a, b) => riskScore(riskByTruck.get(String(b.id))) - riskScore(riskByTruck.get(String(a.id))))
    .slice(0, 14)
    .map((truck) => {
      const risk = riskByTruck.get(String(truck.id)) || { risk_label: "LOW", risk_score: 0 };
      const label = risk.risk_label || "LOW";
      return `
        <div class="shipment-row" data-truck-id="${escapeAttr(truck.id)}">
          <div>
            <div class="row-title">${escapeHtml(truck.callsign)} <span class="badge ${label.toLowerCase()}">${label}</span></div>
            <div class="row-subtitle">${escapeHtml(truck.route_name || "")}</div>
            <div class="row-subtitle">${escapeHtml(truck.origin_country || "")} to ${escapeHtml(truck.destination_country || "")} &middot; ${escapeHtml(truck.service_level || "standard")}</div>
          </div>
          <div class="row-meta">
            <strong>${Math.round((risk.risk_score || 0) * 100)}%</strong>
          </div>
        </div>
      `;
    });
  document.getElementById("shipment-list").innerHTML = rows.join("") || `<div class="empty-state">No ${escapeHtml(transportLabel(transportMode).toLowerCase())} shipments loaded</div>`;
  document.querySelectorAll(".shipment-row").forEach((row) => {
    row.addEventListener("click", () => focusShipment(row.dataset.truckId));
  });
}

function renderLanes(lanes) {
  const visibleLanes = lanes.filter((lane) => laneTransportCategory(lane) === transportMode);
  const html = visibleLanes.slice(0, 12).map((lane) => `
    <div class="lane-row">
      <div class="row-title">${escapeHtml(lane.name)}</div>
      <div class="row-subtitle">${escapeHtml(lane.origin_country)} to ${escapeHtml(lane.destination_country)} &middot; ${escapeHtml(transportLabel(laneTransportCategory(lane)))} &middot; ${escapeHtml(lane.flow_type)}</div>
      <div class="row-subtitle">${number.format(Math.round(lane.distance_km || 0))} km &middot; ${money.format(lane.base_cost_usd || 0)} &middot; ${(Number(lane.reliability || 0) * 100).toFixed(0)}% reliability</div>
    </div>
  `).join("");
  document.getElementById("lane-list").innerHTML = html || `<div class="empty-state">No ${escapeHtml(transportLabel(transportMode).toLowerCase())} lanes loaded</div>`;
}

function renderAlerts(alerts, risks) {
  const source = Array.isArray(alerts) && alerts.length
    ? alerts
    : risks.filter((risk) => risk.risk_label === "HIGH" || risk.risk_label === "CRITICAL").slice(0, 8);
  if (!source.length) {
    document.getElementById("alert-list").innerHTML = `<div class="empty-state">No decision alerts</div>`;
    return;
  }
  document.getElementById("alert-list").innerHTML = source.map((item) => {
    const label = item.risk_label || "HIGH";
    return `
      <div class="alert-item ${label.toLowerCase()}">
        <div class="row-title">${escapeHtml(item.callsign || item.truck_id)} <span class="badge ${label.toLowerCase()}">${label}</span></div>
        <div class="row-subtitle">${escapeHtml(item.message || item.hazard_title || "Risk signal detected")}</div>
        <div class="alert-actions">
          <button class="small-button" data-action="brief" data-truck-id="${escapeAttr(item.truck_id)}"><i data-lucide="brain-circuit"></i>Brief</button>
          <button class="small-button" data-action="reroute" data-truck-id="${escapeAttr(item.truck_id)}"><i data-lucide="git-branch"></i>Reroute</button>
        </div>
      </div>
    `;
  }).join("");
  document.querySelectorAll("[data-action='brief']").forEach((btn) => btn.addEventListener("click", () => focusShipment(btn.dataset.truckId)));
  document.querySelectorAll("[data-action='reroute']").forEach((btn) => btn.addEventListener("click", () => rerouteTruck(btn.dataset.truckId)));
}

function redrawMap(hazardsOverride) {
  if (!map) return;
  const hazards = hazardsOverride || lastHazards;
  layers.lanes.clearLayers();
  layers.trucks.clearLayers();
  layers.hazards.clearLayers();

  drawLanes();
  drawHazards(hazards);
  drawTrucks();
}

function drawLanes() {
  (lastNetwork?.lanes || [])
    .filter((lane) => laneTransportCategory(lane) === transportMode)
    .forEach((lane) => {
      const category = laneTransportCategory(lane);
      const color = category === "land" ? "#2563eb" : category === "water" ? "#059669" : "#7c3aed";
      L.polyline(lane.points, { color, weight: 2, opacity: 0.32 }).addTo(layers.lanes).bindTooltip(lane.name);
    });
}

function drawHazards(hazards) {
  hazards.forEach((hazard) => {
    if (!hazard.centroid_lat || !hazard.centroid_lng) return;
    const label = Number(hazard.severity_weight || 0) >= 0.8 ? "CRITICAL" : "HIGH";
    const color = riskColors[label];
    L.circle([hazard.centroid_lat, hazard.centroid_lng], {
      radius: Number(hazard.radius_km || 50) * 1000,
      color,
      fillColor: color,
      fillOpacity: 0.11,
      weight: 1,
    }).addTo(layers.hazards).bindTooltip(hazard.title || "Hazard");
  });
}

function drawTrucks() {
  const riskByTruck = new Map(lastRisks.map((risk) => [String(risk.truck_id), risk]));
  lastTrucks
    .filter((truck) => truckTransportCategory(truck) === transportMode)
    .forEach((truck) => {
      const risk = riskByTruck.get(String(truck.id)) || { risk_label: "LOW" };
      const color = riskColors[risk.risk_label] || riskColors.LOW;
      const category = truckTransportCategory(truck);
      const icon = L.divIcon({
        className: "",
        html: `<div class="truck-marker" style="background:${color}"></div>`,
        iconSize: [14, 14],
        iconAnchor: [7, 7],
      });
      L.marker([truck.lat, truck.lng], { icon })
        .addTo(layers.trucks)
        .bindPopup(`<strong>${escapeHtml(truck.callsign)}</strong><br>${escapeHtml(truck.route_name || "")}<br>Category: ${escapeHtml(transportLabel(category))}<br>Risk: ${escapeHtml(risk.risk_label || "LOW")}`)
        .on("click", () => focusShipment(truck.id));
    });
}

async function focusShipment(truckId) {
  selectedTruckId = truckId;
  const truck = lastTrucks.find((item) => String(item.id) === String(truckId));
  if (truck && map) map.setView([truck.lat, truck.lng], 5);
  await loadBrief(truckId, true);
}

async function loadBrief(truckId, showLoading = true) {
  if (showLoading) {
    document.getElementById("decision-body").innerHTML = `<div class="empty-state">Loading decision brief</div>`;
  }
  try {
    const brief = await fetchJson(`/ai/brief/${encodeURIComponent(truckId)}`);
    if (brief.error) throw new Error(brief.error);
    renderBrief(brief);
  } catch (error) {
    document.getElementById("decision-body").innerHTML = `<div class="empty-state">${escapeHtml(error.message || "Brief unavailable")}</div>`;
  } finally {
    initIcons();
  }
}

function renderBrief(brief) {
  const route = brief.recommended_route || {};
  const delay = brief.delay_prediction || {};
  document.getElementById("brief-meta").textContent = `${brief.callsign} - ${brief.mode || "offline"} intelligence`;
  document.getElementById("decision-body").innerHTML = `
    <div class="brief-card">
      <div class="brief-title">${escapeHtml(brief.callsign)}</div>
      <div class="brief-text">${escapeHtml(brief.route_recommendation || "")}</div>
      <div class="brief-metrics">
        <div class="metric"><span>Predicted Delay</span><strong>${Math.round(delay.predicted_delay_min || 0)} min</strong></div>
        <div class="metric"><span>Delay Class</span><strong>${escapeHtml(delay.delay_label || "ON_TIME")}</strong></div>
        <div class="metric"><span>Route Risk</span><strong>${Math.round((route.risk_index || 0) * 100)}%</strong></div>
        <div class="metric"><span>Cost Delta</span><strong>${money.format(route.cost_delta_usd || 0)}</strong></div>
      </div>
      <div class="brief-text">${escapeHtml(brief.explanation || "")}</div>
      <button class="action-button" id="apply-reroute-btn" type="button"><i data-lucide="git-branch"></i><span>Apply Reroute</span></button>
    </div>
  `;
  document.getElementById("apply-reroute-btn").addEventListener("click", () => rerouteTruck(brief.truck_id));
}

async function rerouteTruck(truckId) {
  if (!truckId) return;
  try {
    const data = await fetchJson(`/detour/${encodeURIComponent(truckId)}`);
    appendChat(`Reroute queued for ${data.callsign || truckId}. ${data.detour?.decision || ""}`, "assistant");
    await refreshAll();
  } catch (error) {
    appendChat(error.message || "Reroute failed", "assistant");
  }
}

async function rerouteAllCritical() {
  try {
    const data = await fetchJson("/reroute-all-critical");
    appendChat(`Rerouted ${data.rerouted || 0} critical shipments.`, "assistant");
    await refreshAll();
  } catch (error) {
    appendChat(error.message || "Bulk reroute failed", "assistant");
  }
}

async function askDispatcher() {
  const input = document.getElementById("ask-input");
  const query = input.value.trim();
  if (!query) return;
  appendChat(query, "user");
  input.value = "";
  try {
    const data = await fetchJson(`/ask?q=${encodeURIComponent(query)}`);
    appendChat(data.answer || "No answer returned.", "assistant");
  } catch (error) {
    appendChat(error.message || "AI assistant unavailable.", "assistant");
  }
}

function appendChat(text, role) {
  const chat = document.getElementById("ai-chat");
  chat.insertAdjacentHTML("beforeend", `<div class="chat-line ${role === "user" ? "user" : ""}">${escapeHtml(text)}</div>`);
  chat.scrollTop = chat.scrollHeight;
}

function zoomWorld() {
  if (map) map.setView([20, 20], 2);
}

function setSystemLine(text) {
  document.getElementById("system-line").textContent = text;
}

function setText(id, text) {
  document.getElementById(id).textContent = text;
}

function riskScore(risk) {
  return Number((risk && risk.risk_score) || 0);
}

function updateMapMeta(network) {
  document.getElementById("map-meta").textContent = `${network?.stats?.lane_count || 0} lanes, ${network?.stats?.port_count || 0} ports, view: ${transportLabel(transportMode)}`;
}

function updateSystemLine() {
  const visibleCount = lastTrucks.filter((truck) => truckTransportCategory(truck) === transportMode).length;
  const totalCount = lastTrucks.length;
  const countries = lastNetwork?.stats?.country_count || 0;
  setSystemLine(`${number.format(visibleCount)} ${transportLabel(transportMode).toLowerCase()} shipments visible (${number.format(totalCount)} total) across ${countries} countries`);
}

function truckTransportCategory(truck) {
  return normalizeTransportCategory(truck?.transport_category || truck?.mode);
}

function laneTransportCategory(lane) {
  return normalizeTransportCategory(lane?.transport_category || lane?.mode);
}

function normalizeTransportCategory(value) {
  const mode = String(value || "").trim().toLowerCase();
  if (mode === "road" || mode === "land") return "land";
  if (mode === "ocean" || mode === "water" || mode === "sea") return "water";
  if (mode === "intermodal" || mode === "air" || mode === "aerial") return "aerial";
  return "land";
}

function transportLabel(mode) {
  if (mode === "water") return "By Water";
  if (mode === "aerial") return "Aerial";
  return "By Land";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll("`", "&#096;");
}
