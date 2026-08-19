"use strict";

const CONFIG_URL = "config/app_config.json";
const DATA_URL = "data/dashboard_data.json";

const REGION_ORDER = ["ACARAU", "ITAPIPOCA", "ITAPAJE", "TRAIRI", "ATLANTICO"];
const REGION_COLOR = {
  "ACARAU":    "#006ac0",
  "ITAPIPOCA": "#007a41",
  "ITAPAJE":   "#d8a911",
  "TRAIRI":    "#ff3700",
  "ATLANTICO": "#1b4d5f"
};
const REGION_CLASS = {
  "ACARAU":    "ACARAU",
  "ITAPIPOCA": "ITAPIPOCA",
  "ITAPAJE":   "ITAPAJE",
  "TRAIRI":    "TRAIRI",
  "ATLANTICO": "ATLANTICO"
};
const CITY_OFFSETS = {
  "ITAPIPOCA": {dx: -30, dy: 20},
};

let currentConfig = null;
let currentData = null;
let refreshTimer = null;
let resizeTimer = null;

function norm(value){
  return (value || "")
    .toString()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .toUpperCase();
}

function escapeHtml(value){
  return (value ?? "")
    .toString()
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function hasPendencia(row){
  if(!row) return false;
  const raw = row.pendencia ?? row["pendência"] ?? row.tem_pendencia ?? row.temPendencia ?? "";
  if(raw === true) return true;
  const value = norm(raw);
  return value === "SIM" || value === "TRUE" || value === "1" || value === "PENDENCIA";
}

function isFileProtocol(){ return window.location.protocol === "file:"; }
function getEmbeddedData(){ return window.DASHBOARD_DATA || null; }
function safeInt(value){ const num = Number(value || 0); return Number.isFinite(num) ? Math.round(num) : 0; }
function safeFloat(value){ const num = Number(value || 0); return Number.isFinite(num) ? num : 0; }
function formatInt(value){ return safeInt(value).toLocaleString("pt-BR"); }
function formatFloat(value, decimals = 1){ return safeFloat(value).toLocaleString("pt-BR", {minimumFractionDigits: decimals, maximumFractionDigits: decimals}); }
function stripLeadingDoubleZero(value){ const text = String(value || "").trim(); return text.startsWith("00") ? text.slice(2) : text; }
function regionKey(regional){ return norm(regional); }
function regionColor(regional){ return REGION_COLOR[regionKey(regional)] || "#1b4d5f"; }
function regionClass(regional){ return REGION_CLASS[regionKey(regional)] || "ATLANTICO"; }
function regionalDisplay(regional){
  const key = norm(regional);
  if(key === "ACARAU")    return "Acaraú";
  if(key === "ITAPIPOCA") return "Itapipoca";
  if(key === "ITAPAJE")   return "Itapajé";
  if(key === "TRAIRI")    return "Trairí";
  if(key === "ATLANTICO") return "Atlântico";
  return regional || "—";
}
function countScaleColor(count){
  const value = safeInt(count);
  if(value <= 0) return "#cfcfcf";
  if(value <= 2) return "#ffed4c";
  if(value <= 5) return "#fd832b";
  if(value <= 10) return "#ca0303";
  return "#4c1d95";
}
function getLabelFontSize(name){
  const length = (name || "").length;
  if(length > 17) return 8.6;
  if(length > 13) return 9.5;
  return 10.5;
}
function shortCityLabel(name){
  return String(name || "")
    .replace("Jijoca de Jericoacoara", "Jijoca de Jeric.")
    .replace("General Sampaio", "Geral Sampaio")
    .replace("São Luís do Curu", "S.Luís do Curu");
}
function formatDurationLabel(row){
  if(row?.duracao) return row.duracao;
  const totalHours = safeFloat(row?.duracao_horas);
  if(totalHours <= 0) return "—";
  const hours = Math.floor(totalHours);
  const minutes = Math.round((totalHours - hours) * 60);
  return `${hours.toString().padStart(2, "0")}:${minutes.toString().padStart(2, "0")}`;
}

async function loadConfig(){
  try{
    const response = await fetch(`${CONFIG_URL}?t=${Date.now()}`, {cache: "no-store"});
    if(!response.ok) throw new Error(`HTTP ${response.status}`);
    return await response.json();
  }catch{
    return {
      title: "PAINEL OPERACIONAL - ATLÂNTICO",
      refresh_seconds: 60,
    };
  }
}

async function loadData(){
  if(isFileProtocol() && getEmbeddedData()) return getEmbeddedData();
  try{
    const response = await fetch(`${DATA_URL}?t=${Date.now()}`, {cache: "no-store"});
    if(!response.ok) throw new Error(`HTTP ${response.status}`);
    return await response.json();
  }catch(err){
    if(getEmbeddedData()) return getEmbeddedData();
    throw err;
  }
}

function setHeader(config, data){
  const title = (config?.title || data?.meta?.title || "PAINEL OPERACIONAL - ATLÂNTICO").toUpperCase();
  document.getElementById("page-title").textContent = title;
  document.getElementById("updated-at").textContent = data?.meta?.data_updated_at_display || data?.meta?.updated_at_display || "—";
}

function summaryValueCell(value, colorClass = "black"){
  return `
    <div class="summary-row-cell">
      <div class="metric-stack">
        <div class="metric-value ${colorClass}">${escapeHtml(value)}</div>
      </div>
    </div>
  `;
}

function summaryBarCell(value, total, color, valueClass = "black"){
  const numerator = Math.max(0, safeInt(value));
  const denominator = Math.max(0, safeInt(total));
  const pct = denominator > 0 ? Math.min(100, (numerator / denominator) * 100) : 0;
  return `
    <div class="summary-row-cell">
      <div class="bar-stack">
        <div class="bar-value ${valueClass}">${escapeHtml(formatInt(numerator))}</div>
        <div class="progress-track"><div class="progress-fill" style="width:${pct.toFixed(2)}%;background:${color};"></div></div>
      </div>
    </div>
  `;
}

function durationBucketColor(label){
  if(label === "<8h") return "#16a34a";
  if(label === "8-16h") return "#eab308";
  if(label === "16-24h") return "#f97316";
  if(label === "24-48h") return "#dc2626";
  return "#111827";
}
function durationHoursColor(hours){
  const h = safeFloat(hours);
  if(h <= 0)  return "#9ca3af";
  if(h < 8)   return "#16a34a";
  if(h < 16)  return "#eab308";
  if(h < 24)  return "#f97316";
  if(h < 48)  return "#dc2626";
  return "#111827";
}

function durationMini(label, value, total){
  const numerator = Math.max(0, safeInt(value));
  const denominator = Math.max(0, safeInt(total));
  const pct = denominator > 0 ? Math.min(100, (numerator / denominator) * 100) : 0;
  const color = durationBucketColor(label);
  return `
    <div class="duration-mini">
      <div class="duration-value" style="color:${color};">${escapeHtml(formatInt(numerator))}</div>
      <div class="progress-track"><div class="progress-fill" style="width:${pct.toFixed(2)}%;background:${color};"></div></div>
      <div class="duration-label">${escapeHtml(label)}</div>
    </div>
  `;
}

function buildSummaryHeader(){
  return `
    <div class="summary-head-cell">Polo / Clientes Afetados</div>
    <div class="summary-head-cell">Inc Ativas</div>
    <div class="summary-head-cell">Não Desp.</div>
    <div class="summary-head-cell">Equipes</div>
    <div class="summary-head-cell">Inc / Equipe</div>
    <div class="summary-head-cell">Aporte Recom.</div>
    <div class="summary-head-cell">&gt;1 Aviso</div>
    <div class="summary-head-cell duration-head">
      <div class="duration-head-grid">
        <span>&lt;8h</span>
        <span>8-16h</span>
        <span>16-24h</span>
        <span>24-48h</span>
        <span>&gt;48h</span>
      </div>
    </div>
  `;
}

function renderSummary(data){
  const target = document.getElementById("summary-grid");
  const rows = Array.isArray(data?.summary?.regionais) ? data.summary.regionais.slice() : [];
  if(!rows.length){
    target.innerHTML = `<div class="empty-state">Resumo indisponível.</div>`;
    return;
  }

  rows.sort((a, b) => {
    const ia = REGION_ORDER.indexOf(regionKey(a.regional));
    const ib = REGION_ORDER.indexOf(regionKey(b.regional));
    return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib);
  });

  const body = rows.map((row) => {
    const totalInc = safeInt(row.incidencias_ativas);
    const teams = safeInt(row.qtde_equipes);
    const recommended = Math.max(0, safeInt(row.aporte_recomendado ?? Math.ceil((totalInc - (teams * 4)) / 4)));
    const incidentsColor = totalInc === 0 ? "green" : "red";
    const naoDespColor = safeInt(row.nao_despachadas) === 0 ? "black" : "amber";
    const recommendedColor = recommended === 0 ? "black" : "red";
    const incidPorEquipe = teams > 0 ? formatFloat(totalInc / teams, 1) : "0,0";
    const rowClass = norm(row.regional) === "ATLANTICO" ? "summary-row total-row" : "summary-row";

    return `
      <div class="${rowClass}">
        <div class="summary-row-cell region-cell">
          <div class="region-stack">
            <div class="region-name" style="color:${regionColor(row.regional)};">${escapeHtml(String(row.regional || "").toUpperCase())}</div>
            <div class="region-clients-wrap"><span class="region-clients">${escapeHtml(formatInt(row.clientes_afetados))}</span><span class="region-clients-label">clientes</span></div>
          </div>
        </div>
        ${summaryValueCell(formatInt(totalInc), incidentsColor)}
        ${summaryValueCell(formatInt(row.nao_despachadas), naoDespColor)}
        ${summaryValueCell(formatInt(teams), "black")}
        ${summaryValueCell(incidPorEquipe, "black")}
        ${summaryValueCell(formatInt(recommended), recommendedColor)}
        ${summaryBarCell(row.mais_de_um_aviso, totalInc, "#ec4899", "pink")}
        <div class="summary-row-cell duration-cell">
          <div class="duration-grid">
            ${durationMini("<8h", row.dur_lt_8, totalInc)}
            ${durationMini("8-16h", row.dur_8_16, totalInc)}
            ${durationMini("16-24h", row.dur_16_24, totalInc)}
            ${durationMini("24-48h", row.dur_gt_24, totalInc)}
            ${durationMini(">48h", row.dur_gt_48, totalInc)}
          </div>
        </div>
      </div>
    `;
  }).join("");

  target.innerHTML = `<div class="summary-table">${buildSummaryHeader()}${body}</div>`;
}

