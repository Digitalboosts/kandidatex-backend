"""Grant a user an active subscription without payment.

Usage: python scripts/grant_subscription.py user@example.com [days] [interviews]
"""

import asyncio
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db import get_sessionmaker  # noqa: E402
from app.models import Subscription, User, new_id, utcnow  # noqa: E402
from app.subscription.plans import MONTHLY_PLAN  # noqa: E402


async def main(email: str, days: int, interviews: int) -> None:
    async with get_sessionmaker()() as session:
        user = await session.scalar(select(User).where(User.email.ilike(email)))
        if user is None:
            print(f"No user with email {email}. They must sign up first.")
            return
        now = utcnow()
        sub = await session.scalar(select(Subscription).where(Subscription.user_id == user.id))
        if sub is None:
            sub = Subscription(
                user_id=user.id,
                plan_code=MONTHLY_PLAN.code,
                razorpay_plan_id="manual_grant",
                razorpay_subscription_id=f"manual_{new_id()}",
            )
            session.add(sub)
        sub.status = "ACTIVE"
        sub.interviews_included = interviews
        sub.interviews_used = 0
        sub.current_period_start = now
        sub.current_period_end = now + timedelta(days=days)
        await session.commit()
        print(f"Granted {email}: ACTIVE, {interviews} interviews, until {sub.current_period_end}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 365, int(sys.argv[3]) if len(sys.argv) > 3 else 1000))
