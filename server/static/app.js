// 랜덤 여행 챌린지 프론트엔드 (빌드 도구 없는 바닐라 JS + 네이버 지도, 키가 없으면 Leaflet·OpenStreetMap)
// LLM이 만든 텍스트와 주소는 모두 textContent로만 넣어 XSS를 막는다.

const $ = (sel) => document.querySelector(sel);
const SVG_NS = "http://www.w3.org/2000/svg";

const STEPS = [
  ["research", "명소·맛집·교통 리서치"],
  ["plan", "일정 짜기"],
  ["review", "일정 검수"],
  ["budget", "예산 계산"],
  ["done", "완성"],
];
const CAT_ICON = { 관광: "pin", 식사: "meal", 카페: "cafe", 체험: "ticket", 쇼핑: "bag", 휴식: "leaf", 이동: "train", 숙소: "bed" };
const ROUTE_SKIP = new Set(["이동", "숙소"]);

const ui = {
  nights: 1,
  departures: {},
  ranges: {},
  landed: null, // { id, challenge }: 지금 지도에 꽂혀 있는 다트
  queued: "",
  abort: null,
  busy: false,
  tickTimer: null,
  startedAt: 0,
};

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

function icon(name) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", "ico");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(SVG_NS, "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const won = (n) => `${Number(n || 0).toLocaleString("ko-KR")}원`;

// ---------------------------------------------------------------- 초기화
async function init() {
  const meta = await fetch("/api/meta").then((r) => r.json());
  for (const d of meta.departures) ui.departures[d.name] = d;
  for (const d of meta.durations) ui.ranges[d.nights] = d.range_km;
  document.body.dataset.geocoder = meta.geocoder || "nominatim";

  const seg = $("#durations");
  for (const d of meta.durations) {
    seg.append(el("button", {
      type: "button", role: "radio", "data-nights": d.nights,
      "aria-checked": String(d.nights === ui.nights), text: d.label,
      onclick: () => selectNights(d.nights),
    }));
  }
  seg.addEventListener("keydown", onSegKey);

  for (const d of meta.departures) $("#departure").append(el("option", { value: d.name, text: d.name }));
  for (const name of meta.budget_levels)
    $("#budget").append(el("option", { value: name, text: name, selected: name === "보통" }));

  const today = new Date();
  $("#startDate").min = new Date(today.getTime() - today.getTimezoneOffset() * 60000).toISOString().slice(0, 10);

  for (const id of ["#departure", "#travelers", "#budget", "#startDate"])
    $(id).addEventListener("change", updateOptSummary);
  $("#departure").addEventListener("change", drawRange);
  updateOptSummary();
  $("#heroDart")?.insertAdjacentHTML("beforeend", DART_PARTS);

  $("#setupForm").addEventListener("submit", (e) => { e.preventDefault(); start([]); });
  $("#actionbar").addEventListener("click", onAction);
  $("#stage").addEventListener("click", onAction);
  // 공유 링크의 다트를 꽂으려면 지도가 먼저 떠 있어야 한다
  await initMap(meta.naver_map_key);

  const shared = new URLSearchParams(location.search).get("id");
  if (shared) loadShared(shared);
}

function selectNights(n) {
  ui.nights = n;
  document.querySelectorAll("#durations button").forEach((b) => {
    const on = Number(b.dataset.nights) === n;
    b.setAttribute("aria-checked", String(on));
    b.tabIndex = on ? 0 : -1;
  });
  if (!ui.busy && !document.body.classList.contains("has-result")) drawRange();
}

// ---------------------------------------------------------------- 지도
// 지도 엔진은 둘 중 하나다: NAVER_MAP_KEY_ID가 있으면 네이버 지도, 없거나 인증에 실패하면 OpenStreetMap(Leaflet).
// 다트 연출과 범위 원은 아래 엔진 겉면(size·toPoint·fit·circle·html …)만 쓰므로 어느 쪽이든 똑같이 돈다.
const geo = { map: null, view: null, ro: null, naverFailed: false };
const KOREA = [[33.1, 125.6], [38.6, 129.8]];
const MAP_PAD = 16;
const LEAFLET = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min";
const NAVER_MAPS = "https://oapi.map.naver.com/openapi/v3/maps.js";
// 다트: 끝(촉)이 (6, 58)에 오도록 그린 64×64 그림을 DART_PX 크기로 보여 준다. 촉 기준으로 지도에 꽂힌다.
// 색(그라디언트)은 index.html의 <defs>에 있다.
const DART_PX = 104;
const DART_TIP = [(6 * DART_PX) / 64, (58 * DART_PX) / 64];
const DART_PARTS =
  '<path d="M0 0 L14.5 -2.7 Q16 0 14.5 2.7 Z" fill="url(#dart-tip)"/>' +
  '<rect x="13.6" y="-3.6" width="3.4" height="7.2" rx="1.7" fill="url(#dart-collar)"/>' +
  '<rect x="16" y="-4.6" width="21" height="9.2" rx="4.6" fill="url(#dart-barrel)"/>' +
  '<path d="M20 -2.7 H33" stroke="#fff" stroke-opacity=".5" stroke-width="1.3" stroke-linecap="round"/>' +
  '<rect x="36" y="-2" width="9" height="4" rx="2" fill="url(#dart-barrel)"/>' +
  '<path d="M42 0 C44 -10 50 -18 57.5 -17 C64 -16 63.5 -6 58 0 Z" fill="url(#dart-wing-a)"/>' +
  '<path d="M42 0 C44 10 50 18 57.5 17 C64 16 63.5 6 58 0 Z" fill="url(#dart-wing-b)"/>' +
  '<path d="M42 0 H58.5" stroke="#1557c0" stroke-opacity=".55" stroke-width="1.1" stroke-linecap="round"/>' +
  '<path d="M47 -9 C50 -13.5 54 -15 57 -14.4" fill="none" stroke="#fff" stroke-opacity=".45" stroke-width="1.2" stroke-linecap="round"/>';
