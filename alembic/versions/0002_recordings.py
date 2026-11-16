"""Interview recordings stored in Azure Blob Storage.

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("Recording"):
        return
    ts = sa.DateTime(timezone=False)
    now = sa.text("CURRENT_TIMESTAMP")
    op.create_table(
        "Recording",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("interviewId", sa.String, sa.ForeignKey("Interview.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False),
        sa.Column("status", sa.Enum("RECORDING", "READY", "FAILED", name="RecordingStatus"), nullable=False, server_default="RECORDING"),
        sa.Column("blobPath", sa.String, nullable=False),
        sa.Column("contentType", sa.String, nullable=False),
        sa.Column("sizeBytes", sa.BigInteger),
        sa.Column("durationSeconds", sa.Integer),
        sa.Column("chunkCount", sa.Integer),
        sa.Column("startedAt", ts, nullable=False, server_default=now),
        sa.Column("endedAt", ts),
        sa.Column("createdAt", ts, nullable=False, server_default=now),
    )
    op.create_index("Recording_interviewId_createdAt_idx", "Recording", ["interviewId", "createdAt"])


def downgrade() -> None:
    op.drop_index("Recording_interviewId_createdAt_idx", table_name="Recording")
    op.drop_table("Recording")
    if op.get_bind().dialect.name == "postgresql":
        op.execute('DROP TYPE IF EXISTS "RecordingStatus"')
