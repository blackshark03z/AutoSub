"""content transform revisions and provider requests"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_content_transform"
down_revision: str | None = "0002_timeline_revisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("cache_status", sa.String(length=32), nullable=False),
        sa.Column("credential_ref", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("request_hash"),
    )
    op.create_index("ix_provider_requests_provider", "provider_requests", ["provider"])
    op.create_index("ix_provider_requests_request_hash", "provider_requests", ["request_hash"])
    op.create_table(
        "content_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.String(length=64), sa.ForeignKey("projects.project_id"), nullable=False),
        sa.Column("revision_id", sa.String(length=64), nullable=False),
        sa.Column("timeline_revision_id", sa.String(length=64), nullable=False),
        sa.Column("provider_request_hash", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_content_revisions_project_id", "content_revisions", ["project_id"])
    op.create_index("ix_content_revisions_revision_id", "content_revisions", ["revision_id"])
    op.create_index("ix_content_revisions_timeline_revision_id", "content_revisions", ["timeline_revision_id"])


def downgrade() -> None:
    op.drop_index("ix_content_revisions_timeline_revision_id", table_name="content_revisions")
    op.drop_index("ix_content_revisions_revision_id", table_name="content_revisions")
    op.drop_index("ix_content_revisions_project_id", table_name="content_revisions")
    op.drop_table("content_revisions")
    op.drop_index("ix_provider_requests_request_hash", table_name="provider_requests")
    op.drop_index("ix_provider_requests_provider", table_name="provider_requests")
    op.drop_table("provider_requests")