const DART_BODY = `<g transform="translate(6 58) rotate(-50)">${DART_PARTS}</g>`;
function dartSvg(withShadow) {
  return '<svg viewBox="0 0 64 64" aria-hidden="true">' +
    (withShadow ? '<ellipse cx="12" cy="59.5" rx="9" ry="2.8" fill="rgba(25,31,40,.26)"/>' : "") +
    DART_BODY + "</svg>";
}

function loadAsset(tag, attrs) {
  return new Promise((resolve, reject) =>
    document.head.append(el(tag, { ...attrs, onload: resolve, onerror: reject })));
}

// 네이버 지도 JS API v3. 키는 콘솔에 등록한 Web 서비스 URL에서만 통하고, 아니면 navermap_authFailure가 불린다.
async function naverEngine(key) {
  await loadAsset("script", { src: `${NAVER_MAPS}?ncpKeyId=${encodeURIComponent(key)}` });
  const N = window.naver?.maps;
  if (!N?.Map) throw new Error("naver maps not loaded");
  const ll = (lat, lon) => new N.LatLng(lat, lon);
  // 네이버 지도는 자기 상자에 px 크기를 인라인으로 박는다. 그 크기가 페이지 배치를 붙잡지 않도록
  // CSS로 크기가 정해지는 #map 안에 절대 위치 캔버스를 두고 거기에 띄운다.
  const canvas = el("div", { class: "map-canvas" });
  $("#map").append(canvas);
  const map = new N.Map(canvas, {
    center: ll(36, 127.7), zoom: 7, scrollWheel: false,
    zoomControl: true, zoomControlOptions: { position: N.Position.TOP_LEFT, style: N.ZoomControlStyle.SMALL },
  });
  const groups = { range: [], darts: [] };
  const add = (g, overlay) => { groups[g].push(overlay); return overlay; };
  return {
    kind: "naver",
    size: () => { const s = map.getSize(); return { x: s.width, y: s.height }; },
    toPoint: (lat, lon) => map.getProjection().fromCoordToOffset(ll(lat, lon)),
    fit: ([[s, w], [n, e]], maxZoom = 21) => {
      map.fitBounds(new N.LatLngBounds(ll(s, w), ll(n, e)), { top: MAP_PAD, right: MAP_PAD, bottom: MAP_PAD, left: MAP_PAD });
      if (map.getZoom() > maxZoom) map.setZoom(maxZoom, false);
    },
    flyTo: (lat, lon, zoom) => map.morph(ll(lat, lon), zoom, { duration: 1800 }),
    jump: (lat, lon, zoom) => { map.setZoom(zoom, false); map.setCenter(ll(lat, lon)); },
    resize: () => { const box = $("#map"); map.setSize(new N.Size(box.clientWidth, box.clientHeight)); },
    clear: (g) => { for (const overlay of groups[g].splice(0)) overlay.setMap(null); },
    circle: (g, lat, lon, radiusM, { stroke, fill, fillOpacity }) => add(g, new N.Circle({
      map, center: ll(lat, lon), radius: radiusM, clickable: false,
      strokeColor: stroke || fill, strokeWeight: stroke ? 2 : 0, strokeOpacity: stroke ? 1 : 0, strokeStyle: "shortdash",
      fillColor: fill, fillOpacity,
    })),
    html: (g, lat, lon, node, [ax, ay], size) => add(g, new N.Marker({
      map, position: ll(lat, lon), clickable: false,
      icon: { content: node, anchor: new N.Point(ax, ay), ...(size ? { size: new N.Size(size[0], size[1]) } : {}) },
    })),
    destroy: () => { map.destroy(); canvas.remove(); },
  };
}

// OpenStreetMap 대체 지도. OSM 프랑스의 Humanitarian(HOT) 타일은 색이 부드럽고 지명이 한글로 나오며 키가 필요 없다.
async function osmEngine() {
  if (!window.L) {
    await Promise.all([
      loadAsset("link", { rel: "stylesheet", href: `${LEAFLET}.css`, crossorigin: "" }),
      loadAsset("script", { src: `${LEAFLET}.js`, crossorigin: "" }),
    ]);
  }
  const map = L.map("map", { scrollWheelZoom: false });
  L.tileLayer("https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png", {
    subdomains: "abc",
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, Tiles style by <a href="https://www.hotosm.org/">HOT</a>, hosted by <a href="https://openstreetmap.fr/">OSM France</a>',
  }).addTo(map);
  const groups = { range: L.layerGroup().addTo(map), darts: L.layerGroup().addTo(map) };
  return {
    kind: "osm",
    size: () => map.getSize(),
    toPoint: (lat, lon) => map.latLngToContainerPoint([lat, lon]),
    fit: (bounds, maxZoom) => map.fitBounds(bounds, { padding: [MAP_PAD, MAP_PAD], maxZoom }),
    flyTo: (lat, lon, zoom) => map.flyTo([lat, lon], zoom, { duration: 1.8, easeLinearity: 0.2 }),
    jump: (lat, lon, zoom) => map.setView([lat, lon], zoom),
    resize: () => map.invalidateSize(),
    clear: (g) => groups[g].clearLayers(),
    circle: (g, lat, lon, radiusM, { stroke, fill, fillOpacity }) => L.circle([lat, lon], stroke
      ? { radius: radiusM, color: stroke, weight: 2, dashArray: "6 7", fillColor: fill, fillOpacity }
      : { radius: radiusM, stroke: false, fillColor: fill, fillOpacity }).addTo(groups[g]),
    html: (g, lat, lon, node, anchor, size) => L.marker([lat, lon], {
      icon: L.divIcon({ className: "", html: node, iconSize: size || null, iconAnchor: anchor }), interactive: false,
    }).addTo(groups[g]),
    destroy: () => map.remove(),
  };
}

