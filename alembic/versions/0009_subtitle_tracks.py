"""add subtitle content tracks"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_subtitle_tracks"
down_revision: str | None = "0008_simple_workflow_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "subtitle_tracks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("track_id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("track_type", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("source_filename", sa.String(length=255), nullable=True),
        sa.Column("source_sha256", sa.String(length=64), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("review_state", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("fallback_policy", sa.String(length=32), nullable=False, server_default="fallback_to_translation"),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_subtitle_tracks_track_id", "subtitle_tracks", ["track_id"], unique=True)
    op.create_index("ix_subtitle_tracks_project_id", "subtitle_tracks", ["project_id"], unique=False)
    op.create_index("ix_subtitle_tracks_run_id", "subtitle_tracks", ["run_id"], unique=False)
    op.create_index("ix_subtitle_tracks_track_type", "subtitle_tracks", ["track_type"], unique=False)
    op.create_table(
        "subtitle_track_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("item_id", sa.String(length=64), nullable=False),
        sa.Column("track_id", sa.String(length=64), sa.ForeignKey("subtitle_tracks.track_id"), nullable=False),
        sa.Column("cue_id", sa.String(length=64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="ready"),
        sa.Column("warning_codes", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_subtitle_track_items_item_id", "subtitle_track_items", ["item_id"], unique=True)
    op.create_index("ix_subtitle_track_items_track_id", "subtitle_track_items", ["track_id"], unique=False)
    op.create_index("ix_subtitle_track_items_cue_id", "subtitle_track_items", ["cue_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_subtitle_track_items_cue_id", table_name="subtitle_track_items")
    op.drop_index("ix_subtitle_track_items_track_id", table_name="subtitle_track_items")
    op.drop_index("ix_subtitle_track_items_item_id", table_name="subtitle_track_items")
    op.drop_table("subtitle_track_items")
    op.drop_index("ix_subtitle_tracks_track_type", table_name="subtitle_tracks")
    op.drop_index("ix_subtitle_tracks_run_id", table_name="subtitle_tracks")
    op.drop_index("ix_subtitle_tracks_project_id", table_name="subtitle_tracks")
    op.drop_index("ix_subtitle_tracks_track_id", table_name="subtitle_tracks")
    op.drop_table("subtitle_tracks")
