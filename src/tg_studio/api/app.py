from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from tg_studio.api.auth import AUTH_RESPONSE_EXPOSE_HEADERS
from tg_studio.config import settings
from tg_studio.db.session import engine
from tg_studio.modules.ai.api import router as ai_router
from tg_studio.modules.ai.system_prompt import set_engine as set_ai_engine
from tg_studio.modules.business.api import router as business_router
from tg_studio.modules.chat.api import router as chat_router
from tg_studio.modules.coworking.api import router as coworking_router
from tg_studio.modules.coworking.reports_api import router as coworking_reports_router
from tg_studio.modules.coworking.stock_api import router as coworking_stock_router
from tg_studio.modules.google_calendar.api import router as google_calendar_router
from tg_studio.modules.identity.api import router as auth_router
from tg_studio.modules.payments.api import router as payments_router
from tg_studio.modules.supplies.api import router as supplies_router
from tg_studio.modules.tattoo.api import router as tattoo_router

# В проде (https-публичный URL) интерактивная документация и схема API наружу
# не отдаются: /api/docs и /api/openapi.json раскрывают всю поверхность атаки.
_docs_enabled = not settings.require_https

app = FastAPI(
    title="TG Studio API",
    description="CRM backend for tattoo studios — scheduling, customers, analytics",
    version="0.4.0",
    docs_url="/api/docs" if _docs_enabled else None,
    redoc_url="/api/redoc" if _docs_enabled else None,
    openapi_url="/api/openapi.json" if _docs_enabled else None,
)

@app.exception_handler(IntegrityError)
async def integrity_error_handler(request: Request, exc: IntegrityError):
    return JSONResponse(status_code=409, content={"detail": "Conflict: resource already exists"})


# CORS: явные origin'ы публичных URL. allow_credentials + allow_origins=["*"]
# несовместимы (браузер отбрасывает ответ с credentials) и открывали API любому
# сайту с чужими cookie — подтверждено в проде smoke 2026-10-26.
_cors_origins = settings.cors_allowed_origins
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=list(AUTH_RESPONSE_EXPOSE_HEADERS),
    )

# Host-заголовок: в проде принимаем только известные публичные хосты
# (иначе ссылки в письмах/сброс пароля можно подделать чужим Host).
_trusted_hosts = settings.trusted_hosts
if _trusted_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=_trusted_hosts)

app.include_router(auth_router, prefix="/api")
app.include_router(business_router, prefix="/api")
app.include_router(ai_router, prefix="/api")
app.include_router(google_calendar_router, prefix="/api")
app.include_router(tattoo_router, prefix="/api")
app.include_router(supplies_router, prefix="/api")
app.include_router(payments_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(coworking_router, prefix="/api")
app.include_router(coworking_stock_router, prefix="/api")
app.include_router(coworking_reports_router, prefix="/api")


# Initialize AI schema introspection
set_ai_engine(engine)


@app.get("/api/health")
async def health():
    return {"status": "ok"}
