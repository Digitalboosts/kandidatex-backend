"""Subscription rules: who may create interviews, recording payments, and applying Razorpay events."""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..errors import ApiError
from ..models import PaymentTransaction, Subscription, User, UserRole, utcnow
from ..serializers import iso
from . import razorpay_client as razorpay
from .plans import MONTHLY_PLAN, PLANS, Plan
from .schemas import InterviewQuotaDTO, PlanDTO, SubscriptionDTO, TransactionDTO

logger = logging.getLogger("app.subscription")


def money(amount: int, currency: str) -> str:
    symbol = {"INR": "₹", "USD": "$"}.get(currency, f"{currency} ")
    value = amount / 100
    return f"{symbol}{value:,.0f}" if value == int(value) else f"{symbol}{value:,.2f}"


def plan_dto(plan: Plan) -> PlanDTO:
    return PlanDTO(
        code=plan.code,
        name=plan.name,
        description=plan.description,
        amount=plan.amount,
        amount_display=money(plan.amount, plan.currency),
        currency=plan.currency,
        duration_days=plan.duration_days,
        interviews_per_period=plan.interviews_per_period,
    )


def is_entitled(sub: Subscription | None) -> bool:
    """Paid and still inside the paid period (one-time payment: access ends when the period ends)."""
    return (
        sub is not None
        and sub.status == "ACTIVE"
        and sub.current_period_end is not None
        and sub.current_period_end > utcnow()
    )


def quota(sub: Subscription | None) -> InterviewQuotaDTO:
    if sub is None or not is_entitled(sub):
        return InterviewQuotaDTO(included=0, used=0, remaining=0)
    return InterviewQuotaDTO(
        included=sub.interviews_included,
        used=sub.interviews_used,
        remaining=max(sub.interviews_included - sub.interviews_used, 0),
    )


def subscription_dto(sub: Subscription) -> SubscriptionDTO:
    plan = PLANS.get(sub.plan_code, MONTHLY_PLAN)
    return SubscriptionDTO(
        status=sub.status,
        active=is_entitled(sub),
        plan=plan_dto(plan),
        current_period_start=iso(sub.current_period_start),
        current_period_end=iso(sub.current_period_end),
        interviews=quota(sub),
    )


def transaction_dto(tx: PaymentTransaction) -> TransactionDTO:
    return TransactionDTO(
        id=tx.id,
        razorpay_payment_id=tx.razorpay_payment_id,
        amount=tx.amount,
        amount_display=money(tx.amount, tx.currency),
        currency=tx.currency,
        status=tx.status,
        method=tx.method,
        created_at=iso(tx.created_at) or "",
    )


async def get_subscription(session: AsyncSession, user_id: str) -> Subscription | None:
    return await session.scalar(select(Subscription).where(Subscription.user_id == user_id))


async def list_transactions(session: AsyncSession, user_id: str, limit: int = 100) -> list[PaymentTransaction]:
    query = (
        select(PaymentTransaction)
        .where(PaymentTransaction.user_id == user_id)
        .order_by(PaymentTransaction.created_at.desc(), PaymentTransaction.id.desc())
        .limit(limit)
    )
    return list((await session.scalars(query)).all())


# --- The subscription condition -------------------------------------------------------------------------------


async def consume_interview_quota(session: AsyncSession, user: User) -> None:
    """Call right before adding an Interview; the caller commits. Takes one interview from the monthly allowance
    in a single atomic UPDATE, so two parallel requests can never go past the limit. Admins are not metered."""
    if user.role == UserRole.ADMIN or not get_settings().subscription_enforced:
        return
    now = utcnow()
    result = await session.execute(
        update(Subscription)
        .where(
            Subscription.user_id == user.id,
            Subscription.interviews_used < Subscription.interviews_included,
            Subscription.status == "ACTIVE",
            Subscription.current_period_end > now,
        )
        .values(interviews_used=Subscription.interviews_used + 1)
    )
    if result.rowcount == 1:
        return
    sub = await get_subscription(session, user.id)
    if sub is not None and is_entitled(sub):
        raise ApiError(
            402,
            "interview_limit_reached",
            "You have used all interviews included in your plan. Buy the plan again after it ends for a new allowance.",
            {"included": sub.interviews_included, "used": sub.interviews_used},
        )
    raise ApiError(402, "subscription_required", "An active subscription is required to create interviews")


# --- Checkout -------------------------------------------------------------------------------------------------


