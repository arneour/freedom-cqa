"""CQA — Conversation Quality Analytics. HTTP API + раздача фронтенда."""
from __future__ import annotations

import json
import os
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Literal, Optional

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

import datasource  # noqa: E402
import evaluator  # noqa: E402
import rules  # noqa: E402
from db import dumps, now, row, tx  # noqa: E402
from scoring import compute, normalize_item  # noqa: E402

app = FastAPI(title="CQA prototype")
# Фронтенд может жить отдельно (например, веб-ресурс Dynamics 365) — разрешаем CORS.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

CL_JSON = ("data",)
EV_JSON = ("items", "score", "usage")
AP_JSON = ("new_item",)


# ---------------- Чек-листы ----------------

def ensure_default_checklist():
    data = json.loads((Path(__file__).parent / "default_checklist.json").read_text(encoding="utf-8"))
    with tx() as con:
        rows = con.execute("SELECT id, data FROM checklists ORDER BY id").fetchall()
        if rows:
            # Миграция: самая первая (засеянная) версия исходного чек-листа получает актуальные правила auto
            # из default_checklist.json. Версии, сохранённые пользователем, не трогаем.
            auto = {c["id"]: c.get("auto", "") for c in data["criteria"]}
            for r in rows:
                d = json.loads(r["data"])
                if d.get("name") == data["name"]:
                    changed = False
                    for c in d["criteria"]:
                        if c["id"] in auto and c.get("auto") != auto[c["id"]]:
                            c["auto"] = auto[c["id"]]
                            changed = True
                    if changed:
                        con.execute("UPDATE checklists SET data=? WHERE id=?", (dumps(d), r["id"]))
                    break  # только первая по id
            return
        con.execute("INSERT INTO checklists(name,data,created_at,is_active) VALUES(?,?,?,1)",
                    (data["name"], dumps(data), now()))


def get_checklist(cid: int) -> dict:
    with tx() as con:
        r = row(con.execute("SELECT * FROM checklists WHERE id=?", (cid,)).fetchone(), CL_JSON)
    if not r:
        raise HTTPException(404, "Чек-лист не найден")
    return r


def active_checklist() -> dict:
    with tx() as con:
        r = row(con.execute("SELECT * FROM checklists WHERE is_active=1 ORDER BY id DESC LIMIT 1").fetchone(), CL_JSON)
    return r


class ScaleStep(BaseModel):
    value: int
    label: str = ""


class Criterion(BaseModel):
    id: str
    code: str = ""
    section: str = ""
    title: str
    type: Literal["binary", "scale", "flag"]
    weight: float = 0
    na_allowed: bool = False
    description: str = ""
    na_condition: str = ""
    scale: list[ScaleStep] = Field(default_factory=list)
    auto: str = ""  # имя правила из rules.py (используется в режиме локальной модели); пусто — оценивает модель


class Section(BaseModel):
    id: str
    title: str


class ChecklistIn(BaseModel):
    name: str
    sections: list[Section] = Field(default_factory=list)
    criteria: list[Criterion]
    parent_id: Optional[int] = None
    activate: bool = True


def validate_checklist(cl: ChecklistIn) -> list[str]:
    errs = []
    ids = [c.id for c in cl.criteria]
    if len(ids) != len(set(ids)):
        errs.append("ID критериев должны быть уникальными")
    sec_ids = {s.id for s in cl.sections}
    if not cl.criteria:
        errs.append("Добавьте хотя бы один критерий")
    for c in cl.criteria:
        if not c.title.strip():
            errs.append(f"{c.id}: пустое название")
        if c.weight < 0:
            errs.append(f"{c.title}: вес не может быть отрицательным")
        if c.type == "scale":
            if len(c.scale) < 2:
                errs.append(f"{c.title}: у шкалы должно быть минимум 2 ступени")
            elif max(s.value for s in c.scale) <= 0:
                errs.append(f"{c.title}: верхняя ступень шкалы должна быть больше 0")
        if c.auto and c.auto not in rules.RULES:
            errs.append(f"{c.title}: неизвестное правило «{c.auto}»")
        if c.na_allowed and not c.na_condition.strip():
            errs.append(f"{c.title}: укажите условие N/A")
        if c.section and c.section not in sec_ids:
            errs.append(f"{c.title}: неизвестная секция {c.section}")
    if not any(c.type != "flag" and c.weight > 0 for c in cl.criteria):
        errs.append("Нужен хотя бы один критерий с весом больше 0")
    return errs