async function initMap(naverKey) {
  geo.ro = new ResizeObserver(() => {
    if (!geo.map) return;
    geo.map.resize();
    if (geo.view && !ui.busy && !ui.landed && !document.body.classList.contains("has-result")) geo.map.fit(geo.view, 9);
  });
  window.navermap_authFailure = () => {
    geo.naverFailed = true;
    console.warn("네이버 지도 인증에 실패해 OpenStreetMap 지도로 바꿔요. NAVER_MAP_KEY_ID와 콘솔의 Web 서비스 URL을 확인하세요.");
    if (geo.map?.kind === "naver") switchToOsm();
  };
  let engine = null;
  if (naverKey) {
    engine = await naverEngine(naverKey).catch((err) => { console.warn("네이버 지도를 불러오지 못했어요", err); return null; });
    if (engine && geo.naverFailed) { engine.destroy(); engine = null; freshMapBox(); }
  }
  engine ??= await osmEngine().catch(() => null);
  if (!engine) {
    $("#map").textContent = "지도를 불러오지 못했어요. 다트는 그대로 던질 수 있어요.";
    return;
  }
  mountMap(engine);
}

function mountMap(engine) {
  geo.map = engine;
  document.body.dataset.map = engine.kind; // 지도 출처 표기와 다크 모드 필터를 엔진에 맞춘다
  geo.ro.disconnect();
  geo.ro.observe($("#map"));
  geo.map.fit(KOREA);
  drawRange();
  if (ui.landed) showLanding(ui.landed.challenge);
}

// 네이버 지도가 남긴 스타일·요소가 Leaflet에 섞이지 않게 지도 상자를 새것으로 갈아 끼운다
function freshMapBox() {
  const old = $("#map");
  old.replaceWith(el("div", { class: old.className, id: "map", role: "img", "aria-label": old.getAttribute("aria-label") }));
  fx.layer = null;
  stopAiming();
}

async function switchToOsm() {
  try { geo.map.destroy(); } catch { /* 이미 망가진 지도 */ }
  geo.map = null;
  freshMapBox();
  const engine = await osmEngine().catch(() => null);
  if (engine) mountMap(engine);
}

function drawRange() {
  const depName = $("#departure").value;
  const dep = ui.departures[depName];
  const [lo, hi] = ui.ranges[ui.nights] || [0, null];
  const legend = $("#mapLegend");
  legend.replaceChildren(
    el("span", { class: "key" }, el("span", { class: "ring" }),
      hi ? `${depName}에서 ${lo}~${hi}km 안의 육지` : `${depName}에서 ${lo}km 이상 떨어진 전국 어디든`),
    el("span", { class: "key" }, el("span", { class: "hole" }), `${lo}km 안은 너무 가까워서 제외`));
  if (!geo.map || !dep) return;
  geo.map.clear("range");
  const accent = cssVar("--target");
  const ink = cssVar("--ink");
  if (hi) geo.map.circle("range", dep.lat, dep.lon, hi * 1000, { stroke: accent, fill: accent, fillOpacity: 0.06 });
  geo.map.circle("range", dep.lat, dep.lon, lo * 1000, { fill: ink, fillOpacity: 0.16 });
  geo.map.html("range", dep.lat, dep.lon, el("span", { class: "dep-dot" }), [6, 6], [12, 12]);
  geo.map.html("range", dep.lat, dep.lon, el("span", { class: "dep-label", text: `${depName} 출발` }), [-8, 10]);
  // 다트는 남한 육지에만 꽂히므로, 화면은 범위 원과 남한이 겹치는 부분에 맞춘다.
  const ring = hi ? circleBounds(dep.lat, dep.lon, hi) : null;
  const view = ring
    ? [[Math.max(ring[0][0], 33.1), Math.max(ring[0][1], 125.6)],
       [Math.min(ring[1][0], 38.45), Math.min(ring[1][1], 129.8)]]
    : [[33.1, 125.6], [38.45, 129.8]];
  geo.view = view;
  geo.map.fit(view, 9);
}

// 반지름 km 원을 감싸는 위경도 사각형 [[남, 서], [북, 동]]
function circleBounds(lat, lon, km) {
  const dLat = km / 111.32;
  const dLon = km / (111.32 * Math.cos((lat * Math.PI) / 180));
  return [[lat - dLat, lon - dLon], [lat + dLat, lon + dLon]];
}

// 꽂힌 다트와 그 옆 동네 이름표. 아래 카드를 보지 않아도 지도만으로 어디인지 알 수 있게 한다.
function pinDart(d) {
  const dart = el("div", { class: "dart-icon" });
  dart.innerHTML = dartSvg(true);
  geo.map.html("darts", d.lat, d.lon, dart, DART_TIP, [DART_PX, DART_PX]);
  if (!d.name) return;
  geo.map.html("darts", d.lat, d.lon, el("span", { class: "dart-label", text: d.name }), [-12, -6]);
}

// ---- 던지기 연출: 조준 → 비행 → 충돌 ----
const fx = { layer: null, reticle: null, raf: 0, pos: { x: 0, y: 0 }, throwId: 0 };
const EASE_OUT = "cubic-bezier(.16, .84, .24, 1)";

function fxLayer() {
  if (!fx.layer || !fx.layer.isConnected) {
    fx.layer = el("div", { class: "fx-layer" });
    $("#map").append(fx.layer);
  }
  return fx.layer;
}

// 연출용 다트는 지도 좌표가 아니라 화면 좌표에 떠 있어서, 남아 있으면 지도가 움직인 뒤
// 엉뚱한 자리에 두 번째 다트로 보인다. 새로 던지거나 결과를 찍기 전에 조준경만 남기고 지운다.
function clearFx() {
  if (!fx.layer) return;
  for (const node of [...fx.layer.children]) if (node !== fx.reticle) node.remove();
}

function place(node, p) {
  node.style.left = `${p.x}px`;
  node.style.top = `${p.y}px`;
  return node;
}

function startAiming() {
  stopAiming();
  if (!geo.map || reducedMotion()) return;
  fx.reticle = el("div", { class: "reticle" });
  fxLayer().append(fx.reticle);
  const t0 = performance.now();
  const step = (t) => {
    const { x: w, y: h } = geo.map.size();
    const k = (t - t0) / 1000;
    fx.pos = {
      x: w / 2 + w * 0.3 * Math.sin(k * 1.35) + w * 0.07 * Math.sin(k * 4.3 + 1),
      y: h / 2 + h * 0.27 * Math.cos(k * 1.1) + h * 0.06 * Math.sin(k * 3.7),
    };
    fx.reticle.style.transform = `translate(${fx.pos.x}px, ${fx.pos.y}px)`;
    fx.raf = requestAnimationFrame(step);
  };
  fx.raf = requestAnimationFrame(step);
}