async def start_checkout(session: AsyncSession, user: User, plan: Plan = MONTHLY_PLAN) -> str:
    """Creates a one-time Razorpay order for the user and returns its ID (the frontend opens Checkout with it).
    The order ID is stored in the ``razorpay_subscription_id`` column (the table predates one-time payments)."""
    existing = await get_subscription(session, user.id)
    if is_entitled(existing):
        raise ApiError(409, "already_subscribed", "You already have an active plan")
    created = await razorpay.create_order(plan, user.id)
    if existing is None:
        session.add(
            Subscription(
                user_id=user.id,
                plan_code=plan.code,
                razorpay_plan_id="one_time",
                razorpay_subscription_id=created["id"],
                status="CREATED",
                interviews_included=plan.interviews_per_period,
                interviews_used=0,
            )
        )
    else:  # an earlier attempt that never got paid, or a plan that ended: start over
        existing.plan_code = plan.code
        existing.razorpay_plan_id = "one_time"
        existing.razorpay_subscription_id = created["id"]
        existing.status = "CREATED"
        existing.interviews_included = plan.interviews_per_period
        existing.interviews_used = 0
        existing.current_period_start = None
        existing.current_period_end = None
    await session.commit()
    return created["id"]


# --- Recording payments ---------------------------------------------------------------------------------------


@dataclass
class ChargeOutcome:
    transaction: PaymentTransaction
    first_payment: bool  # the user's first successful payment ever: also send the welcome email


async def record_charge(session: AsyncSession, sub: Subscription, payment: dict[str, Any]) -> ChargeOutcome | None:
    """Stores a captured payment and starts the paid period with a fresh interview allowance.
    Returns None when this payment was already recorded (webhooks and the checkout callback can both report it)."""
    payment_id = payment["id"]
    if await session.scalar(select(PaymentTransaction.id).where(PaymentTransaction.razorpay_payment_id == payment_id)):
        return None
    first = (
        await session.scalar(
            select(PaymentTransaction.id).where(
                PaymentTransaction.user_id == sub.user_id, PaymentTransaction.status == "CAPTURED"
            )
        )
    ) is None
    tx = PaymentTransaction(
        user_id=sub.user_id,
        subscription_id=sub.id,
        razorpay_payment_id=payment_id,
        razorpay_invoice_id=payment.get("invoice_id"),
        amount=int(payment.get("amount", 0)),
        currency=payment.get("currency", "INR"),
        status="CAPTURED",
        method=payment.get("method"),
    )
    session.add(tx)
    now = utcnow()
    sub.status = "ACTIVE"
    sub.current_period_start = now
    sub.current_period_end = now + timedelta(days=PLANS.get(sub.plan_code, MONTHLY_PLAN).duration_days)
    sub.interviews_used = 0
    try:
        await session.commit()
    except IntegrityError:  # the other path recorded it a moment ago
        await session.rollback()
        return None
    return ChargeOutcome(transaction=tx, first_payment=first)


async def confirm_checkout(
    session: AsyncSession, user: User, payment_id: str, order_id: str, signature: str
) -> tuple[Subscription, ChargeOutcome | None, bool]:
    """Checks the signature Razorpay Checkout returned, then confirms the payment with Razorpay itself.
    Returns (subscription, new charge or None, paid)."""
    if not razorpay.verify_checkout_signature(order_id, payment_id, signature):
        raise ApiError(400, "invalid_signature", "Payment signature is invalid")
    sub = await get_subscription(session, user.id)
    if sub is None or sub.razorpay_subscription_id != order_id:
        raise ApiError(404, "not_found", "No order matches this payment")
    payment = await razorpay.fetch_payment(payment_id)
    if payment.get("order_id") != order_id:
        raise ApiError(400, "invalid_signature", "Payment does not belong to this order")
    if payment.get("status") != "captured":
        return sub, None, False  # the webhook finishes the job once Razorpay captures it
    outcome = await record_charge(session, sub, payment)
    await session.refresh(sub)
    return sub, outcome, True


async def apply_webhook(session: AsyncSession, event: dict[str, Any]) -> ChargeOutcome | None:
    """Handles a verified Razorpay webhook (`payment.captured`). Unknown orders and events are ignored (answer 200)."""
    if event.get("event") != "payment.captured":
        return None
    payment = ((event.get("payload") or {}).get("payment") or {}).get("entity")
    if not payment or payment.get("status") != "captured" or not payment.get("order_id"):
        return None
    sub = await session.scalar(select(Subscription).where(Subscription.razorpay_subscription_id == payment["order_id"]))
    if sub is None:
        logger.warning("Webhook for unknown order %s", payment["order_id"])
        return None
    return await record_charge(session, sub, payment)
