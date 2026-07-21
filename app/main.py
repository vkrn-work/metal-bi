# -*- coding: utf-8 -*-
"""EMK BI — веб-дашборд «Конверсия КП → Заказ».

Вход по паролю (заглушка: один общий пароль из переменной окружения),
дашборд с фильтрами, страница методики и страница загрузки выгрузок из CRM.
"""
from __future__ import annotations

import os
import secrets
import shutil

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from pipeline import RebuildJob, etl_available
from report import CLIENT_FILTERS, Report

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))

# Пароль входа и ключ подписи cookie задаются переменными окружения Railway
APP_PASSWORD = os.environ.get("APP_PASSWORD", "emk2026")
SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(ROOT, "data"))
PARQUET = os.path.join(DATA_DIR, "base.parquet")

os.makedirs(DATA_DIR, exist_ok=True)

# Если база лежит в образе, а DATA_DIR — примонтированный том, копируем один раз
_bundled = os.path.join(ROOT, "data", "base.parquet")
if not os.path.exists(PARQUET) and os.path.exists(_bundled) and _bundled != PARQUET:
    shutil.copy2(_bundled, PARQUET)

app = FastAPI(title="EMK BI", docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, max_age=60 * 60 * 12)
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))

report = Report(PARQUET)
job = RebuildJob()


def authed(request: Request) -> bool:
    return bool(request.session.get("auth"))


def need_auth() -> JSONResponse:
    return JSONResponse({"error": "auth"}, status_code=401)


# ----------------------------------------------------------------- страницы
@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str = ""):
    if authed(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": error})


@app.post("/login")
def login_submit(request: Request, password: str = Form("")):
    if secrets.compare_digest(password.strip(), APP_PASSWORD):
        request.session["auth"] = True
        return RedirectResponse("/", status_code=303)
    return RedirectResponse("/login?error=1", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    if not authed(request):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        request, "dashboard.html", {"filters": CLIENT_FILTERS, "ready": report.ready}
    )


@app.get("/metrics", response_class=HTMLResponse)
def metrics_page(request: Request):
    """Методика: как считается каждый показатель отчёта."""
    if not authed(request):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "metrics.html", {})


@app.get("/upload", response_class=HTMLResponse)
def upload_page(request: Request):
    if not authed(request):
        return RedirectResponse("/login", status_code=303)
    ok, msg = etl_available()
    return templates.TemplateResponse(
        request, "upload.html", {"etl_ok": ok, "etl_msg": msg, "rows": report.rows}
    )


# ----------------------------------------------------------------- API
@app.get("/api/filters")
def api_filters(request: Request):
    if not authed(request):
        return need_auth()
    return report.filter_options()


@app.post("/api/report")
async def api_report(request: Request):
    if not authed(request):
        return need_auth()
    body = await request.json()
    f = {
        "date_from": body.get("date_from") or None,
        "date_to": body.get("date_to") or None,
        "source": body.get("source") or [],
    }
    for key in CLIENT_FILTERS:
        f[key] = body.get(key) or []
    return JSONResponse(report.build(f))


@app.get("/api/status")
def api_status(request: Request):
    if not authed(request):
        return need_auth()
    ok, msg = etl_available()
    return {
        "rows": report.rows,
        "ready": report.ready,
        "min_date": str(report.min_date) if report.min_date else None,
        "max_date": str(report.max_date) if report.max_date else None,
        "etl_ok": ok,
        "etl_msg": msg,
        "job": job.snapshot(),
    }


@app.post("/api/upload")
async def api_upload(
    request: Request,
    kp: list[UploadFile] = File(default=[]),
    zk: list[UploadFile] = File(default=[]),
    clients: UploadFile | None = File(default=None),
    managers: UploadFile | None = File(default=None),
):
    """Полный пересчёт: выгрузки КП/ЗК + справочники → prepare_base.py → база."""
    if not authed(request):
        return need_auth()
    ok, msg = etl_available()
    if not ok:
        return JSONResponse({"error": msg}, status_code=400)
    if job.snapshot()["running"]:
        return JSONResponse({"error": "пересборка уже идёт"}, status_code=409)
    if not kp and not zk:
        return JSONResponse({"error": "не приложены файлы КП или ЗК"}, status_code=400)

    work = os.path.join(DATA_DIR, "_work")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(os.path.join(work, "kp"), exist_ok=True)
    os.makedirs(os.path.join(work, "zk"), exist_ok=True)

    async def save(upload: UploadFile, path: str):
        with open(path, "wb") as fh:
            while chunk := await upload.read(1 << 20):
                fh.write(chunk)

    for i, f in enumerate(kp):
        await save(f, os.path.join(work, "kp", f"kp_{i}_{os.path.basename(f.filename)}"))
    for i, f in enumerate(zk):
        await save(f, os.path.join(work, "zk", f"zk_{i}_{os.path.basename(f.filename)}"))
    if clients is not None and clients.filename:
        await save(clients, os.path.join(work, "clients.xlsx"))
    if managers is not None and managers.filename:
        await save(managers, os.path.join(work, "managers.xlsx"))

    job.start(work, PARQUET, report.load)
    return {"started": True}


@app.post("/api/upload-parquet")
async def api_upload_parquet(request: Request, file: UploadFile = File(...)):
    """Быстрый путь: заменить базу уже готовым base.parquet."""
    if not authed(request):
        return need_auth()
    if not file.filename.lower().endswith(".parquet"):
        return JSONResponse({"error": "нужен файл .parquet"}, status_code=400)
    tmp = PARQUET + ".new"
    with open(tmp, "wb") as fh:
        while chunk := await file.read(1 << 20):
            fh.write(chunk)
    try:
        probe = Report(tmp)
        if not probe.ready:
            raise RuntimeError("в файле нет строк")
        rows = probe.rows
    except Exception as exc:  # noqa: BLE001
        os.remove(tmp)
        return JSONResponse({"error": f"файл не читается: {exc}"}, status_code=400)
    if os.path.exists(PARQUET):
        shutil.copy2(PARQUET, PARQUET + ".bak")
    os.replace(tmp, PARQUET)
    report.load()
    return {"ok": True, "rows": rows}


@app.get("/healthz")
def healthz():
    return {"ok": True, "rows": report.rows}
