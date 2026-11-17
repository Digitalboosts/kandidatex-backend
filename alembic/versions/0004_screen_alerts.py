"""HIGH screen alerts: screenshot + vision-model response per interview and user.

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("InterviewSummary"):
        ts = sa.DateTime(timezone=False)
        now = sa.text("CURRENT_TIMESTAMP")
        op.create_table(
            "InterviewSummary",
            sa.Column("id", sa.String, primary_key=True),
            sa.Column("interviewId", sa.String, sa.ForeignKey("Interview.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False, unique=True),
            sa.Column("userId", sa.String, sa.ForeignKey("User.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False),
            sa.Column("summary", sa.String, nullable=False),
            sa.Column("wordCount", sa.Integer, nullable=False),
            sa.Column("alertCount", sa.Integer, nullable=False),
            sa.Column("createdAt", ts, nullable=False, server_default=now),
            sa.Column("updatedAt", ts, nullable=False, server_default=now),
        )
    if inspector.has_table("ScreenAlert"):
        return
    op.create_table(
        "ScreenAlert",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("interviewId", sa.String, sa.ForeignKey("Interview.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False),
        sa.Column("userId", sa.String, sa.ForeignKey("User.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False),
        sa.Column("application", sa.String, nullable=False),
        sa.Column("category", sa.String, nullable=False),
        sa.Column("activity", sa.String, nullable=False),
        sa.Column("alertLevel", sa.String, nullable=False),
        sa.Column("message", sa.String, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("imageContentType", sa.String, nullable=False),
        sa.Column("imageBlobPath", sa.String, nullable=False),
        sa.Column("imageBlobUrl", sa.String, nullable=False),
        sa.Column("createdAt", sa.DateTime(timezone=False), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ScreenAlert_interviewId_createdAt_idx", "ScreenAlert", ["interviewId", "createdAt"])
    op.create_index("ScreenAlert_userId_createdAt_idx", "ScreenAlert", ["userId", "createdAt"])


def downgrade() -> None:
    op.drop_index("ScreenAlert_userId_createdAt_idx", table_name="ScreenAlert")
    op.drop_index("ScreenAlert_interviewId_createdAt_idx", table_name="ScreenAlert")
    op.drop_table("ScreenAlert")
    op.drop_table("InterviewSummary")
