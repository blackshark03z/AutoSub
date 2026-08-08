"""add simple workflow runs"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_simple_workflow_runs"
down_revision: str | None = "0007_project_fixture_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "simple_workflow_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("source_metadata_json", sa.Text(), nullable=False),
        sa.Column("requested_settings_json", sa.Text(), nullable=False),
        sa.Column("current_phase", sa.String(length=64), nullable=False),
        sa.Column("internal_state", sa.String(length=64), nullable=False),
        sa.Column("run_directory", sa.Text(), nullable=False),
        sa.Column("output_path", sa.Text(), nullable=True),
        sa.Column("output_hash", sa.String(length=64), nullable=True),
        sa.Column("approval_state", sa.String(length=32), nullable=False, server_default="not_reviewed"),
        sa.Column("failure_category", sa.String(length=64), nullable=True),
        sa.Column("retry_parent_run_id", sa.String(length=64), nullable=True),
        sa.Column("is_test_fixture", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_simple_workflow_runs_run_id", "simple_workflow_runs", ["run_id"], unique=True)
    op.create_index("ix_simple_workflow_runs_project_id", "simple_workflow_runs", ["project_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_simple_workflow_runs_project_id", table_name="simple_workflow_runs")
    op.drop_index("ix_simple_workflow_runs_run_id", table_name="simple_workflow_runs")
    op.drop_table("simple_workflow_runs")
