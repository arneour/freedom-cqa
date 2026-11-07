import { api } from "../api.js";
import { esc, toast } from "../ui.js";

const TYPES = { binary: "Да / Нет", scale: "Шкала", flag: "Флаг грубой ошибки (без баллов)" };

export async function renderChecklists(root, id) {
  const list = await api.checklists();
  const curId = id || (list.find((c) => c.is_active) || list[0]).id;
  const cl = await api.checklist(curId);
  const draft = { name: cl.name, sections: structuredClone(cl.data.sections || []), criteria: structuredClone(cl.data.criteria) };

  root.innerHTML = `
    <div class="cl-layout">
      <aside class="panel">
        <h3>Версии чек-листа</h3>
        <ul class="alist">${list.map((c) => `<li class="${c.id === curId ? "sel" : ""}"><a href="#/checklists/${c.id}">
          <b>#${c.id}</b> ${esc(c.name)} ${c.is_active ? `<span class="pill good">активный</span>` : ""}
          <div class="small muted">${c.criteria_count} критериев · вес ${c.total_weight} · ${c.created_at.slice(0, 16).replace("T", " ")}</div></a></li>`).join("")}</ul>
        <p class="small muted">Изменения сохраняются как новая версия; прошлые оценки остаются привязанными к своей версии.</p>
      </aside>
      <section class="panel" id="editor"></section>
    </div>`;

  const ed = root.querySelector("#editor");
  const draw = () => {
    const total = draft.criteria.filter((c) => c.type !== "flag").reduce((a, c) => a + Number(c.weight || 0), 0);
    ed.innerHTML = `
      <div class="row head">
        <input class="name" id="name" value="${esc(draft.name)}">
        ${cl.is_active ? `<span class="pill good">активный</span>` : `<button id="act">Сделать активным</button>`}
      </div>
      <div class="small muted">Сумма весов: <b class="${total === 100 ? "" : "warn-text"}">${total}</b>${total === 100 ? "" : " — итог всё равно приводится к 100 по формуле с перераспределением"}</div>
      <h4>Секции</h4>
      <div class="sections-ed">${draft.sections.map((s, i) => `<span class="sec-ed"><input data-sec="${i}" value="${esc(s.title)}"><button class="link" data-delsec="${i}" title="Удалить">✕</button></span>`).join("")}
        <button class="link" id="addsec">+ секция</button></div>
      <h4>Критерии</h4>
      <div id="crits">${draft.criteria.map((c, i) => critEditor(c, i, draft.sections)).join("")}</div>
      <button id="add">+ Добавить критерий</button>
      <div class="row save-bar">
        <label class="chk"><input type="checkbox" id="activate" checked> сделать активным</label>
        <button id="save" class="primary">Сохранить как новую версию</button>
        <span class="spacer"></span>
        <label>Применить к набору <input id="grp" size="10" value="${esc(localStorage.getItem("cqa.group") || "DEV")}"></label>
        <button id="apply">Сохранить и оценить набор</button>
      </div>`;
  };
  draw();

  const sync = (e) => {
    const t = e.target;
    const i = t.closest("[data-i]")?.dataset.i;
    if (t.id === "name") draft.name = t.value;
    if (t.dataset.sec !== undefined) draft.sections[t.dataset.sec].title = t.value;
    if (i === undefined || !t.dataset.f) return;
    const c = draft.criteria[i];
    const f = t.dataset.f;
    if (f === "na_allowed") c.na_allowed = t.checked;
    else if (f === "weight") c.weight = Number(t.value);
    else if (f.startsWith("sv") || f.startsWith("sl")) {
      const k = Number(f.slice(2));
      if (f.startsWith("sv")) c.scale[k].value = Number(t.value); else c.scale[k].label = t.value;
    } else c[f] = t.value;
    if (f === "type") {
      if (c.type === "scale" && !c.scale.length) c.scale = [{ value: 10, label: "Полностью" }, { value: 5, label: "Частично" }, { value: 0, label: "Нет" }];
      if (c.type === "flag") { c.weight = 0; c.na_allowed = false; }
      draw();
    }
    if (f === "na_allowed") draw();
  };
  ed.addEventListener("input", sync);
  ed.addEventListener("change", sync);

  ed.addEventListener("click", async (e) => {
    const t = e.target;
    const i = t.closest("[data-i]")?.dataset.i;
    if (t.id === "add") {
      const n = draft.criteria.length + 1;
      let nid = `c${String(n).padStart(2, "0")}`;
      while (draft.criteria.some((c) => c.id === nid)) nid += "x";
      draft.criteria.push({ id: nid, code: String(n).padStart(2, "0"), section: draft.sections[0]?.id || "", title: "Новый критерий", type: "binary", weight: 10, na_allowed: false, description: "Да — …", na_condition: "", scale: [] });
      draw();
      ed.querySelector(`[data-i="${draft.criteria.length - 1}"] input`).focus();
    } else if (t.dataset.del !== undefined) {
      if (confirm(`Удалить критерий «${draft.criteria[t.dataset.del].title}»?`)) { draft.criteria.splice(t.dataset.del, 1); draw(); }
    } else if (t.dataset.up !== undefined) {
      const k = Number(t.dataset.up);
      if (k > 0) { [draft.criteria[k - 1], draft.criteria[k]] = [draft.criteria[k], draft.criteria[k - 1]]; draw(); }
    } else if (t.dataset.addstep !== undefined) {
      draft.criteria[i].scale.push({ value: 0, label: "" }); draw();
    } else if (t.dataset.delstep !== undefined) {
      draft.criteria[i].scale.splice(Number(t.dataset.delstep), 1); draw();
    } else if (t.id === "addsec") {
      let k = draft.sections.length + 1;
      while (draft.sections.some((s) => s.id === `s${k}`)) k++;
      draft.sections.push({ id: `s${k}`, title: "Новая секция" }); draw();
    } else if (t.dataset.delsec !== undefined) {
      const s = draft.sections[t.dataset.delsec];
      if (draft.criteria.some((c) => c.section === s.id)) return toast("В секции есть критерии — сначала перенесите их", "error");
      draft.sections.splice(t.dataset.delsec, 1); draw();
    } else if (t.id === "act") {
      await api.activate(curId); toast("Чек-лист активирован"); renderChecklists(root, curId);
    } else if (t.id === "save" || t.id === "apply") {
      try {
        const saved = await api.saveChecklist({ ...draft, parent_id: curId, activate: ed.querySelector("#activate").checked || t.id === "apply" });
        toast(`Сохранена версия #${saved.id}`);
        if (t.id === "apply") {
          const g = ed.querySelector("#grp").value.trim();
          const job = await api.batch({ group_id: g, checklist_id: saved.id, only_missing: true });
          toast(`Запущена оценка ${job.total} диалогов набора ${g} по чек-листу #${saved.id}`);
          localStorage.setItem("cqa.group", g);
          location.hash = "#/dialogs";
          return;
        }
        location.hash = `#/checklists/${saved.id}`;
      } catch (err) { toast(err.message, "error"); }
    }
  });
}