function rings(geometry){
  if(!geometry) return [];
  if(geometry.type === "Polygon") return [geometry.coordinates];
  if(geometry.type === "MultiPolygon") return geometry.coordinates;
  return [];
}

function getBounds(geojson){
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  (geojson?.features || []).forEach((feature) => {
    rings(feature.geometry).forEach((polygon) => {
      polygon.forEach((ring) => {
        ring.forEach(([x, y]) => {
          minX = Math.min(minX, x);
          minY = Math.min(minY, y);
          maxX = Math.max(maxX, x);
          maxY = Math.max(maxY, y);
        });
      });
    });
  });
  return {minX, minY, maxX, maxY};
}

function createProjector(bounds, width, height, padding = 10, zoom = 1.05){
  const spanX = Math.max(bounds.maxX - bounds.minX, 1e-6);
  const spanY = Math.max(bounds.maxY - bounds.minY, 1e-6);
  const baseScale = Math.min((width - padding * 2) / spanX, (height - padding * 2) / spanY);
  const scale = baseScale * zoom;
  const usedWidth = spanX * scale;
  const usedHeight = spanY * scale;
  const offsetX = (width - usedWidth) / 2;
  const offsetY = (height - usedHeight) / 2;
  return ([lon, lat]) => [
    offsetX + (lon - bounds.minX) * scale,
    height - (offsetY + (lat - bounds.minY) * scale)
  ];
}

