"""add_claude_settings

Revision ID: 6ae34cf92fec
Revises: 99c37d22290b
Create Date: 2026-03-10 23:48:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '6ae34cf92fec'
down_revision: Union[str, None] = '99c37d22290b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'claude_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('api_key', sa.Text(), nullable=True),
        sa.Column('model', sa.String(length=100), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id'),
    )
    op.create_index('ix_claude_settings_id', 'claude_settings', ['id'], unique=False)
    op.create_index('ix_claude_settings_user_id', 'claude_settings', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_claude_settings_user_id', table_name='claude_settings')
    op.drop_index('ix_claude_settings_id', table_name='claude_settings')
    op.drop_table('claude_settings')