function stopAiming() {
  cancelAnimationFrame(fx.raf);
  fx.raf = 0;
  fx.reticle?.remove();
  fx.reticle = null;
}

// 꽂힌 다트의 촉이 가리키는 화면 방향(도, 시계 방향). dartSvg의 rotate(-50)과 짝을 이룬다.
const TIP_ANGLE = 130;
const FLIGHT_MS = 540;
const TRAIL_SEGMENTS = 9;

// 오른쪽 아래에서 포물선을 그리며 날아와, 꽂힌 자세 그대로 과녁에 닿는다.
// 좌표는 모두 과녁 p를 원점으로 한 상대값이고, 다트는 진행 방향(접선)을 따라 기운다.
function flyDart(dartEl, shadow, layer, p, { w, h }) {
  const start = { x: Math.max(240, w - p.x + 70), y: Math.max(110, (h - p.y) * 0.6 + 50) };
  const dist = Math.hypot(start.x, start.y);
  const rad = (TIP_ANGLE * Math.PI) / 180;
  // 끝 접선이 촉 방향과 같아지도록 제어점을 촉 반대편에 둔다. 지도 위로 너무 솟지 않게 제한.
  const reach = Math.min(dist * 0.5, (p.y + 30) / Math.sin(rad));
  const ctrl = { x: -Math.cos(rad) * reach, y: -Math.sin(rad) * reach };
  const at = (t) => ({
    x: (1 - t) * (1 - t) * start.x + 2 * t * (1 - t) * ctrl.x,
    y: (1 - t) * (1 - t) * start.y + 2 * t * (1 - t) * ctrl.y,
  });
  const tilt = (t) => {
    const dx = (1 - t) * (ctrl.x - start.x) - t * ctrl.x;
    const dy = (1 - t) * (ctrl.y - start.y) - t * ctrl.y;
    const deg = (((Math.atan2(dy, dx) * 180) / Math.PI - TIP_ANGLE) % 360 + 360) % 360;
    return deg > 180 ? deg - 360 : deg;
  };

  const trail = document.createElementNS(SVG_NS, "svg");
  trail.setAttribute("class", "fx-trail");
  const segs = Array.from({ length: TRAIL_SEGMENTS }, (_, i) => {
    const line = document.createElementNS(SVG_NS, "line");
    const k = (i + 1) / TRAIL_SEGMENTS;
    line.setAttribute("stroke-opacity", (k * k * 0.55).toFixed(3));
    line.setAttribute("stroke-width", (1 + k * 3).toFixed(2));
    trail.append(line);
    return line;
  });
  layer.insertBefore(trail, shadow);

  return new Promise((resolve) => {
    const t0 = performance.now();
    const frame = (now) => {
      const k = Math.min(1, Math.max(0, (now - t0) / FLIGHT_MS)); // rAF 시각이 t0보다 이를 수 있다
      const t = Math.pow(k, 1.25); // 손을 떠나며 붙는 가속
      const pos = at(t);
      const scale = 1.6 - 0.6 * t;
      dartEl.style.opacity = String(Math.min(1, k / 0.15));
      dartEl.style.transform = `translate(${pos.x}px, ${pos.y}px) rotate(${tilt(t)}deg) scale(${scale})`;
      // 그림자는 포물선 아래 직선 경로(바닥)를 따라오다 촉 밑에서 만난다
      shadow.style.opacity = String(t * t);
      shadow.style.transform = `translate(${start.x * (1 - t)}px, ${start.y * (1 - t)}px) scale(${1 + 1.5 * (1 - t)})`;
      const tail = Math.max(0, t - 0.34);
      segs.forEach((line, i) => {
        const a = at(tail + ((t - tail) * i) / TRAIL_SEGMENTS);
        const b = at(tail + ((t - tail) * (i + 1)) / TRAIL_SEGMENTS);
        line.setAttribute("x1", p.x + a.x); line.setAttribute("y1", p.y + a.y);
        line.setAttribute("x2", p.x + b.x); line.setAttribute("y2", p.y + b.y);
      });
      if (k < 1) raf = requestAnimationFrame(frame);
      else finish();
    };
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      cancelAnimationFrame(raf);
      dartEl.style.opacity = "1";
      dartEl.style.transform = "";
      shadow.style.opacity = "1";
      shadow.style.transform = "";
      trail.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 200, fill: "forwards" }).finished.then(() => trail.remove());
      resolve();
    };
    let raf = requestAnimationFrame(frame);
    // 탭이 가려져 프레임이 멈춰도 일정이 다트를 기다리다 멎지 않게 한다
    setTimeout(finish, FLIGHT_MS + 250);
  });
}

