"""Subscription plan details. Change the price or the interview allowance here."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Plan:
    code: str
    name: str
    description: str
    amount: int  # smallest currency unit (paise): 1_000 paise = INR 10
    currency: str
    duration_days: int  # how long one payment gives access (one-time payment, no auto-renewal)
    interviews_per_period: int  # interviews that can be created during that time

    @property
    def amount_major(self) -> int:
        return self.amount // 100


MONTHLY_PLAN = Plan(
    code="monthly_100",
    name="Kandidatex Monthly",
    description="50 monitored interviews for 30 days. One-time payment, no auto-renewal.",
    amount=10 * 100,
    currency="INR",
    duration_days=30,
    interviews_per_period=50,
)

PLANS = {MONTHLY_PLAN.code: MONTHLY_PLAN}
