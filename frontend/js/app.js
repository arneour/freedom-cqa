import { api } from "./api.js";
import { esc, toast } from "./ui.js";
import { renderAppeals } from "./views/appeals.js";
import { renderChecklists } from "./views/checklists.js";
import { renderDialog } from "./views/dialog.js";
import { renderDialogs } from "./views/dialogs.js";

const view = document.getElementById("view");

async function route() {
  const hash = location.hash.replace(/^#/, "").split("?")[0] || "/dialogs";
  const parts = hash.split("/").filter(Boolean).map(decodeURIComponent);
  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === parts[0] || (parts[0] === "dialog" && a.dataset.nav === "dialogs")));
  // Каждый переход рисуется в новый контейнер: если предыдущий экран ещё догружается,
  // его запоздалая отрисовка попадёт в уже отключённый от страницы элемент.
  const root = document.createElement("div");
  view.replaceChildren(root);
  try {
    if (parts[0] === "dialog" && parts.length >= 3) await renderDialog(root, parts[1], parts[2]);
    else if (parts[0] === "appeals") await renderAppeals(root, parts[1] ? Number(parts[1]) : null);
    else if (parts[0] === "checklists") await renderChecklists(root, parts[1] ? Number(parts[1]) : null);
    else await renderDialogs(root);
  } catch (e) {
    if (!root.isConnected) return;
    root.innerHTML = `<div class="panel error">Ошибка: ${esc(e.message)}</div>`;
    toast(e.message, "error");
  }
}

async function refreshStatus() {
  try {
    const [s, open] = await Promise.all([api.status(), api.appeals("open")]);
    const el = document.getElementById("status");
    el.innerHTML = `<span class="pill ${s.llm ? "good" : "mid"}" title="${s.llm ? "" : "Задайте ANTHROPIC_API_KEY в .env для оценки моделью"}">Оценщик: ${esc(s.evaluator)}${s.llm ? "" : " (демо)"}</span>`;
    const b = document.getElementById("appeals-badge");
    b.textContent = open.length;
    b.classList.toggle("hidden", !open.length);
  } catch { /* сервер недоступен — покажет route() */ }
}

window.addEventListener("hashchange", route);
window.addEventListener("cqa:appeals", refreshStatus);
route();
refreshStatus();