@app.get("/api/checklists")
def list_checklists():
    with tx() as con:
        rs = con.execute("SELECT id,name,created_at,parent_id,is_active,data FROM checklists ORDER BY id DESC").fetchall()
    out = []
    for r in rs:
        d = json.loads(r["data"])
        out.append({"id": r["id"], "name": r["name"], "created_at": r["created_at"], "parent_id": r["parent_id"],
                    "is_active": bool(r["is_active"]), "criteria_count": len(d["criteria"]),
                    "total_weight": sum(c.get("weight", 0) for c in d["criteria"] if c["type"] != "flag")})
    return out


@app.get("/api/checklists/{cid}")
def read_checklist(cid: int):
    return get_checklist(cid)


@app.post("/api/checklists")
def create_checklist(body: ChecklistIn):
    errs = validate_checklist(body)
    if errs:
        raise HTTPException(422, {"errors": errs})
    data = body.model_dump(exclude={"parent_id", "activate"})
    for c in data["criteria"]:
        if c["type"] == "flag":
            c["weight"] = 0
        if c["type"] != "scale":
            c["scale"] = []
        else:
            c["scale"] = sorted(c["scale"], key=lambda s: -s["value"])
    with tx() as con:
        if body.activate:
            con.execute("UPDATE checklists SET is_active=0")
        cur = con.execute("INSERT INTO checklists(name,data,created_at,parent_id,is_active) VALUES(?,?,?,?,?)",
                          (body.name, dumps(data), now(), body.parent_id, int(body.activate)))
        new_id = cur.lastrowid
    return get_checklist(new_id)


@app.post("/api/checklists/{cid}/activate")
def activate_checklist(cid: int):
    get_checklist(cid)
    with tx() as con:
        con.execute("UPDATE checklists SET is_active=0")
        con.execute("UPDATE checklists SET is_active=1 WHERE id=?", (cid,))
    return {"ok": True}


# ---------------- Диалоги ----------------

def upsert_records(group_id: str, records: list[dict]) -> int:
    n = 0
    with tx() as con:
        for r in records:
            tr = r.get("transcript")
            if isinstance(tr, str):
                tr = json.loads(tr)
            if not r.get("conversation_id") or not isinstance(tr, list):
                continue
            con.execute(
                "INSERT INTO dialogs(conversation_id,group_id,channel,language,transcript,audio_url,imported_at) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(group_id,conversation_id) DO UPDATE SET "
                "channel=excluded.channel,language=excluded.language,transcript=excluded.transcript,"
                "audio_url=excluded.audio_url,imported_at=excluded.imported_at",
                (r["conversation_id"], group_id, r.get("channel"), r.get("language"), dumps(tr),
                 r.get("audio_url"), now()))
            n += 1
    return n


