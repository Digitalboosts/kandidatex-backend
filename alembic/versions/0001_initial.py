"""Initial schema (identical to the previous Prisma migration 0001_init).

If the tables already exist because the old Next.js backend created them with
Prisma, this migration does nothing, so pointing this backend at the existing
database and running ``alembic upgrade head`` is safe and keeps all data.

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

ROLE = ("ADMIN", "INTERVIEWER")
STATUS = ("SCHEDULED", "ACTIVE", "COMPLETED", "CANCELLED")
SEVERITY = ("INFO", "WARNING", "CRITICAL")


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("User"):
        return

    now = sa.text("CURRENT_TIMESTAMP")
    ts = sa.DateTime(timezone=False)
    json_type = sa.dialects.postgresql.JSONB() if bind.dialect.name == "postgresql" else sa.JSON()

    op.create_table(
        "User",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("email", sa.String, nullable=False),
        sa.Column("passwordHash", sa.String, nullable=False),
        sa.Column("role", sa.Enum(*ROLE, name="UserRole"), nullable=False, server_default="INTERVIEWER"),
        sa.Column("sessionVersion", sa.Integer, nullable=False, server_default="0"),
        sa.Column("createdAt", ts, nullable=False, server_default=now),
    )
    op.create_index("User_email_key", "User", ["email"], unique=True)

    op.create_table(
        "Interview",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("interviewerId", sa.String, sa.ForeignKey("User.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False),
        sa.Column("candidateName", sa.String, nullable=False),
        sa.Column("status", sa.Enum(*STATUS, name="InterviewStatus"), nullable=False, server_default="SCHEDULED"),
        sa.Column("livekitRoomName", sa.String, nullable=False),
        sa.Column("scheduledAt", ts),
        sa.Column("startedAt", ts),
        sa.Column("endedAt", ts),
        sa.Column("createdAt", ts, nullable=False, server_default=now),
        sa.Column("candidateJoinedAt", ts),
        sa.Column("consentAcceptedAt", ts),
    )
    op.create_index("Interview_livekitRoomName_key", "Interview", ["livekitRoomName"], unique=True)
    op.create_index("Interview_interviewerId_status_idx", "Interview", ["interviewerId", "status"])
    op.create_index("Interview_status_scheduledAt_idx", "Interview", ["status", "scheduledAt"])
    op.create_index("Interview_createdAt_idx", "Interview", ["createdAt"])

    op.create_table(
        "MonitoringEvent",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("interviewId", sa.String, sa.ForeignKey("Interview.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False),
        sa.Column("eventType", sa.String, nullable=False),
        sa.Column("message", sa.String, nullable=False),
        sa.Column("severity", sa.Enum(*SEVERITY, name="EventSeverity"), nullable=False, server_default="INFO"),
        sa.Column("metadata", json_type),
        sa.Column("dedupeKey", sa.String),
        sa.Column("createdAt", ts, nullable=False, server_default=now),
    )
    op.create_index("MonitoringEvent_dedupeKey_key", "MonitoringEvent", ["dedupeKey"], unique=True)
    op.create_index("MonitoringEvent_interviewId_createdAt_idx", "MonitoringEvent", ["interviewId", "createdAt"])
    op.create_index("MonitoringEvent_severity_createdAt_idx", "MonitoringEvent", ["severity", "createdAt"])

    op.create_table(
        "MonitoringStatus",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("interviewId", sa.String, sa.ForeignKey("Interview.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False),
        sa.Column("source", sa.String, nullable=False),
        sa.Column("screenDetected", sa.Boolean, nullable=False),
        sa.Column("visibilityPercentage", sa.Integer, nullable=False),
        sa.Column("clarity", sa.String, nullable=False),
        sa.Column("lighting", sa.String, nullable=False),
        sa.Column("stability", sa.String, nullable=False),
        sa.Column("alignment", sa.String, nullable=False),
        sa.Column("readinessScore", sa.Integer, nullable=False),
        sa.Column("updatedAt", ts, nullable=False),
    )
    op.create_index("MonitoringStatus_interviewId_key", "MonitoringStatus", ["interviewId"], unique=True)


def downgrade() -> None:
    for table in ("MonitoringStatus", "MonitoringEvent", "Interview", "User"):
        op.drop_table(table)
    if op.get_bind().dialect.name == "postgresql":
        for enum in ("EventSeverity", "InterviewStatus", "UserRole"):
            op.execute(f'DROP TYPE IF EXISTS "{enum}"')
