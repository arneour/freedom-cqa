// ДЕМО-страница: лидерборд и профили операторов. Только чтение, основной интерфейс не затрагивает.
import { esc, fmt, scoreClass, toast } from "./ui.js";

const base = () => (window.CQA_CONFIG && window.CQA_CONFIG.apiBase) || "";
const get = async (path) => {
  const r = await fetch(base() + path);
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || r.statusText);
  return d;
};

// Короткие подписи осей для известных критериев; для новых — название критерия.
const SHORT = { c01: "Контакт", c02: "Потребность", c03: "Идентификация", c04: "Консультация",
  c05: "Эмпатия", c06: "Ведение диалога", c07: "Завершение", _no_gross: "Без грубых ошибок" };
const pct = (v) => (v == null ? "—" : Math.round(v) + "%");
const label = (a) => SHORT[a.id] || (a.title.length > 18 ? a.title.slice(0, 17) + "…" : a.title);
const dt = (iso) => new Date(iso).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Almaty" });
const dday = (iso) => new Date(iso + "T00:00:00+05:00").toLocaleDateString("ru-RU", { day: "2-digit", month: "short", timeZone: "Asia/Almaty" });

/* ---------- Многоугольник (радар) ---------- */
function radar(axes, opts = {}) {
  const { size = 420, team = null, mini = false } = opts;
  const n = axes.length;
  const cx = size / 2, cy = size / 2;
  const R = mini ? size / 2 - 2 : size * 0.34;
  const pt = (i, v) => {
    const ang = -Math.PI / 2 + (2 * Math.PI * i) / n;
    return [cx + Math.cos(ang) * R * v, cy + Math.sin(ang) * R * v];
  };
  const poly = (vals) => vals.map((v, i) => pt(i, v).map((x) => x.toFixed(1)).join(",")).join(" ");
  const val = (a) => (a.value == null ? 0 : a.value / 100);
  const id = "g" + Math.random().toString(36).slice(2, 8);

  if (mini) {
    return `<svg class="radar mini" viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" aria-hidden="true">
      <polygon points="${poly(axes.map(() => 1))}" class="ring outer"/>
      <polygon points="${poly(axes.map(val))}" class="shape"/></svg>`;
  }
  const rings = [0.25, 0.5, 0.75, 1].map((k) => `<polygon points="${poly(axes.map(() => k))}" class="ring${k === 1 ? " outer" : ""}"/>`).join("");
  const spokes = axes.map((_, i) => { const [x, y] = pt(i, 1); return `<line x1="${cx}" y1="${cy}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" class="spoke"/>`; }).join("");
  const teamPoly = team ? `<polygon points="${poly(team.map(val))}" class="team"/>` : "";
  const dots = axes.map((a, i) => { const [x, y] = pt(i, val(a)); return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="4.5" class="dot${a.value == null ? " empty" : ""}"><title>${esc(a.title)}: ${a.value == null ? "нет данных" : pct(a.value)} (${a.n} диал.)</title></circle>`; }).join("");
  const labels = axes.map((a, i) => {
    const [x, y] = pt(i, 1.2);
    const anchor = Math.abs(x - cx) < 8 ? "middle" : x > cx ? "start" : "end";
    return `<text x="${x.toFixed(1)}" y="${y.toFixed(1)}" text-anchor="${anchor}" class="axis-label">
      <tspan x="${x.toFixed(1)}" dy="-0.2em">${esc(label(a)).toUpperCase()}</tspan>
      <tspan x="${x.toFixed(1)}" dy="1.25em" class="axis-val">${pct(a.value)}</tspan></text>`;
  }).join("");
  const M = 110; // поля под подписи
  return `<svg class="radar" viewBox="${-M} ${-M / 3} ${size + 2 * M} ${size + (2 * M) / 3}" role="img" aria-label="Профиль по критериям">
    <defs><radialGradient id="${id}" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#ff5a1f" stop-opacity=".55"/><stop offset="100%" stop-color="#ffb347" stop-opacity=".12"/>
    </radialGradient></defs>
    ${rings}${spokes}${teamPoly}
    <polygon points="${poly(axes.map(val))}" class="shape" fill="url(#${id})"/>
    <polygon points="${poly(axes.map(() => 0.12))}" class="core"/>
    ${dots}${labels}</svg>`;
}

/* ---------- График прогрессии ---------- */
function progressionChart(points) {
  const W = 560, H = 220, P = { l: 36, r: 30, t: 14, b: 34 };
  const x = (i) => P.l + (points.length < 2 ? 0 : (i * (W - P.l - P.r)) / (points.length - 1));
  const y = (v) => P.t + (1 - v / 100) * (H - P.t - P.b);
  const line = (key) => {
    const seg = points.map((p, i) => (p[key] == null ? null : `${x(i).toFixed(1)},${y(p[key]).toFixed(1)}`)).filter(Boolean);
    return seg.length ? seg.join(" ") : "";
  };
  const grid = [0, 25, 50, 75, 100].map((v) => `<line x1="${P.l}" x2="${W - P.r}" y1="${y(v)}" y2="${y(v)}" class="grid"/><text x="${P.l - 6}" y="${y(v) + 4}" text-anchor="end" class="tick">${v}</text>`).join("");
  const xt = points.map((p, i) => `<text x="${x(i)}" y="${H - 12}" text-anchor="middle" class="tick">${dday(p.week)}</text>`).join("");
  const dots = points.map((p, i) => (p.avg == null ? "" : `<circle cx="${x(i)}" cy="${y(p.avg)}" r="4" class="pdot"><title>Неделя с ${dday(p.week)}: ${fmt(p.avg)} (${p.n} диал.), команда ${fmt(p.team_avg)}</title></circle>`)).join("");
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Средний итог по неделям">
    ${grid}${xt}
    <polyline points="${line("team_avg")}" class="tline"/>
    <polyline points="${line("avg")}" class="pline"/>${dots}</svg>
    <div class="legend small"><span class="lg op"></span>оператор <span class="lg team"></span>среднее по команде</div>`;
}

const trend = (t) => (t == null ? `<span class="muted">—</span>` : t > 0.05 ? `<span class="up">▲ ${fmt(t)}</span>` : t < -0.05 ? `<span class="down">▼ ${fmt(Math.abs(t))}</span>` : `<span class="muted">= 0</span>`);

/* ---------- Лидерборд ---------- */
async function renderBoard(root) {
  const d = await get("/api/demo/leaderboard");
  root.innerHTML = `
    <div class="demo-note">${esc(d.note)} Период ${dday(d.period.from)} – ${dday(d.period.to)}. Оценки — по чек-листу «${esc(d.checklist.name)}», с учётом решений по апелляциям.</div>
    <section class="panel">
      <div class="row head"><h2>Лидерборд операторов</h2><span class="spacer"></span><span class="muted small">Среднее по команде: <b>${fmt(d.team_avg)}</b></span></div>
      <table class="grid board">
        <thead><tr><th>#</th><th>Оператор</th><th>Профиль</th><th>Средний итог</th><th>Лучший / худший</th><th>Диалогов</th><th>Грубые ошибки</th><th title="Последние 14 дней против предыдущих 14">Тренд</th></tr></thead>
        <tbody>${d.operators.map((o) => `
          <tr class="click" data-op="${esc(o.id)}">
            <td class="rank r${o.rank}">${o.rank}</td>
            <td><a href="#/op/${encodeURIComponent(o.id)}"><b>${esc(o.name)}</b></a><div class="small muted">${esc(o.team)}</div></td>
            <td class="mini-cell">${radar(o.axes, { size: 46, mini: true })}</td>
            <td><div class="bar-cell"><span class="score ${scoreClass(o.avg)}">${fmt(o.avg)}</span><span class="hbar"><i style="width:${o.avg || 0}%"></i></span></div></td>
            <td class="small">${fmt(o.best)} / ${fmt(o.worst)}</td>
            <td>${o.evaluated}${o.evaluated !== o.dialogs ? ` <span class="muted small">из ${o.dialogs}</span>` : ""}</td>
            <td>${o.gross ? `<span class="pill bad">${o.gross}</span>` : `<span class="muted">0</span>`}</td>
            <td>${trend(o.trend)}</td>
          </tr>`).join("")}</tbody>
      </table>
      <p class="small muted">Место — по среднему финальному итогу. Грубые ошибки показаны отдельно и на место не влияют. Смысловые критерии оценивает локальная модель (точность ~73%), поэтому рейтинг предварительный.</p>
    </section>`;
  root.querySelector("tbody").addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-op]");
    if (tr && e.target.tagName !== "A") location.hash = `#/op/${encodeURIComponent(tr.dataset.op)}`;
  });
}