async function throwOne(at, hit, label) {
  const p = geo.map.toPoint(at.lat, at.lon);
  if (fx.reticle) {
    cancelAnimationFrame(fx.raf);
    fx.reticle.classList.remove("locked");
    await fx.reticle.animate(
      [{ transform: `translate(${fx.pos.x}px, ${fx.pos.y}px)` }, { transform: `translate(${p.x}px, ${p.y}px)` }],
      { duration: 420, easing: "cubic-bezier(.3, .7, .2, 1)", fill: "forwards" }).finished;
    fx.pos = { x: p.x, y: p.y };
    fx.reticle.classList.add("locked");
  }
  const layer = fxLayer();
  const { x: w, y: h } = geo.map.size();
  const shadow = place(el("div", { class: "fx-shadow" }), p);
  const dartEl = place(el("div", { class: "fx-dart" }), p);
  dartEl.innerHTML = dartSvg(false);
  layer.append(shadow, dartEl);

  await flyDart(dartEl, shadow, layer, p, { w, h });

  // 충돌: 파문, 파편, 박히며 떨리는 진동, (명중이면) 지도 흔들림
  const ring = place(el("div", { class: `fx-ring${hit ? "" : " miss"}` }), p);
  layer.append(ring);
  ring.animate([{ transform: "scale(.3, .17)", opacity: 0.95 }, { transform: `scale(${hit ? 5.5 : 3.2}, ${hit ? 3.2 : 1.85})`, opacity: 0 }],
    { duration: hit ? 800 : 560, easing: "cubic-bezier(.1, .7, .2, 1)" }).finished.then(() => ring.remove());
  const sparks = hit ? 8 : 5;
  for (let i = 0; i < sparks; i++) {
    const a = (i / sparks) * 360 + Math.random() * 28;
    const far = (hit ? 24 : 14) + Math.random() * 14;
    const spark = place(el("div", { class: `fx-spark${hit ? "" : " miss"}` }), p);
    layer.append(spark);
    spark.animate([
      { transform: `rotate(${a}deg) translateX(5px) scaleX(1)`, opacity: 1 },
      { transform: `rotate(${a}deg) translateX(${far}px) scaleX(.2)`, opacity: 0 },
    ], { duration: 300 + Math.random() * 160, easing: EASE_OUT }).finished.then(() => spark.remove());
  }
  // 첫 프레임은 날아오던 방향으로 살짝 더 파고든 뒤, 촉을 축으로 떨림이 잦아든다
  dartEl.animate(
    [0, -11, 8, -5, 3, -1.2, 0].map((deg, i) => ({ transform: `${i ? "" : "translate(-2.6px, 3.1px) "}rotate(${deg}deg)` })),
    { duration: 680, easing: "ease-out" });
  if (hit) {
    $("#mapWrap").animate(
      [[0, 0], [-4, 3], [4, -2], [-3, 1], [2, -1], [0, 0]].map(([x, y]) => ({ transform: `translate(${x}px, ${y}px)` })),
      { duration: 320, easing: "ease-out" });
    return { dartEl, shadow };
  }

  const tag = place(el("div", { class: "fx-label", text: label }), p);
  layer.append(tag);
  tag.animate([{ opacity: 0, transform: "translate(-50%, -90px)" }, { opacity: 1, transform: "translate(-50%, -104px)" }],
    { duration: 220, easing: EASE_OUT, fill: "forwards" });
  await sleep(520);
  await Promise.all([
    dartEl.animate([{ transform: "rotate(0deg)", opacity: 1 }, { transform: "translate(6px, 14px) rotate(38deg)", opacity: 0 }],
      { duration: 320, easing: "cubic-bezier(.5, 0, .9, .4)", fill: "forwards" }).finished,
    shadow.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 320, fill: "forwards" }).finished,
    tag.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 320, fill: "forwards" }).finished,
  ]);
  dartEl.remove(); shadow.remove(); tag.remove();
  return null;
}

const MISS_LABEL = { "빈 땅": "빗나감", 국외: "국경 밖", "이미 나온 곳": "또 나옴" };

async function animateThrow(ch) {
  if (!geo.map) return;
  const id = ++fx.throwId; // 이 던지기가 끝나기 전에 다른 던지기·결과 표시가 시작되면 물러난다
  const stale = () => id !== fx.throwId;
  geo.map.clear("darts");
  clearFx();
  const d = ch.destination;
  if (reducedMotion()) {
    stopAiming();
    showLanding(ch);
    return;
  }
  const { x: w, y: h } = geo.map.size();
  const inView = ({ x, y }) => x >= 0 && y >= 0 && x <= w && y <= h;
  const misses = (ch.misses || []).filter((m) => inView(geo.map.toPoint(m.lat, m.lon))).slice(0, 4);
  for (const m of misses) {
    await throwOne(m, false, MISS_LABEL[m.reason] || "다시");
    if (stale()) return;
  }
  await throwOne(d, true);
  if (stale()) return;
  stopAiming();
  await sleep(700);
  if (stale()) return;
  // 화면에 떠 있던 연출용 다트를 모두 걷어 내고, 지도에 붙는 다트 하나만 남긴다
  clearFx();
  geo.map.clear("darts");
  pinDart(d);
  geo.map.flyTo(d.lat, d.lon, 10);
}

function showLanding(ch) {
  if (!geo.map) return;
  fx.throwId++;
  clearFx();
  geo.map.clear("darts");
  const d = ch.destination;
  pinDart(d);
  geo.map.jump(d.lat, d.lon, 10);
}

function onSegKey(e) {
  if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key)) return;
  e.preventDefault();
  const buttons = [...document.querySelectorAll("#durations button")];
  const i = buttons.findIndex((b) => b.getAttribute("aria-checked") === "true");
  const step = e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 1;
  const next = buttons[(i + step + buttons.length) % buttons.length];
  selectNights(Number(next.dataset.nights));
  next.focus();
}

function updateOptSummary() {
  const parts = [`${$("#departure").value} 출발`, `${$("#travelers").value}명`, `예산 ${$("#budget").value}`];
  if ($("#startDate").value) parts.push($("#startDate").value);
  $("#optSummary").textContent = parts.join(" · ");
}

function readRequest(exclude) {
  const travelers = Math.min(10, Math.max(1, parseInt($("#travelers").value, 10) || 2));
  return {
    nights: ui.nights,
    departure: $("#departure").value,
    travelers,
    budget_level: $("#budget").value,
    start_date: $("#startDate").value || null,
    exclude,
  };
}

// ---------------------------------------------------------------- 실행
// 다트를 던진 뒤에는 결과 카드가 설정 카드 자리에 같은 크기로 들어간다(body.is-landed).
// 그래서 버튼을 눌러도 화면 배치가 그대로이고, 지도는 늘 카드와 같은 높이다.
function showStage(on) {
  $("#stage").classList.toggle("hidden", !on);
  document.body.classList.toggle("is-landed", on);
  geo.map?.resize();
}

// 카드에 무엇을 보일지는 상태 하나로 정한다: 던지는 중 → 제안(꽂힌 곳) → 일정 짜는 중. 표시는 CSS가 맡는다.
function setStage(state) {
  $("#stage").dataset.state = state;
  // 일정 짜는 중에는 큰 동네 이름을 접고(지도 이름표에 있다) 머리글 한 줄로 줄여, 찾은 곳 목록에 자리를 내준다
  $("#stageLabel").textContent = state === "offer" ? "다트가 꽂힌 곳"
    : state === "planning" ? `${ui.landed.challenge.destination.name} · 일정 짜는 중` : "";
}