@app.post("/api/groups/{group_id}/import")
def import_group(group_id: str):
    try:
        records = datasource.fetch_group(group_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Не удалось получить набор {group_id}: {e}")
    return {"group_id": group_id, "imported": upsert_records(group_id, records)}


class UploadIn(BaseModel):
    group_id: str = "UPLOAD"
    records: list[dict]


@app.post("/api/dialogs/upload")
def upload_dialogs(body: UploadIn):
    n = upsert_records(body.group_id, body.records)
    if not n:
        raise HTTPException(422, "Не найдено ни одной записи с conversation_id и transcript")
    return {"group_id": body.group_id, "imported": n}


@app.get("/api/groups")
def list_groups():
    with tx() as con:
        rs = con.execute("SELECT group_id, COUNT(*) n FROM dialogs GROUP BY group_id ORDER BY group_id").fetchall()
    return [dict(r) for r in rs]


def final_for(ev: dict, checklist: dict, appeals: list[dict]) -> dict:
    """Финальная оценка = исходная + решения проверяющего по апелляциям."""
    items = {k: dict(v) for k, v in (ev.get("items") or {}).items()}
    changed = {}
    for a in sorted(appeals, key=lambda a: a["id"]):
        if a["status"] == "resolved" and a["decision"] == "changed" and a.get("new_item"):
            items[a["criterion_id"]] = {**items.get(a["criterion_id"], {}), **a["new_item"]}
            changed[a["criterion_id"]] = a["id"]
    return {"items": items, "score": compute(checklist["data"], items), "changed": changed}


def appeals_for(con, eval_id: int) -> list[dict]:
    return [row(r, AP_JSON) for r in con.execute("SELECT * FROM appeals WHERE evaluation_id=? ORDER BY id", (eval_id,))]


@app.get("/api/dialogs")
def list_dialogs(group_id: Optional[str] = None):
    q = "SELECT conversation_id,group_id,channel,language,audio_url,transcript FROM dialogs"
    args: tuple = ()
    if group_id:
        q += " WHERE group_id=?"
        args = (group_id,)
    q += " ORDER BY group_id, conversation_id"
    with tx() as con:
        ds = con.execute(q, args).fetchall()
        evs = {}
        for e in con.execute("SELECT e.* FROM evaluations e JOIN (SELECT group_id,conversation_id,MAX(id) mid "
                             "FROM evaluations WHERE status='done' GROUP BY group_id,conversation_id) m ON e.id=m.mid"):
            evs[(e["group_id"], e["conversation_id"])] = row(e, EV_JSON)
        apps = {}
        for a in con.execute("SELECT evaluation_id, SUM(status='open') open_n, COUNT(*) n FROM appeals GROUP BY evaluation_id"):
            apps[a["evaluation_id"]] = dict(a)
    cls: dict[int, dict] = {}
    out = []
    for d in ds:
        tr = json.loads(d["transcript"])
        item = {"conversation_id": d["conversation_id"], "group_id": d["group_id"], "channel": d["channel"],
                "language": d["language"], "has_audio": bool(d["audio_url"]), "messages": len(tr),
                "evaluation": None}
        ev = evs.get((d["group_id"], d["conversation_id"]))
        if ev:
            if ev["checklist_id"] not in cls:
                cls[ev["checklist_id"]] = get_checklist(ev["checklist_id"])
            with tx() as con:
                aps = appeals_for(con, ev["id"])
            fin = final_for(ev, cls[ev["checklist_id"]], aps)
            a = apps.get(ev["id"], {})
            item["evaluation"] = {"id": ev["id"], "checklist_id": ev["checklist_id"], "created_at": ev["created_at"],
                                  "total": ev["score"]["total"], "final_total": fin["score"]["total"],
                                  "gross_error": fin["score"]["gross_error"], "evaluator": ev["evaluator"],
                                  "appeals_open": a.get("open_n") or 0, "appeals": a.get("n") or 0}
        out.append(item)
    return out


@app.get("/api/dialogs/{group_id}/{conversation_id}")
def read_dialog(group_id: str, conversation_id: str, evaluation_id: Optional[int] = None):
    with tx() as con:
        d = row(con.execute("SELECT * FROM dialogs WHERE group_id=? AND conversation_id=?",
                            (group_id, conversation_id)).fetchone(), ("transcript",))
        if not d:
            raise HTTPException(404, "Диалог не найден")
        hist = [dict(r) for r in con.execute(
            "SELECT id,checklist_id,created_at,status,evaluator,error FROM evaluations "
            "WHERE group_id=? AND conversation_id=? ORDER BY id DESC", (group_id, conversation_id))]
    d["evaluations"] = hist
    target = evaluation_id or next((h["id"] for h in hist if h["status"] == "done"), None)
    d["evaluation"] = read_evaluation(target) if target else None
    return d


def run_evaluation(group_id: str, conversation_id: str, checklist_id: int) -> int:
    with tx() as con:
        d = row(con.execute("SELECT * FROM dialogs WHERE group_id=? AND conversation_id=?",
                            (group_id, conversation_id)).fetchone(), ("transcript",))
    if not d:
        raise HTTPException(404, "Диалог не найден")
    cl = get_checklist(checklist_id)
    try:
        items, name, usage, summary = evaluator.evaluate(d, cl["data"])
        usage["summary"] = summary
        score = compute(cl["data"], items)
        vals = ("done", name, dumps(items), dumps(score), dumps(usage), None)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        vals = ("error", evaluator.model_name(), None, None, None, f"{type(e).__name__}: {e}")
    with tx() as con:
        cur = con.execute("INSERT INTO evaluations(group_id,conversation_id,checklist_id,created_at,status,evaluator,"
                          "items,score,usage,error) VALUES(?,?,?,?,?,?,?,?,?,?)",
                          (group_id, conversation_id, checklist_id, now(), *vals))
        return cur.lastrowid


class EvalIn(BaseModel):
    group_id: str
    conversation_id: str
    checklist_id: Optional[int] = None


@app.post("/api/evaluate")
def evaluate_one(body: EvalIn):
    cid = body.checklist_id or active_checklist()["id"]
    eid = run_evaluation(body.group_id, body.conversation_id, cid)
    return read_evaluation(eid)


# ---------------- Пакетная оценка ----------------

JOBS: dict[str, dict] = {}
POOL = ThreadPoolExecutor(max_workers=int(os.environ.get("CQA_CONCURRENCY", "4")))


class BatchIn(BaseModel):
    group_id: str
    checklist_id: Optional[int] = None
    only_missing: bool = True
    conversation_ids: Optional[list[str]] = None


@app.post("/api/evaluate/batch")
def evaluate_batch(body: BatchIn):
    cid = body.checklist_id or active_checklist()["id"]
    with tx() as con:
        ids = [r["conversation_id"] for r in con.execute(
            "SELECT conversation_id FROM dialogs WHERE group_id=? ORDER BY conversation_id", (body.group_id,))]
        if body.only_missing:
            done = {r["conversation_id"] for r in con.execute(
                "SELECT conversation_id FROM evaluations WHERE group_id=? AND checklist_id=? AND status='done'",
                (body.group_id, cid))}
            ids = [i for i in ids if i not in done]
    if body.conversation_ids:
        ids = [i for i in ids if i in set(body.conversation_ids)]
    job_id = uuid.uuid4().hex[:10]
    job = {"id": job_id, "group_id": body.group_id, "checklist_id": cid, "total": len(ids), "done": 0,
           "errors": 0, "cost_usd": 0.0, "status": "running" if ids else "finished", "started_at": now()}
    JOBS[job_id] = job
    lock = threading.Lock()

    def work(conv_id: str):
        eid = run_evaluation(body.group_id, conv_id, cid)
        ev = read_evaluation(eid)
        with lock:
            job["done"] += 1
            if ev["status"] != "done":
                job["errors"] += 1
            job["cost_usd"] = round(job["cost_usd"] + ((ev.get("usage") or {}).get("cost_usd") or 0), 5)
            if job["done"] >= job["total"]:
                job["status"] = "finished"
                job["finished_at"] = now()

    for i in ids:
        POOL.submit(work, i)
    return job


@app.get("/api/jobs/{job_id}")
def read_job(job_id: str):
    if job_id not in JOBS:
        raise HTTPException(404, "Задача не найдена")
    return JOBS[job_id]


# ---------------- Оценки и апелляции ----------------

@app.get("/api/evaluations/{eid}")
def read_evaluation(eid: int):
    with tx() as con:
        ev = row(con.execute("SELECT * FROM evaluations WHERE id=?", (eid,)).fetchone(), EV_JSON)
        if not ev:
            raise HTTPException(404, "Оценка не найдена")
        aps = appeals_for(con, eid)
    cl = get_checklist(ev["checklist_id"])
    ev["checklist"] = {"id": cl["id"], "name": cl["name"], **cl["data"]}
    ev["appeals"] = aps
    if ev["status"] == "done":
        ev["final"] = final_for(ev, cl, aps)
    return ev


class AppealIn(BaseModel):
    criterion_id: str
    comment: str


@app.post("/api/evaluations/{eid}/appeals")
def create_appeal(eid: int, body: AppealIn):
    ev = read_evaluation(eid)
    if ev["status"] != "done":
        raise HTTPException(400, "Нельзя оспорить неуспешную оценку")
    if body.criterion_id not in (ev["items"] or {}):
        raise HTTPException(400, "Критерий не найден в оценке")
    if not body.comment.strip():
        raise HTTPException(422, "Добавьте комментарий")
    if any(a["criterion_id"] == body.criterion_id and a["status"] == "open" for a in ev["appeals"]):
        raise HTTPException(409, "По этому критерию уже есть открытая апелляция")
    with tx() as con:
        con.execute("INSERT INTO appeals(evaluation_id,criterion_id,comment,created_at) VALUES(?,?,?,?)",
                    (eid, body.criterion_id, body.comment.strip(), now()))
    return read_evaluation(eid)


@app.get("/api/appeals")
def list_appeals(status: Optional[str] = None):
    q = ("SELECT a.*, e.group_id, e.conversation_id, e.checklist_id FROM appeals a "
         "JOIN evaluations e ON e.id=a.evaluation_id")
    args: tuple = ()
    if status:
        q += " WHERE a.status=?"
        args = (status,)
    q += " ORDER BY a.status='resolved', a.id DESC"
    with tx() as con:
        rs = [row(r, AP_JSON) for r in con.execute(q, args)]
    return rs


class ResolveIn(BaseModel):
    decision: Literal["confirmed", "changed"]
    reason: str
    answer: Optional[str] = None
    score: Optional[int] = None


@app.post("/api/appeals/{aid}/resolve")
def resolve_appeal(aid: int, body: ResolveIn):
    if not body.reason.strip():
        raise HTTPException(422, "Укажите причину решения")
    with tx() as con:
        a = row(con.execute("SELECT * FROM appeals WHERE id=?", (aid,)).fetchone(), AP_JSON)
    if not a:
        raise HTTPException(404, "Апелляция не найдена")
    if a["status"] != "open":
        raise HTTPException(409, "Апелляция уже рассмотрена")
    ev = read_evaluation(a["evaluation_id"])
    crit = next(c for c in ev["checklist"]["criteria"] if c["id"] == a["criterion_id"])
    new_item = None
    if body.decision == "changed":
        if not body.answer:
            raise HTTPException(422, "Укажите новую оценку")
        new_item, warnings = normalize_item(crit, {"answer": body.answer, "score": body.score})
        if warnings:
            raise HTTPException(422, "; ".join(warnings))
        new_item["explanation"] = f"Изменено проверяющим: {body.reason.strip()}"
        new_item["na_reason"] = body.reason.strip() if new_item["answer"] == "na" else ""
    with tx() as con:
        con.execute("UPDATE appeals SET status='resolved', decision=?, new_item=?, reviewer_reason=?, resolved_at=? "
                    "WHERE id=?", (body.decision, dumps(new_item) if new_item else None, body.reason.strip(), now(), aid))
    return read_evaluation(a["evaluation_id"])


# ---------------- Аудио ----------------

@app.get("/api/audio/{group_id}/{conversation_id}")
def audio(group_id: str, conversation_id: str):
    with tx() as con:
        d = con.execute("SELECT audio_url FROM dialogs WHERE group_id=? AND conversation_id=?",
                        (group_id, conversation_id)).fetchone()
    if not d or not d["audio_url"]:
        raise HTTPException(404, "У диалога нет записи")
    try:
        src = datasource.resolve_audio(d["audio_url"])
    except Exception as e:  # noqa: BLE001
        src = None
        print("audio resolve failed:", e)
    return {"src": src, "player_url": d["audio_url"]}


@app.get("/api/status")
def status():
    cl = active_checklist()
    return {"evaluator": evaluator.model_name() if evaluator.llm_available() else "heuristic",
            "llm": evaluator.llm_available(),
            "active_checklist": {"id": cl["id"], "name": cl["name"]} if cl else None,
            "data_api_configured": bool(os.environ.get("DATA_API_URL")),
            "hybrid_rules": evaluator.mode() == "ollama",
            "rules": {k: v[0] for k, v in rules.RULES.items()}}


ensure_default_checklist()

FRONT = ROOT / "frontend"
app.mount("/static", StaticFiles(directory=FRONT), name="static")


@app.get("/")
def index():
    return FileResponse(FRONT / "index.html")


# ---------------- ДЕМО: профили операторов (отдельная страница, только чтение) ----------------
# Подключается изолированно: ошибка в демо-модуле не мешает основному приложению.
try:
    import demo_operators

    app.include_router(demo_operators.make_router(final_for, appeals_for, get_checklist, active_checklist))

    @app.get("/operators")
    def operators_page():
        return FileResponse(FRONT / "operators.html")
except Exception as _demo_err:  # noqa: BLE001
    print("Демо-страница операторов отключена:", _demo_err)
