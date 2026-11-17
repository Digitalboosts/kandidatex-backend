from typing import Literal

from pydantic import StringConstraints
from typing import Annotated

from ..schemas import CamelModel

Id = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class PlanDTO(CamelModel):
    code: str
    name: str
    description: str
    amount: int  # smallest unit (paise)
    amount_display: str
    currency: str
    duration_days: int
    interviews_per_period: int


class PlanResponse(CamelModel):
    plan: PlanDTO


class CheckoutResponse(CamelModel):
    """Open Razorpay Checkout with `key: keyId`, `order_id: orderId` and `amount`/`currency` from `plan`."""

    key_id: str
    order_id: str
    plan: PlanDTO


class VerifyPaymentRequest(CamelModel):
    razorpay_payment_id: Id
    razorpay_order_id: Id
    razorpay_signature: Id


class InterviewQuotaDTO(CamelModel):
    included: int
    used: int
    remaining: int


class SubscriptionDTO(CamelModel):
    status: str
    active: bool
    plan: PlanDTO
    current_period_start: str | None
    current_period_end: str | None
    interviews: InterviewQuotaDTO


class TransactionDTO(CamelModel):
    id: str
    razorpay_payment_id: str
    amount: int
    amount_display: str
    currency: str
    status: str
    method: str | None
    created_at: str


class SubscriptionMeResponse(CamelModel):
    subscribed: bool
    subscription: SubscriptionDTO | None
    interviews: InterviewQuotaDTO
    transactions: list[TransactionDTO]


class VerifyPaymentResponse(CamelModel):
    status: Literal["active", "pending"]
    subscription: SubscriptionDTO | None
