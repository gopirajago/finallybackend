"""add_broker_settings

Revision ID: 48839d97e372
Revises: 39dd981e7637
Create Date: 2026-03-10 22:05:55.469448

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '48839d97e372'
down_revision: Union[str, None] = '39dd981e7637'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'broker_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('broker', sa.String(length=50), nullable=False),
        sa.Column('api_key', sa.String(), nullable=True),
        sa.Column('api_secret', sa.String(), nullable=True),
        sa.Column('access_token', sa.Text(), nullable=True),
        sa.Column('token_generated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_broker_settings_id', 'broker_settings', ['id'], unique=False)
    op.create_index('ix_broker_settings_user_id', 'broker_settings', ['user_id'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_broker_settings_user_id', table_name='broker_settings')
    op.drop_index('ix_broker_settings_id', table_name='broker_settings')
    op.drop_table('broker_settings')
