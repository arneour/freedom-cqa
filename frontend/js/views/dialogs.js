import { api } from "../api.js";
import { esc, fmt, scoreClass, toast } from "../ui.js";

const state = { group: localStorage.getItem("cqa.group") || "", lang: "", channel: "", job: null };

export async function renderDialogs(root) {
  const [groups, cls] = await Promise.all([api.groups(), api.checklists()]);
  if (!state.group && groups.length) state.group = groups[0].group_id;
  const active = cls.find((c) => c.is_active);

  root.innerHTML = `
    <section class="panel toolbar">
      <div class="row">
        <label>Набор
          <select id="grp">
            ${groups.map((g) => `<option value="${esc(g.group_id)}" ${g.group_id === state.group ? "selected" : ""}>${esc(g.group_id)} (${g.n})</option>`).join("")}
            ${groups.length ? "" : "<option value=''>— нет данных —</option>"}
          </select>
        </label>
        <label>Загрузить из API
          <span class="inline"><input id="gid" placeholder="group_id, напр. DEV" value="${groups.length ? "" : "DEV"}" size="12"><button id="imp">Загрузить</button></span>
        </label>
        <label>Файл JSON
          <span class="inline"><input type="file" id="file" accept=".json,application/json"></span>
        </label>
        <span class="spacer"></span>
        <label>Чек-лист
          <select id="cl">${cls.map((c) => `<option value="${c.id}" ${c.is_active ? "selected" : ""}>#${c.id} ${esc(c.name)}${c.is_active ? " (активный)" : ""}</option>`).join("")}</select>
        </label>
        <button id="batch" class="primary" ${state.group ? "" : "disabled"}>Оценить набор</button>
        <label class="chk"><input type="checkbox" id="onlyMissing" checked> только неоценённые</label>
      </div>
      <div id="job" class="job hidden"></div>
    </section>
    <section class="panel">
      <div class="row filters">
        <select id="fl"><option value="">Все языки</option><option>RU</option><option>KZ</option><option>EN</option></select>
        <select id="fc"><option value="">Все каналы</option><option value="agent chat">Чат</option><option value="voice call">Звонок</option></select>
        <input id="fq" placeholder="Поиск по ID">
        <span class="spacer"></span>
        <span id="summary" class="muted"></span>
      </div>
      <table class="grid">
        <thead><tr><th>ID диалога</th><th>Канал</th><th>Язык</th><th>Реплик</th><th>Итог</th><th>Грубая ошибка</th><th>Апелляции</th><th>Оценщик</th></tr></thead>
        <tbody id="rows"><tr><td colspan="8" class="muted">Загрузка…</td></tr></tbody>
      </table>
    </section>`;

  const $ = (s) => root.querySelector(s);
  $("#fl").value = state.lang;
  $("#fc").value = state.channel;
  let rows = [];

  const draw = () => {
    const q = $("#fq").value.trim().toLowerCase();
    const list = rows.filter((r) => (!state.lang || r.language === state.lang) && (!state.channel || r.channel === state.channel) && (!q || r.conversation_id.toLowerCase().includes(q)));
    const evald = list.filter((r) => r.evaluation);
    const avg = evald.filter((r) => r.evaluation.final_total !== null);
    $("#summary").textContent = `${list.length} диалогов · оценено ${evald.length}` +
      (avg.length ? ` · средний итог ${fmt(avg.reduce((a, r) => a + r.evaluation.final_total, 0) / avg.length)}` : "") +
      ` · грубых ошибок ${evald.filter((r) => r.evaluation.gross_error).length}`;
    $("#rows").innerHTML = list.length ? list.map((r) => {
      const e = r.evaluation;
      const changed = e && e.final_total !== e.total;
      return `<tr class="click" data-id="${esc(r.conversation_id)}">
        <td><a href="#/dialog/${encodeURIComponent(r.group_id)}/${encodeURIComponent(r.conversation_id)}">${esc(r.conversation_id)}</a></td>
        <td>${r.channel === "voice call" ? "📞 Звонок" : "💬 Чат"}</td>
        <td>${esc(r.language)}</td>
        <td>${r.messages}</td>
        <td>${e ? `<span class="score ${scoreClass(e.final_total)}">${fmt(e.final_total)}</span>${changed ? ` <s class="muted">${fmt(e.total)}</s>` : ""}` : `<span class="muted">не оценён</span>`}</td>
        <td>${e ? (e.gross_error ? `<span class="pill bad">Да</span>` : `<span class="muted">нет</span>`) : ""}</td>
        <td>${e && e.appeals ? `${e.appeals_open ? `<span class="pill mid">${e.appeals_open} открыт.</span>` : ""} ${e.appeals - e.appeals_open ? `<span class="muted">${e.appeals - e.appeals_open} решено</span>` : ""}` : ""}</td>
        <td class="muted small">${e ? esc(e.evaluator) : ""}</td></tr>`;
    }).join("") : `<tr><td colspan="8" class="muted">Нет диалогов. Загрузите набор из API (например, DEV) или файл JSON.</td></tr>`;
  };

  const load = async () => {
    rows = state.group ? await api.dialogs(state.group) : [];
    draw();
  };

  $("#rows").addEventListener("click", (ev) => {
    const tr = ev.target.closest("tr[data-id]");
    if (tr && ev.target.tagName !== "A") location.hash = `#/dialog/${encodeURIComponent(state.group)}/${encodeURIComponent(tr.dataset.id)}`;
  });
  $("#grp").onchange = (e) => { state.group = e.target.value; localStorage.setItem("cqa.group", state.group); load(); };
  $("#fl").onchange = (e) => { state.lang = e.target.value; draw(); };
  $("#fc").onchange = (e) => { state.channel = e.target.value; draw(); };
  $("#fq").oninput = draw;

  $("#imp").onclick = async () => {
    const g = $("#gid").value.trim();
    if (!g) return toast("Укажите group_id", "error");
    $("#imp").disabled = true;
    try {
      const r = await api.importGroup(g);
      toast(`Загружено ${r.imported} диалогов из ${g}`);
      state.group = g; localStorage.setItem("cqa.group", g);
      renderDialogs(root);
    } catch (e) { toast(e.message, "error"); } finally { $("#imp").disabled = false; }
  };

  $("#file").onchange = async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    try {
      const j = JSON.parse(await f.text());
      const records = Array.isArray(j) ? j : j.data?.records || j.records || [j];
      const g = j.data?.dataset_group_id || "UPLOAD";
      const r = await api.upload(g, records);
      toast(`Загружено ${r.imported} диалогов в набор ${r.group_id}`);
      state.group = r.group_id; localStorage.setItem("cqa.group", r.group_id);
      renderDialogs(root);
    } catch (err) { toast("Файл не распознан: " + err.message, "error"); }
  };

  $("#batch").onclick = async () => {
    try {
      const job = await api.batch({ group_id: state.group, checklist_id: Number($("#cl").value), only_missing: $("#onlyMissing").checked });
      if (!job.total) return toast("Все диалоги уже оценены этим чек-листом");
      pollJob(job.id, root, load);
    } catch (e) { toast(e.message, "error"); }
  };

  if (state.job) pollJob(state.job, root, load);
  await load();
  if (!active) toast("Нет активного чек-листа", "error");
}

async function pollJob(id, root, reload) {
  state.job = id;
  const box = root.querySelector("#job");
  if (!box) return;
  box.classList.remove("hidden");
  let last = -1;
  while (document.body.contains(box)) {
    const j = await api.job(id).catch(() => null);
    if (!j) break;
    const pct = j.total ? Math.round((j.done / j.total) * 100) : 100;
    box.innerHTML = `<div class="bar"><i style="width:${pct}%"></i></div>
      <span>Оценка ${j.group_id}: ${j.done} / ${j.total}${j.errors ? ` · ошибок ${j.errors}` : ""} · стоимость $${j.cost_usd.toFixed(3)}${j.status === "finished" ? " · готово" : ""}</span>`;
    if (j.done !== last) { last = j.done; reload(); }
    if (j.status === "finished") { state.job = null; toast(`Оценка набора завершена: ${j.done} диалогов`); break; }
    await new Promise((r) => setTimeout(r, 2000));
  }
}
