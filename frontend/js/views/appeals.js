import { api } from "../api.js";
import { answerClass, answerLabel, answerOptions, esc, fmt, mmss, offsets, parseAnswer, scoreClass, SPEAKER, toast } from "../ui.js";

export async function renderAppeals(root, focusId) {
  const list = await api.appeals();
  const open = list.filter((a) => a.status === "open");
  const done = list.filter((a) => a.status !== "open");
  const current = focusId ? list.find((a) => a.id === focusId) : open[0];

  root.innerHTML = `
    <div class="appeals-layout">
      <aside class="panel">
        <h3>На рассмотрении (${open.length})</h3>
        ${item(open, current)}
        <h3>Рассмотрено (${done.length})</h3>
        ${item(done, current)}
      </aside>
      <section class="panel" id="review">${current ? "" : `<div class="empty">Апелляций пока нет. Их подают из карточки диалога кнопкой «Оспорить оценку».</div>`}</section>
    </div>`;
  if (current) await drawReview(root.querySelector("#review"), current, () => renderAppeals(root, current.id));
}

function item(arr, current) {
  if (!arr.length) return `<p class="muted small">—</p>`;
  return `<ul class="alist">${arr.map((a) => `<li class="${current && a.id === current.id ? "sel" : ""}">
    <a href="#/appeals/${a.id}"><b>#${a.id}</b> ${esc(a.conversation_id)} · ${esc(a.criterion_id)}
    ${a.status === "resolved" ? `<span class="pill ${a.decision === "changed" ? "good" : ""}">${a.decision === "changed" ? "изменено" : "подтверждено"}</span>` : ""}</a></li>`).join("")}</ul>`;
}

async function drawReview(box, a, reload) {
  const [ev, dlg] = await Promise.all([api.evaluation(a.evaluation_id), api.dialog(a.group_id, a.conversation_id, a.evaluation_id)]);
  const crit = ev.checklist.criteria.find((c) => c.id === a.criterion_id);
  const orig = ev.items[a.criterion_id];
  const tr = dlg.transcript;
  const isCall = dlg.channel === "voice call";
  const offs = offsets(tr);
  // Фрагмент: доказательства и проверенный участок ± 2 реплики контекста
  const marks = new Set(orig.evidence || []);
  const rng = orig.checked_range;
  let idx = [...marks];
  if (rng) for (let i = rng[0]; i <= rng[1]; i++) idx.push(i);
  if (!idx.length) idx = tr.map((_, i) => i + 1);
  const lo = Math.max(1, Math.min(...idx) - 2), hi = Math.min(tr.length, Math.max(...idx) + 2);

  box.innerHTML = `
    <div class="row head">
      <h2>Апелляция #${a.id}</h2>
      ${a.status === "open" ? `<span class="pill mid">на рассмотрении</span>` : `<span class="pill">рассмотрена ${esc((a.resolved_at || "").replace("T", " ").slice(0, 16))}</span>`}
      <span class="spacer"></span>
      <a href="#/dialog/${encodeURIComponent(a.group_id)}/${encodeURIComponent(a.conversation_id)}?msg=${[...marks][0] || lo}">Открыть карточку диалога →</a>
    </div>
    <div class="review-grid">
      <div>
        <h4>Спорная оценка</h4>
        <div class="crit">
          <div class="crit-head"><span class="code">${esc(crit.code || crit.id)}</span><span class="title">${esc(crit.title)}</span>
            <span class="ans ${answerClass(orig, crit)}">${answerLabel(orig, crit)}</span></div>
          <div class="small muted">Вес ${fmt(crit.weight)} · ${esc(crit.description)}</div>
          <p class="expl">${esc(orig.explanation)}</p>
          ${orig.na_reason ? `<p class="na-reason small"><b>Причина N/A:</b> ${esc(orig.na_reason)}</p>` : ""}
        </div>
        <h4>Комментарий оператора / сотрудника</h4>
        <blockquote>${esc(a.comment)}</blockquote>
        <div class="totals small">Итог диалога сейчас: <span class="score ${scoreClass(ev.final.score.total)}">${fmt(ev.final.score.total)}</span>
          ${ev.final.score.total !== ev.score.total ? ` (исходный ${fmt(ev.score.total)})` : ""}</div>
      </div>
      <div>
        <h4>Фрагмент диалога #${lo}–#${hi}</h4>
        <ol class="msgs compact">${tr.slice(lo - 1, hi).map((m, k) => {
          const n = lo + k;
          return `<li class="msg ${esc(m.speaker)} ${marks.has(n) ? "hl" : ""} ${rng && n >= rng[0] && n <= rng[1] ? "range" : ""}">
            <div class="meta"><span class="n">#${n}</span> <b>${SPEAKER[m.speaker] || esc(m.speaker)}</b> <span class="ts" title="${esc(m.timestamp)}">${isCall ? mmss(offs[n - 1]) : esc(m.timestamp)}</span></div>
            <div class="text">${esc(m.text)}</div></li>`;
        }).join("")}</ol>
      </div>
    </div>
    ${a.status === "open" ? `
      <form id="decide" class="decide">
        <h4>Решение проверяющего</h4>
        <label class="chk"><input type="radio" name="d" value="confirmed" checked> Подтвердить исходную оценку</label>
        <label class="chk"><input type="radio" name="d" value="changed"> Изменить оценку на
          <select name="ans">${answerOptions(crit).map((o) => `<option value="${o.value}">${esc(o.label)}</option>`).join("")}</select></label>
        <textarea name="reason" rows="3" placeholder="Причина решения (обязательно)"></textarea>
        <button class="primary">Сохранить решение</button>
      </form>` : `
      <div class="decision">
        <h4>Решение</h4>
        <p>${a.decision === "changed" ? `Оценка изменена: <b>${answerLabel(orig, crit)}</b> → <b>${answerLabel(a.new_item, crit)}</b>` : `Исходная оценка <b>${answerLabel(orig, crit)}</b> подтверждена`}</p>
        <p class="small">Причина: ${esc(a.reviewer_reason)}</p>
      </div>`}`;

  const f = box.querySelector("#decide");
  if (!f) return;
  f.ans.onfocus = () => { f.d.value = "changed"; };
  f.onsubmit = async (e) => {
    e.preventDefault();
    const decision = f.d.value;
    const reason = f.reason.value.trim();
    if (!reason) return toast("Укажите причину решения", "error");
    const body = { decision, reason };
    if (decision === "changed") Object.assign(body, parseAnswer(f.ans.value));
    try {
      const r = await api.resolve(a.id, body);
      toast(`Решение сохранено. Итог: ${fmt(r.score.total)} → ${fmt(r.final.score.total)}`);
      window.dispatchEvent(new Event("cqa:appeals"));
      reload();
    } catch (err) { toast(err.message, "error"); }
  };
}