// 가려진 만큼만 최소한으로 움직인다. 이미 다 보이면 아무 일도 없다.
const reveal = (sel) => $(sel).scrollIntoView({ behavior: "smooth", block: "nearest" });

function setBusy(busy) {
  ui.busy = busy;
  $("#spinBtn").disabled = busy;
}

async function errorText(res) {
  const detail = (await res.json().catch(() => ({}))).detail;
  return (typeof detail === "string" ? detail : detail?.[0]?.msg) || "요청을 처리하지 못했어요. 조건을 확인해 주세요.";
}

// 1단계: 다트만 던진다 (AI 호출 없음). 꽂힌 곳을 바로 보여 주고 일정도 짤지 묻는다.
async function start(exclude) {
  if (ui.busy) return;
  setBusy(true);
  ui.landed = null;
  for (const sel of ["#result", "#actionbar", "#error", "#throwError"]) $(sel).classList.add("hidden");
  setStage("throwing");
  showStage(true);
  $("#slot").textContent = "다트를 던지는 중";
  $("#slot").className = "slot";
  $("#slotSub").textContent = "기간에 맞는 거리 안의 육지를 노리고 있어요";
  if (geo.map) { fx.throwId++; clearFx(); geo.map.clear("darts"); drawRange(); startAiming(); }
  reveal("#mapWrap");

  try {
    const res = await fetch("/api/darts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(readRequest(exclude)),
    });
    if (!res.ok) throw new Error(await errorText(res));
    const data = await res.json();
    history.replaceState(null, "", `?id=${encodeURIComponent(data.id)}`);
    await land(data.challenge);
    showOffer(data);
    reveal("#stage");
  } catch (err) {
    // 던지기부터 실패하면 설정 카드를 돌려놓고 그 안에서 알린다
    stopAiming();
    showStage(false);
    const e = $("#throwError");
    e.textContent = err.message || "연결이 끊겼어요. 다시 시도해 주세요.";
    e.classList.remove("hidden");
  } finally {
    setBusy(false);
  }
}

// 꽂힌 직후: AI 없이 바로 알 수 있는 것(날씨·지도 링크)과 "일정도 짤까요?" 제안
function showOffer({ id, challenge, weather }) {
  ui.landed = { id, challenge };
  const d = challenge.destination;
  $("#landInfo").replaceChildren(
    el("span", { class: "addr", text: d.full_name, title: `위도 ${d.lat.toFixed(4)}, 경도 ${d.lon.toFixed(4)}` }),
    el("a", { class: "maplink", href: naverMapUrl(d), target: "_blank", rel: "noopener noreferrer" },
      icon("map"), "네이버 지도에서 보기"),
    ...(weather || []).map((w) =>
      el("span", { class: "wchip", text: `${w.date.slice(5).replace("-", "/")} ${w.summary} ${Math.round(w.temp_max)}°` })));
  setStage("offer");
}

// 2단계: 사용자가 원할 때만 일정을 짠다. 서버는 연결과 따로 돌므로, 끊겼다 다시 붙어도 이어서 본다.
async function planTrip() {
  if (ui.busy || !ui.landed) return;
  setBusy(true);
  $("#error").classList.add("hidden");
  $("#foundList").replaceChildren();
  $("#foundCount").textContent = "";
  $("#foundEmpty").classList.remove("hidden");
  setStage("planning");
  ui.startedAt = performance.now();
  ui.queued = "";
  setStep("research");
  startTicking();
  ui.abort = new AbortController();
  try {
    const res = await fetch(`/api/challenges/${encodeURIComponent(ui.landed.id)}/plan`, { method: "POST", signal: ui.abort.signal });
    if (!res.ok) throw new Error(await errorText(res));
    let finished = false;
    await readStream(res.body, (msg) => {
      finished ||= msg.type === "result" || msg.type === "error";
      handleMessage(msg);
    });
    if (!finished) throw new Error("연결이 끊겼어요. 다시 누르면 이어서 볼 수 있어요.");
  } catch (err) {
    if (err.name !== "AbortError") showError(err.message || "연결이 끊겼어요. 다시 누르면 이어서 볼 수 있어요.");
  } finally {
    setBusy(false);
    clearInterval(ui.tickTimer);
  }
}

async function readStream(body, onMessage) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const line = chunk.split("\n").find((l) => l.startsWith("data: "));
      if (line) onMessage(JSON.parse(line.slice(6)));
    }
  }
}

function handleMessage(msg) {
  switch (msg.type) {
    case "queued":
      ui.queued = msg.message;
      break;
    case "stage":
      ui.queued = "";
      setStep(msg.stage);
      break;
    case "found":
      addFound(msg);
      break;
    case "result":
      setStep("done");
      ui.landed = { id: msg.id, challenge: msg.plan.challenge };
      history.replaceState(null, "", `?id=${encodeURIComponent(msg.id)}`);
      renderResult(msg.plan, msg.stats);
      break;
    case "error":
      showError(msg.message);
      break;
  }
}

// 리서치가 끝나는 대로 찾은 명소·맛집을 먼저 보여 준다 (일정이 완성되기 전 볼거리)
function addFound({ kind, items }) {
  const list = $("#foundList");
  list.append(...items.map((it, i) =>
    el("li", { class: "found-chip", "data-kind": kind, title: it.note, style: `animation-delay:${i * 70}ms` },
      icon(kind === "food" ? "meal" : "pin"), el("span", { text: it.name }))));
  $("#foundCount").textContent = `${list.children.length}곳`;
  $("#foundEmpty").classList.add("hidden");
}

// 일정 짜기가 실패하면 같은 다트로 다시 시도할 수 있게 제안 화면으로 돌아간다
function showError(text) {
  const e = $("#error");
  e.textContent = text;
  e.classList.remove("hidden");
  setStage("offer");
}

