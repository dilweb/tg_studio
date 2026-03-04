"""Admin: настройки бизнеса (Freedom Pay credentials)."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from tg_studio.api.deps import SessionDep
from tg_studio.api.routes.admin.deps import OwnerBusinessDep
from tg_studio.db.models import Business

router = APIRouter(prefix="/business", tags=["admin • business"])


class PaymentSettingsUpdate(BaseModel):
    freedom_pay_merchant_id: int | None = Field(default=None, description="Merchant ID из my.freedompay.kz")
    freedom_pay_secret_key: str | None = Field(default=None, max_length=256)


@router.patch("/payment-settings")
async def update_payment_settings(
    body: PaymentSettingsUpdate,
    session: SessionDep,
    business: OwnerBusinessDep,
):
    """Обновить платёжные настройки Freedom Pay для бизнеса."""
    if body.freedom_pay_merchant_id is not None:
        business.freedom_pay_merchant_id = body.freedom_pay_merchant_id
    if body.freedom_pay_secret_key is not None:
        if body.freedom_pay_secret_key.strip() == "":
            raise HTTPException(
                status_code=400,
                detail="freedom_pay_secret_key не может быть пустым",
            )
        business.freedom_pay_secret_key = body.freedom_pay_secret_key.strip()

    await session.commit()
    await session.refresh(business)

    return {
        "freedom_pay_merchant_id": business.freedom_pay_merchant_id,
        "freedom_pay_configured": bool(
            business.freedom_pay_merchant_id and business.freedom_pay_secret_key
        ),
    }
