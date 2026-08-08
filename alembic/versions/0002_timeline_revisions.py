"""timeline revisions

Revision ID: 0002_timeline_revisions
Revises: 0001_bootstrap
Create Date: 2026-07-13
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_timeline_revisions"
down_revision = "0001_bootstrap"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "timeline_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.String(length=64), sa.ForeignKey("projects.project_id"), nullable=False),
        sa.Column("revision_id", sa.String(length=64), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_timeline_revisions_project_id", "timeline_revisions", ["project_id"])
    op.create_index("ix_timeline_revisions_revision_id", "timeline_revisions", ["revision_id"])


def downgrade() -> None:
    op.drop_table("timeline_revisions")
