"""tts generation metadata"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_tts_generation"
down_revision: str | None = "0003_content_transform"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tts_generations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.String(length=64), sa.ForeignKey("projects.project_id"), nullable=False),
        sa.Column("segment_id", sa.String(length=64), nullable=False),
        sa.Column("generation_id", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("voice_id", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("cache_status", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("artifact_path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("character_count", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("credential_ref", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("generation_id"),
    )
    op.create_index("ix_tts_generations_project_id", "tts_generations", ["project_id"])
    op.create_index("ix_tts_generations_segment_id", "tts_generations", ["segment_id"])
    op.create_index("ix_tts_generations_generation_id", "tts_generations", ["generation_id"])
    op.create_index("ix_tts_generations_request_hash", "tts_generations", ["request_hash"])


def downgrade() -> None:
    op.drop_index("ix_tts_generations_request_hash", table_name="tts_generations")
    op.drop_index("ix_tts_generations_generation_id", table_name="tts_generations")
    op.drop_index("ix_tts_generations_segment_id", table_name="tts_generations")
    op.drop_index("ix_tts_generations_project_id", table_name="tts_generations")
    op.drop_table("tts_generations")