// ---------------------------------------------------------------- 진행 상황
function setStep(key) {
  const idx = STEPS.findIndex(([k]) => k === key);
  $("#steps").replaceChildren(...STEPS.map(([, label], i) =>
    el("li", { class: key === "done" || i < idx ? "done" : i === idx ? "active" : "", title: label },
      el("span", { text: label }))));
  $("#stepLabel").textContent = STEPS[idx][1];
}

function startTicking() {
  clearInterval(ui.tickTimer);
  const tick = () => {
    const s = Math.round((performance.now() - ui.startedAt) / 1000);
    $("#elapsed").textContent = `${s}초 · ${ui.queued || "보통 30~40초 걸려요"}`;
  };
  tick();
  ui.tickTimer = setInterval(tick, 1000);
}

// ---------------------------------------------------------------- 다트 결과
async function land(ch) {
  await animateThrow(ch);
  fillLanding(ch);
}

function fillLanding(ch) {
  const d = ch.destination;
  const slot = $("#slot");
  slot.textContent = d.name;
  slot.className = "slot landed";
  const misses = (ch.misses || []).length;
  $("#slotSub").textContent = misses
    ? `${misses}번 빗나간 끝에 육지에 꽂혔어요 · ${ch.departure}에서 직선 ${ch.distance_km}km`
    : `한 번에 육지에 꽂혔어요 · ${ch.departure}에서 직선 ${ch.distance_km}km`;
}

// 네이버 지도는 좌표로 핀을 찍는 공식 웹 링크가 없어서, 읍·면·동 전체 주소로 검색해 연다
const naverMapUrl = (d) => `https://map.naver.com/p/search/${encodeURIComponent(d.full_name || d.name)}`;

// ---------------------------------------------------------------- 결과
// 공유 링크: 일정이 완성됐으면 일정을, 아직이면 꽂힌 곳과 "일정 짜기" 제안을 보여 준다.
async function loadShared(id) {
  let data;
  try {
    const res = await fetch(`/api/challenges/${encodeURIComponent(id)}`);
    if (!res.ok) throw new Error();
    data = await res.json();
  } catch {
    history.replaceState(null, "", location.pathname);
    return;
  }
  const ch = data.challenge;
  selectNights(ch.nights);
  if (ch.departure) $("#departure").value = ch.departure;
  if (data.plan) {
    ui.landed = { id, challenge: ch };
    renderResult(data.plan);
    showLanding(ch);
    return;
  }
  showStage(true);
  fillLanding(ch);
  showOffer({ id, challenge: ch, weather: data.weather });
  showLanding(ch);
  if (data.planning) planTrip(); // 누군가 이미 만들고 있으면 그 진행 상황에 붙는다
}

function routeOf(day) {
  const seq = [];
  for (const a of day.activities || []) {
    if (ROUTE_SKIP.has(a.category)) continue;
    const area = (a.area || "").trim();
    if (area && seq[seq.length - 1] !== area) seq.push(area);
  }
  return seq;
}

function renderResult(plan, stats) {
  const { challenge: ch, itinerary: it, budget } = plan;
  const dest = ch.destination;
  document.body.classList.add("has-result");
  showStage(false);

  const head = el("header", { class: "trip-head" },
    el("div", {},
      el("h2", {}, dest.name, el("span", { class: "province", text: dest.province })),
      el("p", { class: "pitch", text: dest.full_name || "" }),
      dest.lat != null
        ? el("a", { class: "maplink", href: naverMapUrl(dest), target: "_blank", rel: "noopener noreferrer" },
            icon("map"), "네이버 지도에서 위치 보기")
        : null,
    ),
    el("div", {},
      el("h3", { class: "it-title", text: it.title }),
      el("p", { class: "summary", text: it.summary }),
      el("ul", { class: "facts" },
        [["출발", ch.departure], ["기간", ch.duration_label], ["인원", `${ch.travelers}명`], ["예산", ch.budget_level], ["거리", `약 ${ch.distance_km}km`]]
          .map(([k, v]) => el("li", {}, el("span", { class: "k", text: k }), el("span", { text: v }))))
    ),
  );

  const notice = plan.search_degraded
    ? el("p", { class: "notice" }, icon("alert"),
        el("span", { text: "지금은 실시간 검색을 쓸 수 없어 AI가 알고 있는 정보로 작성했어요. 가게·가격·영업시간은 꼭 다시 확인하세요." }))
    : null;

  const main = el("div", { class: "trip-main" },
    it.days.map(renderDay),
    it.rainy_day_backup
      ? el("p", { class: "rainy" }, icon("umbrella"),
          el("span", {}, el("b", { text: "비 오는 날이라면" }), it.rainy_day_backup))
      : null,
  );

  const side = el("aside", { class: "trip-side", "aria-label": "예산과 여행 정보" },
    renderBudget(budget),
    el("section", { class: "side-sec" },
      el("div", { class: "side-actions" },
        actionButton("reroll", "다른 곳으로 다시 돌리기", "refresh", "btn primary"),
        actionButton("copy", "링크 복사", "link", "btn"),
        actionButton("reset", "처음 화면으로", null, "btn ghost")),
      metaLine(plan, stats)),
    renderInfo(plan),
    it.tips?.length
      ? el("section", { class: "side-sec" }, el("h3", { text: "여행 팁" }),
          el("ul", { class: "plain" }, it.tips.map((t) => el("li", { text: t }))))
      : null,
    plan.sources?.length
      ? el("section", { class: "side-sec sources" },
          el("details", {},
            el("summary", { class: "muted", text: `참고한 출처 ${plan.sources.length}곳` }),
            el("ul", { class: "plain" }, plan.sources.slice(0, 12).map((s) =>
              el("li", {}, el("a", { href: safeUrl(s.uri), target: "_blank", rel: "noopener noreferrer", text: s.title }))))))
      : null,
  );
  side.addEventListener("click", onAction);

  $("#result").replaceChildren(head, notice || "", el("div", { class: "trip-grid" }, main, side));
  $("#result").classList.remove("hidden");
  $("#actionbar").classList.remove("hidden");
  if (geo.map) setTimeout(() => geo.map?.resize(), 50);
  $("#result").scrollIntoView({ behavior: "smooth", block: "start" });
}

