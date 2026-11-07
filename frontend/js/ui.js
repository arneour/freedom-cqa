export const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

export function toast(msg, kind = "info") {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = `toast ${kind}`;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), kind === "error" ? 7000 : 3500);
}

export const fmt = (n) => (n === null || n === undefined ? "—" : Number(n).toFixed(2).replace(/\.00$/, ""));

export const SPEAKER = { client: "Клиент", operator: "Оператор", bot: "Бот", system: "Система" };

export function answerLabel(item, crit) {
  if (!item) return "—";
  if (item.answer === "na") return "N/A";
  if (item.answer === "score") return `${item.score} / ${Math.max(...crit.scale.map((s) => s.value))}`;
  if (crit && crit.type === "flag") return item.answer === "yes" ? "Выявлено" : "Нет";
  return item.answer === "yes" ? "Да" : "Нет";
}

export function answerClass(item, crit) {
  if (!item) return "";
  if (item.answer === "na") return "na";
  if (crit.type === "flag") return item.answer === "yes" ? "bad" : "good";
  if (item.answer === "score") {
    const mx = Math.max(...crit.scale.map((s) => s.value));
    return item.score >= mx ? "good" : item.score > 0 ? "mid" : "bad";
  }
  return item.answer === "yes" ? "good" : "bad";
}

export function scoreClass(total) {
  if (total === null || total === undefined) return "na";
  return total >= 85 ? "good" : total >= 60 ? "mid" : "bad";
}

/** Секунды от начала: MM:SS для чатов, ISO 8601 для звонков (относительно первой реплики). */
export function offsets(transcript) {
  const parse = (ts) => {
    if (!ts) return null;
    if (/^\d{1,2}:\d{2}(:\d{2})?$/.test(ts)) return ts.split(":").reduce((a, b) => a * 60 + Number(b), 0);
    const t = Date.parse(ts);
    return Number.isNaN(t) ? null : t / 1000;
  };
  const raw = transcript.map((m) => parse(m.timestamp));
  const first = raw.find((x) => x !== null) ?? 0;
  return raw.map((x) => (x === null ? null : Math.max(0, x - first)));
}

export const mmss = (s) => {
  if (s === null || s === undefined) return "";
  s = Math.round(s);
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
};

/** Варианты ответа для критерия: [{value, label}] где value = "yes" | "no" | "na" | "score:N" */
export function answerOptions(crit) {
  const opts = [];
  if (crit.type === "scale") crit.scale.forEach((s) => opts.push({ value: `score:${s.value}`, label: `${s.value} — ${s.label}` }));
  else if (crit.type === "flag") opts.push({ value: "yes", label: "Выявлено" }, { value: "no", label: "Нет" });
  else opts.push({ value: "yes", label: "Да" }, { value: "no", label: "Нет" });
  if (crit.na_allowed) opts.push({ value: "na", label: "N/A" });
  return opts;
}

export function parseAnswer(v) {
  if (v.startsWith("score:")) return { answer: "score", score: Number(v.slice(6)) };
  return { answer: v, score: null };
}

export const h = (html) => {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
};
