"""Razorpay monthly subscriptions and payment transactions.

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    ts = sa.DateTime(timezone=False)
    now = sa.text("CURRENT_TIMESTAMP")
    if not inspector.has_table("Subscription"):
        op.create_table(
            "Subscription",
            sa.Column("id", sa.String, primary_key=True),
            sa.Column("userId", sa.String, sa.ForeignKey("User.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False, unique=True),
            sa.Column("planCode", sa.String, nullable=False),
            sa.Column("razorpayPlanId", sa.String, nullable=False),
            sa.Column("razorpaySubscriptionId", sa.String, nullable=False, unique=True),
            sa.Column("status", sa.String, nullable=False, server_default="CREATED"),
            sa.Column("interviewsIncluded", sa.Integer, nullable=False),
            sa.Column("interviewsUsed", sa.Integer, nullable=False, server_default="0"),
            sa.Column("currentPeriodStart", ts),
            sa.Column("currentPeriodEnd", ts),
            sa.Column("createdAt", ts, nullable=False, server_default=now),
            sa.Column("updatedAt", ts, nullable=False, server_default=now),
        )
    if not inspector.has_table("PaymentTransaction"):
        op.create_table(
            "PaymentTransaction",
            sa.Column("id", sa.String, primary_key=True),
            sa.Column("userId", sa.String, sa.ForeignKey("User.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False),
            sa.Column("subscriptionId", sa.String, sa.ForeignKey("Subscription.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False),
            sa.Column("razorpayPaymentId", sa.String, nullable=False, unique=True),
            sa.Column("razorpayInvoiceId", sa.String),
            sa.Column("amount", sa.BigInteger, nullable=False),
            sa.Column("currency", sa.String, nullable=False, server_default="INR"),
            sa.Column("status", sa.String, nullable=False),
            sa.Column("method", sa.String),
            sa.Column("createdAt", ts, nullable=False, server_default=now),
        )
        op.create_index("PaymentTransaction_userId_createdAt_idx", "PaymentTransaction", ["userId", "createdAt"])


def downgrade() -> None:
    op.drop_index("PaymentTransaction_userId_createdAt_idx", table_name="PaymentTransaction")
    op.drop_table("PaymentTransaction")
    op.drop_table("Subscription")
