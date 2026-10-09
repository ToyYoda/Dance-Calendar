import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.db import init_db
from app.routers import admin, auth, me, organizer, public, webhooks
from app.security import Forbidden, LoginRequired
from app.web import redirect, render

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("dance_calendar")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    from app.config import DOTENV_LOADED, DOTENV_PATH

    log.info("Konfiguration: %s", DOTENV_PATH if DOTENV_LOADED else f"keine .env gefunden (erwartet: {DOTENV_PATH})")
    if settings.ai_provider == "mock":
        log.warning("KI: TESTMODUS (AI_PROVIDER=mock) – Flyer werden NICHT ausgelesen, es kommen feste Testdaten. "
                    "Für Ollama in .env AI_PROVIDER=openai setzen und den Server neu starten.")
    else:
        log.info("KI: Modell %s über %s", settings.ai_model, settings.ai_base_url)
    yield


app = FastAPI(title="Dance Calendar", lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

for module in (public, auth, me, organizer, admin, webhooks):
    app.include_router(module.router)


def _wants_json(request: Request) -> bool:
    return "/api/" in request.url.path or "application/json" in request.headers.get("accept", "")


@app.exception_handler(LoginRequired)
async def _login_required(request: Request, exc: LoginRequired):
    if _wants_json(request):
        return JSONResponse({"error": "login_required"}, status_code=401)
    target = "/admin/login" if exc.admin else "/login"
    nxt = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return redirect(f"{target}?next={quote(nxt)}")


@app.exception_handler(Forbidden)
async def _forbidden(request: Request, _exc: Forbidden):
    if _wants_json(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return render(request, "error.html", status_code=403, title="Keine Berechtigung",
                  message="Du darfst dieses Event nicht bearbeiten.")


@app.exception_handler(404)
async def _not_found(request: Request, _exc):
    if _wants_json(request):
        return JSONResponse({"error": "not_found"}, status_code=404)
    return render(request, "error.html", status_code=404, title="Nicht gefunden",
                  message="Diese Seite oder dieses Event gibt es nicht (mehr).")
