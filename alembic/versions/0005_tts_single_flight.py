"""durable TTS request single-flight reservations"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_tts_single_flight"
down_revision: str | None = "0004_tts_generation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tts_request_reservations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("voice_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("owner_token", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("generation_id", sa.String(length=64), nullable=True),
        sa.Column("last_error", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("request_hash"),
    )
    op.create_index(
        "ix_tts_request_reservations_request_hash",
        "tts_request_reservations",
        ["request_hash"],
    )


def downgrade() -> None:
    op.drop_index("ix_tts_request_reservations_request_hash", table_name="tts_request_reservations")
    op.drop_table("tts_request_reservations")
