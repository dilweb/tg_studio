from fastapi import APIRouter

from tg_studio.api.routes.admin import bookings, business, masters, schedule, services

router = APIRouter(prefix="/admin")

router.include_router(business.router)
router.include_router(masters.router)
router.include_router(services.router)
router.include_router(schedule.router)
router.include_router(bookings.router)