function geometryPath(feature, project){
  return rings(feature.geometry).flatMap((polygon) => polygon.map((ring) => ring.map(([x, y], idx) => {
    const [px, py] = project([x, y]);
    return `${idx === 0 ? "M" : "L"}${px.toFixed(1)},${py.toFixed(1)}`;
  }).join(" ") + " Z")).join(" ");
}

function featureCentroid(feature, project){
  let bestRing = [];
  rings(feature.geometry).forEach((polygon) => {
    const ring = polygon[0] || [];
    if(ring.length > bestRing.length) bestRing = ring;
  });
  if(!bestRing.length) return [0, 0];
  let sx = 0;
  let sy = 0;
  bestRing.forEach(([x, y]) => { sx += x; sy += y; });
  return project([sx / bestRing.length, sy / bestRing.length]);
}

function renderMap(data){
  const target = document.getElementById("map-container");
  const geojson = data?.map?.geojson;
  if(!geojson){
    target.innerHTML = `<div class="empty-state">Mapa indisponível.</div>`;
    return;
  }

  const width = Math.max(420, target.clientWidth || 520);
  const height = Math.max(520, target.clientHeight || 560);
  const bounds = getBounds(geojson);
  const project = createProjector(bounds, width, height, 10, 1.05);
  const cities = {};
  (data?.map?.cidades_list || []).forEach((row) => { cities[norm(row.municipio)] = row; });

  const parts = [
    `<svg class="map-svg" viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Mapa operacional da região Atlântico">`,
    `<rect class="map-bg" width="${width}" height="${height}"/>`
  ];

  (geojson.features || []).forEach((feature) => {
    const name = feature?.properties?.name || feature?.properties?.description || "";
    const stat = cities[norm(name)] || {incidencias: 0, regional: feature?.properties?.regional || "", tem_equipe_ativa: false};
    parts.push(
      `<path d="${geometryPath(feature, project)}" fill="${countScaleColor(stat.incidencias)}" class="map-outline"><title>${escapeHtml(name)}: ${escapeHtml(formatInt(stat.incidencias || 0))}</title></path>`
    );
  });

  (geojson.features || []).forEach((feature) => {
    const name = feature?.properties?.name || feature?.properties?.description || "";
    const stat = cities[norm(name)] || {incidencias: 0, regional: feature?.properties?.regional || "", tem_equipe_ativa: false};
    const [baseX, baseY] = featureCentroid(feature, project);
    const offset = CITY_OFFSETS[norm(name)] || {dx: 0, dy: 0};
    const x = baseX + offset.dx;
    const y = baseY + offset.dy;
    const label = shortCityLabel(name);
    const fontSize = getLabelFontSize(label);
    if(stat.tem_equipe_ativa){
      parts.push(`<image href="assets/car.png" x="${(x - 8).toFixed(1)}" y="${(y - 25).toFixed(1)}" width="16" height="16"/>`);
    }
    parts.push(`<text x="${x.toFixed(1)}" y="${(y - 7).toFixed(1)}" text-anchor="middle" class="city-label" style="font-size:${fontSize}px;fill:${regionColor(stat.regional || feature?.properties?.regional || "ATLANTICO")};">${escapeHtml(label)}</text>`);
    parts.push(`<text x="${x.toFixed(1)}" y="${(y + 9).toFixed(1)}" text-anchor="middle" class="city-count">${escapeHtml(formatInt(stat.incidencias || 0))}</text>`);
  });

  const legendX = 10;
  const legendY = height - 156;
  const scaleItems = [
    ["#d1d5db", "0 incidências"],
    ["#facc15", "1-2 incidências"],
    ["#f97316", "3-5 incidências"],
    ["#dc2626", "6-10 incidências"],
    ["#4c1d95", ">10 incidências"],
  ];
  parts.push(`<rect class="legend-box" x="${legendX}" y="${legendY}" width="146" height="140" rx="12"/>`);
  parts.push(`<text class="legend-title" x="${legendX + 12}" y="${legendY + 18}">Legenda</text>`);
  scaleItems.forEach(([color, label], index) => {
    const rowY = legendY + 34 + (index * 18);
    parts.push(`<rect x="${legendX + 12}" y="${rowY - 10}" width="12" height="12" rx="3" fill="${color}"/>`);
    parts.push(`<text class="legend-text" x="${legendX + 30}" y="${rowY}">${escapeHtml(label)}</text>`);
  });
  parts.push(`<image href="assets/car.png" x="${legendX + 11}" y="${legendY + 114}" width="14" height="14"/>`);
  parts.push(`<text class="legend-text" x="${legendX + 30}" y="${legendY + 125}">Equipe na cidade</text>`);
  parts.push(`</svg>`);

  target.innerHTML = parts.join("");
}