function actionButton(action, label, iconName, cls) {
  return el("button", { class: cls, type: "button", "data-action": action },
    iconName ? icon(iconName) : null, el("span", { text: label }));
}

function metaLine(plan, stats) {
  const bits = [];
  if (plan.review?.revisions) bits.push(`검수 후 ${plan.review.revisions}번 수정`);
  if (stats?.llm_calls) bits.push(`AI 호출 ${stats.llm_calls}회`);
  if (stats?.duration_s) bits.push(`${Math.round(stats.duration_s)}초`);
  return bits.length ? el("p", { class: "meta-line", text: bits.join(" · ") }) : null;
}

function renderDay(d) {
  const route = routeOf(d);
  return el("section", { class: "day", "aria-labelledby": `day-${d.day}` },
    el("header", { class: "day-head" },
      el("p", { class: "day-label", text: `Day ${d.day}` }),
      el("h3", { id: `day-${d.day}`, text: d.theme }),
      route.length > 1
        ? el("ol", { class: "route", "aria-label": "이동 권역 순서" }, route.map((a) => el("li", { text: a })))
        : null),
    el("ol", { class: "timeline" },
      d.activities.map((a) =>
        el("li", { class: "act", "data-cat": a.category },
          el("time", { class: "act-time", text: a.time }),
          el("span", { class: "act-icon", title: a.category }, icon(CAT_ICON[a.category] || "pin")),
          el("div", { class: "act-body" },
            el("p", { class: "act-title", text: a.title }),
            el("p", { class: "act-meta", text: [a.place, a.area].filter(Boolean).filter((v, i, arr) => arr.indexOf(v) === i).join(" · ") }),
            a.description ? el("p", { class: "act-desc", text: a.description }) : null,
            a.tip ? el("p", { class: "act-tip", text: a.tip }) : null)))));
}

function renderBudget(b) {
  const max = Math.max(...b.by_category.map((c) => c.amount_krw), 1);
  return el("section", { class: "side-sec", "aria-labelledby": "budget-h" },
    el("h3", { id: "budget-h", text: "예상 예산" }),
    el("div", { class: "total" },
      el("span", { class: "big", text: won(b.total_krw) }),
      el("span", { class: "pp", text: `1인당 약 ${won(b.per_person_krw)}` })),
    el("div", { class: "bars" },
      b.by_category.map((c) =>
        el("div", { class: "bar-row" },
          el("span", { text: c.category }),
          el("div", { class: "bar", role: "presentation" }, el("i", { style: `width:${(c.amount_krw / max) * 100}%` })),
          el("span", { class: "amt", text: won(c.amount_krw) })))),
    el("details", { class: "more" },
      el("summary", { text: "항목별로 보기" }),
      el("table", { class: "items" },
        el("tbody", {},
          b.items.map((i) =>
            el("tr", {},
              el("td", {}, i.description, el("br"),
                el("small", { text: `${won(i.unit_cost_krw)} × ${i.quantity}${i.per_person ? ` × ${b.travelers}명` : ""}` })),
              el("td", { text: won(i.subtotal_krw) }))),
          el("tr", {}, el("td", { text: "예비비 10%" }), el("td", { text: won(b.contingency_krw) })))),
      b.assumptions.length
        ? el("ul", { class: "assume" }, b.assumptions.map((a) => el("li", { text: a })))
        : null));
}

function renderInfo(plan) {
  const it = plan.itinerary;
  const rows = [["가는 법", it.getting_there], ["현지 이동", it.local_transport], ["숙소 추천 지역", it.stay_area]]
    .filter(([, v]) => v);
  return el("section", { class: "side-sec" },
    el("h3", { text: "이동·숙소·날씨" }),
    el("dl", { class: "info" },
      rows.map(([k, v]) => el("div", {}, el("dt", { text: k }), el("dd", { text: v }))),
      el("div", {}, el("dt", { text: "날씨" }),
        el("dd", {}, plan.weather?.length ? renderWeather(plan.weather) : plan.weather_text))));
}

function renderWeather(days) {
  return el("div", { class: "weather" },
    days.map((w) =>
      el("div", { class: "wday" },
        el("div", { text: w.date.slice(5).replace("-", "/") }),
        el("strong", { text: w.summary }),
        el("div", { class: "num", text: `${Math.round(w.temp_min)}° / ${Math.round(w.temp_max)}°` }),
        w.precip_prob != null ? el("div", { class: "muted", text: `비 ${w.precip_prob}%` }) : null)));
}

// ---------------------------------------------------------------- 액션
function onAction(e) {
  const btn = e.target.closest("[data-action]");
  if (!btn) return;
  const action = btn.dataset.action;
  if (action === "reroll" && ui.landed) start([ui.landed.challenge.destination.name]);
  else if (action === "plan") planTrip();
  else if (action === "copy") copyLink(btn);
  else if (action === "reset") reset();
}

function reset() {
  ui.abort?.abort(); // 만들던 일정의 화면 갱신을 끊는다 (서버는 계속 만들고, 링크로 다시 볼 수 있다)
  ui.landed = null;
  history.replaceState(null, "", location.pathname);
  document.body.classList.remove("has-result");
  if (geo.map) { fx.throwId++; clearFx(); geo.map.clear("darts"); geo.map.resize(); drawRange(); }
  $("#result").classList.add("hidden");
  $("#actionbar").classList.add("hidden");
  showStage(false);
  window.scrollTo({ top: 0, behavior: "smooth" });
  $("#spinBtn").focus({ preventScroll: true });
}

async function copyLink(btn) {
  const label = btn.querySelector("span");
  const original = label.textContent;
  try {
    await navigator.clipboard.writeText(location.href);
    label.textContent = "복사했어요";
    btn.classList.add("copied");
    btn.querySelector("use")?.setAttribute("href", "#i-check");
    setTimeout(() => {
      label.textContent = original;
      btn.classList.remove("copied");
      btn.querySelector("use")?.setAttribute("href", "#i-link");
    }, 2000);
  } catch {
    window.prompt("이 링크를 복사하세요", location.href);
  }
}

function safeUrl(u) {
  try {
    const url = new URL(u);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : "#";
  } catch {
    return "#";
  }
}

init();
