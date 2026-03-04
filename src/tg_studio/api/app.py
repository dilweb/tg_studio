from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from tg_studio.api.routes import bookings, business, freedompay, kaspi, slots
from tg_studio.api.routes.admin.router import router as admin_router
from tg_studio.config import settings

app = FastAPI(
    title="TG Studio API",
    description="Backend for Telegram Mini App booking system",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # В продакшне заменить на конкретный домен Mini App
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(business.router, prefix="/api")
app.include_router(slots.router, prefix="/api")
app.include_router(bookings.router, prefix="/api")
app.include_router(kaspi.router, prefix="/api")
app.include_router(freedompay.router, prefix="/api")
app.include_router(admin_router, prefix="/api")


@app.get("/api/health")
async def health():
    return {"status": "ok"}