function rankingHeaders(columns){
  return `
    <tr>${columns.map((column) => `<th${column.className ? ` class="${column.className}"` : ""}>${escapeHtml(column.label)}</th>`).join("")}</tr>
  `;
}

function rankingRow(row, index, columns){
  const regional = regionClass(row.regional);
  const municipio = row.municipio || row.sucursal || "—";
  const endereco = row.endereco || row.bairro || "—";
  const equipe = row.equipe || "—";
  const pontoEletrico = row.ponto_eletrico || "—";
  const avisos = formatInt(row.avisos || 0);
  const values = {
    ordem: index + 1,
    inc: `<span class="cell-main">${escapeHtml(stripLeadingDoubleZero(row.numero_display || row.numero || ""))}</span>`,
    duracao: `<span class="cell-main" style="color:${durationHoursColor(row.duracao_horas)};">${escapeHtml(formatDurationLabel(row))}</span>`,
    chi: `<span class="cell-main">${escapeHtml(formatInt(Math.round(safeFloat(row.conh))))}</span>`,
    cli: `<span class="cell-main">${escapeHtml(formatInt(row.clientes_afetados || 0))}</span>`,
    avisos: `<span class="cell-main">${escapeHtml(avisos)}</span>`,
    ponto_eletrico: escapeHtml(pontoEletrico),
    municipio: escapeHtml(municipio),
    endereco: `<span class="cell-subtle">${escapeHtml(endereco)}</span>`,
    regional: `<span class="reg-pill reg-${regional}">${escapeHtml(String(row.regional || "").toUpperCase())}</span>`,
    equipe: escapeHtml(equipe),
  };
  const trClass = hasPendencia(row) ? "rank-row-pendencia" : "";
  return `
    <tr class="${trClass}">${columns.map((column) => `<td${column.className ? ` class="${column.className}"` : ""}>${values[column.key] ?? "—"}</td>`).join("")}</tr>
  `;
}

