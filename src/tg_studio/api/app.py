from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from tg_studio.api.auth import AUTH_RESPONSE_EXPOSE_HEADERS
from tg_studio.db.session import engine
from tg_studio.modules.ai.api import router as ai_router
from tg_studio.modules.ai.system_prompt import set_engine as set_ai_engine
from tg_studio.modules.business.api import router as business_router
from tg_studio.modules.google_calendar.api import router as google_calendar_router
from tg_studio.modules.identity.api import router as auth_router
from tg_studio.modules.scheduling.api import router as scheduling_router
from tg_studio.modules.tattoo.api import router as tattoo_router

app = FastAPI(
    title="TG Studio API",
    description="CRM backend for tattoo studios — scheduling, customers, analytics",
    version="0.4.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

@app.exception_handler(IntegrityError)
async def integrity_error_handler(request: Request, exc: IntegrityError):
    return JSONResponse(status_code=409, content={"detail": "Conflict: resource already exists"})


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=list(AUTH_RESPONSE_EXPOSE_HEADERS),
)

app.include_router(auth_router, prefix="/api")
app.include_router(business_router, prefix="/api")
app.include_router(scheduling_router, prefix="/api")
app.include_router(ai_router, prefix="/api")
app.include_router(google_calendar_router, prefix="/api")
app.include_router(tattoo_router, prefix="/api")


# Initialize AI schema introspection
set_ai_engine(engine)


@app.get("/api/health")
async def health():
    return {"status": "ok"}
