"""0001 core tables

Revision ID: 690ac4b92ae5
Revises: 
Create Date: 2026-11-16 09:22:05.950033

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '690ac4b92ae5'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Bản autogenerate đã chỉnh tay: thêm tên ràng buộc, CHECK, ON DELETE, index.
    op.create_table('labs',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('code', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('service_key', sa.String(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('enabled', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.String(), nullable=False),
    sa.Column('updated_at', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id', name='pk_labs'),
    sa.UniqueConstraint('code', name='uq_labs_code'),
    sa.CheckConstraint("status IN ('real', 'simulated', 'planned')", name='ck_labs_status'),
    sa.CheckConstraint('enabled IN (0, 1)', name='ck_labs_enabled'),
    )
    op.create_table('users',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('email', sa.String(), nullable=False),
    sa.Column('password_hash', sa.String(), nullable=False),
    sa.Column('full_name', sa.String(), nullable=False),
    sa.Column('role', sa.String(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('created_at', sa.String(), nullable=False),
    sa.Column('updated_at', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id', name='pk_users'),
    sa.UniqueConstraint('email', name='uq_users_email'),
    sa.CheckConstraint("role IN ('admin', 'user')", name='ck_users_role'),
    sa.CheckConstraint("status IN ('active', 'disabled')", name='ck_users_status'),
    sa.CheckConstraint('email = lower(email)', name='ck_users_email_lowercase'),
    )
    op.create_table('experiments',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('user_id', sa.String(), nullable=False),
    sa.Column('lab_id', sa.String(), nullable=False),
    sa.Column('state', sa.String(), nullable=False),
    sa.Column('input_data', sa.JSON(), nullable=False),
    sa.Column('error_code', sa.String(), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('created_at', sa.String(), nullable=False),
    sa.Column('started_at', sa.String(), nullable=True),
    sa.Column('finished_at', sa.String(), nullable=True),
    sa.Column('updated_at', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['lab_id'], ['labs.id'], name='fk_experiments_lab_id_labs', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_experiments_user_id_users', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name='pk_experiments'),
    sa.CheckConstraint("state IN ('queued', 'running', 'done', 'failed')", name='ck_experiments_state'),
    )
    op.create_index('ix_experiments_lab_id_created_at', 'experiments', ['lab_id', 'created_at'])
    op.create_index('ix_experiments_user_id_created_at', 'experiments', ['user_id', 'created_at'])
    op.create_table('experiment_events',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('experiment_id', sa.String(), nullable=False),
    sa.Column('level', sa.String(), nullable=False),
    sa.Column('event_type', sa.String(), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('data_json', sa.JSON(none_as_null=True), nullable=True),
    sa.Column('created_at', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['experiment_id'], ['experiments.id'], name='fk_experiment_events_experiment_id_experiments', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='pk_experiment_events'),
    sa.CheckConstraint("level IN ('info', 'warn', 'error')", name='ck_experiment_events_level'),
    )
    op.create_index('ix_experiment_events_experiment_id_id', 'experiment_events', ['experiment_id', 'id'])
    op.create_table('lab_results',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('experiment_id', sa.String(), nullable=False),
    sa.Column('result_kind', sa.String(), nullable=False),
    sa.Column('result_data', sa.JSON(), nullable=False),
    sa.Column('limitations', sa.JSON(), nullable=False),
    sa.Column('guard_passed', sa.Integer(), nullable=False),
    sa.Column('guard_details', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['experiment_id'], ['experiments.id'], name='fk_lab_results_experiment_id_experiments', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='pk_lab_results'),
    sa.UniqueConstraint('experiment_id', name='uq_lab_results_experiment_id'),
    sa.CheckConstraint("result_kind IN ('real', 'simulated', 'planned')", name='ck_lab_results_result_kind'),
    sa.CheckConstraint('guard_passed IN (0, 1)', name='ck_lab_results_guard_passed'),
    sa.CheckConstraint('guard_passed = 0 OR json_array_length(limitations) > 0', name='ck_lab_results_limitations_required'),
    )


def downgrade() -> None:
    # Bản autogenerate đã chỉnh tay: thêm tên ràng buộc, CHECK, ON DELETE, index.
    op.drop_table('lab_results')
    op.drop_index('ix_experiment_events_experiment_id_id', table_name='experiment_events')
    op.drop_table('experiment_events')
    op.drop_index('ix_experiments_user_id_created_at', table_name='experiments')
    op.drop_index('ix_experiments_lab_id_created_at', table_name='experiments')
    op.drop_table('experiments')
    op.drop_table('users')
    op.drop_table('labs')
