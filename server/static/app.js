// 랜덤 여행 챌린지 프론트엔드 (빌드 도구 없는 바닐라 JS + Leaflet 지도)
// LLM이 만든 텍스트와 주소는 모두 textContent로만 넣어 XSS를 막는다.

const $ = (sel) => document.querySelector(sel);
const SVG_NS = "http://www.w3.org/2000/svg";

const STEPS = [
  ["draw", "다트 던지기"],
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
  current: null,
  busy: false,
  tickTimer: null,
  startedAt: 0,
  stepStart: {},
  stepTime: {},
  activeStep: null,
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
const secs = (ms) => `${Math.max(1, Math.round(ms / 1000))}초`;

// ---------------------------------------------------------------- 초기화
async function init() {
  const meta = await fetch("/api/meta").then((r) => r.json());
  for (const d of meta.departures) ui.departures[d.name] = d;
  for (const d of meta.durations) ui.ranges[d.nights] = d.range_km;

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
  initMap();

  $("#setupForm").addEventListener("submit", (e) => { e.preventDefault(); start([]); });
  $("#actionbar").addEventListener("click", onAction);

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
const geo = { map: null, range: null, darts: null, view: null };
// 다트: 끝(촉)이 (6, 58)에 오도록 그린 64×64 그림. 촉 기준으로 지도에 꽂힌다.
function dartSvg(withShadow) {
  return '<svg viewBox="0 0 64 64" aria-hidden="true">' +
    (withShadow ? '<ellipse cx="11" cy="59" rx="8" ry="2.6" fill="rgba(20,14,6,.28)"/>' : "") +
    '<g transform="translate(6 58) rotate(-50)">' +
    '<path d="M0 0 L15 -1.5 L15 1.5 Z" fill="#a3a7ab"/>' +
    '<path d="M2 0 L15 -0.6" stroke="#fff" stroke-opacity=".55" stroke-width=".7"/>' +
    '<rect x="14" y="-3.5" width="17" height="7" rx="3.5" fill="var(--ink)"/>' +
    '<path d="M18.5 -3.3v6.6M21.5 -3.5v7M24.5 -3.5v7M27.5 -3.3v6.6" stroke="var(--surface)" stroke-opacity=".3" stroke-width="1"/>' +
    '<rect x="30" y="-1.25" width="11" height="2.5" rx="1.25" fill="var(--ink-2)"/>' +
    '<path d="M38 0 L57 -12 L53 -1.2 Z" fill="var(--accent)"/>' +
    '<path d="M38 0 L57 12 L53 1.2 Z" fill="color-mix(in srgb, var(--accent) 68%, #000)"/>' +
    '<path d="M38 0 L57 -12" stroke="color-mix(in srgb, var(--accent) 60%, #fff)" stroke-width=".8"/>' +
    "</g></svg>";
}

function initMap() {
  if (!window.L) {
    $("#map").textContent = "지도를 불러오지 못했어요. 다트는 그대로 던질 수 있어요.";
    return;
  }
  geo.map = L.map("map", { scrollWheelZoom: false });
  // OSM 표준 타일: 키가 필요 없고, 출처 표시와 적은 사용량 조건으로 쓸 수 있다.
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(geo.map);
  new ResizeObserver(() => {
    geo.map.invalidateSize();
    if (geo.view && !ui.busy && !document.body.classList.contains("has-result")) geo.map.fitBounds(geo.view, { padding: [16, 16], maxZoom: 9 });
  }).observe($("#map"));
  geo.range = L.layerGroup().addTo(geo.map);
  geo.darts = L.layerGroup().addTo(geo.map);
  geo.map.fitBounds([[33.1, 125.6], [38.6, 129.8]]);
  drawRange();
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
  geo.range.clearLayers();
  const center = [dep.lat, dep.lon];
  const accent = cssVar("--accent");
  const ink = cssVar("--ink");
  let bounds = null;
  if (hi) {
    const ring = L.circle(center, { radius: hi * 1000, color: accent, weight: 2, dashArray: "6 7", fillColor: accent, fillOpacity: 0.06 }).addTo(geo.range);
    bounds = ring.getBounds();
  }
  L.circle(center, { radius: lo * 1000, stroke: false, fillColor: ink, fillOpacity: 0.16 }).addTo(geo.range);
  L.circleMarker(center, { radius: 5, color: ink, weight: 2, fillColor: cssVar("--surface"), fillOpacity: 1 }).addTo(geo.range);
  const label = el("span", { class: "dep-label", text: `${depName} 출발` });
  L.marker(center, { icon: L.divIcon({ className: "", html: label, iconSize: null, iconAnchor: [-8, 10] }), interactive: false }).addTo(geo.range);
  // 다트는 남한 육지에만 꽂히므로, 화면은 범위 원과 남한이 겹치는 부분에 맞춘다.
  const view = bounds
    ? [[Math.max(bounds.getSouth(), 33.1), Math.max(bounds.getWest(), 125.6)],
       [Math.min(bounds.getNorth(), 38.45), Math.min(bounds.getEast(), 129.8)]]
    : [[33.1, 125.6], [38.45, 129.8]];
  geo.view = view;
  geo.map.fitBounds(view, { padding: [16, 16], maxZoom: 9 });
}

function dartIcon() {
  return L.divIcon({ className: "dart-icon", html: dartSvg(true), iconSize: [64, 64], iconAnchor: [6, 58] });
}

// ---- 던지기 연출: 조준 → 비행 → 충돌 ----
const fx = { layer: null, reticle: null, raf: 0, pos: { x: 0, y: 0 } };
const EASE_OUT = "cubic-bezier(.16, .84, .24, 1)";

function fxLayer() {
  if (!fx.layer || !fx.layer.isConnected) {
    fx.layer = el("div", { class: "fx-layer" });
    geo.map.getContainer().append(fx.layer);
  }
  return fx.layer;
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
    const { x: w, y: h } = geo.map.getSize();
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

async function throwOne(latlng, hit, label) {
  const p = geo.map.latLngToContainerPoint(latlng);
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
  const { x: w, y: h } = geo.map.getSize();
  const shadow = place(el("div", { class: "fx-shadow" }), p);
  const dartEl = place(el("div", { class: "fx-dart" }), p);
  dartEl.innerHTML = dartSvg(false);
  layer.append(shadow, dartEl);

  // 화면 오른쪽 아래(던지는 사람 쪽)에서 크게 날아와 작아지며 꽂힌다
  const from = { x: w - p.x + 90, y: h - p.y + 140 };
  shadow.animate([{ transform: "scale(2.6)", opacity: 0 }, { transform: "scale(1)", opacity: 1 }],
    { duration: 560, easing: EASE_OUT, fill: "forwards" });
  await dartEl.animate([
    { transform: `translate(${from.x}px, ${from.y}px) rotate(22deg) scale(3)`, opacity: 0 },
    { opacity: 1, offset: 0.2 },
    { transform: "translate(0, 0) rotate(0deg) scale(1)", opacity: 1 },
  ], { duration: 560, easing: EASE_OUT, fill: "forwards" }).finished;

  // 충돌: 파문, 진동, (명중이면) 지도 흔들림
  const ring = place(el("div", { class: `fx-ring${hit ? "" : " miss"}` }), p);
  layer.append(ring);
  ring.animate([{ transform: "scale(.3)", opacity: 0.95 }, { transform: `scale(${hit ? 5.5 : 3.2})`, opacity: 0 }],
    { duration: hit ? 800 : 560, easing: "cubic-bezier(.1, .7, .2, 1)" }).finished.then(() => ring.remove());
  dartEl.animate(
    [0, -9, 7, -4.5, 2.5, -1, 0].map((deg) => ({ transform: `rotate(${deg}deg)` })),
    { duration: 620, easing: "ease-out" });
  if (hit) {
    $("#mapWrap").animate(
      [[0, 0], [-4, 3], [4, -2], [-3, 1], [2, -1], [0, 0]].map(([x, y]) => ({ transform: `translate(${x}px, ${y}px)` })),
      { duration: 320, easing: "ease-out" });
    return { dartEl, shadow };
  }

  const tag = place(el("div", { class: "fx-label", text: label }), p);
  layer.append(tag);
  tag.animate([{ opacity: 0, transform: "translate(-50%, -56px)" }, { opacity: 1, transform: "translate(-50%, -68px)" }],
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
  geo.darts.clearLayers();
  const d = ch.destination;
  const target = L.latLng(d.lat, d.lon);
  if (reducedMotion()) {
    stopAiming();
    showLanding(ch);
    return;
  }
  const view = geo.map.getBounds();
  const misses = (ch.misses || []).filter((m) => view.contains([m.lat, m.lon])).slice(0, 4);
  for (const m of misses) await throwOne(L.latLng(m.lat, m.lon), false, MISS_LABEL[m.reason] || "다시");
  const hit = await throwOne(target, true);
  stopAiming();
  await sleep(700);
  L.marker(target, { icon: dartIcon(), interactive: false }).addTo(geo.darts);
  hit?.dartEl.remove();
  hit?.shadow.remove();
  geo.map.flyTo(target, 10, { duration: 1.8, easeLinearity: 0.2 });
}

function showLanding(ch) {
  if (!geo.map) return;
  geo.darts.clearLayers();
  const d = ch.destination;
  L.marker([d.lat, d.lon], { icon: dartIcon(), interactive: false }).addTo(geo.darts);
  geo.map.setView([d.lat, d.lon], 10);
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
async function start(exclude) {
  if (ui.busy) return;
  ui.busy = true;
  $("#spinBtn").disabled = true;
  $("#result").classList.add("hidden");
  $("#actionbar").classList.add("hidden");
  $("#error").classList.add("hidden");
  $("#slotPitch").classList.add("hidden");
  $("#stage").classList.remove("hidden");

  ui.startedAt = performance.now();
  ui.stepStart = {};
  ui.stepTime = {};
  setStep("draw");
  startTicking();
  const slot = $("#slot");
  slot.textContent = "다트를 던지는 중";
  slot.className = "slot spinning";
  $("#slotSub").textContent = "기간에 맞는 거리 안의 육지를 노리고 있어요";
  if (geo.map) { geo.darts.clearLayers(); drawRange(); startAiming(); }
  $("#setup").scrollIntoView({ behavior: "smooth", block: "start" });

  try {
    const res = await fetch("/api/challenges", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(readRequest(exclude)),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail?.[0]?.msg || "요청을 처리하지 못했어요. 조건을 확인해 주세요.");
    }
    await readStream(res.body, handleMessage);
  } catch (err) {
    showError(err.message || "연결이 끊겼어요. 다시 시도해 주세요.");
  } finally {
    ui.busy = false;
    $("#spinBtn").disabled = false;
    stopTicking();
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
      $("#slotSub").textContent = msg.message;
      break;
    case "stage":
      setStep(msg.stage);
      break;
    case "challenge":
      land(msg.challenge);
      break;
    case "result":
      setStep("done");
      ui.current = msg.plan;
      history.replaceState(null, "", `?id=${encodeURIComponent(msg.id)}`);
      renderResult(msg.plan, msg.stats);
      break;
    case "error":
      showError(msg.message);
      break;
  }
}

function showError(text) {
  stopAiming();
  $("#slot").classList.remove("spinning");
  const e = $("#error");
  e.textContent = `${text} 위의 버튼으로 다시 돌릴 수 있어요.`;
  e.classList.remove("hidden");
  if (ui.activeStep) $("#steps").querySelector(`[data-step="${ui.activeStep}"]`)?.classList.remove("active");
}

// ---------------------------------------------------------------- 진행 상황
function setStep(key) {
  const now = performance.now();
  const order = STEPS.map(([k]) => k);
  const idx = order.indexOf(key);
  for (const k of order.slice(0, idx)) {
    if (ui.stepStart[k] !== undefined && ui.stepTime[k] === undefined) ui.stepTime[k] = now - ui.stepStart[k];
    if (ui.stepStart[k] === undefined) ui.stepStart[k] = ui.stepStart[ui.activeStep] ?? now;
  }
  if (ui.stepStart[key] === undefined) ui.stepStart[key] = now;
  ui.activeStep = key;

  $("#steps").replaceChildren(...STEPS.map(([k, label], i) => {
    const state = key === "done" || i < idx ? "done" : i === idx ? "active" : "";
    const t = ui.stepTime[k];
    return el("li", { class: state, "data-step": k },
      el("span", { text: label }),
      el("span", { class: "t", text: state === "done" && t && k !== "done" ? secs(t) : "" }));
  }));
}

function startTicking() {
  stopTicking();
  const tick = () => {
    const s = Math.round((performance.now() - ui.startedAt) / 1000);
    $("#elapsed").textContent = `${s}초 경과 · 보통 30~40초 걸려요`;
  };
  tick();
  ui.tickTimer = setInterval(tick, 1000);
}

function stopTicking() {
  clearInterval(ui.tickTimer);
  ui.tickTimer = null;
  if (ui.startedAt && !$("#error").classList.contains("hidden")) return;
  const s = Math.round((performance.now() - ui.startedAt) / 1000);
  if (ui.startedAt) $("#elapsed").textContent = `총 ${s}초 걸렸어요`;
}

// ---------------------------------------------------------------- 다트 결과
async function land(ch) {
  await animateThrow(ch);
  const d = ch.destination;
  const slot = $("#slot");
  slot.textContent = d.name;
  slot.className = "slot landed";
  const misses = (ch.misses || []).length;
  $("#slotSub").textContent = misses
    ? `${misses}번 빗나간 끝에 육지에 꽂혔어요 · ${ch.departure}에서 직선 ${ch.distance_km}km`
    : `한 번에 육지에 꽂혔어요 · ${ch.departure}에서 직선 ${ch.distance_km}km`;
  const pitch = $("#slotPitch");
  pitch.textContent = `${d.full_name} · 위도 ${d.lat.toFixed(4)}, 경도 ${d.lon.toFixed(4)}`;
  pitch.classList.remove("hidden");
}

// ---------------------------------------------------------------- 결과
async function loadShared(id) {
  try {
    const res = await fetch(`/api/challenges/${encodeURIComponent(id)}`);
    if (!res.ok) throw new Error();
    const { plan } = await res.json();
    ui.current = plan;
    selectNights(plan.challenge.nights);
    if (plan.challenge.departure) $("#departure").value = plan.challenge.departure;
    renderResult(plan);
    showLanding(plan.challenge);
  } catch {
    history.replaceState(null, "", location.pathname);
  }
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
  $("#stage").classList.add("hidden");

  const head = el("header", { class: "trip-head" },
    el("div", {},
      el("h2", {}, dest.name, el("span", { class: "province", text: dest.province })),
      el("p", { class: "pitch", text: dest.full_name || "" }),
      dest.lat != null
        ? el("a", { class: "maplink", href: `https://map.kakao.com/link/map/${encodeURIComponent(dest.name)},${dest.lat},${dest.lon}`, target: "_blank", rel: "noopener noreferrer" },
            icon("map"), "카카오맵에서 위치 보기")
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
  if (geo.map) setTimeout(() => geo.map.invalidateSize(), 50);
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
  if (action === "reroll" && ui.current) start([ui.current.challenge.destination.name]);
  else if (action === "copy") copyLink(btn);
  else if (action === "reset") reset();
}

function reset() {
  history.replaceState(null, "", location.pathname);
  document.body.classList.remove("has-result");
  if (geo.map) { geo.darts.clearLayers(); geo.map.invalidateSize(); drawRange(); }
  $("#result").classList.add("hidden");
  $("#actionbar").classList.add("hidden");
  $("#stage").classList.add("hidden");
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
