"""0003 idempotency keys

Revision ID: c3a71e5d9b20
Revises: 95d91d42a91e
Create Date: 2026-11-22 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3a71e5d9b20'
down_revision: Union[str, None] = '95d91d42a91e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('idempotency_keys',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('user_id', sa.String(), nullable=False),
    sa.Column('key', sa.String(), nullable=False),
    sa.Column('fingerprint', sa.String(), nullable=False),
    sa.Column('experiment_id', sa.String(), nullable=False),
    sa.Column('created_at', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['experiment_id'], ['experiments.id'], name='fk_idempotency_keys_experiment_id_experiments', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_idempotency_keys_user_id_users', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='pk_idempotency_keys'),
    sa.UniqueConstraint('user_id', 'key', name='uq_idempotency_keys_user_id_key'),
    )


def downgrade() -> None:
    op.drop_table('idempotency_keys')