function renderRankings(data){
  const durationRows = (data?.rankings?.duracao || []).slice(0, 10);
  const chiRows = (data?.rankings?.chi || []).slice(0, 5);
  const cliRows = (data?.rankings?.cli || []).slice(0, 5);

  const durationCols = [
    {key: "ordem", label: "Ordem", className: "order-col"},
    {key: "inc", label: "Inc", className: "inc-col"},
    {key: "duracao", label: "Duração", className: "metric-col"},
    {key: "avisos", label: "Qtde Avisos", className: "avisos-col"},
    {key: "municipio", label: "Município"},
    {key: "endereco", label: "Endereço"},
    {key: "regional", label: "Regional", className: "region-col"},
    {key: "equipe", label: "Equipe", className: "team-col"},
  ];
  const smallCols = (metricLabel, metricKey) => ([
    {key: "ordem", label: "Ordem", className: "order-col"},
    {key: "inc", label: "Inc", className: "inc-col"},
    {key: metricKey, label: metricLabel, className: "metric-col"},
    {key: "ponto_eletrico", label: "Ponto Elétrico", className: "point-col"},
    {key: "municipio", label: "Município"},
    {key: "endereco", label: "Endereço"},
    {key: "regional", label: "Regional", className: "region-col"},
    {key: "equipe", label: "Equipe", className: "team-col"},
  ]);
  const chiCols = smallCols("CHI", "chi");
  const cliCols = smallCols("CLI", "cli");

  document.getElementById("thead-duration").innerHTML = rankingHeaders(durationCols);
  document.getElementById("thead-chi").innerHTML = rankingHeaders(chiCols);
  document.getElementById("thead-cli").innerHTML = rankingHeaders(cliCols);

  document.getElementById("tbody-duration").innerHTML = durationRows.length
    ? durationRows.map((row, index) => rankingRow(row, index, durationCols)).join("")
    : `<tr><td colspan="8" class="cell-subtle">Sem ocorrências no recorte atual.</td></tr>`;

  document.getElementById("tbody-chi").innerHTML = chiRows.length
    ? chiRows.map((row, index) => rankingRow(row, index, chiCols)).join("")
    : `<tr><td colspan="8" class="cell-subtle">Sem ocorrências no recorte atual.</td></tr>`;

  document.getElementById("tbody-cli").innerHTML = cliRows.length
    ? cliRows.map((row, index) => rankingRow(row, index, cliCols)).join("")
    : `<tr><td colspan="8" class="cell-subtle">Sem ocorrências no recorte atual.</td></tr>`;
}


