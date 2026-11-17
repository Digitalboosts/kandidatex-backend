import json
import logging

from fastapi import APIRouter, BackgroundTasks, Header, Request

from ..deps import CurrentUser, Session
from ..errors import ApiError
from ..models import User
from ..rate_limit import rate_limit
from ..routers._docs import errors
from . import emails, razorpay_client as razorpay, service
from .plans import MONTHLY_PLAN
from .schemas import (
    CheckoutResponse,
    PlanResponse,
    SubscriptionMeResponse,
    VerifyPaymentRequest,
    VerifyPaymentResponse,
)

router = APIRouter(prefix="/api/subscription", tags=["Subscription"])
logger = logging.getLogger("app.subscription")


async def _send_charge_emails(user: User, outcome: service.ChargeOutcome, interviews: int, next_renewal) -> None:  # noqa: ANN001
    plan = MONTHLY_PLAN
    if outcome.first_payment:
        subject, html, text = emails.render_welcome(user.name, plan.name, interviews)
        await emails.send_email(user.email, subject, html, text)
    tx = outcome.transaction
    subject, html, text = emails.render_subscription(
        user.name,
        plan.name,
        service.money(tx.amount, tx.currency),
        interviews,
        tx.razorpay_payment_id,
        tx.created_at,
        next_renewal,
    )
    await emails.send_email(user.email, subject, html, text)


@router.get(
    "/plan",
    response_model=PlanResponse,
    summary="Plan details (public)",
    description="The plan: one-time price, how many days it lasts and interviews included.",
)
async def get_plan() -> PlanResponse:
    return PlanResponse(plan=service.plan_dto(MONTHLY_PLAN))


@router.post(
    "/checkout",
    status_code=201,
    response_model=CheckoutResponse,
    summary="Start a one-time payment",
    description="Creates a Razorpay order (one-time payment, no auto-renewal). Open Razorpay Checkout with "
    "`key: keyId` and `order_id: orderId`, then send its result to `/verify`. Limited to 10 per hour per user.",
    responses=errors(401, 409, 429, 502, 503),
)
async def checkout(user: CurrentUser, session: Session) -> CheckoutResponse:
    rate_limit(f"subscription-checkout:{user.id}", 10, 60 * 60 * 1000)
    order_id = await service.start_checkout(session, user)
    return CheckoutResponse(key_id=razorpay.key_id(), order_id=order_id, plan=service.plan_dto(MONTHLY_PLAN))


@router.post(
    "/verify",
    response_model=VerifyPaymentResponse,
    summary="Confirm the payment after Checkout",
    description="Send the three values Razorpay Checkout returns. The signature is verified and the payment is "
    "re-checked with Razorpay before the subscription is activated, so the browser can't fake a payment. "
    "Sends the welcome and subscription emails the first time. `status: pending` means the payment isn't captured "
    "yet; the webhook will activate it, so poll `GET /me`.",
    responses=errors(400, 401, 404, 502, 503),
)
async def verify(
    body: VerifyPaymentRequest, user: CurrentUser, session: Session, background: BackgroundTasks
) -> VerifyPaymentResponse:
    sub, outcome, paid = await service.confirm_checkout(
        session, user, body.razorpay_payment_id, body.razorpay_order_id, body.razorpay_signature
    )
    if outcome is not None:
        background.add_task(_send_charge_emails, user, outcome, sub.interviews_included, sub.current_period_end)
    return VerifyPaymentResponse(
        status="active" if paid and service.is_entitled(sub) else "pending",
        subscription=service.subscription_dto(sub),
    )


@router.get(
    "/me",
    response_model=SubscriptionMeResponse,
    summary="My subscription, interview count and transactions",
    description="Subscription status and renewal date, how many interviews are included, used and remaining this "
    "period, and the payment history (newest first, at most 100).",
    responses=errors(401),
)
async def me(user: CurrentUser, session: Session) -> SubscriptionMeResponse:
    sub = await service.get_subscription(session, user.id)
    transactions = await service.list_transactions(session, user.id)
    return SubscriptionMeResponse(
        subscribed=service.is_entitled(sub),
        subscription=service.subscription_dto(sub) if sub else None,
        interviews=service.quota(sub),
        transactions=[service.transaction_dto(tx) for tx in transactions],
    )


@router.post(
    "/webhook",
    summary="Razorpay webhook (called by Razorpay)",
    description="Set this URL in the Razorpay dashboard with the event `payment.captured`. Verified with "
    "`X-Razorpay-Signature` and `RAZORPAY_WEBHOOK_SECRET`. A captured payment records the transaction and "
    "starts the paid period with a fresh interview count.",
    responses=errors(400, 503),
)
async def webhook(
    request: Request,
    session: Session,
    background: BackgroundTasks,
    x_razorpay_signature: str | None = Header(default=None),
) -> dict[str, bool]:
    raw = await request.body()
    if not razorpay.verify_webhook_signature(raw, x_razorpay_signature or ""):
        raise ApiError(400, "invalid_signature", "Webhook signature is invalid")
    try:
        event = json.loads(raw)
    except ValueError:
        raise ApiError(400, "invalid_json", "Request body is not valid JSON") from None

    outcome = await service.apply_webhook(session, event)
    if outcome is not None:
        user = await session.get(User, outcome.transaction.user_id)
        sub = await service.get_subscription(session, outcome.transaction.user_id)
        if user is not None and sub is not None:
            background.add_task(_send_charge_emails, user, outcome, sub.interviews_included, sub.current_period_end)
    return {"ok": True}
