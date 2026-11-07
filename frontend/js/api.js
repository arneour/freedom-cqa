const base = () => (window.CQA_CONFIG && window.CQA_CONFIG.apiBase) || "";

async function req(method, path, body) {
  const res = await fetch(base() + path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    let msg = data && data.detail !== undefined ? data.detail : res.statusText;
    if (msg && msg.errors) msg = msg.errors.join("\n");
    else if (Array.isArray(msg)) msg = msg.map((e) => `${(e.loc || []).join(".")}: ${e.msg}`).join("\n");
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

export const api = {
  status: () => req("GET", "/api/status"),
  groups: () => req("GET", "/api/groups"),
  importGroup: (g) => req("POST", `/api/groups/${encodeURIComponent(g)}/import`),
  upload: (group_id, records) => req("POST", "/api/dialogs/upload", { group_id, records }),
  dialogs: (g) => req("GET", "/api/dialogs" + (g ? `?group_id=${encodeURIComponent(g)}` : "")),
  dialog: (g, id, evId) =>
    req("GET", `/api/dialogs/${encodeURIComponent(g)}/${encodeURIComponent(id)}` + (evId ? `?evaluation_id=${evId}` : "")),
  evaluate: (group_id, conversation_id, checklist_id) => req("POST", "/api/evaluate", { group_id, conversation_id, checklist_id }),
  batch: (body) => req("POST", "/api/evaluate/batch", body),
  job: (id) => req("GET", `/api/jobs/${id}`),
  evaluation: (id) => req("GET", `/api/evaluations/${id}`),
  appeal: (evId, criterion_id, comment) => req("POST", `/api/evaluations/${evId}/appeals`, { criterion_id, comment }),
  appeals: (status) => req("GET", "/api/appeals" + (status ? `?status=${status}` : "")),
  resolve: (id, body) => req("POST", `/api/appeals/${id}/resolve`, body),
  checklists: () => req("GET", "/api/checklists"),
  checklist: (id) => req("GET", `/api/checklists/${id}`),
  saveChecklist: (body) => req("POST", "/api/checklists", body),
  activate: (id) => req("POST", `/api/checklists/${id}/activate`),
  exportResults: (g, team) => req("GET", `/api/export/${encodeURIComponent(g)}` + (team ? `?team=${encodeURIComponent(team)}` : "")),
  audio: (g, id) => req("GET", `/api/audio/${encodeURIComponent(g)}/${encodeURIComponent(id)}`),
};
