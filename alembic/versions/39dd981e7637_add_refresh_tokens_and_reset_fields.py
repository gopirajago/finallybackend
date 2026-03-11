"""add_refresh_tokens_and_reset_fields

Revision ID: 39dd981e7637
Revises: d79baad48657
Create Date: 2026-03-10 19:23:53.236500

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '39dd981e7637'
down_revision: Union[str, None] = 'd79baad48657'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'refresh_tokens',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('token', sa.String(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('is_revoked', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_refresh_tokens_id'), 'refresh_tokens', ['id'], unique=False)
    op.create_index(op.f('ix_refresh_tokens_token'), 'refresh_tokens', ['token'], unique=True)

    op.add_column('users', sa.Column('reset_token', sa.String(), nullable=True))
    op.add_column('users', sa.Column('reset_token_expires', sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f('ix_users_reset_token'), 'users', ['reset_token'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_users_reset_token'), table_name='users')
    op.drop_column('users', 'reset_token_expires')
    op.drop_column('users', 'reset_token')

    op.drop_index(op.f('ix_refresh_tokens_token'), table_name='refresh_tokens')
    op.drop_index(op.f('ix_refresh_tokens_id'), table_name='refresh_tokens')
    op.drop_table('refresh_tokens')