function critEditor(c, i, sections) {
  return `
  <div class="crit-ed" data-i="${i}">
    <div class="row">
      <input data-f="code" value="${esc(c.code)}" size="3" title="Номер">
      <input data-f="title" value="${esc(c.title)}" class="grow" placeholder="Название критерия">
      <select data-f="type">${Object.entries(TYPES).map(([k, v]) => `<option value="${k}" ${c.type === k ? "selected" : ""}>${v}</option>`).join("")}</select>
      ${c.type === "flag" ? "" : `<label>Вес <input type="number" min="0" step="1" data-f="weight" value="${c.weight}" style="width:5em"></label>`}
      <select data-f="section">${sections.map((s) => `<option value="${s.id}" ${c.section === s.id ? "selected" : ""}>${esc(s.title)}</option>`).join("")}<option value="" ${c.section ? "" : "selected"}>без секции</option></select>
      <button class="link" data-up="${i}" title="Выше">↑</button>
      <button class="link danger" data-del="${i}" title="Удалить">✕</button>
    </div>
    <textarea data-f="description" rows="2" placeholder="Формулировка: когда ставить «Да» / как оценивать">${esc(c.description)}</textarea>
    ${c.type === "flag" ? "" : `<div class="row">
      <label class="chk"><input type="checkbox" data-f="na_allowed" ${c.na_allowed ? "checked" : ""}> N/A допустим</label>
      ${c.na_allowed ? `<input data-f="na_condition" class="grow" value="${esc(c.na_condition)}" placeholder="Условие N/A">` : ""}
    </div>`}
    ${c.type === "scale" ? `<div class="scale-ed">${c.scale.map((s, k) => `<div class="row"><input type="number" data-f="sv${k}" value="${s.value}" style="width:5em"><input data-f="sl${k}" class="grow" value="${esc(s.label)}" placeholder="Описание ступени"><button class="link" data-delstep="${k}">✕</button></div>`).join("")}
      <button class="link small" data-addstep="1">+ ступень</button> <span class="small muted">Верхняя ступень даёт полный вес критерия.</span></div>` : ""}
  </div>`;
}