/* ---------- Профиль ---------- */
async function renderProfile(root, id) {
  const d = await get(`/api/demo/operators/${encodeURIComponent(id)}`);
  const o = d.operator;
  const sw = (arr, good) => arr.length ? arr.map((a) => `
    <li><b>${esc(a.title)}</b> — <span class="${good ? "up" : "down"}">${pct(a.value)}</span> <span class="muted small">(${a.n} диал.)</span>
      ${a.example ? `<div class="small">пример: <a href="/#/dialog/DEV/${encodeURIComponent(a.example.conversation_id)}">${esc(a.example.conversation_id)}</a> — ${pct(a.example.value)}</div>` : ""}</li>`).join("") : `<li class="muted">нет данных</li>`;
  root.innerHTML = `
    <div class="crumbs"><a href="#/">← Лидерборд</a> / <b>${esc(o.name)}</b></div>
    <div class="demo-note">${esc(d.note)}</div>
    <div class="profile-grid">
      <section class="panel hero">
        <div class="avatar">${esc(o.name.split(" ").map((w) => w[0]).join(""))}</div>
        <div>
          <h2>${esc(o.name)}</h2>
          <div class="muted small">${esc(o.team)} · место <b>${o.rank}</b> из ${d.operators_total}</div>
        </div>
        <div class="kpis">
          <div><span class="score ${scoreClass(o.avg)} big">${fmt(o.avg)}</span><small>средний итог</small></div>
          <div><b>${o.evaluated}</b><small>диалогов</small></div>
          <div><b class="${o.gross ? "bad" : ""}">${o.gross}</b><small>грубых ошибок</small></div>
          <div>${trend(o.trend)}<small>тренд 14 дней</small></div>
        </div>
      </section>
      <section class="radar-card">
        <div class="radar-head"><span>Профиль по критериям</span><span class="small"><span class="lg op"></span>оператор <span class="lg team"></span>команда</span></div>
        ${radar(d.axes, { team: d.team_axes })}
      </section>
      <section class="panel">
        <h3>Сильные стороны</h3><ul class="sw">${sw(d.strengths, true)}</ul>
        <h3>Зоны роста</h3><ul class="sw">${sw(d.weaknesses, false)}</ul>
      </section>
      <section class="panel">
        <h3>Прогрессия: средний итог по неделям</h3>
        ${progressionChart(d.progression)}
      </section>
      <section class="panel">
        <h3>Апелляции</h3>
        <div class="kpis small-kpis">
          <div><b>${d.appeals.total}</b><small>подано</small></div>
          <div><b>${d.appeals.open}</b><small>на рассмотрении</small></div>
          <div><b class="up">${d.appeals.changed}</b><small>оценка изменена</small></div>
          <div><b>${d.appeals.confirmed}</b><small>подтверждена</small></div>
        </div>
      </section>
      <section class="panel wide">
        <h3>Диалоги (${d.dialogs.length})</h3>
        <table class="grid">
          <thead><tr><th>Дата</th><th>ID диалога</th><th>Канал</th><th>Язык</th><th>Итог</th><th>Грубая ошибка</th><th>Апелляции</th></tr></thead>
          <tbody>${d.dialogs.map((x) => `<tr>
            <td class="small" title="${esc(x.started_at)} – ${esc(x.ended_at)}${x.date_source === "generated" ? " (дата сгенерирована)" : ""}">${dt(x.started_at)}${x.date_source === "generated" ? "" : " <span class='muted'>●</span>"}</td>
            <td><a href="/#/dialog/${encodeURIComponent(x.group_id)}/${encodeURIComponent(x.conversation_id)}">${esc(x.conversation_id)}</a></td>
            <td>${x.channel === "voice call" ? "📞 Звонок" : "💬 Чат"}</td><td>${esc(x.language)}</td>
            <td>${x.total == null ? `<span class="muted">не оценён</span>` : `<span class="score ${scoreClass(x.total)}">${fmt(x.total)}</span>${x.original_total !== x.total ? ` <s class="muted">${fmt(x.original_total)}</s>` : ""}`}</td>
            <td>${x.gross ? `<span class="pill bad">Да</span>` : `<span class="muted">нет</span>`}</td>
            <td>${x.appeals || ""}</td></tr>`).join("")}</tbody>
        </table>
        <p class="small muted">● — настоящая дата звонка из транскрипта; остальные даты сгенерированы для демо.</p>
      </section>
    </div>`;
}

async function route() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  const view = document.getElementById("view");
  const root = document.createElement("div");
  view.replaceChildren(root);
  try {
    if (parts[0] === "op" && parts[1]) await renderProfile(root, parts[1]);
    else await renderBoard(root);
  } catch (e) {
    if (!root.isConnected) return;
    root.innerHTML = `<div class="panel error">Ошибка: ${esc(e.message)}</div>`;
    toast(e.message, "error");
  }
}

window.addEventListener("hashchange", route);
route();