function renderTeamsChart(data){
  const svg = document.getElementById("teams-chart");
  const items = Array.isArray(data?.teams?.rows) ? data.teams.rows.slice() : [];
  if(!items.length){
    svg.setAttribute("viewBox", "0 0 900 260");
    svg.innerHTML = `<text x="50%" y="50%" text-anchor="middle" fill="#64748b" font-size="15">Sem equipes ativas.</text>`;
    return;
  }

  const minWidth = 1100;
  const width = Math.max(minWidth, items.length * 52);
  const height = 300;
  const left = 42;
  const right = 18;
  const top = 18;
  const bottom = 84;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const maxValue = Math.max(...items.map((item) => safeInt(item.incidencias)), 1);
  const step = plotWidth / items.length;
  const barWidth = Math.max(16, Math.min(step * 0.66, 34));

  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.innerHTML = "";

  for(let tick = 0; tick <= maxValue; tick += 1){
    const y = top + plotHeight - (tick / maxValue) * plotHeight;
    svg.insertAdjacentHTML("beforeend", `<line class="chart-grid-line" x1="${left}" y1="${y.toFixed(1)}" x2="${width - right}" y2="${y.toFixed(1)}" />`);
    svg.insertAdjacentHTML("beforeend", `<text class="chart-y-label" x="${left - 10}" y="${(y + 4).toFixed(1)}" text-anchor="end">${tick}</text>`);
  }
  svg.insertAdjacentHTML("beforeend", `<line class="chart-axis" x1="${left}" y1="${top + plotHeight}" x2="${width - right}" y2="${top + plotHeight}" />`);

  items.forEach((item, index) => {
    const value = safeInt(item.incidencias);
    const x = left + index * step + (step - barWidth) / 2;
    const h = Math.max(2, (value / maxValue) * (plotHeight - 2));
    const y = top + plotHeight - h;
    const cx = x + (barWidth / 2);
    const fill = regionColor(item.regional);
    const labelParts = String(item.equipe || "—").split("-");
    const line1 = labelParts[0] || "—";
    const line2 = labelParts[1] || "";
    const line3 = labelParts.slice(2).join("-") || "";

    svg.insertAdjacentHTML("beforeend", `
      <rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barWidth.toFixed(1)}" height="${h.toFixed(1)}" rx="4" fill="${fill}" opacity="0.95"/>
      <text class="chart-value-label" x="${cx.toFixed(1)}" y="${(y - 6).toFixed(1)}" text-anchor="middle">${value}</text>
      <text class="chart-team-label" x="${cx.toFixed(1)}" y="${(top + plotHeight + 15).toFixed(1)}" text-anchor="middle">
        <tspan x="${cx.toFixed(1)}" dy="0">${escapeHtml(line1)}</tspan>
        ${line2 ? `<tspan x="${cx.toFixed(1)}" dy="10">${escapeHtml(line2)}</tspan>` : ""}
        ${line3 ? `<tspan x="${cx.toFixed(1)}" dy="10">${escapeHtml(line3)}</tspan>` : ""}
      </text>
    `);
  });
}

function renderAll(config, data){
  setHeader(config, data);
  renderSummary(data);
  renderMap(data);
  renderRankings(data);
  renderTeamsChart(data);
}

function scheduleAutoRefresh(seconds){
  if(refreshTimer) clearInterval(refreshTimer);
  const interval = Math.max(30, safeInt(seconds || 180)) * 1000;
  refreshTimer = setInterval(async () => {
    try{
      currentData = await loadData();
      renderAll(currentConfig, currentData);
    }catch(err){
      console.error("Falha ao atualizar dashboard:", err);
    }
  }, interval);
}

async function boot(){
  try{
    currentConfig = await loadConfig();
    currentData = await loadData();
    renderAll(currentConfig, currentData);
    scheduleAutoRefresh(currentConfig?.refresh_seconds || currentData?.meta?.refresh_seconds || 180);
  }catch(err){
    document.body.innerHTML = `<div class="page-shell"><div class="card empty-state">Erro ao carregar dados: ${escapeHtml(err.message)}</div></div>`;
  }
}

window.addEventListener("resize", () => {
  if(!currentConfig || !currentData) return;
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => renderAll(currentConfig, currentData), 120);
});

boot();
