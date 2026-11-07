import { api } from "../api.js";
import { answerClass, answerLabel, esc, fmt, mmss, offsets, scoreClass, SPEAKER, toast } from "../ui.js";

export async function renderDialog(root, group, convId, evId) {
  const [d, cls] = await Promise.all([api.dialog(group, convId, evId), api.checklists()]);
  const offs = offsets(d.transcript);
  const isCall = d.channel === "voice call";

  root.innerHTML = `
    <div class="crumbs"><a href="#/dialogs">← Диалоги</a> / ${esc(d.group_id)} / <b>${esc(d.conversation_id)}</b></div>
    <div class="card-layout">
      <section class="panel transcript-panel">
        <div class="row head">
          <h2>${esc(d.conversation_id)}</h2>
          <span class="pill">${isCall ? "📞 Звонок" : "💬 Чат"}</span><span class="pill">${esc(d.language)}</span>
          <span class="muted">${d.transcript.length} реплик</span>
        </div>
        ${d.audio_url ? `<div id="player" class="player"><button id="loadAudio">▶ Загрузить запись</button>
            <a href="${esc(d.audio_url)}" target="_blank" rel="noopener" class="small">открыть плеер организаторов ↗</a></div>` : ""}
        <div class="legend small muted">Реплики бота и системные события — контекст, оценивается только оператор.</div>
        <ol id="msgs" class="msgs">
          ${d.transcript.map((m, i) => `
            <li id="msg-${i + 1}" class="msg ${esc(m.speaker)}" data-n="${i + 1}">
              <div class="meta"><span class="n">#${i + 1}</span> <b>${SPEAKER[m.speaker] || esc(m.speaker)}</b>
                <span class="ts" title="${esc(m.timestamp)}">${isCall ? mmss(offs[i]) : esc(m.timestamp)}</span>
                ${isCall && d.audio_url ? `<button class="seek link" data-t="${offs[i]}">▶</button>` : ""}</div>
              <div class="text">${esc(m.text)}</div>
            </li>`).join("")}
        </ol>
      </section>
      <section class="panel result-panel" id="result"></section>
    </div>`;

  const $ = (s) => root.querySelector(s);
  let audio = null;

  const ensureAudio = async () => {
    if (audio) return audio;
    const box = $("#player");
    box.querySelector("button").disabled = true;
    try {
      const r = await api.audio(d.group_id, d.conversation_id);
      if (!r.src) throw new Error("ссылка на запись не найдена");
      audio = document.createElement("audio");
      audio.controls = true;
      audio.src = r.src;
      audio.preload = "metadata";
      box.querySelector("button").replaceWith(audio);
      audio.addEventListener("timeupdate", () => {
        const t = audio.currentTime;
        let cur = 0;
        offs.forEach((o, i) => { if (o !== null && o <= t) cur = i + 1; });
        root.querySelectorAll(".msg.playing").forEach((e) => e.classList.remove("playing"));
        const el = $(`#msg-${cur}`);
        if (el) el.classList.add("playing");
      });
      return audio;
    } catch (e) {
      box.querySelector("button").disabled = false;
      toast("Не удалось загрузить запись: " + e.message + ". Используйте плеер организаторов.", "error");
      return null;
    }
  };
  if ($("#loadAudio")) $("#loadAudio").onclick = ensureAudio;

  const seekTo = async (n) => {
    const a = await ensureAudio();
    if (a && offs[n - 1] !== null) { a.currentTime = offs[n - 1]; a.play().catch(() => {}); }
  };
  $("#msgs").addEventListener("click", (e) => {
    const b = e.target.closest(".seek");
    if (b) seekTo(Number(b.closest(".msg").dataset.n));
  });

  const goto = (nums, range) => {
    root.querySelectorAll(".msg.hl, .msg.range").forEach((e) => e.classList.remove("hl", "range"));
    if (range) for (let i = range[0]; i <= range[1]; i++) $(`#msg-${i}`)?.classList.add("range");
    nums.forEach((n) => $(`#msg-${n}`)?.classList.add("hl"));
    const first = nums[0] || (range && range[0]);
    const el = first && $(`#msg-${first}`);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  drawResult($("#result"), d, cls, { goto, seekTo, isCall: isCall && !!d.audio_url, reload: (id) => renderDialog(root, group, convId, id) });

  // Ссылка вида #/dialog/G/ID?msg=5 — сразу к реплике
  const m = location.hash.match(/[?&]msg=(\d+)/);
  if (m) goto([Number(m[1])]);
}

function drawResult(box, d, cls, ctx) {
  const ev = d.evaluation;
  const active = cls.find((c) => c.is_active);
  const evalBar = `
    <div class="row eval-bar">
      <select id="cl">${cls.map((c) => `<option value="${c.id}" ${(ev ? ev.checklist_id === c.id : c.is_active) ? "selected" : ""}>#${c.id} ${esc(c.name)}${c.is_active ? " ★" : ""}</option>`).join("")}</select>
      <button id="run" class="primary">${ev ? "Переоценить" : "Оценить"}</button>
      ${d.evaluations.length > 1 ? `<select id="hist">${d.evaluations.map((h) => `<option value="${h.id}" ${ev && h.id === ev.id ? "selected" : ""}>Оценка #${h.id} · чек-лист #${h.checklist_id} · ${h.created_at.slice(0, 16).replace("T", " ")}${h.status !== "done" ? " (ошибка)" : ""}</option>`).join("")}</select>` : ""}
    </div>`;

  if (!ev) {
    const err = d.evaluations.find((h) => h.status === "error");
    box.innerHTML = evalBar + `<div class="empty">Диалог ещё не оценён.${err ? `<div class="error small">Последняя попытка: ${esc(err.error)}</div>` : ""}</div>`;
  } else {
    const cl = ev.checklist;
    const fin = ev.final;
    const orig = ev.score;
    const crits = cl.criteria;
    const changed = fin.score.total !== orig.total;
    const usage = ev.usage || {};
    box.innerHTML = evalBar + `
      <div class="total-box">
        <div class="total ${scoreClass(fin.score.total)}">${fmt(fin.score.total)}<small>/100</small></div>
        <div>
          <div><b>Итоговый балл</b>${changed ? ` <span class="muted">(исходный ${fmt(orig.total)}, изменён по апелляции)</span>` : ""}</div>
          <div class="muted small">Применимый вес ${fmt(fin.score.applicable_weight)} → приведён к 100 · чек-лист #${cl.id} «${esc(cl.name)}»</div>
          ${fin.score.gross_error ? `<div class="pill bad big">⚠ Грубая ошибка выявлена</div>` : `<div class="pill good">Грубых ошибок нет</div>`}
        </div>
      </div>
      ${fin.score.sections.length ? `<div class="sections">${fin.score.sections.map((s) => `<div class="sec"><span>${esc(s.title)}</span><b class="${scoreClass(s.score)}">${s.score === null ? "N/A" : fmt(s.score) + "%"}</b><small class="muted">${fmt(s.earned)} из ${fmt(s.max)}</small></div>`).join("")}</div>` : ""}
      ${usage.summary ? `<p class="summary">${esc(usage.summary)}</p>` : ""}
      <div class="crits">${crits.map((c) => critCard(c, ev, fin)).join("")}</div>
      <div class="muted small foot">Оценщик: ${esc(ev.evaluator)}${usage.input_tokens ? ` · токены ${usage.input_tokens}+${usage.output_tokens}${usage.cache_read_input_tokens ? ` (кеш ${usage.cache_read_input_tokens})` : ""}` : ""}${usage.cost_usd ? ` · ≈ $${usage.cost_usd.toFixed(4)}` : ""} · ${ev.created_at.replace("T", " ").slice(0, 16)}</div>`;
  }

  const $ = (s) => box.querySelector(s);
  $("#run").onclick = async () => {
    $("#run").disabled = true;
    $("#run").textContent = "Оценка…";
    try {
      const r = await api.evaluate(d.group_id, d.conversation_id, Number($("#cl").value));
      if (r.status !== "done") toast("Ошибка оценки: " + r.error, "error");
      ctx.reload(r.id);
    } catch (e) { toast(e.message, "error"); $("#run").disabled = false; $("#run").textContent = "Оценить"; }
  };
  if ($("#hist")) $("#hist").onchange = (e) => ctx.reload(Number(e.target.value));
  if (!ev) return;

  box.addEventListener("click", async (e) => {
    const ref = e.target.closest("[data-ev]");
    if (ref) {
      const nums = ref.dataset.ev ? ref.dataset.ev.split(",").map(Number) : [];
      const range = ref.dataset.range ? ref.dataset.range.split(",").map(Number) : null;
      ctx.goto(nums, range);
      if (ctx.isCall && ref.dataset.seek) ctx.seekTo(Number(ref.dataset.seek));
      return;
    }
    const ap = e.target.closest("[data-appeal]");
    if (ap) {
      const card = ap.closest(".crit");
      card.querySelector(".appeal-form").classList.toggle("hidden");
      card.querySelector("textarea").focus();
      return;
    }
    const send = e.target.closest("[data-send]");
    if (send) {
      const card = send.closest(".crit");
      const comment = card.querySelector("textarea").value.trim();
      if (!comment) return toast("Добавьте комментарий к апелляции", "error");
      try {
        await api.appeal(ev.id, send.dataset.send, comment);
        toast("Апелляция сохранена и передана проверяющему");
        ctx.reload(ev.id);
        window.dispatchEvent(new Event("cqa:appeals"));
      } catch (err) { toast(err.message, "error"); }
    }
  });
}

function critCard(c, ev, fin) {
  const orig = ev.items[c.id];
  const item = fin.items[c.id];
  const pts = fin.score.criteria[c.id];
  const appeals = ev.appeals.filter((a) => a.criterion_id === c.id);
  const open = appeals.find((a) => a.status === "open");
  const isChanged = fin.changed[c.id] !== undefined;
  const evid = item.evidence || [];
  const range = item.checked_range;
  const points = c.type === "flag" ? `<span class="muted">без баллов</span>`
    : item.answer === "na" ? `<span class="muted">вес ${fmt(c.weight)} перераспределён</span>`
    : `<b>${fmt(pts.raw)}</b> из ${fmt(c.weight)} <span class="muted">→ ${fmt(pts.final)} из ${fmt(pts.final_max)} после перераспределения</span>`;

  return `
  <article class="crit ${isChanged ? "changed" : ""}">
    <div class="crit-head">
      <span class="code">${esc(c.code || c.id)}</span>
      <span class="title">${esc(c.title)}</span>
      <span class="ans ${answerClass(item, c)}">${answerLabel(item, c)}</span>
    </div>
    <div class="crit-meta small">Вес ${fmt(c.weight)} · ${points}</div>
    <p class="expl">${esc(item.explanation)}</p>
    ${item.answer === "na" && item.na_reason ? `<p class="na-reason small"><b>Причина N/A:</b> ${esc(item.na_reason)}</p>` : ""}
    <div class="refs small">
      ${evid.length ? `Основание: ${evid.map((n) => `<button class="ref" data-ev="${n}" data-seek="${n}">#${n}</button>`).join(" ")}` : ""}
      ${range ? `<button class="ref range-ref" data-ev="" data-range="${range.join(",")}" data-seek="${range[0]}">проверенный участок #${range[0]}–#${range[1]}</button>` : ""}
      ${!evid.length && !range ? `<span class="muted">ссылок нет</span>` : ""}
    </div>
    ${(orig.warnings || []).length ? `<div class="warn small">⚠ ${orig.warnings.map(esc).join("; ")}</div>` : ""}
    ${appeals.map((a) => `
      <div class="appeal ${a.status}">
        <div><b>Апелляция #${a.id}</b> · ${a.status === "open" ? `<span class="pill mid">на рассмотрении</span> <a href="#/appeals/${a.id}" class="small">рассмотреть →</a>` : a.decision === "changed" ? `<span class="pill good">оценка изменена</span>` : `<span class="pill">оценка подтверждена</span>`}</div>
        <div class="small">Комментарий: ${esc(a.comment)}</div>
        ${a.status === "resolved" ? `<div class="small">Исходная оценка: <b>${answerLabel(orig, c)}</b>${a.decision === "changed" ? ` → решение: <b>${answerLabel(a.new_item, c)}</b>` : ""}<br>Причина: ${esc(a.reviewer_reason)}</div>` : ""}
      </div>`).join("")}
    ${open ? "" : `<button class="link small" data-appeal="${c.id}">Оспорить оценку</button>
      <div class="appeal-form hidden"><textarea rows="2" placeholder="Почему оценка неверна?"></textarea><button class="primary small" data-send="${c.id}">Подать апелляцию</button></div>`}
  </article>`;
}
