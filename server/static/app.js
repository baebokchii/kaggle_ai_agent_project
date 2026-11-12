// 랜덤 여행 챌린지 프론트엔드 (빌드 도구 없는 바닐라 JS)
// LLM이 만든 텍스트는 모두 textContent로만 넣어 XSS를 막는다.

const $ = (sel) => document.querySelector(sel);

const STEPS = [
  ["draw", "여행지 추첨"],
  ["research", "명소·맛집·교통 리서치"],
  ["plan", "일정 짜기"],
  ["review", "일정 검수"],
  ["budget", "예산 계산"],
  ["done", "완성"],
];
const CAT_ICON = { 관광: "📍", 식사: "🍽️", 카페: "☕", 체험: "🎟️", 쇼핑: "🛍️", 휴식: "🛌", 이동: "🚆", 숙소: "🏨" };

const ui = {
  nights: 1,
  destinations: [],
  current: null, // 마지막 결과
  spinTimer: null,
  busy: false,
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

const won = (n) => `${Number(n || 0).toLocaleString("ko-KR")}원`;

// ---------------------------------------------------------------- 초기화
async function init() {
  const meta = await fetch("/api/meta").then((r) => r.json());
  ui.destinations = meta.destinations;

  const chips = $("#durations");
  for (const d of meta.durations) {
    chips.append(
      el("button", {
        class: "chip", type: "button", role: "radio",
        "aria-checked": String(d.nights === ui.nights), "data-nights": d.nights,
        text: d.label,
        onclick: () => selectNights(d.nights),
      })
    );
  }
  for (const name of meta.departures) $("#departure").append(el("option", { value: name, text: name }));
  for (const name of meta.budget_levels)
    $("#budget").append(el("option", { value: name, text: name, selected: name === "보통" }));

  const today = new Date();
  const iso = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  $("#startDate").min = iso(today);

  for (const id of ["#departure", "#travelers", "#budget", "#startDate"])
    $(id).addEventListener("change", updateOptSummary);
  updateOptSummary();

  $("#spinBtn").addEventListener("click", () => start([]));

  const shared = new URLSearchParams(location.search).get("id");
  if (shared) loadShared(shared);
}

function selectNights(n) {
  ui.nights = n;
  document.querySelectorAll(".chip").forEach((c) =>
    c.setAttribute("aria-checked", String(Number(c.dataset.nights) === n))
  );
}

function updateOptSummary() {
  const parts = [`${$("#departure").value} 출발`, `${$("#travelers").value}명`, $("#budget").value];
  if ($("#startDate").value) parts.push($("#startDate").value);
  $("#optSummary").textContent = `· ${parts.join(" · ")}`;
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
  $("#error").classList.add("hidden");
  $("#stage").classList.remove("hidden");
  renderSteps(null);
  startSpinning();
  $("#stage").scrollIntoView({ behavior: "smooth", block: "start" });

  try {
    const res = await fetch("/api/challenges", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(readRequest(exclude)),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail?.[0]?.msg || "요청을 처리할 수 없어요.");
    }
    await readStream(res.body, handleMessage);
  } catch (err) {
    showError(err.message || "연결이 끊겼어요. 다시 시도해 주세요.");
  } finally {
    ui.busy = false;
    $("#spinBtn").disabled = false;
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
      renderSteps(msg.stage);
      break;
    case "challenge":
      land(msg.challenge);
      break;
    case "result":
      renderSteps("done", true);
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
  stopSpinning();
  const e = $("#error");
  e.textContent = `😢 ${text}`;
  e.classList.remove("hidden");
}

// ---------------------------------------------------------------- 룰렛
function startSpinning() {
  const slot = $("#slot");
  slot.classList.remove("landed");
  slot.classList.add("spinning");
  $("#slotSub").textContent = "여행지를 추첨하고 있어요…";
  let i = Math.floor(Math.random() * ui.destinations.length);
  clearInterval(ui.spinTimer);
  ui.spinTimer = setInterval(() => {
    i = (i + 1 + Math.floor(Math.random() * 3)) % ui.destinations.length;
    slot.textContent = ui.destinations[i];
  }, 70);
}

function stopSpinning() {
  clearInterval(ui.spinTimer);
  ui.spinTimer = null;
  $("#slot").classList.remove("spinning");
}

function land(ch) {
  // 감속 연출 후 당첨지 표시
  clearInterval(ui.spinTimer);
  const slot = $("#slot");
  const delays = [90, 120, 160, 210, 270, 340];
  let step = 0;
  const tick = () => {
    if (step < delays.length) {
      slot.textContent = ui.destinations[Math.floor(Math.random() * ui.destinations.length)];
      setTimeout(tick, delays[step++]);
      return;
    }
    stopSpinning();
    slot.textContent = ch.destination.name;
    slot.classList.add("landed");
    $("#slotSub").textContent =
      `후보 ${ch.candidates}곳 중 당첨! ${ch.departure}에서 ${ch.duration_label} · 일정을 짜는 중이에요`;
  };
  tick();
}

function renderSteps(active, allDone = false) {
  const ol = $("#steps");
  const order = STEPS.map(([k]) => k);
  const activeIdx = active ? order.indexOf(active) : 0;
  ol.replaceChildren(
    ...STEPS.map(([key, label], i) => {
      let cls = "";
      if (allDone || i < activeIdx) cls = "done";
      else if (i === activeIdx) cls = "active";
      return el("li", { class: cls, text: label });
    })
  );
}

// ---------------------------------------------------------------- 결과
async function loadShared(id) {
  try {
    const res = await fetch(`/api/challenges/${encodeURIComponent(id)}`);
    if (!res.ok) throw new Error();
    const { plan } = await res.json();
    ui.current = plan;
    selectNights(plan.challenge.nights);
    renderResult(plan);
  } catch {
    history.replaceState(null, "", location.pathname);
  }
}

function renderResult(plan, stats) {
  const { challenge: ch, itinerary: it, budget } = plan;
  const dest = ch.destination;
  const root = $("#result");

  const hero = el("section", { class: "hero" },
    el("p", { class: "eyebrow", text: "이번 챌린지 여행지" }),
    el("h2", {}, dest.name, el("span", { class: "province", text: dest.province })),
    el("p", { class: "pitch", text: dest.pitch }),
    el("div", { class: "tags" },
      [`${ch.departure} 출발`, ch.duration_label, `${ch.travelers}명`, `예산 ${ch.budget_level}`, `약 ${ch.distance_km}km`]
        .map((t) => el("span", { class: "tag", text: t }))
    )
  );

  const overview = el("section", { class: "panel" },
    plan.search_degraded
      ? el("p", { class: "notice", text: "⚠️ 지금은 실시간 검색을 쓸 수 없어 AI가 알고 있는 정보로 작성했어요. 가게·가격·영업시간은 꼭 다시 확인하세요." })
      : null,
    el("h3", { class: "it-title", text: it.title }),
    el("p", { text: it.summary }),
    el("div", { class: "info" },
      infoBox("가는 법", it.getting_there),
      infoBox("현지 이동", it.local_transport),
      it.stay_area ? infoBox("숙소 추천 지역", it.stay_area) : null,
      infoBox("비 오는 날 플랜B", it.rainy_day_backup)
    ),
    renderWeather(plan)
  );

  const days = el("section", { class: "panel" }, el("h3", { class: "section-title", text: "📅 일정" }));
  const tabs = el("div", { class: "tabs", role: "tablist" });
  const body = el("div");
  it.days.forEach((d, idx) => {
    tabs.append(el("button", {
      class: "tab", type: "button", role: "tab", "aria-selected": String(idx === 0),
      text: `Day ${d.day}`,
      onclick: (e) => {
        tabs.querySelectorAll(".tab").forEach((t) => t.setAttribute("aria-selected", "false"));
        e.currentTarget.setAttribute("aria-selected", "true");
        body.replaceChildren(renderDay(d));
      },
    }));
  });
  body.append(renderDay(it.days[0]));
  if (it.days.length > 1) days.append(tabs);
  days.append(body);

  const budgetSec = renderBudget(budget);

  const extras = el("section", { class: "panel" },
    el("h3", { class: "section-title", text: "✅ 꿀팁" }),
    el("ul", { class: "plain" }, it.tips.map((t) => el("li", { text: t }))),
    plan.sources?.length
      ? el("details", { class: "more sources" },
          el("summary", { text: `참고한 출처 ${plan.sources.length}개` }),
          el("ul", { class: "plain" },
            plan.sources.slice(0, 12).map((s) =>
              el("li", {}, el("a", { href: safeUrl(s.uri), target: "_blank", rel: "noopener noreferrer", text: s.title }))
            )
          )
        )
      : null,
    plan.review?.revisions
      ? el("p", { class: "muted", text: `🧐 AI 검수자가 일정을 ${plan.review.revisions}번 고쳤어요.` })
      : null,
    stats?.llm_calls
      ? el("p", { class: "muted", text: `🤖 AI 호출 ${stats.llm_calls}회 · ${Math.round(stats.duration_s)}초 걸렸어요.` })
      : null
  );

  const actions = el("div", { class: "actions" },
    el("button", { class: "btn primary wide", type: "button", text: "🎲 다른 곳으로 다시 돌리기",
      onclick: () => start([dest.name]) }),
    el("button", { class: "btn", type: "button", text: "🔗 링크 복사", onclick: copyLink }),
    el("button", { class: "btn", type: "button", text: "처음으로",
      onclick: () => { history.replaceState(null, "", location.pathname); root.classList.add("hidden");
        $("#stage").classList.add("hidden"); window.scrollTo({ top: 0, behavior: "smooth" }); } })
  );

  root.replaceChildren(hero, overview, days, budgetSec, extras, actions);
  root.classList.remove("hidden");
  root.scrollIntoView({ behavior: "smooth", block: "start" });
}

function infoBox(title, text) {
  return el("div", {}, el("b", { text: title }), text);
}

function renderWeather(plan) {
  if (plan.weather?.length) {
    return el("div", { class: "weather" },
      plan.weather.map((w) =>
        el("div", { class: "wday" },
          el("div", { text: w.date.slice(5).replace("-", "/") }),
          el("strong", { text: w.summary }),
          el("div", { text: `${Math.round(w.temp_min)}° / ${Math.round(w.temp_max)}°` }),
          w.precip_prob != null ? el("div", { class: "muted", text: `☔ ${w.precip_prob}%` }) : null
        )
      )
    );
  }
  return el("p", { class: "muted", text: `🌤️ ${plan.weather_text}` });
}

function renderDay(d) {
  return el("div", {},
    el("p", { class: "day-theme", text: d.theme }),
    el("ol", { class: "timeline" },
      d.activities.map((a) =>
        el("li", {},
          el("div", { class: "t-time" }, a.time, el("span", { class: "cat", text: `${CAT_ICON[a.category] || ""} ${a.category}` })),
          el("div", { class: "t-title", text: a.title }),
          el("div", { class: "t-place", text: a.place }),
          el("p", { class: "t-desc", text: a.description }),
          a.tip ? el("p", { class: "t-tip", text: `💡 ${a.tip}` }) : null
        )
      )
    )
  );
}

function renderBudget(b) {
  const max = Math.max(...b.by_category.map((c) => c.amount_krw), 1);
  return el("section", { class: "panel" },
    el("h3", { class: "section-title", text: "💰 예상 예산" }),
    el("div", { class: "total" },
      el("span", { class: "big", text: won(b.total_krw) }),
      el("span", { class: "pp", text: `1인당 약 ${won(b.per_person_krw)} · 예비비 10% 포함` })
    ),
    el("div", { class: "bars" },
      b.by_category.map((c) =>
        el("div", { class: "bar-row" },
          el("span", { text: c.category }),
          el("div", { class: "bar" }, el("i", { style: `width:${(c.amount_krw / max) * 100}%` })),
          el("span", { text: won(c.amount_krw) })
        )
      )
    ),
    el("details", { class: "more" },
      el("summary", { text: "항목별 상세 보기" }),
      el("table", { class: "items" },
        el("tbody", {},
          b.items.map((i) =>
            el("tr", {},
              el("td", {}, i.description, el("br"),
                el("small", { text: `${won(i.unit_cost_krw)} × ${i.quantity}${i.per_person ? ` × ${b.travelers}명` : ""}` })),
              el("td", { text: won(i.subtotal_krw) })
            )
          ),
          el("tr", {}, el("td", { text: "예비비 (10%)" }), el("td", { text: won(b.contingency_krw) }))
        )
      ),
      b.assumptions.length
        ? el("ul", { class: "plain muted" }, b.assumptions.map((a) => el("li", { text: a })))
        : null
    )
  );
}

function safeUrl(u) {
  try {
    const url = new URL(u);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : "#";
  } catch {
    return "#";
  }
}

async function copyLink() {
  try {
    await navigator.clipboard.writeText(location.href);
    alert("링크를 복사했어요!");
  } catch {
    prompt("이 링크를 복사하세요", location.href);
  }
}

init();
